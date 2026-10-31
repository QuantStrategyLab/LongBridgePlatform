"""Characterize plugin alert settings through the public settings loader."""

import json
import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_config_support as config


ALERT_FIELDS = (
    "channels",
    "email_recipients",
    "email_sender_email",
    "email_sender_password",
    "email_smtp_host",
    "email_smtp_port",
    "email_smtp_security",
    "sms_recipients",
    "sms_provider",
    "sms_account_id",
    "sms_auth_token",
    "sms_sender",
    "sms_messaging_service_id",
    "sms_api_base_url",
    "sms_body_max_chars",
    "push_recipients",
    "push_provider",
    "push_app_token",
    "push_access_token",
    "push_api_base_url",
    "push_device",
    "push_priority",
    "push_tags",
    "push_body_max_chars",
    "telegram_chat_ids",
    "telegram_bot_token",
    "telegram_api_base_url",
    "telegram_parse_mode",
    "telegram_disable_web_page_preview",
    "telegram_body_max_chars",
)
LIST_FIELDS = frozenset(
    {"channels", "email_recipients", "sms_recipients", "push_recipients", "telegram_chat_ids"}
)
SCALAR_FIELDS = tuple(field for field in ALERT_FIELDS if field not in LIST_FIELDS)


@pytest.fixture(autouse=True)
def isolated_environment():
    target = {
        "platform_id": "longbridge",
        "strategy_profile": "soxl_soxx_trend_income",
        "execution_mode": "paper",
        "dry_run_only": True,
    }
    with patch.dict(os.environ, {"RUNTIME_TARGET_JSON": json.dumps(target)}, clear=True):
        yield


def _env_name(field):
    return f"STRATEGY_PLUGIN_ALERT_{field.upper()}"


def _alert_values(settings):
    return {field: getattr(settings, f"strategy_plugin_alert_{field}") for field in ALERT_FIELDS}


def _load():
    return config.load_platform_runtime_settings(project_id_resolver=lambda: "test-project")


def _trace_reads(monkeypatch, events, *, split_error=None, read_error=None):
    original_getenv = os.getenv
    original_split = config.resolve_split_env_list

    def getenv(name, default=None):
        if "STRATEGY_PLUGIN_" in name:
            events.append(("get", name))
        if read_error is not None and name == read_error[0]:
            raise read_error[1]
        return original_getenv(name, default)

    def split(name, *args, **kwargs):
        events.append(("split", name))
        if split_error is not None and name == split_error[0]:
            raise split_error[1]
        return original_split(name, *args, **kwargs)

    monkeypatch.setattr(os, "getenv", getenv)
    monkeypatch.setattr(config, "resolve_split_env_list", split)


def _expected_reads():
    events = [
        ("get", "LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON"),
        ("get", "QSL_STRATEGY_PLUGIN_MOUNTS_JSON"),
        ("get", "STRATEGY_PLUGIN_MOUNTS_JSON"),
    ]
    for field in ALERT_FIELDS:
        name = _env_name(field)
        if field in LIST_FIELDS:
            events.append(("split", name))
        if field in LIST_FIELDS or field == "telegram_bot_token":
            events.append(("get", f"QSL_{name}"))
        events.append(("get", name))
    return events


def test_defaults_keep_public_frozen_settings_type():
    settings = _load()

    assert type(settings) is config.PlatformRuntimeSettings
    assert _alert_values(settings) == {
        field: () if field in LIST_FIELDS else None for field in ALERT_FIELDS
    }
    with pytest.raises(FrozenInstanceError):
        settings.strategy_plugin_alert_channels = ("synthetic",)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), (" \t\n", None), (" value \n", "value"),
     ("0", "0"), ("false", "false"), ("not-a-number", "not-a-number")],
)
def test_scalar_values_remain_optional_trimmed_strings(monkeypatch, raw, expected):
    if raw is not None:
        for field in SCALAR_FIELDS:
            monkeypatch.setenv(_env_name(field), raw)

    values = _alert_values(_load())

    assert {field: values[field] for field in SCALAR_FIELDS} == {
        field: expected for field in SCALAR_FIELDS
    }


def test_each_environment_variable_reaches_its_own_field(monkeypatch):
    expected = {}
    for field in ALERT_FIELDS:
        if field in LIST_FIELDS:
            monkeypatch.setenv(_env_name(field), f" {field}-one ;{field}-two\n{field}-one ")
            expected[field] = (f"{field}-one", f"{field}-two")
        else:
            monkeypatch.setenv(_env_name(field), f" synthetic-{field} \n")
            expected[field] = f"synthetic-{field}"

    assert _alert_values(_load()) == expected


