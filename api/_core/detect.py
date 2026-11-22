"""Recognise company job boards (ATS) from URLs, page HTML, or free text.

* detect(url): an ATS URL directly, or a careers page that embeds an ATS, or a plain page.
* discover(name): probes public ATS APIs with slug guesses for a company name.
* find_boards(text): every ATS board linked in a blob of text (used for auto-discovery).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

from . import fetchers

SKIP_SLUGS = {"www", "app", "api", "cdn", "static", "assets", "careers", "jobs", "embed", "js", "v1", "sr-jobs"}

# (kind, regex, config builder). More specific patterns first.
PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?(?:[^\"'\s<>]*?&(?:amp;)?)?for=([\w-]+)",
     lambda m: {"board": m[1]}),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", lambda m: {"board": m[1]}),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)", lambda m: {"board": m[1]}),
    ("lever", r"jobs\.(eu\.)?lever\.co/([\w.-]+)", lambda m: {"company": m[2], "region": "eu" if m[1] else "global"}),
    ("ashby", r"api\.ashbyhq\.com/posting-api/job-board/([\w.%-]+)", lambda m: {"board": m[1]}),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)", lambda m: {"board": m[1]}),
    ("workable", r"workable\.com/api/(?:v1/widget/)?accounts/([\w-]+)", lambda m: {"account": m[1]}),
    ("workable", r"apply\.workable\.com/([\w-]+)", lambda m: {"account": m[1]}),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)", lambda m: {"company": m[1]}),
    ("workday", r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[\w-]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)",
     lambda m: {"host": f"{m[1]}.{m[2]}.myworkdayjobs.com", "tenant": m[1], "site": m[3]}),
    ("recruitee", r"(?<![\w.-])([a-z0-9-]+)\.recruitee\.com", lambda m: {"subdomain": m[1]}),
]
COMPILED = [(k, re.compile(p), b) for k, p, b in PATTERNS]
ATS_HINTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
             "myworkdayjobs.com", "recruitee.com")


def _clean(kind: str, cfg: dict):
    cfg = {k: v.rstrip(".") if isinstance(v, str) else v for k, v in cfg.items()}
    slug = cfg.get("board") or cfg.get("company") or cfg.get("account") or cfg.get("subdomain") or cfg.get("site")
    if not slug or slug.lower() in SKIP_SLUGS or (kind == "workday" and slug in ("wday",)):
        return None
    return cfg


def find_boards(text: str) -> list[tuple[str, dict]]:
    """All distinct company boards linked in the text."""
    if not text or not any(h in text for h in ATS_HINTS):
        return []
    found: dict[str, tuple[str, dict]] = {}
    for kind, rx, build in COMPILED:
        for m in rx.finditer(text):
            cfg = _clean(kind, build(m))
            key = cfg and fetchers.board_key(kind, cfg)
            if key and key not in found:
                found[key] = (kind, cfg)
    return list(found.values())


def _first_board(text: str):
    boards = find_boards(text)
    return boards[0] if boards else None


def _normalize(url: str) -> str:
    url = url.strip()
    return url if re.match(r"^https?://", url, re.I) else f"https://{url}"


def _linkedin_from_url(url: str):
    if "linkedin.com/jobs" not in url:
        return None
    q = parse_qs(urlparse(url).query)
    first = lambda k: (q.get(k) or [""])[0]  # noqa: E731
    cfg = {"keywords": first("keywords"), "location": first("location"), "remote": "2" in first("f_WT").split(",")}
    if first("geoId"):
        cfg["geo_id"] = first("geoId")
    return ("linkedin", cfg) if cfg["keywords"] or cfg["location"] else None


def name_for(kind: str, cfg: dict, url: str = "") -> str:
    if kind == "linkedin":
        return "LinkedIn: " + " · ".join(x for x in [cfg.get("keywords"), cfg.get("location")] if x)
    slug = cfg.get("board") or cfg.get("company") or cfg.get("account") or cfg.get("subdomain") or cfg.get("tenant")
    if not slug:
        slug = urlparse(url).hostname or url
        slug = re.sub(r"^(www|careers|jobs)\.", "", slug).split(".")[0]
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _try(kind: str, cfg: dict):
    try:
        return fetchers.fetch(kind, fetchers.client(), cfg), None
    except Exception as e:  # noqa: BLE001 - surfaced to the user
        return None, fetchers.describe_error(e)


def _result(kind, cfg, jobs, note="", url=""):
    return {
        "kind": kind,
        "config": cfg,
        "name": name_for(kind, cfg, url),
        "url": fetchers.public_url(kind, cfg),
        "job_count": len(jobs),
        "sample": [{"title": j.title, "location": j.location, "url": j.url} for j in jobs[:6]],
        "note": note,
    }


def detect(url: str) -> dict:
    url = _normalize(url)
    direct = _linkedin_from_url(url) or _first_board(url)
    if direct:
        kind, cfg = direct
        jobs, err = _try(kind, cfg)
        if err:
            raise ValueError(f"Recognised a {kind} board but could not read it: {err}")
        return _result(kind, cfg, jobs, url=url)

    try:
        r = fetchers.client().get(url, headers={"Accept": "text/html"})
        r.raise_for_status()
        final_url, page = str(r.url), r.text
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not open that page: {fetchers.describe_error(e)}") from e

    found = _first_board(final_url) or _first_board(page)
    if found:
        kind, cfg = found
        jobs, err = _try(kind, cfg)
        if not err and jobs:
            note = f"This careers page is powered by {kind.title()}. Watching its feed directly (faster and more reliable)."
            return _result(kind, cfg, jobs, note=note, url=url)

    cfg = {"url": url}
    jobs, err = _try("generic", cfg)
    if err:
        raise ValueError(f"Could not read that page: {err}")
    note = ("No known job board detected, so this page will be watched for new job-looking links. "
            "If it finds 0 jobs the page loads jobs with JavaScript; try the company name instead.")
    return _result("generic", cfg, jobs, note=note, url=url)


def _slugs(name: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|technologies|labs)\b\.?", "", name.strip().lower()).strip()
    words = re.findall(r"[a-z0-9]+", base)
    return list(dict.fromkeys(["".join(words), "-".join(words)])) if words else []


def discover(name: str) -> list[dict]:
    """Probe the public ATS APIs for a company by name. Returns boards that have jobs."""
    candidates = []
    for slug in _slugs(name):
        candidates += [
            ("greenhouse", {"board": slug}),
            ("lever", {"company": slug, "region": "global"}),
            ("ashby", {"board": slug}),
            ("workable", {"account": slug}),
            ("smartrecruiters", {"company": slug}),
            ("recruitee", {"subdomain": slug}),
        ]
    with ThreadPoolExecutor(max_workers=12) as pool:
        attempts = list(pool.map(lambda kc: (kc[0], kc[1], *_try(kc[0], kc[1])), candidates))
    hits, seen = [], set()
    display = name.strip().title() if name.strip().islower() else name.strip()
    for kind, cfg, jobs, err in attempts:
        if err or not jobs or kind in seen:
            continue
        seen.add(kind)
        hits.append({**_result(kind, cfg, jobs), "name": display})
    return sorted(hits, key=lambda h: -h["job_count"])
