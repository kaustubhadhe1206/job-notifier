"""Decides which new jobs are worth an email: role keywords, US location, and freshness."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .fetchers import Job, is_precise

US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|"
    "Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|"
    "Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|"
    "New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|"
    "South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|"
    "Wisconsin|Wyoming|District of Columbia"
)
US_CITIES = (
    "San Francisco|SF Bay|Bay Area|Silicon Valley|Palo Alto|Mountain View|Menlo Park|Sunnyvale|San Jose|"
    "Santa Clara|Redwood City|San Mateo|Cupertino|Oakland|Berkeley|Los Angeles|Santa Monica|San Diego|"
    "Irvine|Seattle|Bellevue|Redmond|Portland|New York|NYC|Brooklyn|Manhattan|Boston|Cambridge, MA|"
    "Austin|Dallas|Houston|Denver|Boulder|Chicago|Atlanta|Miami|Philadelphia|Pittsburgh|Washington, DC|"
    "Raleigh|Durham|Salt Lake City|Phoenix|Minneapolis|Detroit|Nashville|Columbus|Baltimore"
)
STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
    "NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
US_RE = re.compile(
    rf"\b(?:United States|North America|Americas|{US_STATES}|{US_CITIES})\b", re.I
)
US_CASE_RE = re.compile(rf"\b(?:US|USA|U\.S\.A?\.?|U\.S|SF|SFBA|NYC|DC)\b|,\s*(?:{STATE_CODES})\b|\b(?:{STATE_CODES})\s*\d{{5}}")
NON_US_RE = re.compile(
    r"\b(?:Europe|EU|EMEA|APAC|LATAM|UK|United Kingdom|England|London|Ireland|Dublin|Germany|Berlin|Munich|"
    r"France|Paris|Netherlands|Amsterdam|Spain|Madrid|Barcelona|Portugal|Lisbon|Italy|Poland|Warsaw|"
    r"Sweden|Stockholm|Denmark|Norway|Finland|Switzerland|Zurich|Austria|Belgium|Czech|Romania|Ukraine|"
    r"Israel|Tel Aviv|India|Bangalore|Bengaluru|Hyderabad|Pune|Mumbai|Delhi|Gurgaon|Noida|Chennai|"
    r"Canada|Toronto|Vancouver|Montreal|Mexico|Brazil|Argentina|Colombia|Chile|Australia|Sydney|"
    r"Melbourne|New Zealand|Singapore|Japan|Tokyo|China|Hong Kong|Korea|Seoul|Philippines|Vietnam|"
    r"Indonesia|Malaysia|Pakistan|Nigeria|Kenya|South Africa|Egypt|Turkey|UAE|Dubai|Greece|Athens|"
    r"Hungary|Budapest|Serbia|Belgrade|Croatia|Bulgaria|Sofia|Estonia|Tallinn|Latvia|Riga|Lithuania|Vilnius|"
    r"Slovakia|Slovenia|Cyprus|Malta|Luxembourg|Iceland|Geneva|Vienna|Prague|Krakow|Copenhagen|Oslo|"
    r"Helsinki|Edinburgh|Manchester|Thailand|Bangkok|Taiwan|Taipei|Bangladesh|Sri Lanka|Nepal|Saudi|"
    r"Qatar|Morocco|Ghana|Peru|Uruguay|Costa Rica|Guatemala|Dominican Republic|Bahamas|Jamaica|"
    r"Suriname|Ecuador|Venezuela|Bolivia|Paraguay|Panama|Honduras|Nicaragua|El Salvador)\b",
    re.I,
)
ANYWHERE_RE = re.compile(r"\b(?:remote|anywhere|worldwide|global|distributed)\b", re.I)
MULTI_LOCATION_RE = re.compile(r"^\s*\d+\s+locations?\s*$", re.I)  # Workday's "3 Locations"


def is_us_location(location: str) -> bool:
    loc = (location or "").strip()
    if not loc or MULTI_LOCATION_RE.match(loc):
        return True  # unknown: never silently drop a job
    if US_RE.search(loc) or US_CASE_RE.search(loc):
        return True
    if NON_US_RE.search(loc):
        return False
    return bool(ANYWHERE_RE.search(loc))  # "Remote" with no region = open to US applicants


# Level markers that mean "experienced" regardless of wording: Google/Meta-style L5+/E5+/IC5+,
# "Software Engineer 3", "SDE-4", "Level 4".
LEVEL_RE = re.compile(
    r"\b(?:L|E|IC|P)\s?-?[5-9]\b"
    r"|(?i:\b(?:engineer|developer|swe|sde|programmer)\s*-?\s*(?:[3-9])\b)"
    r"|(?i:\blevel\s*[3-9]\b)"
    r"|\b[SLP]MTS\b"  # Senior / Lead / Principal Member of Technical Staff (Salesforce, Oracle, ...)
)


def _keyword_re(words):
    words = [w.strip() for w in words or [] if w and w.strip()]
    if not words:
        return None
    # Whole-word match (plural allowed): "intern" won't match "internal", "ML" won't match "HTML".
    return re.compile("|".join(rf"(?<!\w){re.escape(w)}s?(?!\w)" for w in words), re.I)


class Filters:
    def __init__(self, settings: dict):
        self.include = _keyword_re(settings.get("include_keywords"))
        self.exclude = _keyword_re(settings.get("exclude_keywords"))
        self.location = _keyword_re(settings.get("location_keywords"))
        self.companies = _keyword_re(settings.get("exclude_companies"))
        self.seniority = _keyword_re(settings.get("seniority_exclude"))
        self.us_only = bool(settings.get("us_only"))
        self.max_age = timedelta(minutes=int(settings.get("max_age_minutes") or 60))
        self.linkedin_max_age = timedelta(minutes=int(settings.get("linkedin_max_age_minutes") or 1440))
        self.now = datetime.now(timezone.utc)

    def _location_ok(self, job: Job) -> bool:
        if not (self.us_only or self.location):
            return True
        if not job.location:
            return True
        if self.location and self.location.search(job.location):
            return True
        return self.us_only and is_us_location(job.location)

    def _freshness(self, job: Job, observed: bool, max_age: timedelta | None = None) -> str | None:
        """Only jobs posted minutes ago get through.

        An exact publish time must be within max_age, wherever the job came from (LinkedIn has its
        own, longer limit: it often lists jobs an hour or more after they were posted).
        Without one (Workday "Posted Today", Lever's draft date, plain career pages), a job is
        accepted only if it just appeared on a company board we check every few minutes
        (observed=True); from LinkedIn, feeds or a board seen for the first time it isn't."""
        max_age = max_age or self.max_age
        posted = job.posted_at
        age = self.now - posted if is_precise(posted) else None
        if age is None:
            return None if observed else "No exact posting time, so it can't be confirmed as fresh"
        if age > max_age:
            mins = int(age.total_seconds() // 60)
            when = f"{mins} min" if mins < 120 else f"{mins // 60} h" if mins < 2880 else f"{mins // 1440} days"
            return f"Posted {when} ago (older than {int(max_age.total_seconds() // 60)} min)"
        return None

    def hires_engineers(self, job: Job) -> bool:
        """A Software/AI Engineer role in the US at any level: tells us the company is worth
        checking often, because its next entry-level opening could come any minute."""
        if self.include and not self.include.search(job.title):
            return False
        if self.companies and job.company and self.companies.search(job.company):
            return False
        return self._location_ok(job)

    def reason(self, job: Job, baseline: bool = False, observed: bool = False, linkedin: bool = False) -> str | None:
        """Why this job is filtered out, or None if it passes the title/location/freshness filters."""
        return self.static_reason(job) or self._freshness(
            job, observed=observed and not baseline, max_age=self.linkedin_max_age if linkedin else None)

    def static_reason(self, job: Job) -> str | None:
        """All filters except freshness (role, level, company, location)."""
        if self.include and not self.include.search(job.title):
            return "Title doesn't match your roles"
        if self.seniority and not job.loose:  # multi-role HN posts are judged by their description instead
            word = self._senior_word(job.title)
            if word:
                return f"Experienced-level title (“{word}”)"
        if self.exclude and not job.loose:
            m = self.exclude.search(job.title)
            if m:
                return f"Title contains “{m.group(0)}”"
        if self.companies and job.company and self.companies.search(job.company):
            return "Company is on your block list"
        if not self._location_ok(job):
            return "Not in the US"
        return None

    def _senior_word(self, title: str) -> str | None:
        for m in self.seniority.finditer(title):
            # "Member of Technical Staff" is used for every level at AI labs, so it isn't "staff level".
            if m.group(0).lower().startswith("staff") and title[:m.start()].lower().endswith("technical "):
                continue
            return m.group(0)
        m = LEVEL_RE.search(title)
        return m.group(0) if m else None

    def match(self, job: Job, baseline: bool = False, observed: bool = False) -> bool:
        return self.reason(job, baseline, observed) is None
