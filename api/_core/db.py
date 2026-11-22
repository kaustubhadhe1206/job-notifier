"""Postgres storage. Works with Supabase (recommended: free and always on), Neon or any Postgres.

Tables are created (and the default feeds seeded) automatically on first use.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import fetchers
from .presets import DEFAULT_SETTINGS, LINKEDIN_ENTRY_LEVELS, PRESET_SOURCES, PRESET_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,
    config           JSONB NOT NULL DEFAULT '{}'::jsonb,
    url              TEXT,
    origin           TEXT NOT NULL DEFAULT 'manual',   -- manual | preset | auto
    board_key        TEXT UNIQUE,                      -- e.g. 'greenhouse:stripe'; NULL for feeds
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    interval_minutes INT NOT NULL DEFAULT 1,
    initialized      BOOLEAN NOT NULL DEFAULT FALSE,
    etag             TEXT,
    ids_hash         TEXT,
    last_checked_at  TIMESTAMPTZ,
    last_success_at  TIMESTAMPTZ,
    last_error       TEXT,
    error_count      INT NOT NULL DEFAULT 0,
    last_job_count   INT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sources_due_idx ON sources (last_checked_at) WHERE enabled;
CREATE TABLE IF NOT EXISTS jobs (
    id            BIGSERIAL PRIMARY KEY,
    source_id     INT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    company       TEXT,
    location      TEXT,
    url           TEXT,
    posted_at     TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    baseline      BOOLEAN NOT NULL DEFAULT FALSE,
    matched       BOOLEAN NOT NULL DEFAULT FALSE,
    notified      BOOLEAN NOT NULL DEFAULT FALSE,
    notified_at   TIMESTAMPTZ,
    UNIQUE (source_id, external_id)
);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS visa TEXT;   -- stated | history | listed | unknown | blocked | off
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS note TEXT;   -- why it was (not) emailed
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS checks INT NOT NULL DEFAULT 0;   -- attempts to read the description
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS jobs_visa_pending_idx ON jobs (first_seen_at) WHERE visa = 'pending';
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sheet_synced BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS jobs_sheet_pending_idx ON jobs (first_seen_at) WHERE matched AND NOT sheet_synced;
CREATE INDEX IF NOT EXISTS jobs_first_seen_idx ON jobs (first_seen_at DESC);
CREATE INDEX IF NOT EXISTS jobs_pending_idx ON jobs (first_seen_at) WHERE matched AND NOT notified;
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          BIGSERIAL PRIMARY KEY,
    trigger     TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked     INT NOT NULL DEFAULT 0,
    new_jobs    INT NOT NULL DEFAULT 0,
    emailed     INT NOT NULL DEFAULT 0,
    discovered  INT NOT NULL DEFAULT 0,
    errors      INT NOT NULL DEFAULT 0,
    cpu_ms      INT NOT NULL DEFAULT 0,
    message     TEXT
);
"""

SOURCE_COLS = """s.id, s.name, s.kind, s.config, s.url, s.origin, s.board_key, s.enabled, s.interval_minutes,
    s.initialized, s.etag, s.ids_hash, s.last_checked_at, s.last_success_at, s.last_error, s.error_count,
    s.last_job_count, s.created_at"""

JOB_COLS = """j.id, j.title, COALESCE(NULLIF(j.company, ''), s.name) AS company, j.location, j.url,
    j.posted_at, j.first_seen_at, j.baseline, j.matched, j.notified, j.notified_at, j.visa, j.note,
    s.name AS source_name, s.kind AS source_kind, s.origin AS source_origin"""

AUTO_DISABLE_AFTER_ERRORS = 5

_schema_ready = False
_schema_lock = threading.Lock()


def database_url() -> str:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""


