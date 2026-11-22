"""Hidden Jobs API - one FastAPI app served by Vercel's Python runtime at /api/*."""
from __future__ import annotations

import hmac
import os
import sys
from typing import Literal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # local development only; on Vercel env vars come from the project settings
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import Depends, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from _core import checker, db, detect, eligibility, fetchers, notifier, sheets  # noqa: E402
from _core.presets import DEFAULT_SETTINGS  # noqa: E402

app = FastAPI(title="Hidden Jobs", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": f"{exc.__class__.__name__}: {exc}"[:500]})


# ---------------------------------------------------------------- auth


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _same(given: str, secret: str) -> bool:
    return bool(secret) and bool(given) and hmac.compare_digest(given.encode(), secret.encode())


def require_admin(request: Request) -> None:
    if not _same(_bearer(request), os.environ.get("ADMIN_PASSWORD", "")):
        raise HTTPException(401, "Wrong password")


def require_cron(request: Request) -> None:
    given = _bearer(request) or request.query_params.get("key", "")
    if not _same(given, os.environ.get("CRON_SECRET", "")):
        raise HTTPException(401, "Bad or missing cron key")


Admin = [Depends(require_admin)]


# ---------------------------------------------------------------- models


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    config: dict
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool = True


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    config: dict | None = None


class DetectIn(BaseModel):
    url: str = Field(min_length=3, max_length=2000)


class SettingsIn(BaseModel):
    notify_email: str = Field(default="", max_length=320)
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    exclude_companies: list[str] = []
    location_keywords: list[str] = []
    seniority_exclude: list[str] = Field(default_factory=lambda: list(DEFAULT_SETTINGS["seniority_exclude"]))
    max_years_experience: int = Field(default=2, ge=0, le=30)
    us_only: bool = True
    max_age_minutes: int = Field(default=60, ge=5, le=1440)
    skip_ineligible: bool = True
    visa_mode: Literal["require", "allow_unknown", "off"] = "allow_unknown"
    sponsor_companies: list[str] = []
    auto_discover: bool = True
    auto_interval_minutes: int = Field(default=15, ge=5, le=1440)
    auto_idle_interval_minutes: int = Field(default=360, ge=30, le=10080)
    paused: bool = False


WORD_LIST_SETTINGS = ("include_keywords", "exclude_keywords", "exclude_companies", "location_keywords",
                      "sponsor_companies", "seniority_exclude")


def _clean_words(words: list[str]) -> list[str]:
    return list(dict.fromkeys(w.strip() for w in words if w and w.strip()))[:100]


def _setup_status(conn=None) -> dict:
    status = {
        "database": bool(db.database_url()),
        "smtp": notifier.smtp_configured(),
        "cron_secret": bool(os.environ.get("CRON_SECRET")),
        "admin_password": bool(os.environ.get("ADMIN_PASSWORD")),
        "sheets": sheets.configured(),
    }
    if conn is not None:
        status["notify_to"] = notifier.recipient(db.get_settings(conn))
        status["last_cron_run"] = db.last_cron_run(conn)
        status["sheet_url"] = db.get_meta(conn, "sheet_url")
    return status


# ---------------------------------------------------------------- public


@app.get("/api/health")
def health():
    return {"ok": True, "setup": _setup_status()}


@app.api_route("/api/cron", methods=["GET", "POST"], dependencies=[Depends(require_cron)])
def cron():
    """Called every minute by cron-job.org (and once a day by Vercel Cron as a backup)."""
    return checker.run_check("cron")


# ---------------------------------------------------------------- dashboard


@app.get("/api/me", dependencies=Admin)
def me():
    return {"ok": True}


@app.get("/api/overview", dependencies=Admin)
def overview():
    with db.connect() as conn:
        return {
            "sources": db.list_sources(conn),
            "settings": db.get_settings(conn),
            "stats": db.stats(conn),
            "last_run": db.last_run(conn),
            "setup": _setup_status(conn),
            "seed": db.get_meta(conn, "seed"),
        }


@app.get("/api/sources/auto", dependencies=Admin)
def auto_sources(q: str = "", limit: int = 50, offset: int = 0):
    """Auto-discovered companies, most relevant first (there can be thousands)."""
    with db.connect() as conn:
        return db.list_auto_sources(conn, q.strip(), max(1, min(limit, 200)), max(0, offset))


@app.get("/api/jobs", dependencies=Admin)
def jobs(view: str = "matched", limit: int = 200):
    with db.connect() as conn:
        return db.recent_jobs(conn, view, max(1, min(limit, 500)))


@app.post("/api/detect", dependencies=Admin)
def detect_source(body: DetectIn):
    try:
        result = detect.detect(body.url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    result["h1b"] = eligibility.lookup_sponsor(result["name"])
    return result


@app.get("/api/discover", dependencies=Admin)
def discover(q: str):
    if len(q.strip()) < 2:
        raise HTTPException(422, "Type at least 2 characters")
    hits = detect.discover(q)
    record = eligibility.lookup_sponsor(q)
    for h in hits:
        h["h1b"] = record
    return hits


@app.get("/api/sources", dependencies=Admin)
def list_sources():
    with db.connect() as conn:
        return db.list_sources(conn)


@app.post("/api/sources", dependencies=Admin)
def create_source(body: SourceIn):
    try:
        fetchers.validate(body.kind, body.config)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    interval = body.interval_minutes or (10 if body.kind == "linkedin" else 1)
    with db.connect() as conn:
        source = db.create_source(
            conn,
            name=body.name.strip(),
            kind=body.kind,
            config=body.config,
            interval_minutes=interval,
            enabled=body.enabled,
        )
    # Take the baseline right away so the very next new posting triggers an email.
    checker.run_check("baseline", force=True, source_ids=[source["id"]])
    with db.connect() as conn:
        return db.get_source(conn, source["id"])


@app.patch("/api/sources/{source_id}", dependencies=Admin)
def update_source(source_id: int, body: SourcePatch):
    fields = body.model_dump(exclude_none=True)
    with db.connect() as conn:
        current = db.get_source(conn, source_id)
        if not current:
            raise HTTPException(404, "Source not found")
        if "config" in fields:
            try:
                fetchers.validate(current["kind"], fields["config"])
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            fields["url"] = fetchers.public_url(current["kind"], fields["config"])
        return db.update_source(conn, source_id, fields)


@app.delete("/api/sources/{source_id}", dependencies=Admin)
def delete_source(source_id: int):
    with db.connect() as conn:
        if not db.delete_source(conn, source_id):
            raise HTTPException(404, "Source not found")
    return {"ok": True}


@app.post("/api/sources/{source_id}/test", dependencies=Admin)
def test_source(source_id: int):
    with db.connect() as conn:
        source = db.get_source(conn, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    try:
        jobs = fetchers.fetch(source["kind"], fetchers.client(), source["config"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, fetchers.describe_error(e)) from e
    with db.connect() as conn:
        settings = db.get_settings(conn)
    f = checker.Filters(settings)
    # Show matching jobs first, with the full eligibility check (descriptions read) for those.
    passing = [j for j in jobs if f.match(j)]
    sample = (passing + [j for j in jobs if not f.match(j)])[:15]
    checked = passing[:8]
    verdicts = dict(zip((j.external_id for j in checked),
                        checker.evaluate_jobs([(source, j) for j in checked], settings)))
    out = []
    for j in sample:
        v = verdicts.get(j.external_id)
        out.append({"title": j.title, "location": j.location, "url": j.url, "posted_at": j.posted_at,
                    "matches": bool(v and v.ok), "filter": f.reason(j),
                    "visa": v.visa if v else None, "note": v.note if v else None})
    return {"job_count": len(jobs), "match_count": len(passing), "checked": len(checked), "sample": out}


@app.get("/api/h1b", dependencies=Admin)
def h1b_lookup(company: str):
    """H-1B approval history for a company name (USCIS data)."""
    return {"company": company, "record": eligibility.lookup_sponsor(company), "years": eligibility.sponsor_years()}


@app.get("/api/settings", dependencies=Admin)
def get_settings():
    with db.connect() as conn:
        return db.get_settings(conn)


@app.put("/api/settings", dependencies=Admin)
def save_settings(body: SettingsIn):
    values = body.model_dump()
    values["notify_email"] = values["notify_email"].strip()
    for key in WORD_LIST_SETTINGS:
        values[key] = _clean_words(values[key])
    with db.connect() as conn:
        # Apply a changed interval to companies that were already discovered, not just new ones.
        conn.execute(
            """UPDATE sources SET interval_minutes = CASE WHEN relevant_count = 0 THEN %(idle)s ELSE %(active)s END
               WHERE origin = 'auto'""",
            {"active": values["auto_interval_minutes"], "idle": values["auto_idle_interval_minutes"]},
        )
        return db.save_settings(conn, values)


@app.post("/api/test-email", dependencies=Admin)
def test_email():
    with db.connect() as conn:
        to = notifier.recipient(db.get_settings(conn))
    sample = [{
        "title": "Senior Software Engineer (test email)",
        "company": "Hidden Jobs",
        "location": "Remote",
        "url": os.environ.get("APP_URL") or "https://vercel.com",
        "posted_at": None,
        "visa": "stated",
        "note": "Posting mentions sponsorship: “Visa sponsorship is available for this role.”",
    }]
    try:
        notifier.send_jobs_email(to, sample, os.environ.get("APP_URL", ""))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not send email: {e}") from e
    return {"ok": True, "to": to}


@app.post("/api/test-sheet", dependencies=Admin)
def test_sheet():
    try:
        res = sheets.ping()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not reach your Google Sheet: {e}") from e
    if res.get("url"):
        with db.connect() as conn:
            db.set_meta(conn, "sheet_url", res["url"])
    return res


@app.post("/api/run", dependencies=Admin)
def run_now():
    return checker.run_check("manual", force=True)
