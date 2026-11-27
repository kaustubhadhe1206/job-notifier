"""Work-authorization checks for an F-1 student who needs H-1B sponsorship.

For each new job that passes the title/location filters we read the full description and:
  1. block it if it requires a security clearance, US citizenship, a green card, or "US persons"
     (ITAR/export control),
  2. block it if it says there is no visa sponsorship,
  3. otherwise look for evidence the employer sponsors: the posting says so, the company is on
     your own sponsor list, or the company appears in USCIS H-1B approval data.

Text rules are deliberately conservative (they skip "no clearance required", EEO boilerplate
like "without regard to citizenship status", and application questions ending in "?").
"""
from __future__ import annotations

import gzip
import html
import json
import os
import re
import threading
from dataclasses import dataclass, field, replace

DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "h1b_sponsors.json.gz")

# ---------------------------------------------------------------- company names

LEGAL_SUFFIXES = {
    "INC", "INCORPORATED", "LLC", "LLP", "LP", "LTD", "LIMITED", "CORP", "CORPORATION", "CO", "COMPANY",
    "PLC", "PC", "PLLC", "PBC", "GMBH", "AG", "SA", "BV", "NV", "USA", "US", "AMERICA", "AMERICAS", "NA",
    "HOLDINGS", "HOLDING", "GROUP", "THE", "TECHNOLOGIES", "TECHNOLOGY", "TECH", "LABS", "LAB", "SOFTWARE",
    "SYSTEMS", "SOLUTIONS", "SERVICES", "SERVICE", "HQ", "AI", "IO", "COM", "INTERNATIONAL", "GLOBAL",
}


