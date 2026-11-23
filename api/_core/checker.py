"""One polling pass: fetch due sources, store new jobs, discover new company boards, email matches."""
from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import db, detect, discovery, eligibility, fetchers, llm_check, notifier, seeder, sheets
from .filters import Filters

MAX_SOURCES_PER_RUN = 100  # a run checks at most this many sources (mostly cheap "not modified" replies)
FETCH_WORKERS = 20
MAX_DESCRIPTIONS_PER_RUN = 60  # full descriptions fetched per run; the rest wait for the next run
MAX_READ_ATTEMPTS = 4          # tries to read a description before deciding without it
PENDING_PER_RUN = 30           # jobs whose description couldn't be read earlier, retried each run


@dataclass
class FetchResult:
    jobs: list | None = None
    error: str | None = None
    not_modified: bool = False
    etag: str | None = None


def fetch_source(source: dict) -> FetchResult:
    ctx = fetchers.FetchCtx(etag=source.get("etag") if source.get("initialized") else None)
    try:
        jobs = fetchers.fetch(source["kind"], fetchers.client(), source["config"], ctx)
        return FetchResult(jobs=jobs, etag=ctx.new_etag)
    except fetchers.NotModified:
        return FetchResult(not_modified=True, etag=source.get("etag"))
    except Exception as e:  # noqa: BLE001 - recorded on the source and shown in the UI
        return FetchResult(error=fetchers.describe_error(e))


CHANNEL_RANK = {"hidden_new": 0, "hidden": 1, "watchlist": 2, "simplify": 3, "jobboard": 4, "linkedin": 5}


def _channel(source: dict) -> str:
    """Where a job was found (shown as a label in emails, the dashboard and the sheet).
    hidden      a discovered company's own career page (usually before LinkedIn has it)
    hidden_new  same, and that company had no US engineering openings until now
    watchlist   a company you added yourself
    simplify    the SimplifyJobs new grad list on GitHub
    linkedin    LinkedIn search
    jobboard    Hacker News, YC, RemoteOK, We Work Remotely, Himalayas"""
    if source["kind"] == "simplify":
        return "simplify"
    if source["origin"] == "manual" and (source["kind"] != "linkedin" or (source.get("config") or {}).get("company_ids")):
        return "watchlist"  # your companies, including a LinkedIn search limited to companies you picked
    if source["kind"] == "linkedin":
        return "linkedin"
    if source["origin"] == "auto":
        return "hidden_new" if source.get("initialized") and source.get("relevant_count") == 0 else "hidden"
    return "jobboard"


def _recently_checked(source: dict) -> bool:
    """True if the previous successful check was recent, so anything new appeared since then.
    After a long gap (paused source, stalled cron) new jobs could be hours old."""
    last = source.get("last_success_at")
    if not last:
        return False
    gap = datetime.now(timezone.utc) - last
    return gap <= timedelta(minutes=2 * int(source.get("interval_minutes") or 1) + 15)


def _ids_hash(jobs) -> str:
    return hashlib.sha1("\n".join(sorted(j.external_id for j in jobs)).encode()).hexdigest()


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def _dedupe(jobs: list[dict]) -> list[dict]:
    """The same opening often shows up on LinkedIn and on the company board: keep one."""
    unique, seen = [], set()
    for j in jobs:
        key = (_norm(j["company"]), _norm(j["title"]))
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


def _description(item, allow_fetch: bool) -> tuple[str, bool]:
    """(plain text, read_failed)."""
    source, job = item
    if job.description:
        return eligibility.html_to_text(job.description), False
    if not allow_fetch:
        return "", True  # over this run's budget: try again next run
    try:
        return fetchers.fetch_description(source["kind"], source["config"], job.external_id, job.url), False
    except Exception:  # noqa: BLE001 - rate limits, timeouts
        return "", True


def evaluate_jobs(items: list[tuple[dict, fetchers.Job]], settings: dict,
                  attempts: list[int] | None = None,
                  ai_usage: llm_check.Usage | None = None) -> list[eligibility.Verdict]:
    """Read each candidate's description (in parallel) and decide if you're eligible.

    If a description can't be read (LinkedIn rate limits, timeouts) the job stays 'pending' and is
    retried on later runs, so a clearance requirement is never missed just because a request
    failed. After MAX_READ_ATTEMPTS it's decided from the title and company alone.

    With ai_usage given (AI check enabled and within budget), jobs that pass the rules are also
    read by Claude Haiku, which can add restrictions the rules missed."""
    attempts = attempts or [MAX_READ_ATTEMPTS] * len(items)
    with ThreadPoolExecutor(max_workers=10) as pool:
        reads = list(pool.map(lambda ix: _description(ix[1], ix[0] < MAX_DESCRIPTIONS_PER_RUN), enumerate(items)))
    verdicts = []
    for (source, job), (text, failed), attempt in zip(items, reads, attempts):
        if failed and attempt < MAX_READ_ATTEMPTS:
            verdicts.append(eligibility.Verdict(
                False, "pending", f"Couldn't read the description yet (try {attempt} of {MAX_READ_ATTEMPTS}); retrying shortly"))
        else:
            verdicts.append(eligibility.evaluate(job.title, job.company or source["name"], text, settings,
                                                 loose=job.loose))

    if ai_usage is not None:
        to_check = [i for i, v in enumerate(verdicts) if v.ok and reads[i][0]][:llm_check.MAX_CALLS_PER_RUN]
        max_years = int(settings.get("max_years_experience") or 0)

        def ask(i):
            source, job = items[i]
            return llm_check.check(job.title, job.company or source["name"], reads[i][0], max_years, ai_usage)

        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(ask, to_check))
        for i, facts in zip(to_check, answers):
            if facts:
                verdicts[i] = eligibility.apply_ai(verdicts[i], facts, settings)
    return verdicts


