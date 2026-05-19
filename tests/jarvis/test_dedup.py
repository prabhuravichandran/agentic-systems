"""Tests for jarvis.dedup — per-message-once contract + status enum."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from jarvis import dedup, storage


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = tmp_path / "dedup.sqlite"
    c = storage.connect(db)
    storage.init_schema(c)
    yield c
    c.close()


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
    dedup.mark_seen(
        conn, message_id="m2", thread_id="t2", status="excluded"
    )
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
    # On a repeat run, the guard skips the second insert
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
