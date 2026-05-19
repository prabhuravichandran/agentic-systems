"""Jarvis P0 config loader.

Single YAML file at ~/.config/jarvis/config.yaml, loaded into a Config
dataclass via from_dict. Validation is hand-written — for one config file
with ~15 fields, the savings of a schema library don't justify the dep.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ALLOWED_NOTIFY_ON = ("always", "if_drafts", "never")


class ConfigError(Exception):
    """Raised for any malformed config; message names the offending field."""


@dataclass(frozen=True)
class AccountConfig:
    email: str


@dataclass(frozen=True)
class ScheduleConfig:
    hour: int
    weekdays_only: bool


@dataclass(frozen=True)
class LimitsConfig:
    max_drafts_per_run: int
    budget_usd_per_run: float
    max_thread_tokens: int
    max_email_bytes: int


@dataclass(frozen=True)
class ExclusionsConfig:
    skip_mailing_lists: bool
    skip_noreply: bool
    skip_sender_domains: tuple[str, ...]
    skip_gmail_categories: tuple[str, ...]
    skip_labels: tuple[str, ...]


@dataclass(frozen=True)
class ModelsConfig:
    classifier: str
    drafter: str


@dataclass(frozen=True)
class NotificationsConfig:
    notify_on: str


@dataclass(frozen=True)
class StorageConfig:
    db_path: str
    retention_days: int
    trace_body_chars: int


@dataclass(frozen=True)
class Config:
    account: AccountConfig
    schedule: ScheduleConfig
    limits: LimitsConfig
    exclusions: ExclusionsConfig
    models: ModelsConfig
    notifications: NotificationsConfig
    storage: StorageConfig


def _section(parent: dict[str, Any], name: str, prefix: str) -> dict[str, Any]:
    dotted = f"{prefix}.{name}" if prefix else name
    if name not in parent:
        raise ConfigError(f"missing required section '{dotted}'")
    value = parent[name]
    if not isinstance(value, dict):
        raise ConfigError(
            f"invalid value for '{dotted}': expected mapping, "
            f"got {type(value).__name__}"
        )
    return value


def _field(parent: dict[str, Any], name: str, expected: type, prefix: str) -> Any:
    dotted = f"{prefix}.{name}"
    if name not in parent:
        raise ConfigError(f"missing required field '{dotted}'")
    value = parent[name]
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        # YAML treats 1.00 as int; accept int where float is expected
        return float(value)
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ConfigError(
            f"invalid value for '{dotted}': expected {expected.__name__}, "
            f"got {type(value).__name__}"
        )
    return value


def _string_list(parent: dict[str, Any], name: str, prefix: str) -> tuple[str, ...]:
    dotted = f"{prefix}.{name}"
    if name not in parent:
        raise ConfigError(f"missing required field '{dotted}'")
    value = parent[name]
    if not isinstance(value, list):
        raise ConfigError(
            f"invalid value for '{dotted}': expected list, got {type(value).__name__}"
        )
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(
                f"invalid value for '{dotted}[{i}]': expected str, "
                f"got {type(item).__name__}"
            )
    return tuple(value)


def from_dict(data: dict[str, Any]) -> Config:
    if not isinstance(data, dict):
        raise ConfigError(
            f"top-level config must be a mapping, got {type(data).__name__}"
        )

    account_d = _section(data, "account", "")
    account = AccountConfig(email=_field(account_d, "email", str, "account"))

    schedule_d = _section(data, "schedule", "")
    schedule = ScheduleConfig(
        hour=_field(schedule_d, "hour", int, "schedule"),
        weekdays_only=_field(schedule_d, "weekdays_only", bool, "schedule"),
    )
    if not 0 <= schedule.hour <= 23:
        raise ConfigError(
            f"invalid value for 'schedule.hour': must be 0-23, got {schedule.hour}"
        )

    limits_d = _section(data, "limits", "")
    limits = LimitsConfig(
        max_drafts_per_run=_field(limits_d, "max_drafts_per_run", int, "limits"),
        budget_usd_per_run=_field(limits_d, "budget_usd_per_run", float, "limits"),
        max_thread_tokens=_field(limits_d, "max_thread_tokens", int, "limits"),
        max_email_bytes=_field(limits_d, "max_email_bytes", int, "limits"),
    )

    excl_d = _section(data, "exclusions", "")
    # canonicalise sender domains to lower-case once at load time, so filters
    # can match case-insensitively without re-normalising on every message.
    raw_domains = _string_list(excl_d, "skip_sender_domains", "exclusions")
    exclusions = ExclusionsConfig(
        skip_mailing_lists=_field(excl_d, "skip_mailing_lists", bool, "exclusions"),
        skip_noreply=_field(excl_d, "skip_noreply", bool, "exclusions"),
        skip_sender_domains=tuple(d.lower() for d in raw_domains),
        skip_gmail_categories=_string_list(
            excl_d, "skip_gmail_categories", "exclusions"
        ),
        skip_labels=_string_list(excl_d, "skip_labels", "exclusions"),
    )

    models_d = _section(data, "models", "")
    models = ModelsConfig(
        classifier=_field(models_d, "classifier", str, "models"),
        drafter=_field(models_d, "drafter", str, "models"),
    )

    notif_d = _section(data, "notifications", "")
    notif = NotificationsConfig(
        notify_on=_field(notif_d, "notify_on", str, "notifications"),
    )
    if notif.notify_on not in ALLOWED_NOTIFY_ON:
        raise ConfigError(
            f"invalid value for 'notifications.notify_on': "
            f"expected one of {ALLOWED_NOTIFY_ON}, got {notif.notify_on!r}"
        )

    storage_d = _section(data, "storage", "")
    storage = StorageConfig(
        db_path=_field(storage_d, "db_path", str, "storage"),
        retention_days=_field(storage_d, "retention_days", int, "storage"),
        trace_body_chars=_field(storage_d, "trace_body_chars", int, "storage"),
    )

    return Config(
        account=account,
        schedule=schedule,
        limits=limits,
        exclusions=exclusions,
        models=models,
        notifications=notif,
        storage=storage,
    )


def load(path: Path) -> Config:
    try:
        raw = path.read_text()
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ConfigError(f"config YAML parse error in {path}: {e}") from e
    return from_dict(data or {})
