"""Tests for jarvis.classify — parsed shape, error contracts, prompt threading."""
from __future__ import annotations

import json
from typing import Callable

import pytest

from jarvis import classify, prompts
from jarvis.config import Config
from jarvis.types import ClassifiedMessage, MessageMeta
from tests.jarvis.fakes import FakeAnthropicClient

pytestmark = pytest.mark.unit


# -- happy paths ------------------------------------------------------------


def test_empty_messages_returns_empty_no_api_call(
    make_config: Callable[..., Config],
) -> None:
    fake = FakeAnthropicClient({})
    out = classify.classify([], make_config(), fake)
    assert out == []
    assert fake.calls == []


def test_single_message_parsed(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps(
        [
            {
                "id": "m1",
                "class": "ACTION",
                "urgency": "URGENT",
                "reason": "from boss, deadline today",
            }
        ]
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    out = classify.classify(
        [make_message(id="m1", from_addr="boss@company.example")],
        make_config(),
        fake,
    )
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, ClassifiedMessage)
    assert item.id == "m1"
    assert item.class_ == "ACTION"
    assert item.urgency == "URGENT"
    assert "boss" in item.reason


def test_multiple_messages_in_one_batch(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps(
        [
            {"id": "m1", "class": "ACTION", "urgency": "TODAY", "reason": "r1"},
            {"id": "m2", "class": "FYI", "urgency": None, "reason": "r2"},
            {"id": "m3", "class": "NOISE", "urgency": None, "reason": "r3"},
        ]
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    msgs = [make_message(id=mid) for mid in ("m1", "m2", "m3")]
    out = classify.classify(msgs, make_config(), fake)
    assert [c.class_ for c in out] == ["ACTION", "FYI", "NOISE"]
    assert [c.urgency for c in out] == ["TODAY", None, None]


def test_null_urgency_string_normalised(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """Models sometimes emit the literal string "null" instead of JSON null."""
    canned = json.dumps(
        [{"id": "m1", "class": "FYI", "urgency": "null", "reason": "r"}]
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    out = classify.classify(
        [make_message(id="m1")], make_config(), fake
    )
    assert out[0].urgency is None


# -- B5 partial-status: ClassifierMalformedError contract -------------------


@pytest.mark.regression
def test_unparseable_json_raises_classifier_malformed_error(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """B5: malformed-JSON batch → ClassifierMalformedError; orchestrator
    catches and sets runs.status='partial' + records the trace."""
    fake = FakeAnthropicClient(
        {classify.CLASSIFIER_USER_ID: ["this is not json at all"]}
    )
    with pytest.raises(classify.ClassifierMalformedError) as exc_info:
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )
    assert "unparseable" in str(exc_info.value).lower()


def test_top_level_object_not_list_raises(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps({"id": "m1", "class": "ACTION"})  # object, not list
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    with pytest.raises(classify.ClassifierMalformedError) as exc_info:
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )
    assert "array" in str(exc_info.value).lower()


def test_missing_required_field_raises(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps([{"id": "m1"}])  # missing 'class'
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    with pytest.raises(classify.ClassifierMalformedError) as exc_info:
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )
    assert "class" in str(exc_info.value)


def test_invalid_class_value_raises(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps(
        [{"id": "m1", "class": "MAYBE", "urgency": None, "reason": "x"}]
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    with pytest.raises(classify.ClassifierMalformedError) as exc_info:
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )
    assert "MAYBE" in str(exc_info.value)


def test_invalid_urgency_value_raises(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps(
        [{"id": "m1", "class": "ACTION", "urgency": "SOON", "reason": "x"}]
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    with pytest.raises(classify.ClassifierMalformedError) as exc_info:
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )
    assert "SOON" in str(exc_info.value)


def test_item_not_a_dict_raises(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps(["not-an-object", "either"])
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    with pytest.raises(classify.ClassifierMalformedError):
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )


def test_anthropic_exception_propagates(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """Network/API errors from the SDK bubble up — orchestrator handles
    the 401 / 429 distinction at its boundary, not here."""
    fake = FakeAnthropicClient(
        {classify.CLASSIFIER_USER_ID: [RuntimeError("simulated 429")]}
    )
    with pytest.raises(RuntimeError):
        classify.classify(
            [make_message(id="m1")], make_config(), fake
        )


# -- prompt threading -------------------------------------------------------


@pytest.mark.contract
def test_classify_uses_classifier_user_id_in_metadata(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """Contract: classify must tag its call with the classifier user_id so
    fakes (and Anthropic telemetry) can distinguish call sites."""
    canned = json.dumps([])
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    classify.classify([make_message()], make_config(), fake)
    assert fake.calls[0].user_id == "jarvis.classifier"


def test_classify_uses_configured_model(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps([])
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    cfg = make_config()
    classify.classify([make_message()], cfg, fake)
    assert fake.calls[0].model == cfg.models.classifier


@pytest.mark.contract
def test_classify_user_content_wraps_message_in_delimiter_block(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """Contract: the call body must use the delimiter protocol from
    prompts.py — this is how B3 mitigations stay coupled to the call site."""
    canned = json.dumps([])
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    classify.classify(
        [make_message(id="m1", snippet="hello")], make_config(), fake
    )
    user_content = fake.calls[0].user_content
    assert "<<<EMAIL_SNIPPET_START" in user_content
    assert prompts.EMAIL_SNIPPET_END in user_content


def test_classify_system_prompt_includes_untrusted_clause(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    canned = json.dumps([])
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    classify.classify([make_message()], make_config(), fake)
    assert "UNTRUSTED INPUT BOUNDARY" in fake.calls[0].system


def test_classify_strips_whitespace_around_json(
    make_config: Callable[..., Config],
    make_message: Callable[..., MessageMeta],
) -> None:
    """Models often pad output with leading/trailing whitespace or newlines."""
    canned = (
        "\n\n  "
        + json.dumps(
            [{"id": "m1", "class": "FYI", "urgency": None, "reason": "x"}]
        )
        + "  \n"
    )
    fake = FakeAnthropicClient({classify.CLASSIFIER_USER_ID: [canned]})
    out = classify.classify(
        [make_message(id="m1")], make_config(), fake
    )
    assert len(out) == 1
    assert out[0].class_ == "FYI"