def normalize_company(name: str) -> str:
    """'Scale AI, Inc.' -> 'SCALE', 'The Trade Desk Inc' -> 'TRADE DESK', 'Amazon.com Services LLC' -> 'AMAZON'."""
    s = (name or "").upper()
    s = re.sub(r"\(.*?\)", " ", s)              # "Acme (YC W24)" -> "Acme"
    s = s.replace("&", " AND ").replace(".COM", " COM")
    s = re.sub(r"\bL\.?\s?L\.?\s?C\b\.?", " LLC ", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = s.split()
    if tokens and tokens[0] == "THE":
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


_db: dict | None = None
_db_lock = threading.Lock()


def sponsor_db() -> dict:
    """{normalized name: [initial approvals, continuing approvals, last fiscal year, legal name]}"""
    global _db
    with _db_lock:
        if _db is None:
            try:
                with gzip.open(DATA_PATH, "rt", encoding="utf-8") as f:
                    _db = json.load(f)
            except FileNotFoundError:
                _db = {"years": [], "employers": {}}
        return _db


_alias_cache: dict[str, str | None] = {}
_dba: dict[str, str] | None = None


def _dba_names() -> dict[str, str]:
    """'RIPPLING' -> 'PEOPLE CENTER INC D B A RIPPLING' (a few thousand employers list a DBA)."""
    global _dba
    if _dba is None:
        employers, names = sponsor_db()["employers"], {}
        for k in employers:
            if " D B A " in k:
                name = normalize_company(k.split(" D B A ", 1)[1])
                if name not in names or _total(employers[k]) > _total(employers[names[name]]):
                    names[name] = k  # the same DBA filed under several names: keep the biggest
        _dba = names
    return _dba


def _total(rec) -> int:
    return rec[0] + rec[1]


# Words after the brand that don't change which employer it is ("CVS Health", "Meta Platforms").
_GENERIC_REST = {"HEALTH", "HEALTHCARE", "PLATFORMS", "INDUSTRIES", "ENTERPRISES", "WORLDWIDE", "DIGITAL", "CORPORATE"}


def _sponsor_key(key: str) -> str | None:
    """The USCIS employer name for a company, when its legal name differs from the everyday one:
      'JPMorganChase' -> 'JPMORGAN CHASE'  (spacing)
      'Rippling'      -> 'PEOPLE CENTER INC D B A RIPPLING'  (doing business as)
      'Meta'          -> 'META PLATFORMS'  (one-word brand, when one employer clearly dominates:
                         'META PLATFORMS' 7,891 approvals vs 'META SOFT' 4; but not 'Archer':
                         Archer Daniels Midland 181 vs Archer Aviation 38)
    Exact names are a dictionary lookup; the rest scans the list only on a miss (cached), which
    costs far less CPU than indexing 85,000 names on every cold start."""
    employers = sponsor_db()["employers"]
    if key in employers:
        return key
    if key in _alias_cache:
        return _alias_cache[key]
    squashed = key.replace(" ", "")
    word, _, rest = key.partition(" ")
    brand = len(word) >= 3 and (not rest or rest in _GENERIC_REST)
    prefix = word + " "
    found, cands = _dba_names().get(key), []
    first = key[0]
    for k in employers if found is None else ():
        if k[0] != first:  # both checks need the same first letter
            continue
        if found is None and k.replace(" ", "") == squashed:
            found = k
        if brand and k.startswith(prefix):
            cands.append(k)
    if found is None and cands:
        cands.sort(key=lambda k: -_total(employers[k]))
        top, others = _total(employers[cands[0]]), sum(_total(employers[k]) for k in cands[1:])
        if top >= 50 and top >= 5 * others:
            found = cands[0]
    if len(_alias_cache) < 5000:
        _alias_cache[key] = found
    return found


def lookup_sponsor(company: str) -> dict | None:
    key = normalize_company(company)
    if len(key) < 2:
        return None
    found = _sponsor_key(key)
    rec = sponsor_db()["employers"].get(found) if found else None
    if not rec:
        return None
    initial, continuing, last_fy, legal = rec
    return {"initial": initial, "continuing": continuing, "total": initial + continuing,
            "last_fy": last_fy, "legal_name": legal}


def sponsor_years() -> str:
    years = sponsor_db().get("years") or []
    return f"FY{min(years)}–{str(max(years))[-2:]}" if years else ""


# ---------------------------------------------------------------- text rules

def html_to_text(s: str, limit: int = 40000) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return " ".join(s.split())[:limit]


US = r"(?:u\.?\s?s\.?(?:a\.?)?|united states|american)"
NEGATED_BEFORE = re.compile(r"(?:\bno\b|\bnot\b|n't\b|\bnever\b|\bneither\b)[^.;:]{0,25}$", re.I)
NEGATED_AFTER = re.compile(r"^[^.;:]{0,20}\b(?:not|isn't|is not|aren't|no longer)\b(?:\s+\w+)?\s+(?:required|needed|necessary)", re.I)
EEO_CONTEXT = re.compile(r"regard to|discriminat|basis of|protected (?:class|status|characteristic)|equal (?:employment )?opportunity|"
                         r"affirmative action|e-verify|immigration reform|form i-9|eligibility verification", re.I)

CLEARANCE = [re.compile(p, re.I) for p in [
    r"\bts\s*/\s*sci\b",
    r"\btop[\s-]secret\b",
    r"\b(?:security|secret|government|federal|dod|doe|q|l)[\s-]clearances?\b",
    r"\bclearance (?:is |will be )?required\b",
    r"\b(?:active|current|existing|interim) (?:dod |government |federal )?(?:secret|ts|clearance)\b",
    r"\b(?:full[\s-]scope|ci|lifestyle) poly(?:graph)?\b",
    r"\bpolygraph\b",
    r"\bpublic trust (?:clearance|position|background|investigation|eligib\w*|determination)\b",
    r"\b(?:able|ability|eligible|eligibility) to (?:obtain|receive|hold|maintain) (?:an? )?(?:\w+ )?clearance\b",
    # Government-contract vetting, which in practice is limited to US citizens
    r"\b(?:federal|government) (?:background )?(?:investigation|suitability)\b",
    r"\bfederal background (?:check|screening)s?\b",
    r"\bsuitability determination\b",
]]
CITIZENSHIP = [re.compile(p, re.I) for p in [
    rf"\b(?:must|shall|required to|need to|needs to|have to|has to) (?:be|hold|have|possess|maintain) (?:an? )?{US} (?:citizen\w*|nationals?|persons?)\b",
    rf"\b{US} citizen(?:ship|s)? (?:is |are )?(?:required|only|mandatory|needed|is a requirement|is a must)\b",
    rf"\b(?:only|exclusively) (?:open |available )?to {US} citizens\b",
    rf"\b{US} citizens? (?:or|and|/) (?:{US} )?(?:lawful )?(?:permanent residents?|green card holders?|gc holders?|lprs?)\b",
    r"\b(?:requires?|required|requiring) (?:an? )?(?:active )?(?:u\.?\s?s\.? )?(?:citizenship|green card|permanent residency)\b",
    r"\bcitizenship (?:is )?(?:required|mandatory)\b",
    r"\bproof of (?:u\.?\s?s\.? )?citizenship\b",
    r"\b(?:green card|permanent residen\w*|gc) (?:holders? )?(?:only|required)\b",
    r"\b(?:usc|us citizens?|gc)\s*(?:/|or|and|&)\s*(?:gc|usc|green card)(?: holders?)? (?:only|required|candidates only)\b",
    r"\bitar\b",
    rf"\bexport[\s-]control\w*\b[^.]{{0,150}}\b{US} persons?\b",
    rf"\b{US} persons? (?:status )?(?:is |are )?(?:required|only)\b",
    rf"\bmust (?:qualify as|meet the definition of) an? {US} person\b",
]]
NO_SPONSORSHIP = [re.compile(p, re.I) for p in [
    r"\b(?:not|unable to|cannot|can't|can ?not|won't|will not|do not|does not|don't|doesn't|are not able to|is not able to|"
    r"aren't able to|no longer)\s+(?:currently\s+|presently\s+|at this time\s+)?(?:be\s+)?(?:able to\s+)?"
    r"(?:offer|offering|provide|providing|sponsor|sponsoring|support|supporting|consider|considering|accommodate|accept)"
    r"\b[^.?]{0,70}?\b(?:sponsor\w*|visas?|h-?1-?b|work permits?|immigration)",
    r"\bnot (?:be )?eligible for (?:\w+ ){0,3}sponsorship\b",
    r"\bno (?:visa |h-?1-?b |immigration |employment |employer |work |job )?sponsorships?\b",
    r"\bsponsorships? (?:is |are |will )?(?:not|unavailable)\b",
    r"\bwithout (?:the )?(?:need for|need of|requiring|requirement (?:of|for)|use of)? ?(?:the )?(?:current or future |now or in the future |any |future )?"
    r"(?:visa |employer |company |immigration |employment |h-?1-?b )?sponsorship\b",
    r"\b(?:no|not accepting|cannot accept|can't accept|unable to accept)\s+(?:opt|cpt|h-?1-?b|f-?1|ead|visa holders?|visa candidates?)\b",
    r"\b(?:h-?1-?b|visa)s? (?:sponsorship )?(?:is |are )?not (?:available|offered|provided|supported|possible|sponsored)\b",
    r"\b(?:require|need)s? (?:\w+ ){0,4}sponsorship[^.?]{0,80}\b(?:will not|won't|are not|is not|not be|ineligible|cannot)\b",
    r"\b(?:sponsorship|sponsor)[^.?]{0,40}\b(?:is|are) not (?:something|an option)\b",
]]
POSITIVE = [re.compile(p, re.I) for p in [
    r"\b(?:visa|h-?1-?b|immigration|work visa|employment visa) sponsorship (?:is |will be )?(?:available|offered|provided|possible|supported|for qualified)\b",
    r"\b(?:will|can|do|does|are able to|is able to|are happy to|is happy to|are willing to|is willing to|gladly|happily|proudly) "
    r"sponsor (?:\w+ )?(?:visas?|h-?1-?bs?|work visas?|employment visas?|green cards?)\b",
    r"\bwe (?:also |do |can |will )?sponsor (?:visas?|h-?1|work|international|employment|immigration)",
    r"\b(?:offer|offers|offering|provide|provides|providing|support|supports|supporting) (?:\w+ )?(?:visa|h-?1-?b|immigration) sponsorship\b",
    r"\b(?:open|willing) to sponsor\w*\b",
    r"\bsponsorship (?:is )?available\b",
    r"\bh-?1-?b (?:transfers?|sponsorship) (?:is |are )?(?:ok|welcome|available|supported|possible)\b",
    r"\bvisa sponsorship\s*[:\-]\s*yes\b",
    r"\b(?:opt|cpt|stem opt|f-?1)(?: students| candidates| applicants)? (?:are |is )?(?:welcome|encouraged|accepted|ok)\b",
]]
HN_VISA_TAG = re.compile(r"(?:^|\|)\s*(?:visa|visas|visa sponsorship|visa ok|h-?1-?b ok|will sponsor)\s*(?:\||$)", re.I)


# ---------------------------------------------------------------- years of experience

_NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)"
_RANGE = rf"{_NUM}\s*(?:\+|plus|or more|or greater)?\s*(?:(?:-|–|—|to|or)\s*{_NUM}\s*\+?\s*)?"
YOE_PATTERNS = [
    # "3+ years of professional software experience", "2-4 yrs experience", "five years' experience"
    re.compile(rf"\b{_RANGE}(?:years?|yrs?)'?\.?(?:\s+(?:of|in|with|as|at|on|building|working|doing|writing))?"
               rf"(?:\s+[\w/&,+.-]+){{0,6}}?\s+(?:experience|exp)\b", re.I),
    # "experience: 3+ years", "experience of 5 years"
    re.compile(rf"\bexperience\b(?:\s*(?::|of|-|–|—|\())?\s*(?:at least |minimum of |a minimum of )?{_RANGE}(?:years?|yrs?)\b", re.I),
    # Without the word "experience": "4+ years developing NLP systems", "at least 3 years", "3-5 years"
    re.compile(rf"\b{_NUM}\s*(?:\+|plus)\s*(?:years?|yrs?)\b", re.I),
    re.compile(rf"\b(?:at least|a minimum of|minimum of|minimum|min\.)\s+{_NUM}\s*\+?\s*(?:years?|yrs?)\b", re.I),
    re.compile(rf"\b{_NUM}\s*(?:-|–|—|to)\s*{_NUM}\s*\+?\s*(?:years?|yrs?)\b", re.I),
]
YOE_IGNORE = re.compile(r"prefer|nice[\s-]to[\s-]have|bonus|ideally|a plus|is a plus|desired|desirable|optional|"
                        r"not required|we have|we've|our team has|our founders|company has|over the (?:past|last)", re.I)


