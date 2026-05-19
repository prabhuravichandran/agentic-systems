"""Tests for jarvis.filters — pure unit tests, no network.

Covers every exclusion predicate with explicit fixtures, including the
B2 Auto-Submitted regression cases (header absent → passes,
auto-generated → filtered, auto-replied → filtered).
"""
from __future__ import annotations

from typing import Callable

import pytest

from jarvis import filters
from jarvis.config import Config
from jarvis.types import MessageMeta

pytestmark = pytest.mark.unit

USER_EMAIL = "alice@example.com"


# -- happy path --------------------------------------------------------------


def test_personal_email_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    skip, reason = filters.is_excluded(make_message(), make_config())
    assert skip is False
    assert reason == ""


# -- mailing lists / bulk ----------------------------------------------------


def test_list_unsubscribe_header_filters(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(headers={"List-Unsubscribe": "<https://example.com/u>"})
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "mailing_list"


def test_precedence_bulk_filters(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(headers={"Precedence": "Bulk"})
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "mailing_list"


@pytest.mark.contract
def test_header_lookups_are_case_insensitive(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """RFC 5322: header field names are case-insensitive. MessageMeta
    normalises to lower-case at construction so filter rules can't silently
    miss because of a casing change upstream in gmail.py."""
    for variant in ("List-Unsubscribe", "list-unsubscribe", "LIST-UNSUBSCRIBE"):
        msg = make_message(headers={variant: "<https://x.example/u>"})
        skip, _ = filters.is_excluded(msg, make_config())
        assert skip is True, f"header case variant failed: {variant!r}"


# -- B2 regression: Auto-Submitted ------------------------------------------


@pytest.mark.regression
def test_no_auto_submitted_header_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """B2: header absent must NOT trigger filtering (RFC 3834 default is 'no')."""
    skip, _ = filters.is_excluded(make_message(headers={}), make_config())
    assert skip is False


def test_auto_submitted_no_explicit_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(headers={"Auto-Submitted": "no"})
    skip, _ = filters.is_excluded(msg, make_config())
    assert skip is False


@pytest.mark.regression
def test_auto_submitted_auto_generated_filtered(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(headers={"Auto-Submitted": "auto-generated"})
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "mailing_list"


@pytest.mark.regression
def test_auto_submitted_auto_replied_filtered(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """B2: catches vacation autoresponders (RFC 3834's third value)."""
    msg = make_message(headers={"Auto-Submitted": "auto-replied"})
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "mailing_list"


def test_auto_submitted_case_insensitive(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(headers={"Auto-Submitted": "Auto-Generated"})
    skip, _ = filters.is_excluded(msg, make_config())
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
        "noreply+tag123@github.com",
        "NoReply@LinkedIn.com",
    ],
)
def test_noreply_addresses_filtered(
    addr: str,
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    skip, reason = filters.is_excluded(make_message(from_addr=addr), make_config())
    assert skip is True
    assert reason == "noreply"


def test_normal_addresses_not_treated_as_noreply(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    for addr in ("bob@friend.example", "support@company.example", "ceo@startup.io"):
        skip, reason = filters.is_excluded(
            make_message(from_addr=addr), make_config()
        )
        assert (skip, reason) != (True, "noreply"), addr


def test_skip_noreply_disabled_lets_noreply_through(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    cfg = make_config(exclusions={"skip_noreply": False})
    msg = make_message(from_addr="noreply@github.example")
    skip, _ = filters.is_excluded(msg, cfg)
    assert skip is False


# -- sender domain blocklist (Bug 1 regression) -----------------------------


def test_sender_domain_blocklist_filters(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    cfg = make_config(exclusions={"skip_sender_domains": ("chase.com", "irs.gov")})
    msg = make_message(from_addr="statements@chase.com")
    skip, reason = filters.is_excluded(msg, cfg)
    assert skip is True
    assert reason == "sender_domain"


@pytest.mark.regression
def test_sender_domain_blocklist_message_side_case_insensitive(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """Message domain case should not matter — lower-cased in filter."""
    cfg = make_config(exclusions={"skip_sender_domains": ("chase.com",)})
    msg = make_message(from_addr="alerts@Chase.COM")
    skip, _ = filters.is_excluded(msg, cfg)
    assert skip is True


def test_empty_sender_domain_blocklist_allows_all(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    cfg = make_config(exclusions={"skip_sender_domains": ()})
    msg = make_message(from_addr="statements@chase.com")
    skip, reason = filters.is_excluded(msg, cfg)
    assert (skip, reason) != (True, "sender_domain")


# -- Gmail categories (incl. N14 SPAM) --------------------------------------


def test_promotional_category_filtered(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(
        gmail_labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS", "PROMOTIONS")
    )
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "gmail_category"


@pytest.mark.regression
def test_spam_category_filtered_n14(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """N14 phishing — SPAM label is sufficient per design v1.1."""
    msg = make_message(gmail_labels=("UNREAD", "SPAM"))
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "gmail_category"


def test_user_label_blocklist_filters(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    cfg = make_config(exclusions={"skip_labels": ("Personal/Banking",)})
    msg = make_message(gmail_labels=("INBOX", "UNREAD", "Personal/Banking"))
    skip, reason = filters.is_excluded(msg, cfg)
    assert skip is True
    assert reason == "user_label"


# -- size hard-skip ---------------------------------------------------------


def test_oversize_email_filtered(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(size_estimate=2_000_000)
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "too_large"


def test_at_size_boundary_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """Cap is `>`, not `>=` — exactly 1 MB should pass."""
    msg = make_message(size_estimate=1_048_576)
    skip, _ = filters.is_excluded(msg, make_config())
    assert skip is False


# -- Cc-only to large distribution ------------------------------------------


def test_user_in_to_with_few_recipients_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(to_addrs=(USER_EMAIL, "carol@x.example"), cc_addrs=())
    skip, _ = filters.is_excluded(msg, make_config())
    assert skip is False


def test_user_on_cc_only_with_many_recipients_filtered(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(
        to_addrs=("a@x.example", "b@x.example", "c@x.example", "d@x.example"),
        cc_addrs=(USER_EMAIL, "e@x.example", "f@x.example"),
    )
    skip, reason = filters.is_excluded(msg, make_config())
    assert skip is True
    assert reason == "cc_only_distribution"


def test_user_on_cc_with_small_group_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    msg = make_message(
        to_addrs=("a@x.example", "b@x.example"),
        cc_addrs=(USER_EMAIL, "c@x.example"),
    )
    skip, _ = filters.is_excluded(msg, make_config())
    assert skip is False


def test_user_in_to_with_many_recipients_passes(
    make_message: Callable[..., MessageMeta],
    make_config: Callable[..., Config],
) -> None:
    """User addressed directly even in a large CC — not a distribution blast."""
    msg = make_message(
        to_addrs=(USER_EMAIL, "a@x.example"),
        cc_addrs=("b@x.example", "c@x.example", "d@x.example", "e@x.example"),
    )
    skip, _ = filters.is_excluded(msg, make_config())
    assert skip is False
