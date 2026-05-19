"""Tests for jarvis.storage + jarvis.trace.

Covers schema init (idempotent, all four tables), vacuum (success + failure
paths), label_cache TTL semantics, and run/trace row lifecycle including
the B5 partial-failure trace stages.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from jarvis import storage, trace


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db = tmp_path / "test.sqlite"
    c = storage.connect(db)
    storage.init_schema(c)
    yield c
    c.close()


def test_connect_creates_parent_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "jarvis.sqlite"
    c = storage.connect(nested)
    try:
        assert nested.parent.is_dir()
    finally:
        c.close()


def test_init_schema_creates_all_tables(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"seen_items", "runs", "traces", "label_cache"} <= names


def test_init_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    storage.init_schema(conn)
    storage.init_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"seen_items", "runs", "traces", "label_cache"} <= names


def test_seen_items_message_id_is_primary_key(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO seen_items (message_id, thread_id, first_seen_at, status) "
        "VALUES (?, ?, ?, ?)",
        ("m1", "t1", "2026-01-01T00:00:00Z", "drafted"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO seen_items (message_id, thread_id, first_seen_at, status) "
            "VALUES (?, ?, ?, ?)",
            ("m1", "t2", "2026-01-02T00:00:00Z", "excluded"),
        )


def test_vacuum_deletes_old_traces(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    conn.execute(
        "INSERT INTO traces (run_id, message_id, stage, detail_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, "msg-old", "excluded", "{}", "2020-01-01T00:00:00"),
    )
    trace.record_stage(conn, run_id, "msg-new", "excluded", {"reason": "noreply"})
    conn.commit()

    storage.vacuum(conn, retention_days=7)

    msgs = {r[0] for r in conn.execute("SELECT message_id FROM traces").fetchall()}
    assert "msg-new" in msgs
    assert "msg-old" not in msgs


def test_vacuum_deletes_old_runs(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, ?)",
        ("2020-01-01T00:00:00", "success"),
    )
    new_run_id = trace.start_run(conn)
    conn.commit()

    storage.vacuum(conn, retention_days=7)

    ids = {r[0] for r in conn.execute("SELECT run_id FROM runs").fetchall()}
    assert new_run_id in ids
    assert len(ids) == 1


def test_vacuum_keeps_recent_rows(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    trace.record_stage(conn, run_id, "msg-fresh", "excluded", {})
    storage.vacuum(conn, retention_days=7)
    rows = conn.execute(
        "SELECT message_id FROM traces WHERE message_id = ?", ("msg-fresh",)
    ).fetchall()
    assert len(rows) == 1


def test_vacuum_failure_logs_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_conn = MagicMock()
    bad_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
    with caplog.at_level("WARNING"):
        storage.vacuum(bad_conn, retention_days=7)
    assert "vacuum failed" in caplog.text
    # Did NOT raise — that's the whole point


def test_label_cache_miss_returns_none(conn: sqlite3.Connection) -> None:
    assert storage.get_label_id(conn, "Jarvis/Urgent") is None


def test_label_cache_upsert_and_get(conn: sqlite3.Connection) -> None:
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_42")
    assert storage.get_label_id(conn, "Jarvis/Urgent") == "Label_42"


def test_label_cache_upsert_overwrites_existing(conn: sqlite3.Connection) -> None:
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_old")
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_new")
    assert storage.get_label_id(conn, "Jarvis/Urgent") == "Label_new"


def test_label_cache_stale_entry_returns_none(conn: sqlite3.Connection) -> None:
    old = (
        datetime.now(timezone.utc)
        - timedelta(days=storage.LABEL_CACHE_TTL_DAYS + 1)
    ).isoformat()
    conn.execute(
        "INSERT INTO label_cache (label_name, label_id, cached_at) VALUES (?, ?, ?)",
        ("Jarvis/Stale", "Label_X", old),
    )
    conn.commit()
    assert storage.get_label_id(conn, "Jarvis/Stale") is None


def test_label_cache_recent_entry_is_fresh(conn: sqlite3.Connection) -> None:
    recent = (
        datetime.now(timezone.utc)
        - timedelta(days=storage.LABEL_CACHE_TTL_DAYS - 1)
    ).isoformat()
    conn.execute(
        "INSERT INTO label_cache (label_name, label_id, cached_at) VALUES (?, ?, ?)",
        ("Jarvis/Fresh", "Label_Y", recent),
    )
    conn.commit()
    assert storage.get_label_id(conn, "Jarvis/Fresh") == "Label_Y"


# -- trace lifecycle ---------------------------------------------------------


def test_start_run_creates_row_with_started_at(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    row = conn.execute(
        "SELECT started_at, ended_at, status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row[0] is not None
    assert row[1] is None
    assert row[2] is None


def test_end_run_updates_counters_and_status(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    trace.end_run(
        conn,
        run_id,
        status="success",
        unread_scanned=65,
        excluded=50,
        classified_action=8,
        drafts_created=7,
        budget_cents_used=42,
    )
    row = conn.execute(
        "SELECT status, ended_at, unread_scanned, excluded, classified_action, "
        "drafts_created, budget_cents_used FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row[0] == "success"
    assert row[1] is not None
    assert row[2] == 65
    assert row[3] == 50
    assert row[4] == 8
    assert row[5] == 7
    assert row[6] == 42


def test_end_run_with_partial_and_error(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    trace.end_run(
        conn, run_id, status="partial", drafts_created=4, error="budget exceeded"
    )
    row = conn.execute(
        "SELECT status, error FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row[0] == "partial"
    assert row[1] == "budget exceeded"


def test_end_run_rejects_invalid_status(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    with pytest.raises(ValueError) as exc_info:
        trace.end_run(conn, run_id, status="ok")
    assert "ok" in str(exc_info.value)


def test_record_stage_inserts_trace(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    trace.record_stage(conn, run_id, "msg-1", "excluded", {"reason": "mailing_list"})
    row = conn.execute(
        "SELECT run_id, message_id, stage, detail_json FROM traces "
        "WHERE message_id = ?",
        ("msg-1",),
    ).fetchone()
    assert row[0] == run_id
    assert row[1] == "msg-1"
    assert row[2] == "excluded"
    assert json.loads(row[3]) == {"reason": "mailing_list"}


def test_record_stage_rejects_invalid_stage(conn: sqlite3.Connection) -> None:
    run_id = trace.start_run(conn)
    with pytest.raises(ValueError) as exc_info:
        trace.record_stage(conn, run_id, "msg-1", "bogus_stage", {})
    assert "bogus_stage" in str(exc_info.value)


def test_record_stage_draft_create_failed(conn: sqlite3.Connection) -> None:
    """B5 partial-failure path: per-item draft_create_failed traces."""
    run_id = trace.start_run(conn)
    trace.record_stage(
        conn, run_id, "msg-bad", "draft_create_failed",
        {"error": "Gmail API 500"},
    )
    row = conn.execute(
        "SELECT stage, detail_json FROM traces WHERE message_id = ?", ("msg-bad",)
    ).fetchone()
    assert row[0] == "draft_create_failed"
    assert json.loads(row[1]) == {"error": "Gmail API 500"}


def test_record_stage_thread_too_large(conn: sqlite3.Connection) -> None:
    """B5 case: latest message alone exceeds the input-token cap, skip entirely."""
    run_id = trace.start_run(conn)
    trace.record_stage(
        conn, run_id, "msg-big", "thread_too_large", {"input_tokens_est": 25000}
    )
    row = conn.execute(
        "SELECT stage FROM traces WHERE message_id = ?", ("msg-big",)
    ).fetchone()
    assert row[0] == "thread_too_large"
