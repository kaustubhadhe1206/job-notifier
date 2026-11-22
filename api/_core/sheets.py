"""Append emailed jobs to your Google Sheet through a small Apps Script web app.

No Google Cloud project or API keys are needed: the script in google-sheets/Code.gs runs inside
your spreadsheet and only accepts requests that carry SHEETS_SECRET.
"""
from __future__ import annotations

import os
from datetime import datetime

import httpx

from . import fetchers

SPONSORSHIP_TEXT = {
    "stated": "Sponsors visas (in posting)",
    "history": "Known H-1B sponsor",
    "listed": "Sponsor (your list)",
    "unknown": "Not mentioned",
}


def configured() -> bool:
    return bool(os.environ.get("SHEETS_WEBHOOK_URL") and os.environ.get("SHEETS_SECRET"))


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _call(payload: dict) -> dict:
    url = os.environ.get("SHEETS_WEBHOOK_URL", "").strip()
    secret = os.environ.get("SHEETS_SECRET", "").strip()
    if not (url and secret):
        raise RuntimeError("SHEETS_WEBHOOK_URL and SHEETS_SECRET are not set")
    # Apps Script answers with a redirect to googleusercontent.com; the shared client follows it.
    r = fetchers.client().post(url, json={**payload, "secret": secret}, timeout=httpx.Timeout(45.0, connect=10.0))
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"Google didn't return JSON (HTTP {r.status_code}). Check the Apps Script deployment: "
            "type Web app, Execute as 'Me', Who has access 'Anyone', and the URL ends in /exec."
        ) from None
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "Apps Script reported an error")
    return data


def ping() -> dict:
    """Check the connection without adding rows. Returns {ok, url, sheet, rows}."""
    return _call({"action": "ping"})


def append_jobs(jobs: list[dict]) -> dict:
    """Add jobs to the sheet (newest first). The script skips jobs already in the sheet."""
    rows = [{
        "id": j["id"],
        "found_at": _iso(j.get("first_seen_at")),
        "title": j["title"],
        "company": j.get("company") or "",
        "location": j.get("location") or "",
        "url": j.get("url") or "",
        "sponsorship": SPONSORSHIP_TEXT.get(j.get("visa") or "", ""),
        "details": j.get("note") or "",
        "source": j.get("source_name") or "",
        "posted_at": _iso(j.get("posted_at")),
    } for j in jobs]
    return _call({"action": "append", "jobs": rows})
