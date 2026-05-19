"""Sonnet-based drafter — one reply body per top-N action item.

Builds a delimited thread (via prompts.render_drafter_user_content),
sends one Anthropic call, returns the reply body. Returns `None` for two
distinct skip-this-message cases that the orchestrator handles:

- the most recent message alone exceeds `max_thread_tokens` (record
  `thread_too_large` trace and skip)
- the model returned `INSUFFICIENT_CONTEXT_SENTINEL`, meaning it would
  have had to invent content (skip — user drafts manually)
"""
from __future__ import annotations

import logging
from typing import Any

from . import prompts
from .config import Config
from .types import Thread, ThreadMessage

log = logging.getLogger(__name__)

DRAFTER_USER_ID = "jarvis.drafter"


def draft_reply(
    thread: Thread,
    cfg: Config,
    anthropic_client: Any,
) -> str | None:
    """Draft a reply to the latest message in `thread`.

    Returns the body text, or `None` if the thread should be skipped
    (too large, or the model declined for insufficient context).
    """
    truncated = _truncate_to_budget(thread, cfg.limits.max_thread_tokens)
    if truncated is None:
        log.info(
            "draft_reply skipping thread %s: latest message alone exceeds "
            "max_thread_tokens=%d",
            thread.thread_id,
            cfg.limits.max_thread_tokens,
        )
        return None

    user_content = prompts.render_drafter_user_content(truncated)
    response = anthropic_client.messages.create(
        model=cfg.models.drafter,
        max_tokens=1500,
        system=prompts.system_prompt_drafter(cfg),
        messages=[{"role": "user", "content": user_content}],
        metadata={"user_id": DRAFTER_USER_ID},
    )
    body = response.content[0].text.strip()
    if body == prompts.INSUFFICIENT_CONTEXT_SENTINEL:
        return None
    return body


def _estimate_tokens(text: str) -> int:
    """Crude estimate: ~4 chars per token. Good enough for P0 budget math."""
    return max(1, len(text) // 4)


def _truncate_to_budget(
    thread: Thread, max_input_tokens: int
) -> Thread | None:
    """Drop oldest messages until estimated input tokens ≤ max_input_tokens.

    NEVER truncates the most recent message — it's the one being replied
    to. If that message alone exceeds the cap, returns `None` (orchestrator
    records `thread_too_large` and skips this email entirely).

    For threads under the cap, this is a no-op — returns the input thread.
    """
    if not thread.messages:
        return thread

    latest = thread.messages[-1]
    latest_cost = _estimate_tokens(latest.body_text)
    if latest_cost > max_input_tokens:
        return None

    kept: list[ThreadMessage] = [latest]
    budget = max_input_tokens - latest_cost
    # Walk older messages from newest-of-the-older to oldest, fitting in
    # whatever budget remains. Stop at the first one that doesn't fit so
    # the kept set stays contiguous (no gaps in the conversation).
    for older in reversed(thread.messages[:-1]):
        cost = _estimate_tokens(older.body_text)
        if cost <= budget:
            kept.append(older)
            budget -= cost
        else:
            break
    # Restore chronological order (oldest first).
    kept.reverse()
    return Thread(thread_id=thread.thread_id, messages=tuple(kept))
