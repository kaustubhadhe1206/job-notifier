# Hidden Jobs

Emails you within minutes when a **Software Engineer / AI Engineer** job is posted at a **US tech company**, with a focus on startups and mid-sized companies most people haven't heard of.

Every emailed job is also added to a **Google Sheet** (newest first, with Status and Notes columns for tracking applications).

Everything runs on free tiers: **Vercel** (React dashboard + Python API), **Supabase** (Postgres), **Gmail** (email), **Google Sheets** (via a small Apps Script), **cron-job.org** (every-minute trigger).

## How it finds jobs

1. **Discovery feeds** – Hacker News "Who is hiring", Y Combinator startup job posts, RemoteOK, Himalayas, We Work Remotely, plus LinkedIn searches for "Software Engineer" and "AI Engineer" in the United States. These are full of small companies.
2. **Auto-discovery** – when a feed post links to a company's own job board (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Workday), that company is added to the watch list automatically. From then on its new postings are caught on the company's own board, usually before they reach LinkedIn. The list grows by itself every day.
3. **Your companies** – type a company name or paste a careers URL in the dashboard. These are checked every minute.
4. **Filters** – only jobs whose title matches your role keywords, located in the US (or remote open to the US) and posted in the last 7 days are considered. All of this is editable in Settings.
5. **Work authorization (F-1 → H-1B)** – for every job that passes the filters, the full description is read before anything is emailed:
   - **Skipped** if it needs a security clearance (TS/SCI, Secret, polygraph, public trust…), US citizenship, a green card / permanent residency, or "US persons" (ITAR / export control), or if it says there's no visa sponsorship ("must be authorized to work without sponsorship", "no OPT/H-1B", …). Defense contractors and ITAR-bound companies (Lockheed, Raytheon, Booz Allen, SpaceX, Anduril…) are on the company block list by default.
   - **Emailed** otherwise, labeled by sponsorship evidence: the posting says so ("we sponsor visas", "visa sponsorship available", HN posts tagged VISA, "OPT welcome"), the company is on your own sponsor list, the company had H-1B approvals in USCIS data (FY2021–23, about 85,000 employers), or "sponsorship not mentioned". Confirmed sponsors are listed first in each email.
   - If a description can't be read (LinkedIn often rate-limits), the job waits and is retried up to 4 times a few minutes apart, so a clearance requirement isn't missed because of a failed request.
   - Every decision is shown with its reason (and the quoted sentence) in the email and under **Jobs → Skipped for visa / clearance**.

   By default (**Everything without restrictions**), jobs that say nothing about sponsorship are emailed too. Not mentioning it doesn't guarantee sponsorship, which is why each job is labeled. For only confirmed sponsors, switch Settings → Visa sponsorship to **Only confirmed sponsors**.

The first time a source is checked, its existing jobs are recorded but **not** emailed (except ones posted in the last 48 h). After that, every new posting that matches is emailed. Several jobs found in the same minute arrive as one email.

## Deploy

Follow **[SETUP.md](SETUP.md)**: step-by-step setup of GitHub, Supabase, Gmail, the Google Sheet, Vercel and cron-job.org (about 40 minutes, all free).

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt uvicorn
copy .env.example .env          # then fill in the values
uvicorn index:app --app-dir api --reload --port 8000
```

In a second terminal:

```bash
npm install
npm run dev                     # http://localhost:5173 (API calls are proxied to :8000)
```

## Good to know

- **"Immediately" means within about 1–2 minutes** for companies you add, 10–15 minutes for the feeds, and about 15 minutes for auto-discovered companies (checked in rotation). No job site pushes notifications, so checking often is as fast as it gets.
- **LinkedIn is best-effort.** It has no public API, its terms prohibit automated access, and it often blocks requests from cloud servers (HTTP 429). The app retries on its own; company boards are the reliable path.
- **Staffing agencies** sometimes show up from LinkedIn. Add their names under Settings → "Never email jobs from these companies".
- **Free-tier CPU:** the dashboard shows CPU used in the last 24 h and a monthly projection. Compare it with your Vercel plan's limit (Vercel → Usage). If it gets close, raise "Check each discovered company every … minutes" to 30 or 60.
- Auto-discovered boards that fail 5 times in a row are paused automatically.
- **The visa checks are text rules, not legal advice.** They catch the standard wording, but always read the posting before applying. Company names are matched to USCIS legal names (e.g. "Scale AI" → SCALE AI INC), so a company that files under a very different legal name ("CircleCI" → Circle Internet Services) won't be found; add it to "Companies you know sponsor".
- **Refreshing H-1B data:** when USCIS publishes a new year at [uscis.gov/archive/h-1b-employer-data-hub-files](https://www.uscis.gov/archive/h-1b-employer-data-hub-files), run `python scripts/build_h1b_data.py 2022 2023 2024` (or download the CSVs in your browser and pass the file paths if USCIS blocks the script), then redeploy.

## Project layout

```
api/index.py            FastAPI app (all /api/* routes; Vercel serverless function)
api/_core/fetchers.py   one fetcher per job source (ATS boards + discovery feeds)
api/_core/checker.py    the polling pass: fetch → store → discover → email
api/_core/filters.py    role / US-location / recency filters
api/_core/eligibility.py clearance / citizenship / sponsorship checks + H-1B company lookup
api/_core/data/         h1b_sponsors.json.gz (built from USCIS data by scripts/build_h1b_data.py)
api/_core/detect.py     recognise job boards from URLs, pages, company names and feed text
api/_core/presets.py    default filters and discovery feeds
api/_core/db.py         Postgres schema and queries
api/_core/notifier.py   email over SMTP
api/_core/sheets.py     Google Sheet sync (talks to google-sheets/Code.gs)
google-sheets/Code.gs   Apps Script you paste into your spreadsheet
src/                    React dashboard (Vite)
vercel.json             build, API routing, daily backup cron
```
