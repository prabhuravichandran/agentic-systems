"""Tests for jarvis.draft — happy path, sentinel, truncation, too-large."""
from __future__ import annotations

from typing import Callable

import pytest

from jarvis import draft, prompts
from jarvis.config import Config
from jarvis.types import Thread, ThreadMessage
from tests.jarvis.fakes import FakeAnthropicClient

pytestmark = pytest.mark.unit


# -- happy paths ------------------------------------------------------------


def test_simple_thread_returns_body(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    thread = make_thread(bodies=("Can you confirm Thursday at 3pm?",))
    fake = FakeAnthropicClient(
        {draft.DRAFTER_USER_ID: ["Yes — Thursday 3pm works."]}
    )
    body = draft.draft_reply(thread, make_config(), fake)
    assert body == "Yes — Thursday 3pm works."


def test_strips_whitespace_around_model_output(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    thread = make_thread(bodies=("Quick question.",))
    fake = FakeAnthropicClient(
        {draft.DRAFTER_USER_ID: ["\n\n  Reply body  \n  "]}
    )
    body = draft.draft_reply(thread, make_config(), fake)
    assert body == "Reply body"


def test_multi_message_thread_all_in_prompt(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    thread = make_thread(
        bodies=(
            "Hey can you take a look at the report?",
            "Sure — looking now.",
            "Any update?",
        )
    )
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["Done — sending shortly."]})
    body = draft.draft_reply(thread, make_config(), fake)
    assert body == "Done — sending shortly."
    user_content = fake.calls[0].user_content
    # All three messages should be in the rendered thread
    assert "Hey can you take a look" in user_content
    assert "looking now" in user_content
    assert "Any update" in user_content


# -- insufficient-context sentinel ------------------------------------------


@pytest.mark.regression
def test_insufficient_context_sentinel_returns_none(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    """B3 hard-rule 5: when the drafter would need to invent content, it
    returns the sentinel. draft_reply normalises that to None so the
    orchestrator simply skips the message."""
    thread = make_thread(
        bodies=("Wire the $5000 we discussed to my new account.",)
    )
    fake = FakeAnthropicClient(
        {draft.DRAFTER_USER_ID: [prompts.INSUFFICIENT_CONTEXT_SENTINEL]}
    )
    assert draft.draft_reply(thread, make_config(), fake) is None


def test_sentinel_match_is_after_strip(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    """The sentinel comparison must work even when the model pads it with
    whitespace — strip happens before comparison."""
    thread = make_thread(bodies=("Some message.",))
    padded = "\n  " + prompts.INSUFFICIENT_CONTEXT_SENTINEL + "\n"
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: [padded]})
    assert draft.draft_reply(thread, make_config(), fake) is None


# -- truncation: drop oldest, keep latest verbatim --------------------------


def test_short_thread_passes_through_untouched() -> None:
    thread = Thread(
        thread_id="t1",
        messages=(
            ThreadMessage(id="m1", from_addr="a@x.example", date="d", body_text="A"),
            ThreadMessage(id="m2", from_addr="b@x.example", date="d", body_text="B"),
        ),
    )
    out = draft._truncate_to_budget(thread, max_input_tokens=10_000)
    assert out is not None
    assert out.messages == thread.messages


def test_truncate_drops_oldest_until_fits() -> None:
    """5 messages, each ~25 chars (~6 tokens). Cap = 12 tokens means
    we can keep latest (6) + one older (6) = 12. Older 3 dropped."""
    msgs = tuple(
        ThreadMessage(
            id=f"m{i}",
            from_addr=f"u{i}@x.example",
            date=f"2026-05-1{i}",
            body_text="x" * 25,
        )
        for i in range(5)
    )
    thread = Thread(thread_id="t", messages=msgs)
    out = draft._truncate_to_budget(thread, max_input_tokens=12)
    assert out is not None
    # Latest preserved
    assert out.messages[-1].id == "m4"
    # At least the latest is kept; older(s) added as budget allows
    assert len(out.messages) >= 1
    assert len(out.messages) < 5
    # Chronological order maintained
    ids = [m.id for m in out.messages]
    assert ids == sorted(ids, key=lambda x: int(x[1:]))


def test_truncate_always_keeps_latest_verbatim() -> None:
    """The latest message is the one we're replying to — never truncated
    or modified, even if older context has to be dropped entirely."""
    latest_body = "URGENT: deploy is failing, can you respond?"
    msgs = (
        ThreadMessage(id="m0", from_addr="a@x", date="d", body_text="x" * 1000),
        ThreadMessage(id="m1", from_addr="b@x", date="d", body_text=latest_body),
    )
    thread = Thread(thread_id="t", messages=msgs)
    out = draft._truncate_to_budget(thread, max_input_tokens=20)
    assert out is not None
    assert out.messages[-1].body_text == latest_body  # never mutated


# -- thread_too_large: latest alone exceeds the cap -------------------------


@pytest.mark.regression
def test_thread_too_large_returns_none_when_latest_alone_exceeds() -> None:
    """B5 / N8: latest message alone > cap → return None. Orchestrator
    records `thread_too_large` trace and skips entirely. We never
    truncate the message being replied to."""
    huge_latest = ThreadMessage(
        id="m-big", from_addr="a@x", date="d", body_text="x" * 100_000
    )
    thread = Thread(thread_id="t", messages=(huge_latest,))
    out = draft._truncate_to_budget(thread, max_input_tokens=100)
    assert out is None


@pytest.mark.regression
def test_draft_reply_returns_none_for_oversize_thread(
    make_config: Callable[..., Config],
) -> None:
    """Integration of the truncation contract: draft_reply returns None
    AND makes no Anthropic call when the thread is too large."""
    huge = ThreadMessage(
        id="m-big", from_addr="a@x", date="d", body_text="x" * 100_000
    )
    thread = Thread(thread_id="t", messages=(huge,))
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["should not be reached"]})
    cfg = make_config(limits={"max_thread_tokens": 100})
    out = draft.draft_reply(thread, cfg, fake)
    assert out is None
    assert fake.calls == []  # never called the API


