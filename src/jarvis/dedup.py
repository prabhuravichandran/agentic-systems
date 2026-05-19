"""Per-message dedup against the `seen_items` table.

Design v1.1 model: every Gmail message_id is processed at most once.
`is_seen(conn, message_id)` returns True for any prior status — callers
that already drafted, excluded, or classified-as-noise a message should
never re-process it. New messages in a thread surface as new IDs.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

VALID_STATUSES = ("drafted", "excluded", "classified_noise", "classified_fyi")


class DedupError(Exception):
    """Raised when callers misuse the per-message-once contract."""


def is_seen(conn: sqlite3.Connection, message_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_items WHERE message_id = ? LIMIT 1", (message_id,)
    ).fetchone()
    return row is not None


def get_status(conn: sqlite3.Connection, message_id: str) -> str | None:
    """Return the recorded status enum value, or None if never seen."""
    row = conn.execute(
        "SELECT status FROM seen_items WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row[0] if row else None


def mark_seen(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    thread_id: str,
    status: str,
    draft_id: str | None = None,
    urgency: str | None = None,
) -> None:
    """Record a (message_id, status) row.

    Raises DedupError if `status` isn't in the enum or if the message_id
    was already recorded — the per-message-once contract means callers
    should call `is_seen` first. `draft_id` and `urgency` are only
    meaningful when status='drafted'.
    """
    if status not in VALID_STATUSES:
        raise DedupError(
            f"invalid seen_items.status {status!r}; "
            f"expected one of {VALID_STATUSES}"
        )
    if status != "drafted" and (draft_id is not None or urgency is not None):
        raise DedupError(
            "draft_id and urgency are only valid when status='drafted'"
        )
    try:
        conn.execute(
            "INSERT INTO seen_items "
            "(message_id, thread_id, first_seen_at, status, draft_id, urgency) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                message_id,
                thread_id,
                datetime.now(timezone.utc).isoformat(),
                status,
                draft_id,
                urgency,
            ),
        )
    except sqlite3.IntegrityError as e:
        raise DedupError(
            f"message_id {message_id!r} already in seen_items; "
            f"callers must check is_seen() first"
        ) from e
    conn.commit()


def get_draft_id(conn: sqlite3.Connection, message_id: str) -> str | None:
    """Return the Gmail draft ID for a previously-drafted message, if any."""
    row = conn.execute(
        "SELECT draft_id FROM seen_items WHERE message_id = ? AND status = 'drafted'",
        (message_id,),
    ).fetchone()
    return row[0] if row else None
