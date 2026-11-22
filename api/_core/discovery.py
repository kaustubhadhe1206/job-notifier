"""Turn company names seen hiring engineers (on LinkedIn and the job feeds) into watched career pages.

LinkedIn often shows a job hours or days after it was published on the company's own career page.
So whenever a company shows up there, we look up its career page on the hiring platforms
(Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee) and watch it directly from then
on: its next opening reaches you minutes after it's published instead of after LinkedIn.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import db, detect, fetchers

LOOKUPS_PER_RUN = 2   # ~10 small requests per company; 2 per minute is ~2,900 companies a day
TIME_BUDGET_S = 30    # skip this step if the run has already taken this long


def _best_board(name: str) -> dict | None:
    try:
        hits = detect.discover(name, detect.BULK_PLATFORMS)
    except Exception:  # noqa: BLE001 - a failed lookup just means "not found this time"
        return None
    return hits[0] if hits else None


def lookup_queued_companies(conn, interval_minutes: int, started: datetime) -> int:
    if (datetime.now(timezone.utc) - started).total_seconds() > TIME_BUDGET_S:
        return 0
    companies = db.claim_companies(conn, LOOKUPS_PER_RUN)
    conn.commit()
    if not companies:
        return 0
    with ThreadPoolExecutor(max_workers=LOOKUPS_PER_RUN) as pool:
        hits = list(pool.map(lambda c: _best_board(c["name"]), companies))
    boards, results = [], {}
    for company, hit in zip(companies, hits):
        results[company["key"]] = "found" if hit else "none"
        if hit:
            boards.append({"name": company["name"], "kind": hit["kind"], "config": hit["config"], "url": hit["url"],
                           "board_key": fetchers.board_key(hit["kind"], hit["config"])})
    added = db.add_auto_sources(conn, boards, interval_minutes)
    db.finish_companies(conn, results)
    return added
