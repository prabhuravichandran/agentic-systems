"""Run row + trace row lifecycle for Jarvis.

`start_run` opens a row in `runs`, `end_run` closes it with the final
status + counters, and `record_stage` appends per-message events to `traces`.
The valid status values match the design doc's run-outcome mapping table;
the valid trace stages cover both the success path and the
B5 partial-failure cases (draft_create_failed, thread_too_large).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

VALID_STATUSES = ("success", "partial", "failure")

VALID_STAGES = (
    "excluded",
    "classified",
    "drafted",
    "draft_create_failed",
    "thread_too_large",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_run(conn: sqlite3.Connection) -> int:
    """Insert a row in `runs` with started_at=now; return its run_id."""
    cursor = conn.execute(
        "INSERT INTO runs (started_at) VALUES (?)", (_now(),)
    )
    conn.commit()
    run_id = cursor.lastrowid
    assert run_id is not None
    return run_id


def end_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    unread_scanned: int = 0,
    excluded: int = 0,
    classified_action: int = 0,
    drafts_created: int = 0,
    budget_cents_used: int = 0,
    error: str | None = None,
) -> None:
    """Close a run with final status + counters."""
    if status not in VALID_STATUSES:
        raise ValueError(
            f"invalid run status {status!r}; expected one of {VALID_STATUSES}"
        )
    conn.execute(
        "UPDATE runs SET ended_at = ?, status = ?, "
        "unread_scanned = ?, excluded = ?, classified_action = ?, "
        "drafts_created = ?, budget_cents_used = ?, error = ? "
        "WHERE run_id = ?",
        (
            _now(),
            status,
            unread_scanned,
            excluded,
            classified_action,
            drafts_created,
            budget_cents_used,
            error,
            run_id,
        ),
    )
    conn.commit()


def record_stage(
    conn: sqlite3.Connection,
    run_id: int,
    message_id: str,
    stage: str,
    detail: dict[str, Any],
) -> None:
    """Insert one trace row for a (run, message, stage) event."""
    if stage not in VALID_STAGES:
        raise ValueError(
            f"invalid trace stage {stage!r}; expected one of {VALID_STAGES}"
        )
    conn.execute(
        "INSERT INTO traces (run_id, message_id, stage, detail_json) "
        "VALUES (?, ?, ?, ?)",
        (run_id, message_id, stage, json.dumps(detail)),
    )
    conn.commit()
