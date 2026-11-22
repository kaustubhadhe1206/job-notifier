"""Job fetchers: one function per source type.

Two families:
* Company boards (Greenhouse, Lever, Ashby, Workday, ...): a company's own ATS feed. Jobs land
  here first, before LinkedIn and job boards, so these are watched every few minutes.
* Discovery feeds (Hacker News, YC, RemoteOK, Himalayas, We Work Remotely, LinkedIn search):
  jobs from many small companies at once. Links to company boards found in these feeds are
  added to the watch list automatically (see checker.py).

Every fetcher has the signature fetcher(client, config, ctx) -> list[Job].
"""
from __future__ import annotations

import html
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
MAX_JOBS_PER_SOURCE = 2000
ALGOLIA = "https://hn.algolia.com/api/v1"


@dataclass
class Job:
    external_id: str
    title: str
    url: str = ""
    location: str = ""
    company: str = ""
    posted_at: datetime | None = None
    text: str = ""        # raw text scanned for links to company job boards (not stored)
    loose: bool = False   # multi-role post (e.g. HN): skip the title exclude-filter
    description: str = "" # full description when the list API includes it (else fetched on demand)


@dataclass
class FetchCtx:
    """Per-fetch state. Single-request fetchers use the ETag so an unchanged board costs
    one tiny '304 Not Modified' response instead of a download + parse."""
    etag: str | None = None
    new_etag: str | None = None


class NotModified(Exception):
    pass


class FetchError(Exception):
    pass


# One shared client: creating an SSL context costs ~0.25s CPU, and pooled connections stay
# warm between cron runs on the same serverless instance.
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(12.0, connect=6.0),
                follow_redirects=True,
                limits=httpx.Limits(max_connections=40, max_keepalive_connections=20),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"},
            )
        return _client


def _get(c: httpx.Client, url: str, ctx: FetchCtx | None = None, **kw) -> httpx.Response:
    headers = dict(kw.pop("headers", None) or {})
    if ctx and ctx.etag:
        headers["If-None-Match"] = ctx.etag
    r = c.get(url, headers=headers, **kw)
    if r.status_code == 304:
        raise NotModified()
    r.raise_for_status()
    if ctx:
        ctx.new_etag = r.headers.get("etag")
    return r


