"""One polling pass: fetch due sources, store new jobs, discover new company boards, email matches."""
from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db, detect, eligibility, fetchers, notifier, sheets
from .filters import Filters

MAX_SOURCES_PER_RUN = 60   # keeps one run well inside Vercel's 60s limit and the free CPU budget
FETCH_WORKERS = 12
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
                  attempts: list[int] | None = None) -> list[eligibility.Verdict]:
    """Read each candidate's description (in parallel) and decide if you're eligible.

    If a description can't be read (LinkedIn rate limits, timeouts) the job stays 'pending' and is
    retried on later runs, so a clearance requirement is never missed just because a request
    failed. After MAX_READ_ATTEMPTS it's decided from the title and company alone."""
    attempts = attempts or [MAX_READ_ATTEMPTS] * len(items)
    with ThreadPoolExecutor(max_workers=10) as pool:
        reads = list(pool.map(lambda ix: _description(ix[1], ix[0] < MAX_DESCRIPTIONS_PER_RUN), enumerate(items)))
    verdicts = []
    for (source, job), (text, failed), attempt in zip(items, reads, attempts):
        if failed and attempt < MAX_READ_ATTEMPTS:
            verdicts.append(eligibility.Verdict(
                False, "pending", f"Couldn't read the description yet (try {attempt} of {MAX_READ_ATTEMPTS}); retrying shortly"))
        else:
            verdicts.append(eligibility.evaluate(job.title, job.company or source["name"], text, settings))
    return verdicts


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
    candidates: list[tuple[int, dict, fetchers.Job]] = []  # (job row id, source, job)

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
            # if they were posted in the last 48h (see filters.BASELINE_RECENT).
            baseline = not source["initialized"]
            if source["kind"] in fetchers.BOARD_KINDS:
                for j in res.jobs:  # company boards: the company is the board itself
                    j.company = j.company or source["name"]
            reasons = {j.external_id: filters.reason(j, baseline=baseline) for j in res.jobs}
            # Jobs start unmatched; candidates are decided below after reading their descriptions.
            rows = [{
                "external_id": j.external_id,
                "title": j.title[:500],
                "company": (j.company or "")[:200],
                "location": (j.location or "")[:300],
                "url": j.url,
                "posted_at": j.posted_at.isoformat() if j.posted_at else None,
                "baseline": baseline,
                "matched": False,
                "note": None if baseline else reasons[j.external_id],
            } for j in res.jobs]
            inserted = db.insert_jobs(conn, source["id"], rows)
            db.source_ok(conn, source["id"], job_count=len(res.jobs), etag=res.etag, ids_hash=ids_hash)

            if not baseline:
                summary["new_jobs"] += len(inserted)
            new_rows = {r["external_id"]: r["id"] for r in inserted}
            candidates += [(new_rows[j.external_id], source, j) for j in res.jobs
                           if j.external_id in new_rows and reasons[j.external_id] is None]

            # Auto-discovery: links to company boards inside feed posts become new watched sources.
            if settings.get("auto_discover") and source["kind"] in fetchers.FEED_KINDS and inserted:
                new_ids = {r["external_id"] for r in inserted}
                for j in res.jobs:
                    if j.external_id not in new_ids:
                        continue
                    for kind, cfg in detect.find_boards(f"{j.url} {j.text}"):
                        key = fetchers.board_key(kind, cfg)
                        if key not in boards:
                            name = j.company if j.company and len(j.company) < 60 else detect.name_for(kind, cfg)
                            boards[key] = {"name": name, "kind": kind, "config": cfg,
                                           "url": fetchers.public_url(kind, cfg), "board_key": key}

        if boards:
            interval = int(settings.get("auto_interval_minutes") or 15)
            summary["discovered"] = db.add_auto_sources(conn, list(boards.values()), interval)
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
            verdicts = evaluate_jobs([(s, j) for _, s, j, _ in work], settings, [n for *_, n in work])
            db.set_eligibility(conn, [{"id": jid, "matched": v.ok, "visa": v.visa, "note": v.note, "checks": n}
                                      for (jid, _, _, n), v in zip(work, verdicts)])
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
                # Confirmed sponsors first, then jobs that just don't mention sponsorship.
                rank = {"stated": 0, "listed": 1, "history": 2}
                unique.sort(key=lambda j: rank.get(j.get("visa"), 3))
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
