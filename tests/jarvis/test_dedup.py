"""Tests for jarvis.dedup — per-message-once contract + status enum."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from jarvis import dedup

pytestmark = pytest.mark.unit


def test_unseen_message_returns_false(conn: sqlite3.Connection) -> None:
    assert dedup.is_seen(conn, "m-unknown") is False


def test_get_status_unseen_returns_none(conn: sqlite3.Connection) -> None:
    assert dedup.get_status(conn, "m-unknown") is None


def test_mark_seen_drafted_persists_draft_id_and_urgency(
    conn: sqlite3.Connection,
) -> None:
    dedup.mark_seen(
        conn,
        message_id="m1",
        thread_id="t1",
        status="drafted",
        draft_id="r-42",
        urgency="URGENT",
    )
    assert dedup.is_seen(conn, "m1") is True
    assert dedup.get_status(conn, "m1") == "drafted"
    assert dedup.get_draft_id(conn, "m1") == "r-42"


def test_mark_seen_excluded(conn: sqlite3.Connection) -> None:
    dedup.mark_seen(conn, message_id="m2", thread_id="t2", status="excluded")
    assert dedup.get_status(conn, "m2") == "excluded"
    assert dedup.get_draft_id(conn, "m2") is None


def test_mark_seen_classified_noise(conn: sqlite3.Connection) -> None:
    dedup.mark_seen(
        conn, message_id="m3", thread_id="t3", status="classified_noise"
    )
    assert dedup.get_status(conn, "m3") == "classified_noise"


def test_mark_seen_classified_fyi(conn: sqlite3.Connection) -> None:
    dedup.mark_seen(
        conn, message_id="m4", thread_id="t4", status="classified_fyi"
    )
    assert dedup.get_status(conn, "m4") == "classified_fyi"


def test_mark_seen_rejects_invalid_status(conn: sqlite3.Connection) -> None:
    with pytest.raises(dedup.DedupError) as exc_info:
        dedup.mark_seen(
            conn, message_id="m5", thread_id="t5", status="something_else"
        )
    assert "something_else" in str(exc_info.value)


def test_mark_seen_rejects_draft_id_when_not_drafted(
    conn: sqlite3.Connection,
) -> None:
    """draft_id and urgency only make sense when status='drafted'."""
    with pytest.raises(dedup.DedupError):
        dedup.mark_seen(
            conn,
            message_id="m6",
            thread_id="t6",
            status="excluded",
            draft_id="r-99",
        )


def test_mark_seen_rejects_urgency_when_not_drafted(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(dedup.DedupError):
        dedup.mark_seen(
            conn,
            message_id="m7",
            thread_id="t7",
            status="classified_fyi",
            urgency="LATER",
        )


# -- Bug 3 inverse-check regressions ----------------------------------------


@pytest.mark.regression
def test_mark_seen_drafted_requires_draft_id(conn: sqlite3.Connection) -> None:
    """Bug 3: status='drafted' must carry a draft_id; otherwise we'd
    silently insert NULL and `get_draft_id` would return None for a row
    that's supposedly drafted."""
    with pytest.raises(dedup.DedupError) as exc_info:
        dedup.mark_seen(
            conn,
            message_id="m8",
            thread_id="t8",
            status="drafted",
            urgency="URGENT",  # draft_id missing
        )
    assert "draft_id" in str(exc_info.value)


@pytest.mark.regression
def test_mark_seen_drafted_requires_urgency(conn: sqlite3.Connection) -> None:
    """Bug 3: status='drafted' must carry an urgency too."""
    with pytest.raises(dedup.DedupError) as exc_info:
        dedup.mark_seen(
            conn,
            message_id="m9",
            thread_id="t9",
            status="drafted",
            draft_id="r-1",  # urgency missing
        )
    assert "urgency" in str(exc_info.value)


@pytest.mark.regression
def test_mark_seen_drafted_with_neither_rejected(
    conn: sqlite3.Connection,
) -> None:
    with pytest.raises(dedup.DedupError):
        dedup.mark_seen(
            conn, message_id="m10", thread_id="t10", status="drafted"
        )


def test_mark_seen_twice_for_same_message_raises(
    conn: sqlite3.Connection,
) -> None:
    """Per-message-once contract — second mark_seen must fail loudly."""
    dedup.mark_seen(
        conn, message_id="m-dup", thread_id="t1", status="excluded"
    )
    with pytest.raises(dedup.DedupError) as exc_info:
        dedup.mark_seen(
            conn,
            message_id="m-dup",
            thread_id="t1",
            status="drafted",
            draft_id="r-1",
            urgency="TODAY",
        )
    assert "m-dup" in str(exc_info.value)


def test_orchestrator_pattern_is_seen_then_mark(
    conn: sqlite3.Connection,
) -> None:
    """The canonical caller pattern — guard mark_seen with is_seen."""
    if not dedup.is_seen(conn, "m-new"):
        dedup.mark_seen(
            conn, message_id="m-new", thread_id="t-new", status="excluded"
        )
    if not dedup.is_seen(conn, "m-new"):  # False — already seen
        dedup.mark_seen(
            conn, message_id="m-new", thread_id="t-new", status="excluded"
        )
    rows = conn.execute(
        "SELECT COUNT(*) FROM seen_items WHERE message_id = ?", ("m-new",)
    ).fetchone()
    assert rows[0] == 1


def test_get_draft_id_returns_none_for_non_drafted(
    conn: sqlite3.Connection,
) -> None:
    dedup.mark_seen(
        conn, message_id="m-fyi", thread_id="t1", status="classified_fyi"
    )
    assert dedup.get_draft_id(conn, "m-fyi") is None


def test_different_messages_dont_collide(conn: sqlite3.Connection) -> None:
    dedup.mark_seen(
        conn, message_id="m-a", thread_id="t-shared", status="drafted",
        draft_id="r-a", urgency="URGENT",
    )
    dedup.mark_seen(
        conn, message_id="m-b", thread_id="t-shared", status="drafted",
        draft_id="r-b", urgency="TODAY",
    )
    assert dedup.get_status(conn, "m-a") == "drafted"
    assert dedup.get_status(conn, "m-b") == "drafted"
    assert dedup.get_draft_id(conn, "m-a") == "r-a"
    assert dedup.get_draft_id(conn, "m-b") == "r-b"


def test_mark_seen_persists_first_seen_at_from_now(
    conn: sqlite3.Connection, now: datetime
) -> None:
    """G4: injected `now` is what gets persisted, deterministically."""
    dedup.mark_seen(
        conn,
        message_id="m-timed",
        thread_id="t-timed",
        status="excluded",
        now=now,
    )
    row = conn.execute(
        "SELECT first_seen_at FROM seen_items WHERE message_id = ?", ("m-timed",)
    ).fetchone()
    assert row[0] == now.isoformat()