def _num(s: str | None) -> int | None:
    if s is None:
        return None
    s = s.lower()
    return int(s) if s.isdigit() else _NUMBER_WORDS.get(s)


# Section headings: years listed under "Preferred qualifications" / "Nice to have" aren't required.
_PREFERRED_HEADING = re.compile(r"preferred qualifications|preferred skills|preferred experience|nice[\s-]to[\s-]haves?|"
                                r"bonus points|pluses|bonus if|what would make you stand out|ideal candidate", re.I)
_REQUIRED_HEADING = re.compile(r"(?:minimum|basic|required|must[\s-]have) (?:qualifications|requirements|skills)|"
                               r"\brequirements\b|what you(?:'|’)ll need|what you bring|you have\b|who you are", re.I)


def _in_preferred_section(text: str, pos: int) -> bool:
    last_pref = max((m.start() for m in _PREFERRED_HEADING.finditer(text, 0, pos)), default=-1)
    last_req = max((m.start() for m in _REQUIRED_HEADING.finditer(text, 0, pos)), default=-1)
    return last_pref > last_req


def required_years(text: str, loose: bool = False) -> tuple[int, re.Match] | None:
    """Years of experience a posting requires, or None if it doesn't say.

    For a normal posting this is the largest stated minimum ("5+ years", with "2+ years of Go"
    alongside, means 5). For a multi-role post (HN "Who is hiring") it's the smallest, since
    one of the roles may fit. Mentions marked preferred / nice-to-have are ignored."""
    found: list[tuple[int, re.Match]] = []
    for rx in YOE_PATTERNS:
        for m in rx.finditer(text):
            low = _num(m.group(1))
            if low is None or low > 12:  # "20 years of experience" is usually about the company
                continue
            # Look 80 chars back, but forward only to the end of this sentence: the next line may be a
            # "Preferred qualifications" heading that has nothing to do with this requirement.
            end = _SENTENCE_END.search(text, m.end())
            context = text[max(0, m.start() - 80):min(end.start() if end else len(text), m.end() + 40)]
            if YOE_IGNORE.search(context) or _in_preferred_section(text, m.start()):
                continue
            found.append((low, m))
    if not found:
        return None
    return (min if loose else max)(found, key=lambda x: x[0])