def _dt(value) -> datetime | None:
    """Parse ISO strings, 'YYYY-MM-DD HH:MM:SS UTC', RFC 822 dates and epoch seconds/ms."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = value / 1000 if value > 1e11 else value
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(value).strip()
        if re.match(r"^[A-Za-z]{3}, \d", s):
            return parsedate_to_datetime(s)
        s = s.replace("Z", "+00:00")
        if s.endswith(" UTC"):
            s = s[:-4]
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError, TypeError):
        return None


def _join(*parts) -> str:
    return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def _strip_tags(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s or "").split())


def _text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


# ================================================================ company boards (ATS)


def fetch_greenhouse(c, cfg, ctx):
    data = _get(c, f"https://boards-api.greenhouse.io/v1/boards/{cfg['board']}/jobs", ctx).json()
    return [
        Job(str(j["id"]), j.get("title") or "", j.get("absolute_url") or "",
            (j.get("location") or {}).get("name") or "", j.get("company_name") or "",
            _dt(j.get("first_published")))
        for j in data.get("jobs", [])
    ]


def fetch_lever(c, cfg, ctx):
    base = "https://api.eu.lever.co" if cfg.get("region") == "eu" else "https://api.lever.co"
    data = _get(c, f"{base}/v0/postings/{cfg['company']}", ctx, params={"mode": "json"}).json()
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
            loc = _join(loc, "Remote")
        desc = " ".join([j.get("descriptionPlain") or "", *[f"{x.get('text', '')} {x.get('content', '')}" for x in j.get("lists") or []],
                         j.get("additionalPlain") or ""])
        jobs.append(Job(j["id"], j.get("text") or "", j.get("hostedUrl") or "", loc, "", _dt(j.get("createdAt")),
                        description=desc))
    return jobs


def fetch_ashby(c, cfg, ctx):
    data = _get(c, f"https://api.ashbyhq.com/posting-api/job-board/{cfg['board']}", ctx).json()
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = _join(loc, "Remote")
        jobs.append(Job(j["id"], j.get("title") or "", j.get("jobUrl") or j.get("applyUrl") or "", loc, "",
                        _dt(j.get("publishedAt")), description=j.get("descriptionPlain") or j.get("descriptionHtml") or ""))
    return jobs


def fetch_workable(c, cfg, ctx):
    data = _get(c, f"https://apply.workable.com/api/v1/widget/accounts/{cfg['account']}").json()
    company = data.get("name") or ""
    jobs = []
    for j in data.get("jobs", []):
        loc = _join(j.get("city"), j.get("state"), j.get("country"))
        if j.get("telecommuting"):
            loc = _join(loc, "Remote")
        jid = j.get("shortcode") or j.get("url") or j.get("title")
        jobs.append(Job(str(jid), j.get("title") or "", j.get("url") or j.get("shortlink") or "", loc, company,
                        _dt(j.get("published_on") or j.get("created_at"))))
    return jobs


def fetch_smartrecruiters(c, cfg, ctx):
    company_id = cfg["company"]
    jobs, offset = [], 0
    while offset < 1000:
        data = _get(c, f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings",
                    params={"limit": 100, "offset": offset}).json()
        content = data.get("content") or []
        for j in content:
            loc = j.get("location") or {}
            loc_s = loc.get("fullLocation") or _join(loc.get("city"), loc.get("region"), loc.get("country"))
            if loc.get("remote") and "remote" not in loc_s.lower():
                loc_s = _join(loc_s, "Remote")
            jobs.append(Job(str(j["id"]), j.get("name") or "",
                            f"https://jobs.smartrecruiters.com/{company_id}/{j['id']}", loc_s,
                            (j.get("company") or {}).get("name") or "", _dt(j.get("releasedDate"))))
        offset += len(content)
        if not content or offset >= (data.get("totalFound") or 0):
            break
    return jobs


def fetch_recruitee(c, cfg, ctx):
    data = _get(c, f"https://{cfg['subdomain']}.recruitee.com/api/offers/").json()
    return [
        Job(str(j["id"]), j.get("title") or "", j.get("careers_url") or j.get("url") or "",
            j.get("location") or _join(j.get("city"), j.get("country")), j.get("company_name") or "",
            _dt(j.get("published_at") or j.get("created_at")),
            description=f"{j.get('description') or ''} {j.get('requirements') or ''}")
        for j in data.get("offers", [])
    ]


def _workday_posted(text: str) -> datetime | None:
    t = (text or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in t:
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*days?", t)
    return now - timedelta(days=int(m.group(1))) if m else None


def fetch_workday(c, cfg, ctx):
    host, tenant, site = cfg["host"], cfg["tenant"], cfg["site"]
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    jobs, page = [], 20
    for offset in range(0, 100, page):  # newest ~100 postings is enough for change detection
        r = c.post(api, json={"appliedFacets": {}, "limit": page, "offset": offset, "searchText": cfg.get("search", "")},
                   headers={"Accept": "application/json", "Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        posts = data.get("jobPostings") or []
        for j in posts:
            path = j.get("externalPath")
            if path:
                jobs.append(Job(path, j.get("title") or "", f"https://{host}/{site}{path}",
                                j.get("locationsText") or "", "", _workday_posted(j.get("postedOn"))))
        if len(posts) < page or offset + page >= (data.get("total") or 0):
            break
    return jobs


# ================================================================ discovery feeds


def fetch_hn_hiring(c, cfg, ctx):
    """Monthly 'Ask HN: Who is hiring?' thread: hundreds of mostly small/startup companies.
    Each top-level comment is one company; the first line is 'Company | Role | Location | ...'."""
    stories = _get(c, f"{ALGOLIA}/search_by_date",
                   params={"tags": "story,author_whoishiring", "hitsPerPage": 10}).json()["hits"]
    story = next((s for s in stories if (s.get("title") or "").lower().startswith("ask hn: who is hiring")), None)
    if not story:
        return []
    sid = int(story["objectID"])
    hits = _get(c, f"{ALGOLIA}/search_by_date",
                params={"tags": f"comment,story_{sid}", "hitsPerPage": 1000}).json()["hits"]
    jobs = []
    for h in hits:
        if h.get("parent_id") != sid:
            continue  # replies, not job posts
        raw = html.unescape(h.get("comment_text") or "")
        header = _strip_tags(re.split(r"<p>|\n", raw, maxsplit=1)[0])[:300]
        if not header:
            continue
        parts = [p.strip() for p in header.split("|")]
        jobs.append(Job(
            external_id=str(h["objectID"]),
            title=header,
            url=f"https://news.ycombinator.com/item?id={h['objectID']}",
            location=" | ".join(parts[1:])[:300],
            company=parts[0][:100],
            posted_at=_dt(h.get("created_at")),
            text=raw,
            loose=True,
            description=raw,
        ))
    return jobs


def fetch_hn_jobs(c, cfg, ctx):
    """Y Combinator startups' job posts on Hacker News ('Acme (YC W24) is hiring ...')."""
    hits = _get(c, f"{ALGOLIA}/search_by_date", params={"tags": "job", "hitsPerPage": 100}).json()["hits"]
    jobs = []
    for h in hits:
        title = h.get("title") or ""
        company = re.split(r"\s+\(YC|\s+is hiring|\s+hiring|\s+[-–—:]\s+", title, maxsplit=1, flags=re.I)[0].strip()
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        story = html.unescape(h.get("story_text") or "")
        jobs.append(Job(str(h["objectID"]), title, url, "", company[:100], _dt(h.get("created_at")),
                        text=url + " " + story, loose=True, description=story))
    return jobs