@pytest.mark.parametrize(
    ("prefixed", "expected"),
    [(None, ("fallback", "second")), ("", ("fallback", "second")),
     (" preferred;next\npreferred ", ("preferred", "next")), (" \t\n", ())],
)
def test_list_prefix_precedence_and_order(monkeypatch, prefixed, expected):
    for field in LIST_FIELDS:
        name = _env_name(field)
        monkeypatch.setenv(name, "fallback;second\nfallback")
        if prefixed is not None:
            monkeypatch.setenv(f"QSL_{name}", prefixed)

    values = _alert_values(_load())

    assert {field: values[field] for field in LIST_FIELDS} == {
        field: expected for field in LIST_FIELDS
    }


def test_list_items_keep_case_and_literal_values(monkeypatch):
    for field in LIST_FIELDS:
        monkeypatch.setenv(_env_name(field), " One ; one,,0\nfalse;One ")

    values = _alert_values(_load())

    assert all(values[field] == ("One", "one", "0", "false") for field in LIST_FIELDS)


def test_scalar_prefixes_other_than_telegram_bot_token_are_not_aliases(monkeypatch):
    fields = tuple(field for field in SCALAR_FIELDS if field != "telegram_bot_token")
    for field in fields:
        monkeypatch.setenv(f"QSL_{_env_name(field)}", "synthetic-prefixed-value")

    values = _alert_values(_load())

    assert all(values[field] is None for field in fields)


@pytest.mark.parametrize(
    ("prefixed", "expected"),
    [(None, "synthetic-fallback"), ("", "synthetic-fallback"),
     (" synthetic-preferred \n", "synthetic-preferred"), (" \t\n", None)],
)
def test_telegram_bot_token_prefix_precedence(monkeypatch, prefixed, expected):
    name = _env_name("telegram_bot_token")
    monkeypatch.setenv(name, " synthetic-fallback ")
    if prefixed is not None:
        monkeypatch.setenv(f"QSL_{name}", prefixed)

    assert _load().strategy_plugin_alert_telegram_bot_token == expected


def test_each_load_reads_environment_after_project_resolution(monkeypatch):
    values = iter(("first", "second"))

    def resolve_project():
        value = next(values)
        monkeypatch.setenv(_env_name("push_priority"), f" {value} ")
        return f"test-project-{value}"

    first = config.load_platform_runtime_settings(project_id_resolver=resolve_project)
    second = config.load_platform_runtime_settings(project_id_resolver=resolve_project)

    assert (first.project_id, first.strategy_plugin_alert_push_priority) == ("test-project-first", "first")
    assert (second.project_id, second.strategy_plugin_alert_push_priority) == ("test-project-second", "second")


def test_reads_finish_in_order_before_settings_construction(monkeypatch):
    events = []
    _trace_reads(monkeypatch, events)
    settings_type = config.PlatformRuntimeSettings

    def resolve_project():
        events.append(("resolve", "project"))
        return "test-project"

    def construct(**kwargs):
        events.append(("construct", "settings"))
        assert tuple(kwargs)[-33:] == (
            *(f"strategy_plugin_alert_{field}" for field in ALERT_FIELDS),
            "runtime_target", "trusted_runtime_risk_policy", "strategy_metadata",
        )
        return settings_type(**kwargs)

    monkeypatch.setattr(config, "PlatformRuntimeSettings", construct)

    settings = config.load_platform_runtime_settings(project_id_resolver=resolve_project)

    assert type(settings) is settings_type
    assert events == [("resolve", "project"), *_expected_reads(), ("construct", "settings")]


def test_project_resolution_failure_prevents_plugin_reads(monkeypatch):
    events = []
    _trace_reads(monkeypatch, events)
    error = RuntimeError("synthetic project resolution failure")

    def fail():
        raise error

    with patch.object(config, "PlatformRuntimeSettings") as constructor:
        with pytest.raises(RuntimeError) as raised:
            config.load_platform_runtime_settings(project_id_resolver=fail)

    assert raised.value is error
    assert events == []
    constructor.assert_not_called()


@pytest.mark.parametrize("field", ["channels", "sms_recipients", "telegram_chat_ids"])
def test_split_failure_preserves_error_and_stops_later_reads(monkeypatch, field):
    events = []
    name = _env_name(field)
    error = ValueError(f"synthetic invalid list: {field}")
    _trace_reads(monkeypatch, events, split_error=(name, error))

    with patch.object(config, "PlatformRuntimeSettings") as constructor:
        with pytest.raises(ValueError) as raised:
            _load()

    expected = _expected_reads()
    assert raised.value is error
    assert events == expected[:expected.index(("split", name)) + 1]
    constructor.assert_not_called()


def test_environment_read_failure_is_not_swallowed(monkeypatch):
    events = []
    name = _env_name("telegram_parse_mode")
    error = OSError("synthetic environment read failure")
    _trace_reads(monkeypatch, events, read_error=(name, error))

    with patch.object(config, "PlatformRuntimeSettings") as constructor:
        with pytest.raises(OSError) as raised:
            _load()

    expected = _expected_reads()
    assert raised.value is error
    assert events == expected[:expected.index(("get", name)) + 1]
    constructor.assert_not_called()
