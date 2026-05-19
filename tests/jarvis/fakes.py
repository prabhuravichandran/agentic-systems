"""Fake Anthropic client for jarvis tests.

Shape mirrors the real SDK's `client.messages.create(...)` so production
classify/draft code is dependency-injected with either an
`anthropic.Anthropic()` instance or this fake — no separate test-mode
branch.

Routing: by `metadata["user_id"]` (documented Anthropic metadata field;
ignored by real API except for telemetry). The two callers tag themselves
as `"jarvis.classifier"` and `"jarvis.drafter"` so test fixtures read as
`responses_by_user_id={"jarvis.classifier": [...], "jarvis.drafter": [...]}`.

Each call is appended to `client.calls` so tests can assert on the prompts
that were sent — this is how prompt-content regressions get caught without
a round trip to Anthropic.

Failure injection: a queued response that is an exception instance is
raised instead of returned. Use for B5 partial-status fixtures (API 429,
401, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeContent:
    """Mirror of anthropic.types.TextBlock — `.text` is the only field used."""

    text: str


@dataclass
class FakeResponse:
    """Mirror of anthropic.types.Message — `.content[0].text` is the only path used."""

    content: list[FakeContent]


@dataclass
class FakeCall:
    """Record of one .messages.create(...) call, captured for test assertions."""

    model: str
    system: str
    user_content: str
    max_tokens: int
    user_id: str
    raw_messages: list[dict[str, Any]]
    raw_kwargs: dict[str, Any] = field(default_factory=dict)


class _MessagesProxy:
    """Mirrors `client.messages` namespace on the real Anthropic SDK."""

    def __init__(self, parent: "FakeAnthropicClient") -> None:
        self._parent = parent

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> FakeResponse:
        user_id = (metadata or {}).get("user_id", "")
        user_content = ""
        if messages and isinstance(messages[0].get("content"), str):
            user_content = messages[0]["content"]
        call = FakeCall(
            model=model,
            system=system,
            user_content=user_content,
            max_tokens=max_tokens,
            user_id=user_id,
            raw_messages=list(messages),
            raw_kwargs=dict(kwargs),
        )
        self._parent.calls.append(call)

        if user_id not in self._parent._responses:
            raise KeyError(
                f"FakeAnthropicClient: no responses queued for user_id "
                f"{user_id!r}; available: {list(self._parent._responses)}"
            )
        queue = self._parent._responses[user_id]
        idx = self._parent._consumed[user_id]
        if idx >= len(queue):
            raise IndexError(
                f"FakeAnthropicClient: exhausted responses for user_id "
                f"{user_id!r} ({len(queue)} queued, called {idx + 1} times)"
            )
        next_item = queue[idx]
        self._parent._consumed[user_id] = idx + 1

        if isinstance(next_item, BaseException):
            raise next_item
        return FakeResponse(content=[FakeContent(text=next_item)])


class FakeAnthropicClient:
    """Drop-in replacement for `anthropic.Anthropic()` in tests.

    Construct with a `responses_by_user_id` map: each user_id key maps to a
    queue of canned responses. Each queue entry is either a `str` (returned
    as the response text) or a `BaseException` (raised on consume).
    """

    def __init__(self, responses_by_user_id: dict[str, list[Any]]) -> None:
        self._responses: dict[str, list[Any]] = {
            uid: list(resps) for uid, resps in responses_by_user_id.items()
        }
        self._consumed: dict[str, int] = {uid: 0 for uid in self._responses}
        self.calls: list[FakeCall] = []
        self.messages = _MessagesProxy(self)

    def calls_for(self, user_id: str) -> list[FakeCall]:
        """Convenience: only the calls for one user_id."""
        return [c for c in self.calls if c.user_id == user_id]
