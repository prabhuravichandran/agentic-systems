"""SQLite storage layer for Jarvis.

Owns connection management, idempotent schema bootstrap, lazy vacuum, and the
label-id cache. Per-message dedup (seen_items table ops) lives in dedup.py;
run/trace lifecycle lives in trace.py.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

LABEL_CACHE_TTL_DAYS = 30

# deviation: design doc shows `traces` without an explicit `created_at` column,
# but the vacuum SQL filters on `created_at`. Adding it as
# `DEFAULT CURRENT_TIMESTAMP` so callers don't have to pass it; this is a doc
# completion rather than a real departure from the design intent.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS seen_items (
    message_id     TEXT PRIMARY KEY,
    thread_id      TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL,
    status         TEXT NOT NULL,
    draft_id       TEXT,
    urgency        TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    status              TEXT,
    unread_scanned      INTEGER DEFAULT 0,
    excluded            INTEGER DEFAULT 0,
    classified_action   INTEGER DEFAULT 0,
    drafts_created      INTEGER DEFAULT 0,
    budget_cents_used   INTEGER DEFAULT 0,
    error               TEXT
);

CREATE TABLE IF NOT EXISTS traces (
    run_id      INTEGER NOT NULL,
    message_id  TEXT NOT NULL,
    stage       TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_traces_run ON traces(run_id);

CREATE TABLE IF NOT EXISTS label_cache (
    label_name  TEXT PRIMARY KEY,
    label_id    TEXT NOT NULL,
    cached_at   TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection, ensuring the parent dir exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables idempotently. Safe to call on every run."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def vacuum(conn: sqlite3.Connection, retention_days: int) -> None:
    """Delete traces and runs rows older than retention_days.

    Wrapped in try/except — vacuum failure logs a warning and returns;
    it never aborts the caller. Must be called at the top of
    run_morning_brief BEFORE start_run so a vacuum failure can't leave a
    half-written run row.
    """
    try:
        conn.execute(
            "DELETE FROM traces WHERE created_at < "
            "datetime('now', '-' || ? || ' days')",
            (retention_days,),
        )
        conn.execute(
            "DELETE FROM runs WHERE started_at < "
            "datetime('now', '-' || ? || ' days')",
            (retention_days,),
        )
        conn.commit()
    except sqlite3.Error as e:
        log.warning("vacuum failed, continuing: %s", e)


def get_label_id(conn: sqlite3.Connection, label_name: str) -> str | None:
    """Return cached Gmail label_id or None if missing/stale.

    Entries older than LABEL_CACHE_TTL_DAYS are treated as missing so that
    if a user deletes and recreates a Jarvis label in Gmail, we'll refresh
    rather than get stuck on a stale 404-returning ID.
    """
    row = conn.execute(
        "SELECT label_id, cached_at FROM label_cache WHERE label_name = ?",
        (label_name,),
    ).fetchone()
    if row is None:
        return None
    cached_at = datetime.fromisoformat(row[1])
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - cached_at > timedelta(days=LABEL_CACHE_TTL_DAYS):
        return None
    return row[0]


def upsert_label(conn: sqlite3.Connection, label_name: str, label_id: str) -> None:
    """Insert or refresh a (label_name → label_id) mapping with current timestamp."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO label_cache (label_name, label_id, cached_at) VALUES (?, ?, ?) "
        "ON CONFLICT(label_name) DO UPDATE SET label_id = excluded.label_id, "
        "cached_at = excluded.cached_at",
        (label_name, label_id, now),
    )
    conn.commit()