def _ensure_schema(conn: psycopg.Connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        try:
            conn.execute(SCHEMA)
            seeded = conn.execute(
                "INSERT INTO settings (key, value) VALUES ('_seeded', 'true') ON CONFLICT DO NOTHING RETURNING key"
            ).fetchone()
            _migrate_presets(conn, fresh=bool(seeded))
            conn.commit()
        except psycopg.errors.UniqueViolation:  # two cold starts raced on CREATE TABLE
            conn.rollback()
        _schema_ready = True


def _add_preset(conn, p: dict) -> None:
    conn.execute(
        "INSERT INTO sources (name, kind, config, url, origin, interval_minutes) VALUES (%s, %s, %s, %s, 'preset', %s)",
        (p["name"], p["kind"], Jsonb(p["config"]), fetchers.public_url(p["kind"], p["config"]), p["interval_minutes"]),
    )


def _migrate_presets(conn, fresh: bool) -> None:
    """Fresh database: add all default feeds. Existing database: apply preset changes made since
    it was created, once (tracked by the _presets_version setting)."""
    if fresh:
        for p in PRESET_SOURCES:
            _add_preset(conn, p)
        version = PRESET_VERSION
    else:
        row = conn.execute("SELECT value FROM settings WHERE key = '_presets_version'").fetchone()
        version = int(row["value"]) if row else 1
        if version >= PRESET_VERSION:
            return
        if version < 2:
            # v2: entry-level filter on the default LinkedIn searches
            conn.execute(
                "UPDATE sources SET config = config || %s "
                "WHERE origin = 'preset' AND kind = 'linkedin' AND NOT (config ? 'experience')",
                (Jsonb({"experience": LINKEDIN_ENTRY_LEVELS}),),
            )
        for p in PRESET_SOURCES:
            if p.get("since_version", 1) > version:
                _add_preset(conn, p)
        version = PRESET_VERSION
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('_presets_version', %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (Jsonb(version),),
    )


@contextmanager
def connect():
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add your Postgres connection string to the environment.")
    # prepare_threshold=None keeps us compatible with Supabase's transaction pooler (port 6543).
    conn = psycopg.connect(url, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except psycopg.Error:
            pass
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------- settings


def get_settings(conn) -> dict:
    s = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    for row in conn.execute("SELECT key, value FROM settings"):
        if row["key"] in s:
            s[row["key"]] = row["value"]
    return s


def save_settings(conn, values: dict) -> dict:
    for key, value in values.items():
        if key in DEFAULT_SETTINGS:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, Jsonb(value)),
            )
    return get_settings(conn)


# ---------------------------------------------------------------- sources


def list_sources(conn) -> list[dict]:
    return conn.execute(
        f"""SELECT {SOURCE_COLS},
               (SELECT count(*) FROM jobs j WHERE j.source_id = s.id AND j.matched) AS match_count
            FROM sources s
            ORDER BY CASE s.origin WHEN 'manual' THEN 0 WHEN 'preset' THEN 1 ELSE 2 END, s.created_at DESC"""
    ).fetchall()


def get_source(conn, source_id: int) -> dict | None:
    return conn.execute(f"SELECT {SOURCE_COLS} FROM sources s WHERE s.id = %s", (source_id,)).fetchone()


def create_source(conn, *, name, kind, config, interval_minutes, enabled=True) -> dict:
    """Add a source by hand. If the board was auto-discovered earlier, promote that row instead."""
    row = conn.execute(
        """INSERT INTO sources (name, kind, config, url, origin, board_key, interval_minutes, enabled)
           VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s)
           ON CONFLICT (board_key) DO UPDATE SET origin = 'manual', name = EXCLUDED.name,
               interval_minutes = EXCLUDED.interval_minutes, enabled = TRUE, error_count = 0
           RETURNING id""",
        (name, kind, Jsonb(config), fetchers.public_url(kind, config), fetchers.board_key(kind, config),
         interval_minutes, enabled),
    ).fetchone()
    return get_source(conn, row["id"])


def add_auto_sources(conn, boards: list[dict], interval_minutes: int) -> int:
    """Insert newly discovered company boards; ones already watched are ignored."""
    if not boards:
        return 0
    rows = conn.execute(
        """INSERT INTO sources (name, kind, config, url, origin, board_key, interval_minutes)
           SELECT r.name, r.kind, r.config, r.url, 'auto', r.board_key, %s
           FROM jsonb_to_recordset(%s) AS r(name text, kind text, config jsonb, url text, board_key text)
           ON CONFLICT (board_key) DO NOTHING
           RETURNING id""",
        (interval_minutes, Jsonb(boards)),
    ).fetchall()
    return len(rows)


def update_source(conn, source_id: int, fields: dict) -> dict | None:
    sets, params = [], []
    for key in ("name", "enabled", "interval_minutes", "url"):
        if fields.get(key) is not None:
            sets.append(f"{key} = %s")
            params.append(fields[key])
    if fields.get("enabled"):
        sets.append("error_count = 0")
    if fields.get("config") is not None:
        # A changed config is effectively a new feed: re-baseline instead of emailing every job.
        sets += ["config = %s", "initialized = FALSE", "etag = NULL", "ids_hash = NULL", "last_error = NULL"]
        params.append(Jsonb(fields["config"]))
    if sets:
        conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id = %s", (*params, source_id))
    return get_source(conn, source_id)


