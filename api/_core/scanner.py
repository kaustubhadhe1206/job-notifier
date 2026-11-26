"""Check discovered companies' career pages from GitHub Actions (.github/workflows/scan.yml).

Vercel's free plan has CPU for a few hundred companies checked often, but Common Crawl lists
about 10,000. GitHub Actions runs this script every few minutes on GitHub's machines (free for
public repositories), so every company can be checked at its interval: companies hiring US
engineers every few minutes, the others daily.

The scanner only finds new jobs. Each one that passes your filters is stored as "being checked"
(visa = 'pending'), and the next every-minute Vercel run reads its description, runs the visa
and AI checks, and emails it, exactly like jobs found on Vercel. So the scanner needs only
DATABASE_URL, and if it stops, Vercel keeps checking companies on its own (just less often).
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import checker, db, fetchers
from .filters import Filters

SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "240"))  # how long one run keeps working
BATCH = 300        # companies claimed at a time
WORKERS = 32       # parallel requests (mostly waiting on the network)
IDLE_SLEEP = 20    # seconds to wait when no company is due


def run(seconds: int = SCAN_SECONDS) -> dict:
    cpu0, t0, started = time.process_time(), time.monotonic(), datetime.now(timezone.utc)
    totals = {"checked": 0, "unchanged": 0, "new_jobs": 0, "candidates": 0, "errors": 0, "batches": 0}
    error_samples: list[dict] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while time.monotonic() - t0 < seconds:
            with db.connect() as conn:
                settings = db.get_settings(conn)
                sources = db.claim_due_sources(conn, limit=0, auto_limit=BATCH)  # discovered companies only
            if not sources:
                time.sleep(IDLE_SLEEP)
                continue
            results = list(pool.map(checker.fetch_source, sources))
            summary = {"unchanged": 0, "new_jobs": 0, "errors": []}
            with db.connect() as conn:
                candidates = checker.process_results(conn, sources, results, settings, Filters(settings),
                                                     summary, {}, {})
                # The Vercel run reads each description, checks eligibility and emails it.
                db.mark_for_check(conn, [jid for jid, _, _ in candidates])
            totals["batches"] += 1
            totals["checked"] += len(sources)
            totals["unchanged"] += summary["unchanged"]
            totals["new_jobs"] += summary["new_jobs"]
            totals["candidates"] += len(candidates)
            totals["errors"] += len(summary["errors"])
            error_samples = (error_samples + summary["errors"])[:5]
            print(f"batch {totals['batches']}: {len(sources)} companies, {summary['unchanged']} unchanged, "
                  f"{summary['new_jobs']} new jobs, {len(candidates)} to check, {len(summary['errors'])} errors",
                  flush=True)
    cpu_ms = int((time.process_time() - cpu0) * 1000)
    with db.connect() as conn:
        db.record_run(conn, trigger="scanner", started_at=started, checked=totals["checked"],
                      new_jobs=totals["new_jobs"], emailed=0, discovered=0, errors=totals["errors"], cpu_ms=cpu_ms)
        db.set_meta(conn, "scanner", {**totals, "finished_at": datetime.now(timezone.utc).isoformat(),
                                      "errors_sample": error_samples})
    return {**totals, "cpu_ms": cpu_ms, "errors_sample": error_samples}
