"""Fake-only Phase 1 tests. The real UES contract is independently qualified."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib
import json
from types import SimpleNamespace

import pytest

from application.plain_dca_paper_adapter import (
    PlainDcaPaperError, check_plain_dca_paper_binding, consume_plain_dca_preview,
)


NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Preview:
    no_order: bool = True
    no_execute: bool = True
    preview_only: bool = True
    live_ready: bool = False


def readback():
    return {
        "observed_at": NOW, "valid_until": NOW + timedelta(minutes=1),
        "target_id": "paper", "environment": "longbridge-paper",
        "live_enabled": False, "kelly_enabled": False, "stop_loss_enabled": False,
        "protected_env": {
            "CLOUD_RUN_SERVICE": "longbridge-quant-paper-service",
            "RUNTIME_TARGET_ENABLED": "false", "LONGBRIDGE_DRY_RUN_ONLY": "false",
            "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "fake-paper-account",
            "RUNTIME_TARGET_JSON": json.dumps({
                "platform_id": "longbridge", "service_name": "longbridge-quant-paper-service",
                "execution_environment": "paper", "dry_run_only": False,
                "execution_mode": "live", "account_scope": "PAPER",
                "account_selector": ["PAPER"], "deployment_selector": "PAPER",
            }),
        },
    }


def inputs():
    binding = SimpleNamespace(environment="paper", account_ref="fake-paper-account",
                              profile="nasdaq100_buy_and_hold")
    return dict(plan=SimpleNamespace(mandate=SimpleNamespace(binding=binding)),
                calendar=None, period=None, facts=None, quote=None, capabilities=None,
                now=NOW)


@pytest.fixture
def planner(monkeypatch):
    calls = []

    def evaluate(**kwargs):
        calls.append(kwargs)
        return Preview()

    api = SimpleNamespace(evaluate_dca_period=evaluate, PeriodPreview=Preview)
    original = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", lambda name: api if name ==
                        "us_equity_strategies.plain_dca" else original(name))
    return calls, api


@pytest.mark.parametrize("environment", ["longbridge-sg", "longbridge-hk", "", None,
                                         "longbridge-paper ", "LONGBRIDGE-PAPER"])
def test_reject_non_paper_environment_before_planner(environment, planner):
    value = readback()
    value["environment"] = environment
    with pytest.raises(PlainDcaPaperError):
        consume_plain_dca_preview(read_binding=lambda: value, **inputs())
    assert planner[0] == []


@pytest.mark.parametrize("target", ["sg", "hk", "longbridge-sg", "", None])
def test_reject_other_resolved_targets(target):
    value = readback()
    value["target_id"] = target
    with pytest.raises(PlainDcaPaperError):
        check_plain_dca_paper_binding(value, now=NOW)


def test_exact_paper_binding_passes():
    assert check_plain_dca_paper_binding(readback(), now=NOW) is None


@pytest.mark.parametrize("name", ["QSL_RUNTIME_TARGET_ENABLED", "QSL_LONGBRIDGE_DRY_RUN_ONLY"])
@pytest.mark.parametrize("value", ["true", None, "", 0])
def test_conflicting_or_unresolved_runtime_alias_blocks(name, value):
    binding = readback()
    binding["protected_env"][name] = value
    with pytest.raises(PlainDcaPaperError):
        check_plain_dca_paper_binding(binding, now=NOW)


def test_identical_aliases_pass_and_conflicting_target_alias_blocks():
    binding = readback()
    env = binding["protected_env"]
    env.update(QSL_RUNTIME_TARGET_ENABLED="false", QSL_LONGBRIDGE_DRY_RUN_ONLY="false",
               QSL_RUNTIME_TARGET_JSON=env["RUNTIME_TARGET_JSON"])
    assert check_plain_dca_paper_binding(binding, now=NOW) is None
    target = json.loads(env["RUNTIME_TARGET_JSON"])
    target["execution_environment"] = "live"
    env["QSL_RUNTIME_TARGET_JSON"] = json.dumps(target)
    with pytest.raises(PlainDcaPaperError):
        check_plain_dca_paper_binding(binding, now=NOW)


@pytest.mark.parametrize("field", ["live_enabled", "kelly_enabled", "stop_loss_enabled"])
@pytest.mark.parametrize("state", [True, None, "false", 0])
def test_unknown_or_enabled_safety_blocks(field, state):
    value = readback()
    value[field] = state
    with pytest.raises(PlainDcaPaperError):
        check_plain_dca_paper_binding(value, now=NOW)


@pytest.mark.parametrize("patch", [
    {"execution_environment": "live"}, {"execution_environment": None},
    {"account_scope": "SG"}, {"account_selector": ["PAPER", "SG"]},
    {"deployment_selector": "SG"}, {"platform_id": "fake-other"},
    {"service_name": "fake-other-service"}, {"dry_run_only": True},
])
def test_protected_target_mismatch_blocks(patch):
    value = readback()
    target = json.loads(value["protected_env"]["RUNTIME_TARGET_JSON"])
    target.update(patch)
    value["protected_env"]["RUNTIME_TARGET_JSON"] = json.dumps(target)
    with pytest.raises(PlainDcaPaperError):
        check_plain_dca_paper_binding(value, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("RUNTIME_TARGET_JSON", "{fake-private-value"),
    ("RUNTIME_TARGET_ENABLED", "true"), ("RUNTIME_TARGET_ENABLED", ""),
    ("LONGBRIDGE_DRY_RUN_ONLY", ""), ("CLOUD_RUN_SERVICE", "fake-other-service"),
    ("LONGBRIDGE_PHYSICAL_ACCOUNT_ID", "PAPER"),
])
def test_invalid_protected_values_fail_without_echo(field, value, capsys):
    snapshot = readback()
    snapshot["protected_env"][field] = value
    with pytest.raises(PlainDcaPaperError) as exc:
        check_plain_dca_paper_binding(snapshot, now=NOW)
    assert "fake-private-value" not in str(exc.value)
    assert capsys.readouterr() == ("", "")


def test_consumer_passes_inputs_and_preserves_preview(planner):
    args = inputs()
    assert consume_plain_dca_preview(read_binding=readback, **args) == Preview()
    assert planner[0] == [args]


def test_each_call_rereads_binding_across_target_change_and_restart(planner):
    value = readback()

    def reader():
        return deepcopy(value)

    consume_plain_dca_preview(read_binding=reader, **inputs())
    value["environment"] = "longbridge-sg"
    for current_reader in (reader, lambda: deepcopy(value)):
        with pytest.raises(PlainDcaPaperError):
            consume_plain_dca_preview(read_binding=current_reader, **inputs())
    assert len(planner[0]) == 1


def test_reader_failure_is_static_and_never_calls_planner(planner, capsys):
    def unavailable():
        raise OSError("fake-private-value")

    with pytest.raises(PlainDcaPaperError) as exc:
        consume_plain_dca_preview(read_binding=unavailable, **inputs())
    assert str(exc.value) == "paper_binding_readback_unavailable"
    assert planner[0] == []
    assert capsys.readouterr() == ("", "")


def test_plan_cannot_use_another_account_or_offline_binding(planner):
    for field, value in (("account_ref", "fake-other-account"),
                         ("environment", "offline_preview"), ("profile", "fake-other")):
        args = inputs()
        setattr(args["plan"].mandate.binding, field, value)
        with pytest.raises(PlainDcaPaperError):
            consume_plain_dca_preview(read_binding=readback, **args)
    assert planner[0] == []


def test_missing_installed_ues_api_blocks(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError("fake-private-value")

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(PlainDcaPaperError, match="^plain_dca_api_unavailable$"):
        consume_plain_dca_preview(read_binding=readback, **inputs())


def test_unsafe_planner_result_is_not_forwarded(planner):
    planner[1].evaluate_dca_period = lambda **kwargs: Preview(no_execute=False)
    with pytest.raises(PlainDcaPaperError, match="^plain_dca_preview_contract_invalid$"):
        consume_plain_dca_preview(read_binding=readback, **inputs())


@pytest.mark.parametrize("patch", [
    {"observed_at": NOW - timedelta(minutes=6)},
    {"observed_at": NOW + timedelta(seconds=1)}, {"valid_until": NOW},
    {"observed_at": None}, {"observed_at": NOW.replace(tzinfo=None)},
])
def test_stale_future_or_unknown_readback_blocks(patch, planner):
    value = readback()
    value.update(patch)
    with pytest.raises(PlainDcaPaperError, match="^paper_binding_readback_stale$"):
        consume_plain_dca_preview(read_binding=lambda: value, **inputs())
    assert planner[0] == []
