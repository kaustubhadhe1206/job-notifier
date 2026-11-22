"""Defaults tuned for: Software Engineer / AI Engineer roles at US tech companies,
with emphasis on startups and mid-sized companies. Everything is editable in the dashboard."""

DEFAULT_SETTINGS = {
    "notify_email": "",  # empty = NOTIFY_EMAIL env var, else SMTP_USER
    "include_keywords": [
        "software engineer", "software developer", "software development engineer", "SWE", "SDE",
        "backend", "back-end", "back end", "frontend", "front-end", "full stack", "full-stack", "fullstack",
        "AI engineer", "AI/ML engineer", "ML engineer", "machine learning engineer", "applied AI", "applied ML",
        "LLM", "GenAI", "generative AI", "member of technical staff", "founding engineer", "product engineer",
        "platform engineer", "infrastructure engineer",
    ],
    "exclude_keywords": [
        "manager", "director", "head of", "VP", "vice president", "recruiter", "sales", "account executive",
    ],
    # Defense contractors and ITAR-bound companies: nearly all roles need US citizenship or a clearance.
    # Also a good place for staffing agencies you keep seeing on LinkedIn.
    "exclude_companies": [
        "Lockheed Martin", "Northrop Grumman", "Raytheon", "RTX", "General Dynamics", "BAE Systems", "L3Harris",
        "Leidos", "SAIC", "Booz Allen", "CACI", "ManTech", "Peraton", "Parsons", "KBR", "Huntington Ingalls",
        "General Atomics", "Sierra Nevada", "Textron", "SpaceX", "Blue Origin", "Anduril",
    ],
    "location_keywords": [],      # extra places to allow besides the US
    "us_only": True,
    "max_age_days": 7,            # ignore postings older than this (when the date is known)
    # Work authorization (F-1 student who will need H-1B sponsorship)
    "skip_ineligible": True,      # skip jobs needing clearance, US citizenship/green card, or saying "no sponsorship"
    "visa_mode": "allow_unknown", # allow_unknown: email every job without restrictions; jobs that don't
                                  #   mention sponsorship are included and labeled "not mentioned"
                                  # require: only email jobs with evidence of sponsorship
                                  # off: ignore sponsorship entirely
    "sponsor_companies": [],      # companies you know sponsor (treated as sponsors)
    "auto_discover": True,        # add company boards found in feeds to the watch list
    "auto_interval_minutes": 15,  # how often each auto-discovered company is checked
    "paused": False,
}

# Discovery feeds created automatically on first start.
PRESET_SOURCES = [
    {"name": "Hacker News: Who is hiring", "kind": "hn_hiring", "config": {}, "interval_minutes": 10},
    {"name": "Y Combinator startups (HN jobs)", "kind": "hn_jobs", "config": {}, "interval_minutes": 10},
    {"name": "RemoteOK", "kind": "remoteok", "config": {}, "interval_minutes": 15},
    {"name": "Himalayas", "kind": "himalayas", "config": {}, "interval_minutes": 15},
    {"name": "We Work Remotely (programming)", "kind": "weworkremotely", "config": {}, "interval_minutes": 15},
    {"name": "LinkedIn: Software Engineer · United States", "kind": "linkedin",
     "config": {"keywords": "software engineer", "location": "United States"}, "interval_minutes": 10},
    {"name": "LinkedIn: AI Engineer · United States", "kind": "linkedin",
     "config": {"keywords": "AI engineer", "location": "United States"}, "interval_minutes": 10},
]
