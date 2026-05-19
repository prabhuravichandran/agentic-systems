"""macOS notification via osascript subprocess + tri-state gating.

`notify(title, body)` shells out to osascript and tolerates any failure
(missing binary, timeout, sandbox denial) by logging — a notification
failure must never abort or fail a run.

`should_notify(...)` implements the design v1.1 tri-state:
    notify_on: 'always' | 'if_drafts' | 'never'
with the invariant that **failure-status runs always notify**, bypassing
notify_on. Otherwise the user has no signal that auth expired.
"""
from __future__ import annotations

import json
import logging
import subprocess

log = logging.getLogger(__name__)


def _quote(s: str) -> str:
    """JSON-encode a string for safe inclusion in an osascript -e literal.

    `ensure_ascii=False` keeps Unicode (·, em-dash, emoji) intact rather
    than \\uXXXX-escaping them — osascript handles UTF-8 natively and the
    notification banner displays the original glyphs.
    """
    return json.dumps(s, ensure_ascii=False)


def notify(title: str, body: str) -> None:
    """Display a macOS notification. Failures are swallowed and logged."""
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f"display notification {_quote(body)} "
                f"with title {_quote(title)}",
            ],
            check=False,
            timeout=5,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.warning("notification failed, continuing: %s", e)


def should_notify(
    notify_on: str, *, drafts_count: int, run_status: str
) -> bool:
    """Tri-state notification gating; failure runs always notify."""
    if run_status == "failure":
        return True
    if notify_on == "always":
        return True
    if notify_on == "never":
        return False
    if notify_on == "if_drafts":
        return drafts_count > 0
    raise ValueError(
        f"invalid notify_on value {notify_on!r}; "
        f"expected 'always' | 'if_drafts' | 'never'"
    )