def fetch_remoteok(c, cfg, ctx):
    data = _get(c, "https://remoteok.com/api").json()
    jobs = []
    for j in data:
        if not isinstance(j, dict) or not j.get("id") or not j.get("position"):
            continue  # first element is a legal notice
        jobs.append(Job(str(j["id"]), j["position"], j.get("url") or "", j.get("location") or "Remote",
                        j.get("company") or "", _dt(j.get("epoch") or j.get("date")),
                        text=j.get("apply_url") or "", description=j.get("description") or ""))
    return jobs


def fetch_himalayas(c, cfg, ctx):
    jobs = []
    for offset in range(0, int(cfg.get("max", 60)), 20):
        data = _get(c, "https://himalayas.app/jobs/api", params={"limit": 20, "offset": offset}).json()
        batch = data.get("jobs") or []
        for j in batch:
            restrictions = [r if isinstance(r, str) else (r or {}).get("name", "") for r in j.get("locationRestrictions") or []]
            loc = ", ".join(r for r in restrictions if r) or "Remote (worldwide)"
            link = j.get("applicationLink") or ""
            jobs.append(Job(str(j.get("guid") or link or j.get("title")), j.get("title") or "", link, loc,
                            j.get("companyName") or "", _dt(j.get("pubDate")), text=link,
                            description=j.get("description") or j.get("excerpt") or ""))
        if len(batch) < 20:
            break
    return jobs


WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-front-end-programming-jobs.rss",
]


def fetch_weworkremotely(c, cfg, ctx):
    jobs = []
    for feed in cfg.get("feeds") or WWR_FEEDS:
        root = ET.fromstring(_get(c, feed, headers={"Accept": "application/rss+xml, application/xml"}).content)
        for item in root.iter("item"):
            raw_title = (item.findtext("title") or "").strip()
            company, sep, role = raw_title.partition(": ")
            link = (item.findtext("link") or "").strip()
            desc = html.unescape(item.findtext("description") or "")
            jobs.append(Job(item.findtext("guid") or link, role if sep else raw_title, link,
                            item.findtext("region") or "Remote", company if sep else "",
                            _dt(item.findtext("pubDate")), text=desc, description=desc))
    return jobs