def _quote(text: str, m: re.Match) -> str:
    a, b = max(0, m.start() - 50), min(len(text), m.end() + 50)
    return ("…" if a else "") + text[a:b].strip() + ("…" if b < len(text) else "")


# Sentence boundary: . ! ? followed by a capital letter, not "U.S." / "e.g." style abbreviations.
_SENTENCE_END = re.compile(r"(?<![A-Z.])(?<!\b[a-z])[.!?](?=\s+[A-Z0-9(\"“•\-])|\n")


def _sentence(text: str, m: re.Match) -> str:
    start = 0
    for b in _SENTENCE_END.finditer(text, 0, m.start()):
        start = b.end()
    end_m = _SENTENCE_END.search(text, m.end())
    return text[start:end_m.end() if end_m else len(text)]


def _find(patterns, text: str, eeo_guard: bool = False):
    for rx in patterns:
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 40):m.start()]
            after = text[m.end():m.end() + 40]
            if NEGATED_BEFORE.search(before) and rx not in NO_SPONSORSHIP:
                continue
            if NEGATED_AFTER.search(after):
                continue
            # skip application questions ("Will you now or in the future require sponsorship?")
            sentence_end = re.search(r"[.?!]", after)
            if sentence_end and sentence_end.group() == "?" and rx in NO_SPONSORSHIP:
                continue
            # EEO boilerplate ("without regard to ... citizenship status") only counts if it's the
            # same sentence; a nearby "equal opportunity employer" line must not cancel a real requirement.
            if eeo_guard and EEO_CONTEXT.search(_sentence(text, m)):
                continue
            return m
    return None


