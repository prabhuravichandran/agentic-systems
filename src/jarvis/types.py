"""Shared Jarvis data types.

`MessageMeta` is the lightweight envelope returned by `gmail.list_unread` —
metadata + Gmail-provided snippet, but no full body. Full-body content lives
in `Thread` (populated by `gmail.get_thread`) and is only fetched for the
top-N action items that get drafted.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MessageMeta:
    """Per-message metadata sufficient for filtering, dedup, and classification.

    Header dict keys use RFC canonical case (`List-Unsubscribe`,
    `Auto-Submitted`, `Precedence`) — `gmail.list_unread` is responsible
    for normalising on the way in. Filter code reads values, not keys,
    so case-insensitivity is only relevant on the lookup itself.
    """

    id: str
    thread_id: str
    from_addr: str
    to_addrs: tuple[str, ...]
    cc_addrs: tuple[str, ...]
    subject: str
    date: str
    headers: dict[str, str] = field(default_factory=dict)
    snippet: str = ""
    gmail_labels: tuple[str, ...] = field(default_factory=tuple)
    size_estimate: int = 0


@dataclass(frozen=True)
class ThreadMessage:
    """One message within a Thread; carries full body text."""

    id: str
    from_addr: str
    date: str
    body_text: str


@dataclass(frozen=True)
class Thread:
    """A Gmail thread with full bodies for every message.

    `messages` is ordered oldest-first; the message we're drafting a reply
    to is `messages[-1]` and must never be truncated by `_truncate_to_budget`.
    """

    thread_id: str
    messages: tuple[ThreadMessage, ...]

    @property
    def latest_message_id(self) -> str:
        return self.messages[-1].id


@dataclass(frozen=True)
class ClassifiedMessage:
    """Output of the Haiku classifier for one input message."""

    id: str
    class_: str  # "ACTION" | "FYI" | "NOISE"
    urgency: str | None  # "URGENT" | "TODAY" | "LATER" | None
    reason: str
