"""Offline ticks must never become broker, clock, state or scheduler writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest

from application.plain_dca_paper_adapter import PlainDcaPaperError
from application.plain_dca_paper_scheduler import scheduled_plain_dca_preview

pytest_plugins = ("test_plain_dca_dependency",)

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def synthetic_paper_manifest(monkeypatch):
    paper = SimpleNamespace(id="paper", environment="longbridge-paper", mode="paper",
                            service="fake-paper-service")
    monkeypatch.setattr("application.plain_dca_paper_adapter.load_runtime_target_manifest",
                        lambda: SimpleNamespace(targets=(paper,)))


def readback():
    service = "fake-paper-service"
    return {
        "observed_at": NOW, "valid_until": NOW+timedelta(minutes=1),
        "target_id": "paper", "environment": "longbridge-paper",
        "live_enabled": False, "kelly_enabled": False, "stop_loss_enabled": False,
        "protected_env": {
            "CLOUD_RUN_SERVICE": service, "RUNTIME_TARGET_ENABLED": "false",
            "LONGBRIDGE_DRY_RUN_ONLY": "false", "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "fake-paper-account",
            "RUNTIME_TARGET_JSON": json.dumps({
                "platform_id": "longbridge", "service_name": service,
                "execution_environment": "paper", "execution_mode": "live", "dry_run_only": False,
                "account_scope": "PAPER", "account_selector": ["PAPER"], "deployment_selector": "PAPER",
            }),
        },
    }


@pytest.mark.parametrize("flag", [{}, {"enabled": False}])
def test_disabled_tick_does_not_read_bindings_or_facts(flag):
    binding, inputs = Mock(), Mock()
    assert scheduled_plain_dca_preview(read_binding=binding, read_inputs=inputs, now=NOW, **flag) is None
    binding.assert_not_called()
    inputs.assert_not_called()


@pytest.mark.parametrize("flag", [None, 0, 1, "false", "true"])
def test_malformed_flag_cannot_enable_tick(flag):
    binding, inputs = Mock(), Mock()
    with pytest.raises(PlainDcaPaperError, match="scheduler_flag_invalid"):
        scheduled_plain_dca_preview(enabled=flag, read_binding=binding, read_inputs=inputs, now=NOW)
    binding.assert_not_called()
    inputs.assert_not_called()


@pytest.mark.parametrize("patch", [
    {"environment": "longbridge-sg"}, {"target_id": "sg"},
    {"live_enabled": True}, {"kelly_enabled": None}, {"stop_loss_enabled": True},
    {"observed_at": NOW-timedelta(minutes=6)},
])
def test_binding_failure_prevents_fact_reads(patch):
    inputs = Mock()
    with pytest.raises(PlainDcaPaperError):
        scheduled_plain_dca_preview(enabled=True, read_binding=lambda: {**readback(), **patch},
                                    read_inputs=inputs, now=NOW)
    inputs.assert_not_called()


def test_enabled_offline_tick_rechecks_binding_after_fact_read(monkeypatch):
    from application import plain_dca_paper_adapter as adapter
    from test_plain_dca_paper_adapter import Preview

    planner = Mock(return_value=Preview())
    original = adapter.importlib.import_module
    api = SimpleNamespace(evaluate_dca_period=planner, PeriodPreview=Preview)
    monkeypatch.setattr(adapter.importlib, "import_module", lambda name: api if name == "us_equity_strategies.plain_dca" else original(name))
    binding = Mock(return_value=readback())
    plan = SimpleNamespace(mandate=SimpleNamespace(binding=SimpleNamespace(
        account_ref="fake-paper-account", environment="paper", profile="nasdaq100_buy_and_hold")))
    values = dict(plan=plan, calendar=None, period=None, facts=None, quote=None, capabilities=None)
    inputs = Mock(return_value=values)
    result = scheduled_plain_dca_preview(enabled=True, read_binding=binding, read_inputs=inputs, now=NOW)
    assert result.no_order and result.no_execute and result.preview_only and not result.live_ready
    assert binding.call_count == 2
    inputs.assert_called_once_with()
    planner.assert_called_once_with(**values, now=NOW)
    # A changed protected binding after inputs were fetched blocks the planner.
    planner.reset_mock()
    changed = deepcopy(readback())
    changed["protected_env"]["RUNTIME_TARGET_ENABLED"] = "true"
    binding = Mock(side_effect=[readback(), changed])
    with pytest.raises(PlainDcaPaperError):
        scheduled_plain_dca_preview(enabled=True, read_binding=binding, read_inputs=inputs, now=NOW)
    planner.assert_not_called()


def test_reader_failures_never_echo_values():
    with pytest.raises(PlainDcaPaperError, match="^paper_binding_readback_unavailable$"):
        scheduled_plain_dca_preview(enabled=True, read_binding=Mock(side_effect=RuntimeError("fake-private")),
                                    read_inputs=Mock(), now=NOW)
    with pytest.raises(PlainDcaPaperError, match="^plain_dca_scheduler_inputs_unavailable$"):
        scheduled_plain_dca_preview(enabled=True, read_binding=readback,
                                    read_inputs=Mock(side_effect=RuntimeError("fake-private")), now=NOW)


def test_tick_consumes_real_installed_preview_without_changing_frozen_inputs(installed_dca_case):
    from us_equity_strategies.plain_dca import evaluate_dca_period

    now = installed_dca_case["now"]
    protected = readback()
    protected.update(observed_at=now, valid_until=now+timedelta(minutes=1))
    values = {k: v for k, v in installed_dca_case.items() if k != "now"}
    binding, inputs = Mock(return_value=protected), Mock(return_value=values)
    expected = evaluate_dca_period(**installed_dca_case)
    result = scheduled_plain_dca_preview(enabled=True, read_binding=binding, read_inputs=inputs, now=now)
    assert result == expected
    assert result.status == "DUE" and result.new_tranche == installed_dca_case["plan"].final_tranche
    assert result.no_order and result.no_execute and result.preview_only and not result.live_ready
    assert binding.call_count == 2
    assert scheduled_plain_dca_preview(enabled=True, read_binding=binding, read_inputs=inputs, now=now) == expected
