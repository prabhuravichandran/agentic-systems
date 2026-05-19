"""Tests for jarvis.storage + jarvis.trace.

Covers schema init (idempotent, all four tables, FK enforcement), vacuum
(success + failure paths via mocked sqlite3.Error), label_cache TTL
semantics, and run/trace row lifecycle including the B5 partial-failure
trace stages (draft_create_failed, thread_too_large,
classifier_malformed_json, gmail_rate_limited).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from jarvis import storage, trace

pytestmark = pytest.mark.unit


def test_connect_creates_parent_dir(tmp_path):  # type: ignore[no-untyped-def]
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


@pytest.mark.contract
def test_traces_foreign_key_to_runs_is_enforced(conn: sqlite3.Connection) -> None:
    """Bug 4 / G4: FK enforcement on traces.run_id requires
    PRAGMA foreign_keys = ON, which `storage.connect` sets. If this
    regresses, integration tests later will silently accept orphan traces."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO traces (run_id, message_id, stage, detail_json) "
            "VALUES (?, ?, ?, ?)",
            (99999, "msg-orphan", "excluded", "{}"),
        )


# -- vacuum ------------------------------------------------------------------


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


@pytest.mark.regression
def test_vacuum_failure_logs_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """G3: assert log record properties (level + logger name), not text —
    text is not a contract. The contract is `vacuum never aborts the caller`
    and `the failure is observable via WARNING-level logs`."""
    bad_conn = MagicMock()
    bad_conn.execute.side_effect = sqlite3.OperationalError("database is locked")
    with caplog.at_level("WARNING", logger="jarvis.storage"):
        storage.vacuum(bad_conn, retention_days=7)
    assert any(
        rec.levelname == "WARNING" and rec.name == "jarvis.storage"
        for rec in caplog.records
    ), f"expected a WARNING from jarvis.storage; got {caplog.records!r}"


# -- label cache (G4: injectable `now` makes these deterministic) -----------


def test_label_cache_miss_returns_none(conn: sqlite3.Connection) -> None:
    assert storage.get_label_id(conn, "Jarvis/Urgent") is None


def test_label_cache_upsert_and_get(
    conn: sqlite3.Connection, now: datetime
) -> None:
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_42", now=now)
    assert storage.get_label_id(conn, "Jarvis/Urgent", now=now) == "Label_42"


def test_label_cache_upsert_overwrites_existing(
    conn: sqlite3.Connection, now: datetime
) -> None:
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_old", now=now)
    storage.upsert_label(conn, "Jarvis/Urgent", "Label_new", now=now)
    assert storage.get_label_id(conn, "Jarvis/Urgent", now=now) == "Label_new"


def test_label_cache_just_under_ttl_is_fresh(
    conn: sqlite3.Connection, now: datetime
) -> None:
    """Boundary: cached_at = now - (TTL - 1s) is still fresh."""
    cached = now - timedelta(days=storage.LABEL_CACHE_TTL_DAYS, seconds=-1)
    storage.upsert_label(conn, "Jarvis/Fresh", "Label_F", now=cached)
    assert storage.get_label_id(conn, "Jarvis/Fresh", now=now) == "Label_F"


def test_label_cache_just_over_ttl_is_stale(
    conn: sqlite3.Connection, now: datetime
) -> None:
    """Boundary: cached_at = now - (TTL + 1s) is stale → None."""
    cached = now - timedelta(days=storage.LABEL_CACHE_TTL_DAYS, seconds=1)
    storage.upsert_label(conn, "Jarvis/Stale", "Label_S", now=cached)
    assert storage.get_label_id(conn, "Jarvis/Stale", now=now) is None


# -- trace lifecycle ---------------------------------------------------------


def test_start_run_creates_row_with_started_at(
    conn: sqlite3.Connection, now: datetime
) -> None:
    run_id = trace.start_run(conn, now=now)
    row = conn.execute(
        "SELECT started_at, ended_at, status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row[0] == now.isoformat()
    assert row[1] is None
    assert row[2] is None


def test_end_run_updates_counters_and_status(
    conn: sqlite3.Connection, now: datetime
) -> None:
    run_id = trace.start_run(conn, now=now)
    end = now + timedelta(minutes=2)
    trace.end_run(
        conn,
        run_id,
        status="success",
        unread_scanned=65,
        excluded=50,
        classified_action=8,
        drafts_created=7,
        budget_cents_used=42,
        now=end,
    )
    row = conn.execute(
        "SELECT status, ended_at, unread_scanned, excluded, classified_action, "
        "drafts_created, budget_cents_used FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row[0] == "success"
    assert row[1] == end.isoformat()
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


@pytest.mark.regression
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


@pytest.mark.regression
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


@pytest.mark.regression
def test_record_stage_classifier_malformed_json(conn: sqlite3.Connection) -> None:
    """B5 partial: a classifier batch returned unparseable JSON. The batch
    is keyed by its first message_id (caller's choice) so the trace is
    grep-able."""
    run_id = trace.start_run(conn)
    trace.record_stage(
        conn,
        run_id,
        "batch-first-msg-id",
        "classifier_malformed_json",
        {"raw_response_excerpt": "<<<garbage>>>"},
    )
    row = conn.execute(
        "SELECT stage FROM traces WHERE message_id = ?", ("batch-first-msg-id",)
    ).fetchone()
    assert row[0] == "classifier_malformed_json"


@pytest.mark.regression
def test_record_stage_gmail_rate_limited(conn: sqlite3.Connection) -> None:
    """B5 partial: 429 from Gmail mid-run, drafts produced before the 429
    are persisted; this stage records the message we couldn't process."""
    run_id = trace.start_run(conn)
    trace.record_stage(
        conn,
        run_id,
        "msg-throttled",
        "gmail_rate_limited",
        {"retry_after_s": 60},
    )
    row = conn.execute(
        "SELECT stage FROM traces WHERE message_id = ?", ("msg-throttled",)
    ).fetchone()
    assert row[0] == "gmail_rate_limited"
