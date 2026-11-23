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
    # Experience level: entry level / up to ~2 years
    "seniority_exclude": [        # skip titles containing these (plus levels like III, IV, L5+, E5+, "Engineer 3")
        "senior", "sr", "snr", "staff", "principal", "lead", "architect", "distinguished", "fellow", "chief",
        "manager", "director", "head of", "VP", "expert", "intern", "internship", "co-op", "III", "IV",
    ],
    "max_years_experience": 2,    # skip jobs whose description requires more years than this (0 = don't check)
    "us_only": True,
    "max_age_minutes": 60,        # only email jobs posted within this many minutes (see filters._freshness)
    # Work authorization (F-1 student who will need H-1B sponsorship)
    "skip_ineligible": True,      # skip jobs needing clearance, US citizenship/green card, or saying "no sponsorship"
    "visa_mode": "allow_unknown", # allow_unknown: email every job without restrictions; jobs that don't
                                  #   mention sponsorship are included and labeled "not mentioned"
                                  # require: only email jobs with evidence of sponsorship
                                  # off: ignore sponsorship entirely
    "sponsor_companies": [],      # companies you know sponsor (treated as sponsors)
    # AI check (Claude Haiku): reads each job that passes the rules; needs ANTHROPIC_API_KEY
    "ai_check": True,
    "ai_monthly_limit_usd": 2.0,  # stop AI checks for the rest of the month after this much spend (0 = no limit)
    "ai_credit_usd": 0.0,         # credit you loaded in the Anthropic Console (for the "credit left" estimate)
    "ai_credit_set_on": "",       # set automatically when you change ai_credit_usd
    "auto_discover": True,        # add company boards found in feeds to the watch list
    "auto_interval_minutes": 15,  # discovered companies with US engineering openings: checked this often
    "auto_idle_interval_minutes": 360,  # discovered companies without any: checked this often
    "paused": False,
}

# LinkedIn experience-level filter (f_E): 1 internship, 2 entry level, 3 associate, 4 mid-senior, ...
LINKEDIN_ENTRY_LEVELS = "2,3"

# Bump when presets change; existing databases are migrated once (see db._migrate_presets).
PRESET_VERSION = 4

# Big tech and well-known tech companies you asked to watch (added as "Your companies", label
# "Watchlist"). Each entry: (name, kind, config, check interval in minutes). The Greenhouse /
# Lever / Ashby boards were verified to exist; edit or delete any of them in the dashboard.
_GH, _LV, _AB = "greenhouse", "lever", "ashby"
WATCHLIST = [
    # Own career sites (dedicated connectors)
    ("Amazon", "amazon", {}, 2),
    ("Apple", "apple", {}, 2),
    ("Google", "google", {}, 2),
    ("Microsoft", "microsoft", {}, 2),
    ("Netflix", "eightfold", {"host": "explore.jobs.netflix.net", "domain": "netflix.com", "company": "Netflix"}, 2),
    # Workday
    ("NVIDIA", "workday", {"host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"}, 2),
    ("Salesforce", "workday", {"host": "salesforce.wd12.myworkdayjobs.com", "tenant": "salesforce",
                               "site": "External_Career_Site"}, 2),
    ("Adobe", "workday", {"host": "adobe.wd5.myworkdayjobs.com", "tenant": "adobe", "site": "external_experienced"}, 2),
    # Career sites with no usable feed: LinkedIn search limited to these companies (past hour)
    ("LinkedIn: Meta, Uber, Cisco, Oracle, IBM, Tesla", "linkedin",
     {"keywords": "engineer", "location": "United States", "experience": LINKEDIN_ENTRY_LEVELS,
      "company_ids": "10667,1815218,1063,1028,1009,15564"}, 10),
    # Greenhouse / Lever / Ashby
    *[(n, _GH, {"board": b}, 1) for n, b in [
        ("Stripe", "stripe"), ("Airbnb", "airbnb"), ("Databricks", "databricks"), ("Pinterest", "pinterest"),
        ("Lyft", "lyft"), ("Coinbase", "coinbase"), ("Robinhood", "robinhood"), ("Dropbox", "dropbox"),
        ("Reddit", "reddit"), ("Figma", "figma"), ("Instacart", "instacart"), ("Roblox", "roblox"),
        ("Discord", "discord"), ("Cloudflare", "cloudflare"), ("Twilio", "twilio"), ("Datadog", "datadog"),
        ("MongoDB", "mongodb"), ("Anthropic", "anthropic"), ("Scale AI", "scaleai"), ("Brex", "brex"),
        ("Asana", "asana"), ("Okta", "okta"), ("Elastic", "elastic"), ("GitLab", "gitlab"), ("Samsara", "samsara"),
        ("Affirm", "affirm"), ("Chime", "chime"), ("Duolingo", "duolingo")]],
    *[(n, _LV, {"company": b, "region": "global"}, 1) for n, b in [("Palantir", "palantir"), ("Spotify", "spotify")]],
    *[(n, _AB, {"board": b}, 1) for n, b in [
        ("OpenAI", "openai"), ("Notion", "notion"), ("Ramp", "ramp"), ("Plaid", "plaid"), ("Snowflake", "snowflake"),
        ("Perplexity", "perplexity")]],
]

# Discovery feeds created automatically on first start.
PRESET_SOURCES = [
    {"name": "Hacker News: Who is hiring", "kind": "hn_hiring", "config": {}, "interval_minutes": 10},
    {"name": "Y Combinator startups (HN jobs)", "kind": "hn_jobs", "config": {}, "interval_minutes": 10},
    {"name": "RemoteOK", "kind": "remoteok", "config": {}, "interval_minutes": 15},
    {"name": "Himalayas", "kind": "himalayas", "config": {}, "interval_minutes": 15},
    {"name": "We Work Remotely (programming)", "kind": "weworkremotely", "config": {}, "interval_minutes": 15},
    {"name": "LinkedIn: Software Engineer · United States", "kind": "linkedin",
     "config": {"keywords": "software engineer", "location": "United States", "experience": LINKEDIN_ENTRY_LEVELS},
     "interval_minutes": 10},
    {"name": "LinkedIn: AI Engineer · United States", "kind": "linkedin",
     "config": {"keywords": "AI engineer", "location": "United States", "experience": LINKEDIN_ENTRY_LEVELS},
     "interval_minutes": 10},
    {"name": "LinkedIn: New Grad Engineer · United States", "kind": "linkedin", "since_version": 2,
     "config": {"keywords": "new grad engineer", "location": "United States", "experience": LINKEDIN_ENTRY_LEVELS},
     "interval_minutes": 15},
    # Only roles with Age "0d"; each one's Apply link is opened and its description checked.
    {"name": "Simplify: New Grad SWE + AI/ML", "kind": "simplify", "since_version": 4,
     "config": {"repo": "SimplifyJobs/New-Grad-Positions", "sections": "Software Engineering, Machine Learning"},
     "interval_minutes": 2},
]
