"""Check discovered companies' career pages from GitHub Actions (.github/workflows/scan.yml).

Vercel's free plan has CPU for a few hundred companies checked often, but Common Crawl lists
about 10,000. GitHub Actions runs this script every few minutes on GitHub's machines (free for
public repositories), so every company can be checked at its interval: companies hiring US
engineers every few minutes, the others daily.

The scanner only finds new jobs. Each one that passes your filters is stored as "being checked"
(visa = 'pending'), and the next every-minute Vercel run reads its description, runs the visa
and AI checks, and emails it, exactly like jobs found on Vercel. So the scanner needs only
DATABASE_URL, and if it stops, Vercel keeps checking companies on its own (just less often).

It also reads LinkedIn job descriptions that LinkedIn refused to send to Vercel: GitHub's machines
have different IP addresses, so LinkedIn usually answers them. The text is stored with the job and
the next Vercel run checks it as usual (no AI key or email settings needed here).

Workday (about 4,700 of the companies) rate-limits one machine hitting many of its sites at
once, so Workday requests go a few at a time; if Workday keeps answering "too many requests",
Workday is skipped for the rest of the run and those companies are put back in the queue.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import checker, db, fetchers
from .filters import Filters

SCAN_SECONDS = int(os.environ.get("SCAN_SECONDS", "240"))  # how long one run keeps working
BATCH = 300        # companies claimed at a time
WORKERS = 32       # parallel requests (mostly waiting on the network)
IDLE_SLEEP = 20    # seconds to wait when no company is due
GENTLE_KINDS = {"workday": 3}  # platform -> requests at a time
TRIP_AFTER_429 = 15            # "too many requests" answers before a platform is skipped this run
LINKEDIN_READS = 8             # LinkedIn descriptions read per minute (one at a time, a few seconds apart)
LINKEDIN_GAP = 3               # seconds between LinkedIn requests


class _Gate:
    """Per-platform concurrency limit plus a breaker that trips after repeated HTTP 429s."""

    def __init__(self):
        self.sems = {k: threading.Semaphore(n) for k, n in GENTLE_KINDS.items()}
        self.hits: dict[str, int] = {}
        self.tripped: set[str] = set()
        self.lock = threading.Lock()

    def fetch(self, source: dict) -> checker.FetchResult | None:
        """The fetch result, or None if the company should be put back unchecked."""
        kind = source["kind"]
        if kind in self.tripped:
            return None
        sem = self.sems.get(kind)
        if sem is None:
            return checker.fetch_source(source)
        with sem:
            if kind in self.tripped:
                return None
            res = checker.fetch_source(source)
        if res.error and "429" in res.error:
            with self.lock:
                self.hits[kind] = self.hits.get(kind, 0) + 1
                if self.hits[kind] >= TRIP_AFTER_429:
                    self.tripped.add(kind)
            return None  # not a real failure: retry later
        return res


def read_linkedin_descriptions(limit: int = LINKEDIN_READS) -> tuple[int, int]:
    """Read descriptions LinkedIn refused to Vercel. Returns (read, refused); stops at the first
    refusal so this machine doesn't get blocked too."""
    with db.connect() as conn:
        todo = db.linkedin_to_read(conn, limit)
    read = 0
    for i, j in enumerate(todo):
        if i:
            time.sleep(LINKEDIN_GAP)
        try:
            text = fetchers.fetch_description(j["kind"], j["config"], j["external_id"], j["url"] or "")
        except Exception as e:  # noqa: BLE001 - rate limits, timeouts
            print(f"LinkedIn description refused ({type(e).__name__}: {str(e)[:80]}); trying again in a minute", flush=True)
            return read, 1
        if text:
            with db.connect() as conn:
                db.save_description(conn, j["id"], text)
            read += 1
    return read, 0


def run(seconds: int = SCAN_SECONDS) -> dict:
    cpu0, t0, started = time.process_time(), time.monotonic(), datetime.now(timezone.utc)
    totals = {"checked": 0, "unchanged": 0, "new_jobs": 0, "candidates": 0, "errors": 0, "put_back": 0,
              "batches": 0, "linkedin_read": 0, "linkedin_refused": 0}
    last_linkedin = -60.0
    error_samples: list[dict] = []
    gate = _Gate()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while time.monotonic() - t0 < seconds:
            if time.monotonic() - last_linkedin >= 60:
                last_linkedin = time.monotonic()
                read, refused = read_linkedin_descriptions()
                totals["linkedin_read"] += read
                totals["linkedin_refused"] += refused
                if read or refused:
                    print(f"LinkedIn descriptions: {read} read for Vercel to check{', then refused' if refused else ''}",
                          flush=True)
            with db.connect() as conn:
                settings = db.get_settings(conn)
                # discovered companies only; platforms that keep refusing are left for the next run
                sources = db.claim_due_sources(conn, limit=0, auto_limit=BATCH, skip_kinds=sorted(gate.tripped))
            if not sources:
                time.sleep(IDLE_SLEEP)
                continue
            fetched = list(pool.map(gate.fetch, sources))
            done = [(s, r) for s, r in zip(sources, fetched) if r is not None]
            put_back = [s["id"] for s, r in zip(sources, fetched) if r is None]
            summary = {"unchanged": 0, "new_jobs": 0, "errors": []}
            with db.connect() as conn:
                db.release_sources(conn, put_back)
                candidates = checker.process_results(conn, [s for s, _ in done], [r for _, r in done], settings,
                                                     Filters(settings), summary, {}, {})
                # The Vercel run reads each description, checks eligibility and emails it.
                db.mark_for_check(conn, [jid for jid, _, _ in candidates])
            totals["batches"] += 1
            totals["checked"] += len(done)
            totals["put_back"] += len(put_back)
            totals["unchanged"] += summary["unchanged"]
            totals["new_jobs"] += summary["new_jobs"]
            totals["candidates"] += len(candidates)
            totals["errors"] += len(summary["errors"])
            error_samples = (error_samples + summary["errors"])[:5]
            print(f"batch {totals['batches']}: {len(done)} checked, {len(put_back)} put back"
                  f"{' (skipping ' + ', '.join(sorted(gate.tripped)) + ': too many requests)' if gate.tripped else ''}, "
                  f"{summary['unchanged']} unchanged, {summary['new_jobs']} new jobs, {len(candidates)} to check, "
                  f"{len(summary['errors'])} errors", flush=True)
    cpu_ms = int((time.process_time() - cpu0) * 1000)
    with db.connect() as conn:
        db.record_run(conn, trigger="scanner", started_at=started, checked=totals["checked"],
                      new_jobs=totals["new_jobs"], emailed=0, discovered=0, errors=totals["errors"], cpu_ms=cpu_ms)
        db.set_meta(conn, "scanner", {**totals, "finished_at": datetime.now(timezone.utc).isoformat(),
                                      "skipped_platforms": sorted(gate.tripped), "errors_sample": error_samples})
    return {**totals, "cpu_ms": cpu_ms, "skipped_platforms": sorted(gate.tripped), "errors_sample": error_samples}
