"""Tests for jarvis.filters — pure unit tests, no network.

Covers every exclusion predicate with explicit fixtures, including the
B2 Auto-Submitted regression cases (header absent → passes,
auto-generated → filtered, auto-replied → filtered).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from jarvis import filters
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
from jarvis.types import MessageMeta


USER_EMAIL = "alice@example.com"


def _cfg(**overrides: Any) -> Config:
    """Build a default Config; pass section dicts to override fields."""
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
    return Config(
        account=AccountConfig(email=USER_EMAIL),
        schedule=ScheduleConfig(hour=8, weekdays_only=True),
        limits=LimitsConfig(**limits),
        exclusions=ExclusionsConfig(**excl),
        models=ModelsConfig(
            classifier="claude-haiku-4-5-20251001", drafter="claude-sonnet-4-6"
        ),
        notifications=NotificationsConfig(notify_on="if_drafts"),
        storage=StorageConfig(
            db_path="/tmp/x.sqlite", retention_days=7, trace_body_chars=500
        ),
    )


def _msg(**overrides: Any) -> MessageMeta:
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


# -- happy path --------------------------------------------------------------


def test_personal_email_passes() -> None:
    skip, reason = filters.is_excluded(_msg(), _cfg())
    assert skip is False
    assert reason == ""


# -- mailing lists / bulk ----------------------------------------------------


def test_list_unsubscribe_header_filters() -> None:
    msg = _msg(headers={"List-Unsubscribe": "<https://example.com/u>"})
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "mailing_list"


def test_precedence_bulk_filters() -> None:
    msg = _msg(headers={"Precedence": "Bulk"})
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "mailing_list"


# -- B2 regression: Auto-Submitted ------------------------------------------


def test_no_auto_submitted_header_passes() -> None:
    """The core B2 regression — header absent must NOT trigger filtering."""
    msg = _msg(headers={})
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is False, "missing Auto-Submitted header treated as 'no' per RFC 3834"


def test_auto_submitted_no_explicit_passes() -> None:
    msg = _msg(headers={"Auto-Submitted": "no"})
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is False


def test_auto_submitted_auto_generated_filtered() -> None:
    msg = _msg(headers={"Auto-Submitted": "auto-generated"})
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "mailing_list"


def test_auto_submitted_auto_replied_filtered() -> None:
    """Vacation autoresponders — catches RFC 3834's third value."""
    msg = _msg(headers={"Auto-Submitted": "auto-replied"})
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "mailing_list"


def test_auto_submitted_case_insensitive() -> None:
    msg = _msg(headers={"Auto-Submitted": "Auto-Generated"})
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is True


# -- noreply ----------------------------------------------------------------


@pytest.mark.parametrize(
    "addr",
    [
        "noreply@github.com",
        "no-reply@bank.example",
        "do-not-reply@vendor.example",
        "donotreply@news.example",
        "mailer-daemon@example.com",
        "noreply+tag123@github.com",  # plus-addressed noreply
        "NoReply@LinkedIn.com",  # case
    ],
)
def test_noreply_addresses_filtered(addr: str) -> None:
    msg = _msg(from_addr=addr)
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "noreply"


def test_normal_addresses_not_treated_as_noreply() -> None:
    for addr in ("bob@friend.example", "support@company.example", "ceo@startup.io"):
        skip, reason = filters.is_excluded(_msg(from_addr=addr), _cfg())
        assert (skip, reason) != (True, "noreply"), addr


def test_skip_noreply_disabled_lets_noreply_through() -> None:
    cfg = _cfg(exclusions={"skip_noreply": False})
    msg = _msg(from_addr="noreply@github.example")
    skip, _ = filters.is_excluded(msg, cfg)
    assert skip is False


# -- sender domain blocklist ------------------------------------------------


def test_sender_domain_blocklist_filters() -> None:
    cfg = _cfg(exclusions={"skip_sender_domains": ("chase.com", "irs.gov")})
    msg = _msg(from_addr="statements@chase.com")
    skip, reason = filters.is_excluded(msg, cfg)
    assert skip is True
    assert reason == "sender_domain"


def test_sender_domain_blocklist_is_case_insensitive() -> None:
    cfg = _cfg(exclusions={"skip_sender_domains": ("chase.com",)})
    msg = _msg(from_addr="alerts@Chase.COM")
    skip, _ = filters.is_excluded(msg, cfg)
    assert skip is True


def test_empty_sender_domain_blocklist_allows_all() -> None:
    cfg = _cfg(exclusions={"skip_sender_domains": ()})
    msg = _msg(from_addr="statements@chase.com")
    skip, reason = filters.is_excluded(msg, cfg)
    assert (skip, reason) != (True, "sender_domain")


# -- Gmail categories (incl. N14 SPAM) --------------------------------------


def test_promotional_category_filtered() -> None:
    msg = _msg(gmail_labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS", "PROMOTIONS"))
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "gmail_category"


def test_spam_category_filtered_n14() -> None:
    """N14 phishing — SPAM label is sufficient per design v1.1."""
    msg = _msg(gmail_labels=("UNREAD", "SPAM"))
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "gmail_category"


def test_user_label_blocklist_filters() -> None:
    cfg = _cfg(exclusions={"skip_labels": ("Personal/Banking",)})
    msg = _msg(gmail_labels=("INBOX", "UNREAD", "Personal/Banking"))
    skip, reason = filters.is_excluded(msg, cfg)
    assert skip is True
    assert reason == "user_label"


# -- size hard-skip ---------------------------------------------------------


def test_oversize_email_filtered() -> None:
    msg = _msg(size_estimate=2_000_000)  # 2 MB > 1 MB cap
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "too_large"


def test_at_size_boundary_passes() -> None:
    msg = _msg(size_estimate=1_048_576)  # exactly the cap
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is False  # > comparison, equal passes


# -- Cc-only to large distribution ------------------------------------------


def test_user_in_to_with_few_recipients_passes() -> None:
    msg = _msg(to_addrs=(USER_EMAIL, "carol@x.example"), cc_addrs=())
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is False


def test_user_on_cc_only_with_many_recipients_filtered() -> None:
    msg = _msg(
        to_addrs=("a@x.example", "b@x.example", "c@x.example", "d@x.example"),
        cc_addrs=(USER_EMAIL, "e@x.example", "f@x.example"),
    )
    skip, reason = filters.is_excluded(msg, _cfg())
    assert skip is True
    assert reason == "cc_only_distribution"


def test_user_on_cc_with_small_group_passes() -> None:
    """Cc-only is fine if the group is small (≤5 total)."""
    msg = _msg(
        to_addrs=("a@x.example", "b@x.example"),
        cc_addrs=(USER_EMAIL, "c@x.example"),
    )
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is False


def test_user_in_to_with_many_recipients_passes() -> None:
    """User addressed directly even in a large CC — not a distribution blast."""
    msg = _msg(
        to_addrs=(USER_EMAIL, "a@x.example"),
        cc_addrs=("b@x.example", "c@x.example", "d@x.example", "e@x.example"),
    )
    skip, _ = filters.is_excluded(msg, _cfg())
    assert skip is False