def delete_source(conn, source_id: int) -> bool:
    return conn.execute("DELETE FROM sources WHERE id = %s", (source_id,)).rowcount > 0


def claim_due_sources(conn, *, force=False, ids=None, limit=60) -> list[dict]:
    """Atomically pick due sources and stamp last_checked_at, so overlapping runs never fetch the
    same source twice. Your own companies and feeds go first; auto-discovered boards rotate."""
    return conn.execute(
        f"""UPDATE sources s SET last_checked_at = now()
            WHERE s.id IN (
                SELECT id FROM sources
                WHERE enabled
                  AND (%(ids)s::int[] IS NULL OR id = ANY(%(ids)s::int[]))
                  AND ((%(force)s AND origin <> 'auto') OR last_checked_at IS NULL
                       OR last_checked_at <= now() - make_interval(mins => interval_minutes) + interval '20 seconds')
                ORDER BY (origin = 'auto'), last_checked_at NULLS FIRST
                LIMIT %(limit)s
                FOR UPDATE SKIP LOCKED)
            RETURNING {SOURCE_COLS}""",
        {"ids": ids, "force": force, "limit": limit},
    ).fetchall()


def source_ok(conn, source_id: int, *, job_count=None, etag=None, ids_hash=None) -> None:
    conn.execute(
        """UPDATE sources SET initialized = TRUE, last_success_at = now(), last_error = NULL, error_count = 0,
               last_job_count = COALESCE(%s, last_job_count), etag = %s, ids_hash = COALESCE(%s, ids_hash)
           WHERE id = %s""",
        (job_count, etag, ids_hash, source_id),
    )


def source_error(conn, source_id: int, error: str) -> None:
    conn.execute(
        """UPDATE sources SET last_error = %s, error_count = error_count + 1,
               enabled = CASE WHEN origin = 'auto' AND error_count + 1 >= %s THEN FALSE ELSE enabled END
           WHERE id = %s""",
        (error[:500], AUTO_DISABLE_AFTER_ERRORS, source_id),
    )


# ---------------------------------------------------------------- jobs


def insert_jobs(conn, source_id: int, rows: list[dict]) -> list[dict]:
    """Insert jobs, skipping ones already known. Returns only the newly inserted rows."""
    if not rows:
        return []
    return conn.execute(
        """INSERT INTO jobs (source_id, external_id, title, company, location, url, posted_at, baseline, matched, note)
           SELECT %(sid)s, r.external_id, r.title, r.company, r.location, r.url, r.posted_at, r.baseline, r.matched, r.note
           FROM jsonb_to_recordset(%(rows)s) AS r(external_id text, title text, company text, location text,
                                                url text, posted_at timestamptz, baseline boolean, matched boolean,
                                                note text)
           ON CONFLICT (source_id, external_id) DO NOTHING
           RETURNING id, external_id, title, matched""",
        {"sid": source_id, "rows": Jsonb(rows)},
    ).fetchall()


def set_eligibility(conn, verdicts: list[dict]) -> None:
    """verdicts: [{id, matched, visa, note, checks}] — decision after reading descriptions."""
    if verdicts:
        conn.execute(
            """UPDATE jobs j SET matched = r.matched, visa = r.visa, note = r.note, checks = r.checks,
                   checked_at = now()
               FROM jsonb_to_recordset(%s) AS r(id bigint, matched boolean, visa text, note text, checks int)
               WHERE j.id = r.id""",
            (Jsonb(verdicts),),
        )


def pending_jobs(conn, limit: int) -> list[dict]:
    """Candidates whose description couldn't be read yet; retried a few minutes apart."""
    return conn.execute(
        """SELECT j.id, j.external_id, j.title, j.company, j.location, j.url, j.posted_at, j.checks,
                  s.id AS source_id, s.name AS source_name, s.kind, s.config
           FROM jobs j JOIN sources s ON s.id = j.source_id
           WHERE j.visa = 'pending' AND j.first_seen_at > now() - interval '1 day'
             AND (j.checked_at IS NULL OR j.checked_at < now() - interval '3 minutes')
           ORDER BY j.first_seen_at LIMIT %s""",
        (limit,),
    ).fetchall()