def fetch_linkedin(c, cfg, ctx):
    """LinkedIn's public (logged-out) job search, newest first, last 24h.

    LinkedIn's terms forbid automated access and it rate-limits datacenter IPs, so this is
    best-effort: keep the interval at 10+ minutes.
    """
    jobs = []
    for page in range(int(cfg.get("pages", 3))):
        params = {"keywords": cfg.get("keywords", ""), "location": cfg.get("location", ""),
                  "f_TPR": "r86400", "sortBy": "DD", "start": page * 10}
        if cfg.get("geo_id"):
            params["geoId"] = cfg["geo_id"]
        if cfg.get("remote"):
            params["f_WT"] = "2"
        r = c.get("https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search", params=params,
                  headers={"Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
        if r.status_code == 429:
            if jobs:
                break  # keep what we already have
            raise FetchError("LinkedIn rate-limited this request (HTTP 429). It usually recovers on its own.")
        if r.status_code == 400 or not r.text.strip():
            break  # no (more) results
        r.raise_for_status()
        cards = BeautifulSoup(r.text, "html.parser").select("li")
        before = len(jobs)
        for card in cards:
            link = card.select_one("a.base-card__full-link") or card.select_one("a[href*='/jobs/view/']")
            href = link["href"].split("?")[0] if link and link.has_attr("href") else ""
            urn_node = card.select_one("[data-entity-urn]")
            urn = urn_node["data-entity-urn"] if urn_node else ""
            m = re.search(r"(\d{6,})$", urn) or re.search(r"(\d{6,})/?$", href)
            title = _text(card.select_one(".base-search-card__title"))
            if not m or not title:
                continue
            time_node = card.select_one("time")
            jobs.append(Job(m.group(1), title, href or f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                            _text(card.select_one(".job-search-card__location")),
                            _text(card.select_one(".base-search-card__subtitle")),
                            _dt(time_node.get("datetime")) if time_node else None))
        if len(jobs) == before:
            break
    return jobs


JOBLIKE_LINK = re.compile(r"job|career|position|opening|vacanc|requisition|/role|/apply", re.I)


def fetch_generic(c, cfg, ctx):
    """Any careers page: collects links that look like job postings and diffs them.
    Only works for pages that render their job list as plain HTML (no JavaScript)."""
    url = cfg["url"]
    r = _get(c, url, headers={"Accept": "text/html"})
    soup = BeautifulSoup(r.text, "html.parser")
    page_url = str(r.url)
    pattern = (cfg.get("link_contains") or "").strip().lower()
    jobs, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"]).split("#")[0]
        if not href.startswith("http") or href.rstrip("/") in (page_url.rstrip("/"), url.rstrip("/")):
            continue
        if pattern:
            if pattern not in href.lower():
                continue
        elif not JOBLIKE_LINK.search(href):
            continue
        title = _text(a)
        if not 4 <= len(title) <= 200 or href in seen:
            continue
        seen.add(href)
        jobs.append(Job(href, title, href, text=href))
    return jobs


# ================================================================ full descriptions (on demand)


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", s or "")
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())[:40000]


def fetch_description(kind: str, cfg: dict, job_id: str, url: str, c: httpx.Client | None = None) -> str:
    """Full plain-text description of one job. Used only for new jobs that already passed the
    title/location filters, so this stays a handful of requests per run."""
    c = c or client()
    j = {"Accept": "application/json"}
    if kind == "greenhouse":
        data = _get(c, f"https://boards-api.greenhouse.io/v1/boards/{cfg['board']}/jobs/{job_id}").json()
        return _strip_html(html.unescape(data.get("content") or ""))
    if kind == "workable":
        d = _get(c, f"https://apply.workable.com/api/v2/accounts/{cfg['account']}/jobs/{job_id}").json()
        return _strip_html(" ".join(d.get(k) or "" for k in ("description", "requirements", "benefits")))
    if kind == "smartrecruiters":
        d = _get(c, f"https://api.smartrecruiters.com/v1/companies/{cfg['company']}/postings/{job_id}").json()
        sections = ((d.get("jobAd") or {}).get("sections") or {}).values()
        return _strip_html(" ".join(s.get("text") or "" for s in sections if isinstance(s, dict)))
    if kind == "workday":
        d = _get(c, f"https://{cfg['host']}/wday/cxs/{cfg['tenant']}/{cfg['site']}{job_id}", headers=j).json()
        return _strip_html((d.get("jobPostingInfo") or {}).get("jobDescription") or "")
    if kind == "linkedin":
        r = _get(c, f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}", headers={"Accept": "text/html"})
        m = re.search(r'show-more-less-html__markup[^>]*>(.*?)</div>', r.text, re.S)
        return _strip_html(m.group(1) if m else "")
    if url.startswith("http") and "news.ycombinator.com" not in url:
        return _strip_html(_get(c, url, headers={"Accept": "text/html"}).text)
    return ""


# ================================================================ registry

FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
    "recruitee": fetch_recruitee,
    "workday": fetch_workday,
    "hn_hiring": fetch_hn_hiring,
    "hn_jobs": fetch_hn_jobs,
    "remoteok": fetch_remoteok,
    "himalayas": fetch_himalayas,
    "weworkremotely": fetch_weworkremotely,
    "linkedin": fetch_linkedin,
    "generic": fetch_generic,
}

