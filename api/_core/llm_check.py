"""Second-opinion eligibility check with Claude Haiku.

The text rules in eligibility.py are fast and free but miss unusual wording ("Government customer
requires U.S. citizenship", "4+ years developing ..."). For each job that the rules let through,
Claude Haiku reads the description and returns structured facts; any restriction it finds blocks
the job. It never un-blocks a job the rules rejected, and if the API is unavailable, out of budget
or declines, the rule-based decision stands.

Cost: roughly $0.0002-0.001 per job (Claude Haiku 5.5 at $0.10 / $0.50 per million input / output
tokens). Spend is tracked from each response's token counts and capped monthly in Settings.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field

import anthropic

MODEL = "claude-haiku-5-5"
PRICE_INPUT_PER_MTOK = 0.10   # USD, prompts up to 100K tokens (ours are far smaller)
PRICE_OUTPUT_PER_MTOK = 0.50
MAX_DESCRIPTION_CHARS = 30000  # ~7.5K tokens; longer postings are cut here and the note says so
MAX_CALLS_PER_RUN = 40

SYSTEM = """You check job postings for a candidate on an F-1 student visa (OPT) who will need H-1B \
sponsorship, has about {years} years of experience, and is not a US citizen or permanent resident.

Read the posting and report only what it actually states:
- citizenship_required: true if the role is limited to US citizens, green card / permanent residents, \
or "U.S. persons" (ITAR / export control), including phrasing like "the government customer requires \
U.S. citizenship". Equal-opportunity boilerplate ("without regard to citizenship status") is NOT a requirement.
- clearance_required: true if it requires or expects a security clearance, public trust, polygraph, \
or a federal / government background investigation or suitability determination.
- sponsorship: "will_not_sponsor" if it says it can't sponsor visas or requires authorization to work \
without sponsorship now or in the future; "will_sponsor" if it says it sponsors visas / H-1B or welcomes \
OPT candidates; otherwise "not_mentioned". An application question ("Will you require sponsorship?") alone \
is "not_mentioned".
- min_years_required: the minimum years of professional experience the posting REQUIRES for this role \
(e.g. "4+ years developing ..." -> 4, "3-5 years" -> 3). Ignore years listed only as preferred / nice to \
have. Use -1 if no number of years is required.
- posted_by_recruiter: true if the poster is not the employer: a staffing / recruiting agency, an IT consultancy placing people at a client ("our client", "on behalf of", C2C / W2 contract via a vendor), or a job board / aggregator reposting another company's job. false if the hiring company posts its own job.
- evidence: the exact sentence(s) supporting any restriction or sponsorship statement, at most 300 \
characters; empty string if there is none."""

SCHEMA = {
    "type": "object",
    "properties": {
        "citizenship_required": {"type": "boolean"},
        "clearance_required": {"type": "boolean"},
        "sponsorship": {"type": "string", "enum": ["will_sponsor", "will_not_sponsor", "not_mentioned"]},
        "min_years_required": {"type": "integer"},
        "posted_by_recruiter": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
    "required": ["citizenship_required", "clearance_required", "sponsorship", "min_years_required",
                 "posted_by_recruiter", "evidence"],
    "additionalProperties": False,
}


def configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def _get_client() -> anthropic.Anthropic:
    global _client
    with _client_lock:
        if _client is None:
            _client = anthropic.Anthropic(timeout=30.0, max_retries=1)  # reads ANTHROPIC_API_KEY
        return _client


@dataclass
class Usage:
    """Token and dollar totals for one run (thread-safe)."""
    calls: int = 0
    errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def cost_usd(self) -> float:
        return (self.input_tokens * PRICE_INPUT_PER_MTOK + self.output_tokens * PRICE_OUTPUT_PER_MTOK) / 1_000_000

    def add(self, input_tokens: int = 0, output_tokens: int = 0, error: str = "") -> None:
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            if error:
                self.errors += 1
                self.last_error = error[:300]


def check(title: str, company: str, description: str, max_years: int, usage: Usage) -> dict | None:
    """Ask Claude Haiku what the posting requires. Returns the parsed facts, or None on any failure."""
    body = description[:MAX_DESCRIPTION_CHARS]
    try:
        response = _get_client().messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM.format(years=max_years or 2),
            messages=[{"role": "user", "content": f"Job title: {title}\nCompany: {company}\n\nJob posting:\n{body}"}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.AuthenticationError:
        usage.add(error="Invalid ANTHROPIC_API_KEY")
        return None
    except anthropic.PermissionDeniedError as e:
        usage.add(error=f"Permission denied (check credit balance / key permissions): {e.message}")
        return None
    except anthropic.RateLimitError:
        usage.add(error="Rate limited by the Anthropic API")
        return None
    except anthropic.BadRequestError as e:
        usage.add(error=f"Bad request: {e.message}")  # e.g. "credit balance is too low"
        return None
    except anthropic.APIStatusError as e:
        usage.add(error=f"Anthropic API error {e.status_code}")
        return None
    except anthropic.APIConnectionError:
        usage.add(error="Could not reach the Anthropic API")
        return None

    u = response.usage
    tokens_in = (u.input_tokens or 0) + (getattr(u, "cache_creation_input_tokens", 0) or 0) + \
        (getattr(u, "cache_read_input_tokens", 0) or 0)
    if response.stop_reason == "refusal":
        usage.add(tokens_in, u.output_tokens or 0, error="Model declined to answer")
        return None
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        facts = json.loads(text)
    except (ValueError, TypeError):
        usage.add(tokens_in, u.output_tokens or 0, error=f"Unparseable answer (stop_reason={response.stop_reason})")
        return None
    usage.add(tokens_in, u.output_tokens or 0)
    facts["truncated"] = len(description) > MAX_DESCRIPTION_CHARS
    return facts