def claim_pending(conn, limit: int = 100) -> list[dict]:
    """Mark matched-but-unsent jobs as notified and return them. SKIP LOCKED makes this safe
    when two runs overlap: each job is claimed (and emailed) exactly once."""
    return conn.execute(
        f"""UPDATE jobs j SET notified = TRUE, notified_at = now()
            FROM sources s
            WHERE s.id = j.source_id AND j.id IN (
                SELECT id FROM jobs
                WHERE matched AND NOT notified AND first_seen_at > now() - interval '2 days'
                ORDER BY first_seen_at LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING {JOB_COLS}""",
        (limit,),
    ).fetchall()


def unclaim(conn, job_ids: list[int]) -> None:
    conn.execute("UPDATE jobs SET notified = FALSE, notified_at = NULL WHERE id = ANY(%s)", (job_ids,))


def claim_sheet_rows(conn, limit: int = 200) -> list[dict]:
    """Matched jobs not yet written to the Google Sheet (claimed exactly once, like emails)."""
    return conn.execute(
        f"""UPDATE jobs j SET sheet_synced = TRUE
            FROM sources s
            WHERE s.id = j.source_id AND j.id IN (
                SELECT id FROM jobs
                WHERE matched AND NOT sheet_synced AND first_seen_at > now() - interval '2 days'
                ORDER BY first_seen_at LIMIT %s FOR UPDATE SKIP LOCKED)
            RETURNING {JOB_COLS}""",
        (limit,),
    ).fetchall()


def unclaim_sheet(conn, job_ids: list[int]) -> None:
    conn.execute("UPDATE jobs SET sheet_synced = FALSE WHERE id = ANY(%s)", (job_ids,))


def get_meta(conn, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = %s", (f"_{key}",)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (f"_{key}", Jsonb(value)),
    )


def recent_jobs(conn, view: str = "matched", limit: int = 200) -> list[dict]:
    where = {
        "matched": "j.matched",
        "review": "NOT j.matched AND j.visa IN ('blocked', 'unknown', 'pending')",
        "new": "NOT j.baseline OR j.matched OR j.visa IS NOT NULL",
        "all": "TRUE",
    }.get(view, "j.matched")
    return conn.execute(
        f"""SELECT {JOB_COLS} FROM jobs j JOIN sources s ON s.id = j.source_id
            WHERE {where} ORDER BY j.first_seen_at DESC, j.id DESC LIMIT %s""",
        (limit,),
    ).fetchall()


def stats(conn) -> dict:
    row = conn.execute(
        """SELECT count(*) AS tracked_total,
                  count(*) FILTER (WHERE matched) AS matched_total,
                  count(*) FILTER (WHERE matched AND first_seen_at > now() - interval '24 hours') AS matched_24h,
                  count(*) FILTER (WHERE notified AND notified_at > now() - interval '24 hours') AS emailed_24h,
                  count(*) FILTER (WHERE visa = 'blocked' AND first_seen_at > now() - interval '24 hours') AS blocked_24h,
                  count(*) FILTER (WHERE visa = 'unknown' AND NOT matched
                                   AND first_seen_at > now() - interval '24 hours') AS unverified_24h
           FROM jobs"""
    ).fetchone()
    row.update(conn.execute(
        """SELECT count(*) FILTER (WHERE origin = 'auto') AS auto_sources,
                  count(*) FILTER (WHERE origin = 'auto' AND enabled) AS auto_active,
                  count(*) FILTER (WHERE origin = 'auto' AND created_at > now() - interval '24 hours') AS auto_24h
           FROM sources"""
    ).fetchone())
    row.update(conn.execute(
        """SELECT count(*) AS runs_24h, COALESCE(sum(cpu_ms), 0) AS cpu_ms_24h
           FROM runs WHERE started_at > now() - interval '24 hours'"""
    ).fetchone())
    return row


# ---------------------------------------------------------------- runs


def record_run(conn, *, trigger, started_at, checked, new_jobs, emailed, discovered, errors, cpu_ms, message=None):
    conn.execute(
        "INSERT INTO runs (trigger, started_at, checked, new_jobs, emailed, discovered, errors, cpu_ms, message) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (trigger, started_at, checked, new_jobs, emailed, discovered, errors, cpu_ms, message),
    )
    conn.execute("DELETE FROM runs WHERE started_at < now() - interval '3 days'")


def last_run(conn) -> dict | None:
    return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()


def last_cron_run(conn) -> dict | None:
    return conn.execute("SELECT * FROM runs WHERE trigger = 'cron' ORDER BY id DESC LIMIT 1").fetchone()
