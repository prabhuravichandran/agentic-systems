"""Shared fixtures for the jarvis test suite.

Three fixture families:
- `conn`         — fresh in-memory-ish SQLite with the full schema initialised
- `now`          — fixed UTC clock for time-dependent tests
- `make_config`, `make_message`, `make_thread` — factory-returning fixtures
  so tests can build only what they need with concise overrides
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from jarvis import storage
from jarvis.config import (
    AccountConfig,
    Config,
    ExclusionsConfig,
    LimitsConfig,
    ModelsConfig,
    NotificationsConfig,
    ScheduleConfig,
    StorageConfig,
)
from jarvis.types import MessageMeta, Thread, ThreadMessage

USER_EMAIL = "alice@example.com"


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Fresh on-disk SQLite under tmp_path with full schema."""
    db = tmp_path / "jarvis-test.sqlite"
    c = storage.connect(db)
    storage.init_schema(c)
    yield c
    c.close()


@pytest.fixture
def now() -> datetime:
    """Fixed UTC clock for deterministic time-dependent tests."""
    return datetime(2026, 5, 19, 8, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_config() -> Callable[..., Config]:
    """Factory: build a default Config; pass section dicts as kwargs to override."""

    def _factory(**overrides: Any) -> Config:
        email = overrides.pop("email", USER_EMAIL)
        excl = dict(
            skip_mailing_lists=True,
            skip_noreply=True,
            skip_sender_domains=(),
            skip_gmail_categories=("PROMOTIONS", "SOCIAL", "SPAM"),
            skip_labels=(),
        )
        excl.update(overrides.pop("exclusions", {}))
        limits = dict(
            max_drafts_per_run=8,
            budget_usd_per_run=1.0,
            max_thread_tokens=20000,
            max_email_bytes=1_048_576,
        )
        limits.update(overrides.pop("limits", {}))
        notifications = dict(notify_on="if_drafts")
        notifications.update(overrides.pop("notifications", {}))
        return Config(
            account=AccountConfig(email=email),
            schedule=ScheduleConfig(hour=8, weekdays_only=True),
            limits=LimitsConfig(**limits),
            exclusions=ExclusionsConfig(**excl),
            models=ModelsConfig(
                classifier="claude-haiku-4-5-20251001",
                drafter="claude-sonnet-4-6",
            ),
            notifications=NotificationsConfig(**notifications),
            storage=StorageConfig(
                db_path="/tmp/jarvis.sqlite",
                retention_days=7,
                trace_body_chars=500,
            ),
        )

    return _factory


@pytest.fixture
def make_message() -> Callable[..., MessageMeta]:
    """Factory: build a default MessageMeta; override any field via kwargs."""

    def _factory(**overrides: Any) -> MessageMeta:
        base = MessageMeta(
            id="m1",
            thread_id="t1",
            from_addr="bob@friend.example",
            to_addrs=(USER_EMAIL,),
            cc_addrs=(),
            subject="Hello",
            date="2026-05-19T08:00:00Z",
            headers={},
            snippet="just saying hi",
            gmail_labels=("INBOX", "UNREAD"),
            size_estimate=4096,
        )
        return replace(base, **overrides)

    return _factory


@pytest.fixture
def make_thread() -> Callable[..., Thread]:
    """Factory: build a simple Thread; pass `bodies` for per-message body_text."""

    def _factory(
        *,
        thread_id: str = "t1",
        from_addr: str = "sender@example.com",
        bodies: tuple[str, ...] = ("Hello, can you confirm Thursday?",),
    ) -> Thread:
        messages = tuple(
            ThreadMessage(
                id=f"msg-{i}",
                from_addr=from_addr,
                date=f"2026-05-1{i}T08:00:00Z",
                body_text=body,
            )
            for i, body in enumerate(bodies)
        )
        return Thread(thread_id=thread_id, messages=messages)

    return _factory
