"""Tests for jarvis.config — loading, validation, field-named errors."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jarvis import config
from jarvis.config import Config, ConfigError, from_dict

pytestmark = pytest.mark.unit


EXAMPLE_PATH = Path(__file__).parent.parent.parent / "src" / "jarvis" / "config.example.yaml"


def _good() -> dict:
    """A fresh, valid config dict — tests mutate copies of this."""
    return {
        "account": {"email": "alice@example.com"},
        "schedule": {"hour": 8, "weekdays_only": True},
        "limits": {
            "max_drafts_per_run": 8,
            "budget_usd_per_run": 1.00,
            "max_thread_tokens": 20000,
            "max_email_bytes": 1048576,
        },
        "exclusions": {
            "skip_mailing_lists": True,
            "skip_noreply": True,
            "skip_sender_domains": [],
            "skip_gmail_categories": ["PROMOTIONS", "SOCIAL", "SPAM"],
            "skip_labels": [],
        },
        "models": {
            "classifier": "claude-haiku-4-5-20251001",
            "drafter": "claude-sonnet-4-6",
        },
        "notifications": {"notify_on": "if_drafts"},
        "storage": {
            "db_path": "~/.local/share/jarvis/jarvis.sqlite",
            "retention_days": 7,
            "trace_body_chars": 500,
        },
    }


def test_shipped_example_loads_cleanly() -> None:
    """The example YAML in src/jarvis/ must always parse — it's the user template."""
    cfg = config.load(EXAMPLE_PATH)
    assert isinstance(cfg, Config)
    assert cfg.account.email
    assert cfg.notifications.notify_on in config.ALLOWED_NOTIFY_ON


def test_happy_path_from_dict() -> None:
    cfg = from_dict(_good())
    assert cfg.account.email == "alice@example.com"
    assert cfg.schedule.hour == 8
    assert cfg.schedule.weekdays_only is True
    assert cfg.limits.max_drafts_per_run == 8
    assert cfg.limits.budget_usd_per_run == pytest.approx(1.0)
    assert cfg.exclusions.skip_gmail_categories == ("PROMOTIONS", "SOCIAL", "SPAM")
    assert cfg.exclusions.skip_sender_domains == ()
    assert cfg.notifications.notify_on == "if_drafts"


def test_int_accepted_where_float_expected() -> None:
    """YAML 1 vs 1.0 — accept int for float fields."""
    data = _good()
    data["limits"]["budget_usd_per_run"] = 2
    cfg = from_dict(data)
    assert cfg.limits.budget_usd_per_run == pytest.approx(2.0)


def test_missing_section_names_section() -> None:
    data = _good()
    del data["limits"]
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    assert "limits" in str(exc_info.value)


def test_missing_field_names_dotted_path() -> None:
    data = _good()
    del data["limits"]["max_drafts_per_run"]
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    assert "limits.max_drafts_per_run" in str(exc_info.value)


def test_wrong_type_names_field_and_expected_type() -> None:
    data = _good()
    data["limits"]["max_drafts_per_run"] = "not an int"
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    msg = str(exc_info.value)
    assert "limits.max_drafts_per_run" in msg
    assert "int" in msg
    assert "str" in msg


def test_bool_not_accepted_as_int() -> None:
    """Python's True == 1 — config validation must reject it explicitly."""
    data = _good()
    data["limits"]["max_drafts_per_run"] = True
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    assert "limits.max_drafts_per_run" in str(exc_info.value)


def test_skip_sender_domains_must_be_strings() -> None:
    data = _good()
    data["exclusions"]["skip_sender_domains"] = ["chase.com", 123]
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    msg = str(exc_info.value)
    assert "exclusions.skip_sender_domains[1]" in msg
    assert "str" in msg


@pytest.mark.regression
def test_skip_sender_domains_lowercased_at_load() -> None:
    """Bug 1: user writing `Chase.com` in YAML must still match lowercase
    domains from incoming messages. Canonicalisation happens once at load."""
    data = _good()
    data["exclusions"]["skip_sender_domains"] = ["Chase.com", "IRS.GOV"]
    cfg = from_dict(data)
    assert cfg.exclusions.skip_sender_domains == ("chase.com", "irs.gov")


def test_invalid_notify_on_value_rejected() -> None:
    data = _good()
    data["notifications"]["notify_on"] = "maybe"
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    msg = str(exc_info.value)
    assert "notifications.notify_on" in msg
    assert "maybe" in msg


def test_all_notify_on_values_accepted() -> None:
    for value in ("always", "if_drafts", "never"):
        data = _good()
        data["notifications"]["notify_on"] = value
        cfg = from_dict(data)
        assert cfg.notifications.notify_on == value


def test_schedule_hour_out_of_range_rejected() -> None:
    data = _good()
    data["schedule"]["hour"] = 24
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    assert "schedule.hour" in str(exc_info.value)


def test_section_must_be_mapping_not_list() -> None:
    data = _good()
    data["account"] = ["alice@example.com"]
    with pytest.raises(ConfigError) as exc_info:
        from_dict(data)
    msg = str(exc_info.value)
    assert "account" in msg
    assert "mapping" in msg


def test_load_missing_file_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError) as exc_info:
        config.load(missing)
    assert str(missing) in str(exc_info.value)


def test_load_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("account: {email: alice\n  schedule:\n")  # unterminated
    with pytest.raises(ConfigError) as exc_info:
        config.load(bad)
    assert "YAML" in str(exc_info.value) or "parse" in str(exc_info.value)


def test_config_is_frozen() -> None:
    cfg = from_dict(_good())
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        cfg.account.email = "mallory@example.com"  # type: ignore[misc]


def test_example_yaml_round_trips_via_from_dict() -> None:
    """Loading the example via load() should match parsing+from_dict directly."""
    via_load = config.load(EXAMPLE_PATH)
    via_dict = from_dict(yaml.safe_load(EXAMPLE_PATH.read_text()))
    assert via_load == via_dict