def ai_usage_for_run(conn, settings: dict) -> llm_check.Usage | None:
    """A Usage tracker if the AI check should run now (enabled, key set, under the monthly limit)."""
    if not (settings.get("ai_check") and llm_check.configured()):
        return None
    limit = float(settings.get("ai_monthly_limit_usd") or 0)
    if limit > 0 and db.ai_spend(conn)["month_usd"] >= limit:
        return None
    return llm_check.Usage()


def run_check(trigger: str = "cron", force: bool = False, source_ids: list[int] | None = None) -> dict:
    cpu0, started = time.process_time(), datetime.now(timezone.utc)

    with db.connect() as conn:
        settings = db.get_settings(conn)
        sources = db.claim_due_sources(conn, force=force, ids=source_ids, limit=MAX_SOURCES_PER_RUN)

    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        results = list(pool.map(fetch_source, sources))

    filters = Filters(settings)
    summary = {"checked": len(sources), "unchanged": 0, "new_jobs": 0, "matched": 0, "emailed": 0,
               "discovered": 0, "errors": [], "new": []}
    boards: dict[str, dict] = {}
    names: dict[str, str] = {}
    candidates: list[tuple[int, dict, fetchers.Job]] = []  # (job row id, source, job)
    auto_interval = int(settings.get("auto_interval_minutes") or 15)
    idle_interval = int(settings.get("auto_idle_interval_minutes") or 360)

    with db.connect() as conn:
        for source, res in zip(sources, results):
            if res.error:
                db.source_error(conn, source["id"], res.error)
                summary["errors"].append({"source": source["name"], "error": res.error})
                continue
            ids_hash = _ids_hash(res.jobs) if res.jobs is not None else None
            if res.not_modified or (source["initialized"] and ids_hash == source["ids_hash"]):
                db.source_ok(conn, source["id"], etag=res.etag)
                summary["unchanged"] += 1
                continue

            # First successful fetch = baseline: jobs that were already open are only emailed
            # if their exact posting time is within max_age_minutes (see filters._freshness).
            baseline = not source["initialized"]
            if source["kind"] in fetchers.BOARD_KINDS:
                for j in res.jobs:  # company boards: the company is the board itself
                    j.company = j.company or source["name"]
            observed = source["kind"] in fetchers.OBSERVED_KINDS and _recently_checked(source)
            engineering = [j for j in res.jobs if filters.hires_engineers(j)]

            # Only jobs that fit your filters (role, level, company, US) are stored: with thousands
            # of companies watched, storing every posting would fill the free database.
            channel = _channel(source)
            reasons, rows = {}, []
            for j in res.jobs:
                if filters.static_reason(j) is not None:
                    continue
                reasons[j.external_id] = filters.reason(j, baseline=baseline, observed=observed)
                rows.append({
                    "external_id": j.external_id,
                    "title": j.title[:500],
                    "company": (j.company or "")[:200],
                    "location": (j.location or "")[:300],
                    "url": j.url,
                    "posted_at": j.posted_at.isoformat() if fetchers.is_precise(j.posted_at) else None,
                    "baseline": baseline,
                    "matched": False,  # decided below, after reading the description
                    "note": reasons[j.external_id],
                    "channel": channel,
                })
            inserted = db.insert_jobs(conn, source["id"], rows)

            # Companies with US engineering openings are checked often; others every few hours.
            interval = None
            if source["origin"] == "auto" and source["kind"] in fetchers.BOARD_KINDS:
                interval = auto_interval if engineering else idle_interval
            db.source_ok(conn, source["id"], job_count=len(res.jobs), etag=res.etag, ids_hash=ids_hash,
                         relevant_count=len(engineering), interval_minutes=interval)

            if not baseline:
                summary["new_jobs"] += len(inserted)
            new_rows = {r["external_id"]: r["id"] for r in inserted}
            candidates += [(new_rows[j.external_id], source, j) for j in res.jobs
                           if j.external_id in new_rows and reasons.get(j.external_id, "x") is None]

            # Discovery from feeds and LinkedIn: (1) links to company career pages inside posts,
            # (2) names of companies hiring engineers, looked up on the hiring platforms later.
            if settings.get("auto_discover") and source["kind"] in fetchers.FEED_KINDS:
                for j in engineering:
                    for kind, cfg in detect.find_boards(f"{j.url} {j.text}"):
                        key = fetchers.board_key(kind, cfg)
                        if key not in boards:
                            name = j.company if j.company and len(j.company) < 60 else detect.name_for(kind, cfg)
                            boards[key] = {"name": name, "kind": kind, "config": cfg,
                                           "url": fetchers.public_url(kind, cfg), "board_key": key}
                    key = eligibility.normalize_company(j.company)
                    if len(key) >= 2 and len(j.company) < 60:
                        names[key] = j.company.strip()

        if boards:
            summary["discovered"] = db.add_auto_sources(conn, list(boards.values()), auto_interval)
        if names:
            summary["queued_companies"] = db.enqueue_companies(conn, names)
        conn.commit()

        # Look up career pages for a few queued company names, and grow the list from Common Crawl.
        if settings.get("auto_discover"):
            summary["discovered"] += discovery.lookup_queued_companies(conn, auto_interval, started)
            summary["seeded"] = seeder.step(conn, auto_interval, started)
            conn.commit()

        # Work-authorization check (clearance / citizenship / sponsorship) for new candidates,
        # plus earlier ones whose description couldn't be read yet.
        work = [(jid, s, j, 1) for jid, s, j in candidates]
        for p in db.pending_jobs(conn, PENDING_PER_RUN):
            src = {"id": p["source_id"], "name": p["source_name"], "kind": p["kind"], "config": p["config"]}
            job = fetchers.Job(p["external_id"], p["title"], p["url"] or "", p["location"] or "", p["company"] or "",
                               p["posted_at"])
            work.append((p["id"], src, job, p["checks"] + 1))
        if work:
            ai_usage = ai_usage_for_run(conn, settings)
            verdicts = evaluate_jobs([(s, j) for _, s, j, _ in work], settings, [n for *_, n in work], ai_usage)
            db.set_eligibility(conn, [{"id": jid, "matched": v.ok, "visa": v.visa, "note": v.note, "checks": n}
                                      for (jid, _, _, n), v in zip(work, verdicts)])
            if ai_usage and ai_usage.calls:
                db.record_ai_usage(conn, ai_usage)
                summary["ai_checks"] = ai_usage.calls
                summary["ai_cost_usd"] = round(ai_usage.cost_usd, 5)
                if ai_usage.errors:
                    summary["errors"].append({"source": "AI check", "error": ai_usage.last_error})
            conn.commit()
            summary["candidates"] = len(work)
            summary["blocked"] = sum(1 for v in verdicts if v.visa == "blocked")
            summary["pending"] = sum(1 for v in verdicts if v.visa == "pending")
            summary["matched"] = sum(1 for v in verdicts if v.ok)
            summary["new"] = [{"source": s["name"], "title": j.title, "visa": v.visa, "ok": v.ok, "note": v.note}
                              for (_, s, j, _), v in zip(work, verdicts)][:30]

        email_error = None
        if not settings.get("paused"):
            pending = db.claim_pending(conn)
            conn.commit()
            if pending:
                # The same opening often shows up on LinkedIn and on the company board: send it once.
                unique = _dedupe(pending)
                # Hidden jobs first, then by sponsorship evidence.
                rank = {"stated": 0, "listed": 1, "history": 2}
                unique.sort(key=lambda j: (CHANNEL_RANK.get(j.get("channel"), 5), rank.get(j.get("visa"), 3)))
                try:
                    notifier.send_jobs_email(notifier.recipient(settings), unique, os.environ.get("APP_URL", ""))
                    summary["emailed"] = len(unique)
                except Exception as e:  # noqa: BLE001 - un-claim so the next run retries
                    db.unclaim(conn, [j["id"] for j in pending])
                    email_error = f"Email failed: {e}"[:300]
                    summary["errors"].append({"source": "email", "error": email_error})
                conn.commit()

        # Google Sheet: every matched job, independent of email (and of "pause emails").
        if sheets.configured():
            rows = db.claim_sheet_rows(conn)
            conn.commit()
            if rows:
                try:
                    res = sheets.append_jobs(_dedupe(rows))
                    summary["sheet_rows"] = res.get("appended", 0)
                    if res.get("url"):
                        db.set_meta(conn, "sheet_url", res["url"])
                except Exception as e:  # noqa: BLE001 - un-claim so the next run retries
                    db.unclaim_sheet(conn, [j["id"] for j in rows])
                    sheet_error = f"Google Sheet failed: {e}"[:300]
                    summary["errors"].append({"source": "sheet", "error": sheet_error})
                    email_error = f"{email_error}; {sheet_error}" if email_error else sheet_error

        cpu_ms = int((time.process_time() - cpu0) * 1000)
        db.record_run(conn, trigger=trigger, started_at=started, checked=summary["checked"],
                      new_jobs=summary["new_jobs"], emailed=summary["emailed"], discovered=summary["discovered"],
                      errors=len(summary["errors"]), cpu_ms=cpu_ms, message=email_error)

    summary["cpu_ms"] = cpu_ms
    summary["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 2)
    return summary
