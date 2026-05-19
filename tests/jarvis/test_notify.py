"""Tests for jarvis.notify — osascript invocation + tri-state gating."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from jarvis import notify

pytestmark = pytest.mark.unit


# -- subprocess invocation --------------------------------------------------


def test_notify_invokes_osascript_with_json_escaped_body() -> None:
    with patch("jarvis.notify.subprocess.run") as mock_run:
        notify.notify("Jarvis", "7 drafts · 3 urgent")
    args = mock_run.call_args
    assert args.args[0][0] == "osascript"
    assert args.args[0][1] == "-e"
    script = args.args[0][2]
    assert "display notification" in script
    assert '"7 drafts · 3 urgent"' in script
    assert '"Jarvis"' in script
    assert args.kwargs["check"] is False  # never raise from subprocess
    assert args.kwargs["timeout"] == 5


def test_notify_quotes_special_chars_via_json() -> None:
    """JSON-encoded bodies neutralise quote chars that would break the
    osascript -e string."""
    with patch("jarvis.notify.subprocess.run") as mock_run:
        notify.notify("Jarvis", 'Quoted "thing" and \\backslash')
    script = mock_run.call_args.args[0][2]
    # The JSON-encoded body keeps the quotes escaped inside the script arg
    assert '\\"thing\\"' in script
    assert "\\\\backslash" in script


def test_notify_swallows_filenotfound_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No osascript on a non-Mac box → must not crash, must log."""
    with patch(
        "jarvis.notify.subprocess.run",
        side_effect=FileNotFoundError("osascript"),
    ):
        with caplog.at_level("WARNING", logger="jarvis.notify"):
            notify.notify("Jarvis", "ok")
    assert any(
        rec.levelname == "WARNING" and rec.name == "jarvis.notify"
        for rec in caplog.records
    )


def test_notify_swallows_timeout_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch(
        "jarvis.notify.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=5),
    ):
        with caplog.at_level("WARNING", logger="jarvis.notify"):
            notify.notify("Jarvis", "ok")
    assert any(
        rec.levelname == "WARNING" for rec in caplog.records
    )


def test_notify_swallows_oserror() -> None:
    """Any other OSError (e.g. PermissionError) is swallowed too."""
    with patch(
        "jarvis.notify.subprocess.run",
        side_effect=OSError("permission denied"),
    ):
        notify.notify("Jarvis", "ok")  # must not raise


# -- tri-state gating + failure-always-fires invariant ----------------------


@pytest.mark.regression
def test_failure_status_always_fires_regardless_of_notify_on() -> None:
    """B5: failure notifications bypass notify_on entirely. Otherwise the
    user has no signal that auth expired or budget overran."""
    for value in ("always", "if_drafts", "never"):
        assert notify.should_notify(
            value, drafts_count=0, run_status="failure"
        ) is True


def test_always_fires_on_success_with_zero_drafts() -> None:
    assert notify.should_notify(
        "always", drafts_count=0, run_status="success"
    ) is True


def test_always_fires_on_partial() -> None:
    assert notify.should_notify(
        "always", drafts_count=4, run_status="partial"
    ) is True


def test_never_silences_success() -> None:
    assert notify.should_notify(
        "never", drafts_count=5, run_status="success"
    ) is False


def test_never_silences_partial() -> None:
    assert notify.should_notify(
        "never", drafts_count=2, run_status="partial"
    ) is False


def test_if_drafts_silent_when_zero_drafts() -> None:
    assert notify.should_notify(
        "if_drafts", drafts_count=0, run_status="success"
    ) is False


def test_if_drafts_fires_when_drafts_present() -> None:
    assert notify.should_notify(
        "if_drafts", drafts_count=1, run_status="success"
    ) is True


def test_if_drafts_fires_on_partial_with_drafts() -> None:
    assert notify.should_notify(
        "if_drafts", drafts_count=4, run_status="partial"
    ) is True


def test_invalid_notify_on_raises() -> None:
    with pytest.raises(ValueError) as exc_info:
        notify.should_notify(
            "maybe", drafts_count=0, run_status="success"
        )
    assert "maybe" in str(exc_info.value)
