"""Haiku-based email classifier.

One batched Anthropic call per ~50 unread messages: each message becomes a
`<<<EMAIL_SNIPPET_START ...>>>` block in the user-content (see prompts.py),
and the model returns a JSON array of (id, class, urgency, reason) items.

The orchestrator catches `ClassifierMalformedError` and sets
`runs.status='partial'` per the B5 mapping; a `classifier_malformed_json`
trace row is also recorded.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from . import prompts
from .config import Config
from .types import ClassifiedMessage, MessageMeta

log = logging.getLogger(__name__)

CLASSIFIER_USER_ID = "jarvis.classifier"

VALID_CLASSES = ("ACTION", "FYI", "NOISE")
VALID_URGENCIES = ("URGENT", "TODAY", "LATER")
# JSON nulls deserialise to Python None; the model sometimes emits the
# string "null" instead — treat both as no-urgency.
_NULL_URGENCY_VARIANTS = (None, "null", "None")


class ClassifierMalformedError(Exception):
    """Raised when the model's response isn't valid JSON or has wrong shape."""


def classify(
    messages: Sequence[MessageMeta],
    cfg: Config,
    anthropic_client: Any,
) -> list[ClassifiedMessage]:
    """Classify a batch of messages in a single Haiku call.

    Returns a parsed `ClassifiedMessage` per input item. Raises
    `ClassifierMalformedError` if the response is unparseable, the
    top-level shape is wrong, a required field is missing, or class /
    urgency contain unrecognised values.
    """
    if not messages:
        return []

    user_content = prompts.render_classifier_user_content(
        messages, cfg.account.email
    )
    response = anthropic_client.messages.create(
        model=cfg.models.classifier,
        max_tokens=4000,
        system=prompts.system_prompt_classifier(cfg),
        messages=[{"role": "user", "content": user_content}],
        metadata={"user_id": CLASSIFIER_USER_ID},
    )
    text = response.content[0].text.strip()
    try:
        items = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClassifierMalformedError(
            f"classifier returned unparseable JSON; first 200 chars: "
            f"{text[:200]!r}"
        ) from e
    if not isinstance(items, list):
        raise ClassifierMalformedError(
            f"classifier expected a JSON array, got {type(items).__name__}"
        )

    results: list[ClassifiedMessage] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ClassifierMalformedError(
                f"classifier item {i}: expected object, "
                f"got {type(item).__name__}"
            )
        try:
            msg_id = item["id"]
            klass = item["class"]
        except KeyError as e:
            raise ClassifierMalformedError(
                f"classifier item {i}: missing required field {e}"
            ) from e
        urgency_raw = item.get("urgency")
        reason = item.get("reason", "")

        if klass not in VALID_CLASSES:
            raise ClassifierMalformedError(
                f"classifier item {msg_id!r}: class must be one of "
                f"{VALID_CLASSES}, got {klass!r}"
            )
        if urgency_raw in _NULL_URGENCY_VARIANTS:
            urgency: str | None = None
        elif urgency_raw in VALID_URGENCIES:
            urgency = urgency_raw
        else:
            raise ClassifierMalformedError(
                f"classifier item {msg_id!r}: urgency must be one of "
                f"{VALID_URGENCIES} or null, got {urgency_raw!r}"
            )

        results.append(
            ClassifiedMessage(
                id=msg_id, class_=klass, urgency=urgency, reason=reason
            )
        )
    return results