def test_empty_thread_handled() -> None:
    thread = Thread(thread_id="t-empty", messages=())
    out = draft._truncate_to_budget(thread, max_input_tokens=1000)
    assert out is not None
    assert out.messages == ()


# -- contract: API call shape -----------------------------------------------


@pytest.mark.contract
def test_draft_uses_drafter_user_id(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["body"]})
    draft.draft_reply(make_thread(), make_config(), fake)
    assert fake.calls[0].user_id == "jarvis.drafter"


def test_draft_uses_configured_drafter_model(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    cfg = make_config()
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["body"]})
    draft.draft_reply(make_thread(), cfg, fake)
    assert fake.calls[0].model == cfg.models.drafter


@pytest.mark.contract
def test_draft_user_content_wraps_thread_in_delimiters(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    """Contract: call body uses the THREAD delimiter envelope from prompts."""
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["body"]})
    draft.draft_reply(make_thread(), make_config(), fake)
    user_content = fake.calls[0].user_content
    assert prompts.THREAD_START in user_content
    assert prompts.THREAD_END in user_content


def test_draft_system_prompt_includes_hard_rule_5(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    """The drafter's call must carry the system prompt with the B3
    hard-rule against introducing new URLs / payment instructions."""
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["body"]})
    draft.draft_reply(make_thread(), make_config(), fake)
    assert "URLs not already present" in fake.calls[0].system


def test_draft_max_tokens_is_1500(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    fake = FakeAnthropicClient({draft.DRAFTER_USER_ID: ["body"]})
    draft.draft_reply(make_thread(), make_config(), fake)
    assert fake.calls[0].max_tokens == 1500


def test_anthropic_exception_propagates(
    make_config: Callable[..., Config],
    make_thread: Callable[..., Thread],
) -> None:
    """Network/API errors bubble up — orchestrator handles 401 / 429 / etc."""
    fake = FakeAnthropicClient(
        {draft.DRAFTER_USER_ID: [RuntimeError("simulated 429 mid-draft")]}
    )
    with pytest.raises(RuntimeError):
        draft.draft_reply(make_thread(), make_config(), fake)