BOARD_KINDS = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "workday"}
FEED_KINDS = set(FETCHERS) - BOARD_KINDS  # feeds are scanned for links to new company boards

REQUIRED_CONFIG = {
    "greenhouse": ["board"],
    "lever": ["company"],
    "ashby": ["board"],
    "workable": ["account"],
    "smartrecruiters": ["company"],
    "recruitee": ["subdomain"],
    "workday": ["host", "tenant", "site"],
    "generic": ["url"],
}


def board_key(kind: str, cfg: dict) -> str | None:
    """Stable identity of a company board, used to avoid watching the same board twice."""
    value = {
        "greenhouse": cfg.get("board"),
        "lever": cfg.get("company"),
        "ashby": cfg.get("board"),
        "workable": cfg.get("account"),
        "smartrecruiters": cfg.get("company"),
        "recruitee": cfg.get("subdomain"),
        "workday": f"{cfg.get('host')}/{cfg.get('site')}",
    }.get(kind)
    return f"{kind}:{value.lower()}" if value else None


def public_url(kind: str, cfg: dict) -> str:
    """Human-facing page for a source (linked from the dashboard)."""
    builders = {
        "greenhouse": lambda: f"https://job-boards.greenhouse.io/{cfg['board']}",
        "lever": lambda: f"https://jobs{'.eu' if cfg.get('region') == 'eu' else ''}.lever.co/{cfg['company']}",
        "ashby": lambda: f"https://jobs.ashbyhq.com/{cfg['board']}",
        "workable": lambda: f"https://apply.workable.com/{cfg['account']}/",
        "smartrecruiters": lambda: f"https://jobs.smartrecruiters.com/{cfg['company']}",
        "recruitee": lambda: f"https://{cfg['subdomain']}.recruitee.com/",
        "workday": lambda: f"https://{cfg['host']}/{cfg['site']}",
        "hn_hiring": lambda: "https://news.ycombinator.com/submitted?id=whoishiring",
        "hn_jobs": lambda: "https://news.ycombinator.com/jobs",
        "remoteok": lambda: "https://remoteok.com/remote-dev-jobs",
        "himalayas": lambda: "https://himalayas.app/jobs",
        "weworkremotely": lambda: "https://weworkremotely.com/categories/remote-programming-jobs",
        "linkedin": lambda: "https://www.linkedin.com/jobs/search/?" + str(httpx.QueryParams(
            {"keywords": cfg.get("keywords", ""), "location": cfg.get("location", ""), "f_TPR": "r86400", "sortBy": "DD"})),
        "generic": lambda: cfg["url"],
    }
    try:
        return builders[kind]()
    except KeyError:
        return ""


def validate(kind: str, cfg: dict) -> None:
    if kind not in FETCHERS:
        raise ValueError(f"Unknown source type '{kind}'")
    missing = [k for k in REQUIRED_CONFIG.get(kind, []) if not str(cfg.get(k, "")).strip()]
    if missing:
        raise ValueError(f"Missing config for {kind}: {', '.join(missing)}")
    if kind == "linkedin" and not (cfg.get("keywords") or cfg.get("location")):
        raise ValueError("LinkedIn search needs keywords or a location")


def fetch(kind: str, c: httpx.Client, cfg: dict, ctx: FetchCtx | None = None) -> list[Job]:
    validate(kind, cfg)
    jobs, seen = [], set()
    for j in FETCHERS[kind](c, cfg, ctx or FetchCtx()):
        j.title = " ".join((j.title or "").split())
        if not j.external_id or not j.title or j.external_id in seen:
            continue
        seen.add(j.external_id)
        jobs.append(j)
        if len(jobs) >= MAX_JOBS_PER_SOURCE:
            break
    return jobs


def describe_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        hint = {404: " (board not found)", 429: " (rate-limited, will retry)"}.get(code, "")
        return f"HTTP {code} from {e.request.url.host}{hint}"
    if isinstance(e, httpx.TimeoutException):
        return "Timed out waiting for the site"
    if isinstance(e, httpx.HTTPError):
        return f"Network error: {e.__class__.__name__}"
    if isinstance(e, ET.ParseError):
        return "Feed returned invalid XML"
    return (str(e) or e.__class__.__name__)[:300]
