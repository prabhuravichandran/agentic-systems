"""Pure exclusion predicates for the unread-message stream.

`is_excluded(msg, cfg)` returns `(skip: bool, reason: str)`. No I/O, no
network — every input is a `MessageMeta` plus the user's `Config`.

Coverage:
- mailing-list / bulk / auto-submitted headers
- noreply-style sender locals (incl. plus-addressing)
- user-curated sender-domain blocklist (`exclusions.skip_sender_domains`)
- Gmail-category labels (PROMOTIONS / SOCIAL / SPAM by default — N14
  phishing is covered structurally by SPAM here)
- user-curated Gmail-label blocklist (`exclusions.skip_labels`)
- 1 MB hard-skip on size (N8)
- user-on-Cc-only with >5 recipients
"""
from __future__ import annotations

from .config import Config
from .types import MessageMeta

NOREPLY_LOCALS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "mailer-daemon",
    "mail-daemon",
    "postmaster",
)


def is_excluded(msg: MessageMeta, cfg: Config) -> tuple[bool, str]:
    """Return (True, reason) if msg should be skipped, else (False, '')."""
    if cfg.exclusions.skip_mailing_lists and (
        msg.headers.get("List-Unsubscribe")
        or msg.headers.get("Precedence", "").lower() == "bulk"
        or msg.headers.get("Auto-Submitted", "no").lower() != "no"
    ):
        return True, "mailing_list"
    if cfg.exclusions.skip_noreply and _is_noreply(msg.from_addr):
        return True, "noreply"
    if _sender_domain(msg.from_addr) in cfg.exclusions.skip_sender_domains:
        return True, "sender_domain"
    if any(cat in msg.gmail_labels for cat in cfg.exclusions.skip_gmail_categories):
        return True, "gmail_category"  # covers SPAM / PROMOTIONS / SOCIAL
    if any(lbl in msg.gmail_labels for lbl in cfg.exclusions.skip_labels):
        return True, "user_label"
    if msg.size_estimate > cfg.limits.max_email_bytes:
        return True, "too_large"
    if _is_cc_only_to_large_group(msg, cfg):
        return True, "cc_only_distribution"
    # TODO(P1): display-name / sender-domain spoof detection. Deferred — no
    # reliable corpus to tune against in P0; SPAM-label exclusion above
    # covers Gmail-flagged phishing per N14.
    return False, ""


def _is_noreply(addr: str) -> bool:
    """Match common no-reply locals, including Gmail plus-addressing variants."""
    if "@" not in addr:
        return False
    local = addr.split("@", 1)[0].lower()
    plus_root = local.split("+", 1)[0]
    return plus_root in NOREPLY_LOCALS


def _sender_domain(addr: str) -> str:
    """Return the lower-cased domain part of an address, or '' if malformed."""
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[1].lower()


def _is_cc_only_to_large_group(msg: MessageMeta, cfg: Config) -> bool:
    """True iff user is on Cc only (not To) and total recipients exceeds 5."""
    total = len(msg.to_addrs) + len(msg.cc_addrs)
    if total <= 5:
        return False
    return cfg.account.email not in msg.to_addrs
