"""Grow the watch list with thousands of company career pages from Common Crawl.

Common Crawl (commoncrawl.org) is a free, monthly crawl of billions of web pages. Every page it
saw under job-boards.greenhouse.io, jobs.ashbyhq.com, *.myworkdayjobs.com, ... is a company's
career page, so its index lists the hiring pages of thousands of companies (about 10,000 in the
2026-43 crawl), most of which you've never heard of.

The index *search server* (index.commoncrawl.org) is often down (HTTP 504), so this reads the
index *files* directly from Common Crawl's download site (data.commoncrawl.org), which is a CDN:
  1. cluster.idx is a sorted table of contents (one line per block of ~3,000 index entries).
     A binary search over it with small range requests finds the blocks for one platform.
  2. Each block is a separately gzipped piece of a large cdx file; one range request reads it.
One block is read per step (one step a minute), so a whole crawl takes about an hour, and the
list is refreshed from the newest crawl every month. Workable isn't read here: the Workable
search source already covers every company on Workable.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import httpx

from . import db, detect, fetchers

INDEX_SERVER = "https://index.commoncrawl.org"
DATA = "https://data.commoncrawl.org/cc-index/collections"
FALLBACK_CRAWL = "CC-MAIN-2026-43"  # used if the crawl list can't be fetched
# SURT keys (reversed host names) of the hiring platforms whose company pages we want.
PREFIXES = ["io,greenhouse,boards)/", "io,greenhouse,job-boards)/", "com,ashbyhq,jobs)/", "co,lever,jobs)/",
            "com,smartrecruiters,jobs)/", "com,myworkdayjobs,"]
STEP_EVERY = timedelta(minutes=1)
RETRY_AFTER = timedelta(minutes=20)
RESEED_AFTER = timedelta(days=30)
TIME_BUDGET_S = 20      # only start a step if the run has taken less than this
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
CHUNK = 4096


def _range(url: str, start: int, end: int) -> bytes:
    r = fetchers.client().get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content


def _latest_crawl(current: str | None) -> str:
    try:
        r = fetchers.client().get(f"{INDEX_SERVER}/collinfo.json", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()[0]["id"]
    except Exception:  # noqa: BLE001 - the crawl list lives on the flaky index server
        return current or FALLBACK_CRAWL


def _blocks(crawl: str, prefix: str) -> list[list]:
    """[cdx file, offset, length] of every index block that can contain URLs starting with prefix."""
    url = f"{DATA}/{crawl}/indexes/cluster.idx"
    size = int(fetchers.client().head(url, timeout=TIMEOUT).headers["content-length"])

    def first_key_after(pos: int) -> str:
        data = _range(url, pos, min(pos + CHUNK - 1, size - 1)).decode("utf-8", "replace")
        lines = data.split("\n")
        return lines[1 if pos else 0].split(" ", 1)[0]

    lo, hi = 0, size  # find a position just before the first line >= prefix
    while hi - lo > CHUNK:
        mid = (lo + hi) // 2
        if first_key_after(mid) < prefix:
            lo = mid
        else:
            hi = mid
    blocks, previous, pos, carry = [], None, lo, ""
    while pos < size:
        chunk = _range(url, pos, min(pos + 262143, size - 1)).decode("utf-8", "replace")
        lines = (carry + chunk).split("\n")
        carry = lines.pop()
        if pos == lo and lo:
            lines = lines[1:]  # partial first line
        for line in lines:
            key, _, rest = line.partition(" ")
            fields = rest.split("\t")
            if len(fields) < 4:
                continue
            block = [fields[1], int(fields[2]), int(fields[3])]
            if key < prefix:
                previous = block  # the block before the first match can end with matching URLs
            elif key.startswith(prefix):
                blocks.append(block)
            else:
                return ([previous] if previous else []) + blocks
        pos += 262144
    return ([previous] if previous else []) + blocks


def _boards_in_block(crawl: str, prefix: str, block: list) -> list[dict]:
    name, offset, length = block
    data = gzip.decompress(_range(f"{DATA}/{crawl}/indexes/{name}", offset, offset + length - 1))
    boards: dict[str, dict] = {}
    seen: set[str] = set()
    for line in data.decode("utf-8", "replace").splitlines():
        if not line.startswith(prefix):
            continue
        try:
            page = json.loads(line.split(" ", 2)[2]).get("url", "")
        except (IndexError, ValueError):
            continue
        parts = page.split("/")
        if len(parts) < 4:
            continue
        # one board per host + first path segment(s); skip repeats before running the regexes
        head = "/".join(parts[2:5] if "myworkday" in parts[2] else parts[2:4])
        if head in seen:
            continue
        seen.add(head)
        for kind, cfg in detect.find_boards(page):
            key = fetchers.board_key(kind, cfg)
            boards[key] = {"name": detect.name_for(kind, cfg), "kind": kind, "config": cfg,
                           "url": fetchers.public_url(kind, cfg), "board_key": key}
    return list(boards.values())


def step(conn, interval_minutes: int, started: datetime) -> int:
    """Read one index block, if it's time. Returns how many companies were added.

    State (meta 'seed'): crawl, host = index into PREFIXES, page = block number, pages = block count,
    blocks = the current platform's block list, added = total added, done_at, next_at, last_error."""
    now = datetime.now(timezone.utc)
    if (now - started).total_seconds() > TIME_BUDGET_S:
        return 0
    st = db.get_meta(conn, "seed") or {}
    if st.get("next_at") and now < datetime.fromisoformat(st["next_at"]):
        return 0
    if st.get("done_at"):
        if now - datetime.fromisoformat(st["done_at"]) < RESEED_AFTER:
            return 0
        st = {"added": st.get("added", 0), "last_crawl": st.get("last_crawl")}  # monthly refresh

    added = 0
    try:
        if not st.get("crawl"):
            st.update({"crawl": _latest_crawl(st.get("last_crawl")), "host": 0, "page": 0, "pages": None})
        prefix = PREFIXES[st["host"]]
        if st.get("pages") is None:
            st["blocks"] = _blocks(st["crawl"], prefix)
            st["pages"] = len(st["blocks"])
        if st["page"] < st["pages"]:
            boards = _boards_in_block(st["crawl"], prefix, st["blocks"][st["page"]])
            added = db.add_auto_sources(conn, boards, interval_minutes)
            st["page"] += 1
        if st["page"] >= st["pages"]:
            st.update({"host": st["host"] + 1, "page": 0, "pages": None, "blocks": None})
        if st["host"] >= len(PREFIXES):
            st.update({"done_at": now.isoformat(), "last_crawl": st["crawl"], "crawl": None})
        st.update({"added": st.get("added", 0) + added, "next_at": (now + STEP_EVERY).isoformat(), "last_error": None})
    except Exception as e:  # noqa: BLE001 - network hiccups: try again later
        st.update({"next_at": (now + RETRY_AFTER).isoformat(), "last_error": fetchers.describe_error(e)})
    db.set_meta(conn, "seed", st)
    return added
