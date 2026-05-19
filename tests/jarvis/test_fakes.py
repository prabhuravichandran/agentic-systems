"""Tests for the FakeAnthropicClient itself.

The fake is the substrate for every classify / draft / brief test below.
If the fake has a bug — wrong tag matching, broken queue, mis-recorded
calls — every downstream test gives false signal. So we test it first.
"""
from __future__ import annotations

import pytest

from tests.jarvis.fakes import (
    FakeAnthropicClient,
    FakeCall,
    FakeContent,
    FakeResponse,
)

pytestmark = pytest.mark.unit


def _call(
    client: FakeAnthropicClient,
    user_id: str = "jarvis.classifier",
    system: str = "test-system",
    user_content: str = "test-user",
) -> FakeResponse:
    return client.messages.create(
        model="claude-haiku-test",
        max_tokens=100,
        system=system,
        messages=[{"role": "user", "content": user_content}],
        metadata={"user_id": user_id},
    )


def test_returns_queued_string_as_response_text() -> None:
    fake = FakeAnthropicClient({"jarvis.classifier": ["hello"]})
    resp = _call(fake)
    assert isinstance(resp, FakeResponse)
    assert resp.content[0].text == "hello"


def test_routes_by_user_id() -> None:
    fake = FakeAnthropicClient(
        {
            "jarvis.classifier": ["classifier-output"],
            "jarvis.drafter": ["draft-output"],
        }
    )
    assert _call(fake, "jarvis.classifier").content[0].text == "classifier-output"
    assert _call(fake, "jarvis.drafter").content[0].text == "draft-output"


def test_records_every_call() -> None:
    fake = FakeAnthropicClient({"jarvis.classifier": ["r1", "r2"]})
    _call(fake, system="system-A", user_content="user-A")
    _call(fake, system="system-B", user_content="user-B")
    assert len(fake.calls) == 2
    assert fake.calls[0].system == "system-A"
    assert fake.calls[0].user_content == "user-A"
    assert fake.calls[0].user_id == "jarvis.classifier"
    assert fake.calls[1].user_content == "user-B"


def test_calls_for_filters_by_user_id() -> None:
    fake = FakeAnthropicClient(
        {"jarvis.classifier": ["c"], "jarvis.drafter": ["d1", "d2"]}
    )
    _call(fake, "jarvis.classifier")
    _call(fake, "jarvis.drafter")
    _call(fake, "jarvis.drafter")
    assert len(fake.calls_for("jarvis.classifier")) == 1
    assert len(fake.calls_for("jarvis.drafter")) == 2


def test_unknown_user_id_raises_keyerror() -> None:
    fake = FakeAnthropicClient({"jarvis.classifier": ["x"]})
    with pytest.raises(KeyError) as exc_info:
        _call(fake, user_id="jarvis.unknown")
    assert "jarvis.unknown" in str(exc_info.value)
    # Still recorded — caller can inspect the bad call
    assert len(fake.calls) == 1


def test_exhausted_queue_raises_indexerror() -> None:
    fake = FakeAnthropicClient({"jarvis.classifier": ["once"]})
    _call(fake)
    with pytest.raises(IndexError):
        _call(fake)


def test_queued_exception_is_raised_not_returned() -> None:
    """Failure injection: queue an exception instance, it's raised on consume.
    This is how B5 partial-status fixtures (Anthropic 429, 401) get
    threaded through the orchestrator tests."""
    fake = FakeAnthropicClient(
        {"jarvis.classifier": [RuntimeError("simulated API 429")]}
    )
    with pytest.raises(RuntimeError) as exc_info:
        _call(fake)
    assert "simulated API 429" in str(exc_info.value)


def test_mixed_queue_strings_and_exceptions() -> None:
    """The fake supports interleaved success/failure responses."""
    fake = FakeAnthropicClient(
        {
            "jarvis.classifier": [
                "first ok",
                RuntimeError("transient"),
                "third ok",
            ]
        }
    )
    assert _call(fake).content[0].text == "first ok"
    with pytest.raises(RuntimeError):
        _call(fake)
    assert _call(fake).content[0].text == "third ok"


def test_metadata_absent_routes_to_empty_user_id() -> None:
    """If a caller forgets metadata, the empty user_id surfaces clearly
    rather than silently matching some default."""
    fake = FakeAnthropicClient({"jarvis.classifier": ["x"]})
    with pytest.raises(KeyError) as exc_info:
        fake.messages.create(
            model="m",
            max_tokens=10,
            system="s",
            messages=[{"role": "user", "content": "u"}],
        )
    # user_id is "" in the error and the call is still recorded
    assert "''" in str(exc_info.value) or '""' in str(exc_info.value)
    assert fake.calls[-1].user_id == ""


def test_call_captures_max_tokens_and_model() -> None:
    fake = FakeAnthropicClient({"jarvis.drafter": ["body"]})
    fake.messages.create(
        model="claude-sonnet-test",
        max_tokens=1500,
        system="sys",
        messages=[{"role": "user", "content": "u"}],
        metadata={"user_id": "jarvis.drafter"},
    )
    assert fake.calls[0].model == "claude-sonnet-test"
    assert fake.calls[0].max_tokens == 1500


def test_extra_kwargs_captured_raw() -> None:
    """Non-essential kwargs (e.g. temperature, stop_sequences) are passed
    through to raw_kwargs so tests can assert on them without breaking the
    fake's signature."""
    fake = FakeAnthropicClient({"jarvis.classifier": ["x"]})
    fake.messages.create(
        model="m",
        max_tokens=10,
        system="s",
        messages=[{"role": "user", "content": "u"}],
        metadata={"user_id": "jarvis.classifier"},
        temperature=0.0,
        stop_sequences=["</done>"],
    )
    assert fake.calls[0].raw_kwargs == {
        "temperature": 0.0,
        "stop_sequences": ["</done>"],
    }


def test_content_field_shape_matches_sdk() -> None:
    """Sanity: production code reads response.content[0].text — verify
    the fake exposes the same shape so swapping in the real SDK Just Works."""
    fake = FakeAnthropicClient({"jarvis.classifier": ["abc"]})
    resp = _call(fake)
    assert hasattr(resp, "content")
    assert isinstance(resp.content, list)
    assert hasattr(resp.content[0], "text")
    assert resp.content[0].text == "abc"
