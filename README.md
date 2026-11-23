# Job Notifier

Emails you within minutes when a **Software Engineer / AI Engineer** job is posted at a **US tech company**, with a focus on startups and mid-sized companies most people haven't heard of.

Every emailed job is also added to a **Google Sheet** (newest first, with Status and Notes columns for tracking applications).

Everything runs on free tiers: **Vercel** (React dashboard + Python API), **Supabase** (Postgres), **Gmail** (email), **Google Sheets** (via a small Apps Script), **cron-job.org** (every-minute trigger).

## How it finds jobs

1. **Discovery feeds** – Hacker News "Who is hiring", Y Combinator startup job posts, RemoteOK, Himalayas, We Work Remotely, plus LinkedIn searches for "Software Engineer" and "AI Engineer" in the United States. These are full of small companies.
   - **Simplify** – the [SimplifyJobs New Grad list](https://github.com/SimplifyJobs/New-Grad-Positions) (Software Engineering and Data Science, AI & Machine Learning sections), checked every 2 minutes. Only roles posted today (Age "0d") count. The app opens each row's **Apply** link, reads the actual posting (through the Greenhouse / Lever / Ashby / Workday / Oracle / SmartRecruiters / Workable APIs, or the page itself), and runs the same eligibility rules and AI check as every other job; the list's legend emojis are ignored. These jobs are labeled **Simplify**. Every open role on the list (not just today's) is also scanned for company career pages, which are then watched as Hidden companies.
2. **Auto-discovery of companies you don't know** – the app watches company career pages directly (the Greenhouse / Lever / Ashby / Workday / Workable / SmartRecruiters feed behind the page), where jobs appear first. It finds those companies three ways:
   - **Links in job posts:** a feed post linking to a company's career page.
   - **Company names:** every company seen hiring engineers on LinkedIn or the feeds is looked up on the hiring platforms and its career page is watched from then on (about 2 lookups per minute).
   - **Common Crawl web index:** thousands of company career pages listed in the public Common Crawl index are added a page at a time (one step every 10 minutes, retried automatically when the index is busy, refreshed monthly).

   Companies with US Software/AI Engineer openings are checked every 15 minutes; the rest every 6 hours (both editable). Only jobs matching your filters are stored, so thousands of companies fit in the free database.
3. **Your companies** – type a company name or paste a careers URL in the dashboard. These are checked every 2 minutes.
4. **Filters** – only jobs whose title matches your role keywords, located in the US (or remote open to the US), entry level (no Senior/Staff/Lead titles, at most 2 years of experience required) and **posted within the last 60 minutes** are considered. All of this is editable in Settings.
5. **Work authorization (F-1 → H-1B)** – for every job that passes the filters, the full description is read before anything is emailed:
   - **Skipped** if it needs a security clearance (TS/SCI, Secret, polygraph, public trust…), US citizenship, a green card / permanent residency, or "US persons" (ITAR / export control), or if it says there's no visa sponsorship ("must be authorized to work without sponsorship", "no OPT/H-1B", …). Defense contractors and ITAR-bound companies (Lockheed, Raytheon, Booz Allen, SpaceX, Anduril…) are on the company block list by default.
   - **Emailed** otherwise, labeled by sponsorship evidence: the posting says so ("we sponsor visas", "visa sponsorship available", HN posts tagged VISA, "OPT welcome"), the company is on your own sponsor list, the company had H-1B approvals in USCIS data (FY2021–23, about 85,000 employers), or "sponsorship not mentioned". Confirmed sponsors are listed first in each email.
   - If a description can't be read (LinkedIn often rate-limits), the job waits and is retried up to 4 times a few minutes apart, so a clearance requirement isn't missed because of a failed request.
   - **Optional AI check:** with an `ANTHROPIC_API_KEY`, Claude Haiku 5.5 also reads every job that passes the rules and blocks unusual phrasings of citizenship, clearance, sponsorship and experience requirements (a few cents a month; spend and estimated credit left are shown in the dashboard; SETUP.md step 9).
   - Every decision is shown with its reason (and the quoted sentence) in the email and under **Jobs → Skipped for visa / clearance**.

   By default (**Everything without restrictions**), jobs that say nothing about sponsorship are emailed too. Not mentioning it doesn't guarantee sponsorship, which is why each job is labeled. For only confirmed sponsors, switch Settings → Visa sponsorship to **Only confirmed sponsors**.

**Freshness (only jobs posted minutes ago):** a job is emailed only if it's provably fresh:
- it just appeared on a company board that is checked every 1–15 minutes (and, if the board gives an exact publish time, that time is within 60 minutes), or
- LinkedIn / a job feed shows an exact posting time within the last 60 minutes ("23 minutes ago"). LinkedIn searches only ask for jobs from the past hour.

Jobs without proof of freshness ("posted today", no date, jobs already open when a company is first discovered) are tracked but never emailed. The window is editable in Settings.

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
api/_core/llm_check.py  optional Claude Haiku second opinion on each job's requirements
api/_core/data/         h1b_sponsors.json.gz (built from USCIS data by scripts/build_h1b_data.py)
api/_core/detect.py     recognise job boards from URLs, pages, company names and feed text
api/_core/presets.py    default filters and discovery feeds
api/_core/db.py         Postgres schema and queries
api/_core/notifier.py   email over SMTP
api/_core/sheets.py     Google Sheet sync (talks to google-sheets/Code.gs)
api/_core/discovery.py  looks up career pages for company names seen on LinkedIn / feeds
api/_core/seeder.py     adds company career pages from the Common Crawl web index
google-sheets/Code.gs   Apps Script you paste into your spreadsheet
src/                    React dashboard (Vite)
vercel.json             build, API routing, daily backup cron
```
