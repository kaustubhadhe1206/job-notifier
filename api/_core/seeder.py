"""Grow the watch list with thousands of company career pages from Common Crawl.

Common Crawl (commoncrawl.org) is a free public index of billions of web pages. Asking it for every
page under boards.greenhouse.io, jobs.lever.co, jobs.ashbyhq.com, ... lists the career pages of
thousands of companies, most of which you've never heard of. We read one index page every
10 minutes (a few hundred to a few thousand companies each), so the whole list is loaded within
a few days, then refreshed monthly from the newest crawl.

The index is often overloaded (HTTP 504); a failed step is simply retried 20 minutes later.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from . import db, detect, fetchers

CC = "https://index.commoncrawl.org"
HOSTS = ["boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.ashbyhq.com", "jobs.lever.co",
         "apply.workable.com", "jobs.smartrecruiters.com"]
STEP_EVERY = timedelta(minutes=10)
RETRY_AFTER = timedelta(minutes=20)
RESEED_AFTER = timedelta(days=30)
TIME_BUDGET_S = 20      # only start a step if the run has taken less than this
TIMEOUT = httpx.Timeout(25.0, connect=10.0)


def _get(path: str, **params) -> httpx.Response:
    r = fetchers.client().get(f"{CC}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _latest_crawl() -> str:
    return _get("/collinfo.json").json()[0]["id"]


def _num_pages(crawl: str, host: str) -> int:
    r = fetchers.client().get(f"{CC}/{crawl}-index", params={"url": f"{host}/*", "output": "json", "showNumPages": "true"},
                              timeout=TIMEOUT)
    if r.status_code == 404:
        return 0  # nothing indexed for this host in this crawl
    r.raise_for_status()
    return int(r.json().get("pages") or 0)


def _boards_on_page(crawl: str, host: str, page: int) -> list[dict]:
    text = _get(f"/{crawl}-index", url=f"{host}/*", output="json", fl="url", page=page).text
    prefixes = set()
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        url = json.loads(line).get("url", "")
        parts = url.split("/")
        if len(parts) > 3 and parts[3]:
            prefixes.add(f"{parts[2]}/{parts[3].split('?')[0]}")
    boards = {}
    for prefix in prefixes:
        for kind, cfg in detect.find_boards(prefix):
            key = fetchers.board_key(kind, cfg)
            boards[key] = {"name": detect.name_for(kind, cfg), "kind": kind, "config": cfg,
                           "url": fetchers.public_url(kind, cfg), "board_key": key}
    return list(boards.values())


def step(conn, interval_minutes: int, started: datetime) -> int:
    """Read one Common Crawl index page, if it's time. Returns how many companies were added."""
    now = datetime.now(timezone.utc)
    if (now - started).total_seconds() > TIME_BUDGET_S:
        return 0
    st = db.get_meta(conn, "seed") or {}
    if st.get("next_at") and now < datetime.fromisoformat(st["next_at"]):
        return 0
    if st.get("done_at"):
        if now - datetime.fromisoformat(st["done_at"]) < RESEED_AFTER:
            return 0
        st = {"added": st.get("added", 0)}  # monthly refresh from the newest crawl

    added = 0
    try:
        if not st.get("crawl"):
            st.update({"crawl": _latest_crawl(), "host": 0, "page": 0, "pages": None})
        host = HOSTS[st["host"]]
        if st.get("pages") is None:
            st["pages"] = _num_pages(st["crawl"], host)
        if st["page"] < st["pages"]:
            added = db.add_auto_sources(conn, _boards_on_page(st["crawl"], host, st["page"]), interval_minutes)
            st["page"] += 1
        if st["page"] >= st["pages"]:
            st.update({"host": st["host"] + 1, "page": 0, "pages": None})
        if st["host"] >= len(HOSTS):
            st.update({"done_at": now.isoformat(), "crawl": None})
        st.update({"added": st.get("added", 0) + added, "next_at": (now + STEP_EVERY).isoformat(), "last_error": None})
    except Exception as e:  # noqa: BLE001 - the index is often overloaded; try again later
        st.update({"next_at": (now + RETRY_AFTER).isoformat(), "last_error": fetchers.describe_error(e)})
    db.set_meta(conn, "seed", st)
    return added