@dataclass
class Verdict:
    ok: bool     # email it?
    visa: str    # stated | history | listed | unknown | blocked | off
    note: str    # short human explanation shown in the email and dashboard
    tags: list = field(default_factory=list)  # labels only, never block: easy_apply | recruiter


def apply_ai(verdict: Verdict, facts: dict, settings: dict) -> Verdict:
    """Combine the rule verdict with Claude Haiku's reading of the posting. The AI can only add
    restrictions (or confirm sponsorship); it never overrides a rule that already blocked a job."""
    if facts.get("posted_by_recruiter") and "recruiter" not in verdict.tags:
        verdict = replace(verdict, tags=[*verdict.tags, "recruiter"])
    if not verdict.ok:
        return verdict
    tags = verdict.tags
    quote = (facts.get("evidence") or "").strip()
    quote = f": “{quote[:300]}”" if quote else ""
    if settings.get("skip_ineligible", True):
        if facts.get("citizenship_required"):
            return Verdict(False, "blocked", f"AI check: US citizens / green card / US persons only{quote}", tags)
        if facts.get("clearance_required"):
            return Verdict(False, "blocked", f"AI check: needs a security clearance or federal background check{quote}", tags)
        if facts.get("sponsorship") == "will_not_sponsor" and (settings.get("visa_mode") or "allow_unknown") != "off":
            return Verdict(False, "blocked", f"AI check: says no visa sponsorship{quote}", tags)
    max_years = int(settings.get("max_years_experience") or 0)
    years = facts.get("min_years_required")
    if max_years > 0 and isinstance(years, int) and years > max_years:
        return Verdict(False, "blocked", f"AI check: asks for {years}+ years of experience{quote}", tags)
    if facts.get("sponsorship") == "will_sponsor" and verdict.visa in ("unknown", "history", "listed"):
        return Verdict(True, "stated", f"AI check: posting says it sponsors visas{quote}", tags)
    suffix = " (AI-checked)" if not facts.get("truncated") else " (AI-checked first 30,000 characters)"
    return Verdict(verdict.ok, verdict.visa, (verdict.note + suffix) if verdict.note else suffix.strip(" ()"), tags)


def evaluate(title: str, company: str, description: str, settings: dict, loose: bool = False) -> Verdict:
    mode = settings.get("visa_mode") or "allow_unknown"  # allow_unknown | require | off
    text = f"{title}. {description or ''}"
    read_note = "" if description else " (couldn't read the full description)"

    max_years = int(settings.get("max_years_experience") or 0)
    if max_years > 0 and description:
        req = required_years(text, loose=loose)
        if req and req[0] > max_years:
            return Verdict(False, "blocked", f"Asks for {req[0]}+ years of experience: “{_quote(text, req[1])}”")

    if settings.get("skip_ineligible", True):
        m = _find(CLEARANCE, text)
        if m:
            return Verdict(False, "blocked", f"Needs a security clearance: “{_quote(text, m)}”")
        m = _find(CITIZENSHIP, text, eeo_guard=True)
        if m:
            return Verdict(False, "blocked", f"US citizens / green card / US persons only: “{_quote(text, m)}”")
        if mode != "off":
            m = _find(NO_SPONSORSHIP, text)
            if m:
                return Verdict(False, "blocked", f"Says no visa sponsorship: “{_quote(text, m)}”")

    if mode == "off":
        return Verdict(True, "off", "")

    if HN_VISA_TAG.search(title):
        return Verdict(True, "stated", "Post is tagged VISA (sponsors visas)")
    m = _find(POSITIVE, text)
    if m:
        return Verdict(True, "stated", f"Posting mentions sponsorship: “{_quote(text, m)}”")

    listed = {normalize_company(c) for c in settings.get("sponsor_companies") or [] if c.strip()}
    if normalize_company(company) in listed:
        return Verdict(True, "listed", "On your sponsor list")

    rec = lookup_sponsor(company)
    if rec and rec["total"] > 0:
        what = f"{rec['initial']} new" if rec["initial"] else f"{rec['continuing']} extension/transfer"
        return Verdict(True, "history",
                       f"Got {rec['total']} H-1B approvals ({what}) in {sponsor_years()} as {rec['legal_name']}{read_note}")

    note = (f"No clearance, citizenship or sponsorship restrictions in the posting; "
            f"no H-1B record found for “{company}” ({sponsor_years()}){read_note}")
    return Verdict(mode == "allow_unknown", "unknown", note)
