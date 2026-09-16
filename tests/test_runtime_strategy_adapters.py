import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from unittest.mock import patch

from quant_platform_kit.common.runtime_target import build_runtime_target
from runtime_config_support import PlatformRuntimeSettings
from strategy_runtime import LoadedStrategyRuntime

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HK_STRATEGIES_SRC = ROOT.parent / "HkEquityStrategies" / "src"
if str(HK_STRATEGIES_SRC) not in sys.path:
    sys.path.insert(0, str(HK_STRATEGIES_SRC))

from application.runtime_strategy_adapters import build_runtime_strategy_adapters


V7_PROFILE = "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve"


def test_runtime_strategy_adapters_build_market_history_inputs():
    observed = {}

    class FakeBrokerAdapters:
        def build_market_data_port(self, quote_context):
            observed["market_data_port_context"] = quote_context
            return "market-data-port"

        def build_market_history_loader(self, market_data_port):
            observed["market_history_loader_port"] = market_data_port
            return "market-history-loader"

        def build_price_history(self, market_data_port, symbol):
            observed.setdefault("price_history_calls", []).append((market_data_port, symbol))
            return [{"close": 1.0}]

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={"trend_ma_window": 150},
        available_inputs=("market_history", "benchmark_history", "qqq_history"),
        benchmark_symbol="QQQ",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **_kwargs: key,
        broker_adapters=FakeBrokerAdapters(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(AssertionError("unexpected fallback")),
        build_strategy_evaluation_inputs_fn=lambda **_kwargs: {},
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
    )

    result = adapters.calculate_strategy_indicators("quote-context")

    assert observed["market_data_port_context"] == "quote-context"
    assert observed["market_history_loader_port"] == "market-data-port"
    assert observed["price_history_calls"] == [("market-data-port", "QQQ"), ("market-data-port", "QQQ")]
    assert result == {
        "market_history": "market-history-loader",
        "benchmark_history": [{"close": 1.0}],
        "qqq_history": [{"close": 1.0}],
    }


def test_runtime_strategy_adapters_materialize_hk_direct_market_history():
    observed = {}

    class FakeBrokerAdapters:
        strategy_symbols = ("02800", "02834")

        def build_market_data_port(self, quote_context):
            observed["market_data_port_context"] = quote_context
            return "market-data-port"

        def build_market_history_loader(self, market_data_port):
            observed["market_history_loader_port"] = market_data_port

            def load_market_history(_broker_client, symbol):
                observed.setdefault("history_calls", []).append(symbol)
                return pd.Series(
                    [10.0, 11.0],
                    index=pd.to_datetime(["2026-05-29", "2026-06-01"], utc=True),
                    dtype=float,
                )

            return load_market_history

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile="hk_global_etf_tactical_rotation",
        strategy_runtime_config={"universe_symbols": ("02800", "02834")},
        available_inputs=("market_history",),
        benchmark_symbol="QQQ",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **_kwargs: key,
        broker_adapters=FakeBrokerAdapters(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(AssertionError("unexpected fallback")),
        build_strategy_evaluation_inputs_fn=lambda **_kwargs: {},
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
    )

    result = adapters.calculate_strategy_indicators("quote-context")

    assert observed["market_data_port_context"] == "quote-context"
    assert observed["market_history_loader_port"] == "market-data-port"
    assert observed["history_calls"] == ["02800", "02834"]
    assert sorted(result["market_history"]) == ["02800", "02834"]
    assert result["market_history"]["02800"][0]["date"] == pd.Timestamp(
        "2026-05-29",
        tz="UTC",
    )
    assert result["market_history"]["02800"][0]["close"] == 10.0


def test_runtime_strategy_adapters_fall_back_to_rotation_indicators():
    observed = {}

    def fake_rotation_indicators(quote_context, *, trend_window, completed_session_date):
        observed["rotation_call"] = (quote_context, trend_window, completed_session_date)
        return {"rotation": True}

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={"trend_ma_window": 180},
        available_inputs=("portfolio_snapshot",),
        benchmark_symbol="QQQ",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **_kwargs: key,
        broker_adapters=SimpleNamespace(build_market_data_port=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected market data port"))),
        calculate_rotation_indicators_fn=fake_rotation_indicators,
        build_strategy_evaluation_inputs_fn=lambda **_kwargs: {},
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
    )

    result = adapters.calculate_strategy_indicators("quote-context")

    assert observed["rotation_call"][:2] == ("quote-context", 180)
    assert observed["rotation_call"][2]
    assert result == {"rotation": True}


def test_runtime_strategy_adapters_resolve_plan_builds_inputs_and_maps_decision():
    observed = {}

    class FakeBrokerAdapters:
        def build_portfolio_snapshot_from_account_state(self, account_state):
            observed["snapshot_from_account_state"] = account_state
            return "snapshot-from-account-state"

        def build_account_state_from_snapshot(self, snapshot):
            observed["account_state_from_snapshot"] = snapshot
            return {"derived": True}

    def fake_evaluate(**kwargs):
        observed["evaluation_inputs"] = kwargs
        return SimpleNamespace(decision="decision-1")

    def fake_build_inputs(**kwargs):
        observed["build_inputs"] = kwargs
        return {
            "portfolio_snapshot": kwargs["portfolio_snapshot"],
            "account_state": kwargs["account_state"],
            "translator": kwargs["translator"],
            "signal_text_fn": kwargs["signal_text_fn"],
        }

    def fake_map_plan(decision, **kwargs):
        observed["map_call"] = (decision, kwargs)
        return {"plan": True}

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=fake_evaluate),
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={"trend_ma_window": 150},
        available_inputs=("portfolio_snapshot", "account_state", "benchmark_history"),
        benchmark_symbol="QQQ",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **_kwargs: f"tr:{key}",
        broker_adapters=FakeBrokerAdapters(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=fake_build_inputs,
        map_strategy_decision_to_plan_fn=fake_map_plan,
    )

    result = adapters.resolve_rebalance_plan(indicators={"benchmark_history": [{"close": 1.0}]}, snapshot="snapshot-1")

    assert observed["account_state_from_snapshot"] == "snapshot-1"
    assert observed["build_inputs"]["portfolio_snapshot"] == "snapshot-1"
    assert observed["build_inputs"]["account_state"] == {"derived": True}
    assert observed["evaluation_inputs"]["portfolio_snapshot"] == "snapshot-1"
    assert observed["map_call"] == (
        "decision-1",
        {
            "account_state": {"derived": True},
            "snapshot": "snapshot-1",
            "strategy_profile": "soxl_soxx_trend_income",
            "runtime_metadata": {},
        },
    )
    assert result == {"plan": True}


def test_v7_loaded_runtime_uses_real_entrypoint_risk_gate_and_mapper(monkeypatch):
    import json
    import sys

    sys.path.insert(0, str(ROOT / "tests"))
    from test_v7_paper_application import _protected_env, _record
    from test_v7_paper_preview import _available_inputs
    from application.v7_paper_application import build_v7_runtime_binding
    from decision_mapper import map_strategy_decision_to_plan
    from quant_platform_kit.common.runtime_inputs import build_strategy_evaluation_inputs
    from strategy_loader import (
        load_strategy_entrypoint_for_profile,
        load_strategy_runtime_adapter_for_profile,
    )

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    settings = PlatformRuntimeSettings(
        project_id=None,
        secret_name="paper",
        account_prefix="PAPER",
        strategy_profile=V7_PROFILE,
        strategy_display_name="V7",
        strategy_domain="us_equity",
        account_region="PAPER",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        runtime_target=target,
    )
    entrypoint = load_strategy_entrypoint_for_profile(V7_PROFILE)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(V7_PROFILE)
    loaded_runtime = LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=settings,
    )
    inputs = _available_inputs()
    adapters = build_runtime_strategy_adapters(
        strategy_runtime=loaded_runtime,
        strategy_profile=V7_PROFILE,
        strategy_runtime_config=loaded_runtime.merged_runtime_config,
        available_inputs=runtime_adapter.available_inputs,
        benchmark_symbol="SOXX",
        signal_text_fn=lambda icon: str(icon),
        translator=lambda key, **_kwargs: str(key),
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs,
        map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan,
    )

    plan = adapters.resolve_rebalance_plan(
        indicators=inputs["derived_indicators"],
        snapshot=inputs["portfolio_snapshot"],
    )

    assert plan["execution"]["no_execute"] is True
    assert plan["execution"]["no_order"] is True
    assert plan["execution"]["execution_authorized"] is False
    assert plan["execution"]["risk_gate"] == "REJECT"
    assert plan["allocation"]["targets"] == {}


def test_runtime_strategy_adapters_add_execution_policy_to_runtime_metadata():
    observed = {}

    class FakeBrokerAdapters:
        def build_account_state_from_snapshot(self, _snapshot):
            return None

    def fake_evaluate(**_kwargs):
        return SimpleNamespace(
            decision="decision-1",
            metadata={
                "signal": "ok",
                "longbridge_execution_policy": {
                    "reserved_cash_floor_usd": 1.0,
                    "reserved_cash_ratio": 0.0,
                },
            },
        )

    def fake_map_plan(decision, **kwargs):
        observed["map_call"] = (decision, kwargs)
        return {"plan": True}

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=fake_evaluate),
        strategy_profile="russell_top50_leader_rotation",
        strategy_runtime_config={},
        available_inputs=("portfolio_snapshot",),
        benchmark_symbol="QQQ",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **_kwargs: f"tr:{key}",
        broker_adapters=FakeBrokerAdapters(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **kwargs: kwargs,
        map_strategy_decision_to_plan_fn=fake_map_plan,
        execution_policy={"reserved_cash_floor_usd": 250.0, "reserved_cash_ratio": 0.03},
    )

    result = adapters.resolve_rebalance_plan(indicators={}, snapshot="snapshot-1")

    assert result == {"plan": True}
    assert observed["map_call"][1]["runtime_metadata"] == {
        "signal": "ok",
        "longbridge_execution_policy": {
            "reserved_cash_floor_usd": 250.0,
            "reserved_cash_ratio": 0.03,
        },
    }


def test_frozen_plan_reapplies_risk_gate_and_maps_only_the_original_target() -> None:
    observed = {}
    stamped = SimpleNamespace(total_equity=1000, positions=(), metadata={
        "unrealized_pnl_pct": -0.25, "consecutive_losses": 6,
    })
    runtime = SimpleNamespace(
        _stamp_portfolio_risk_metadata=lambda inputs: (
            observed.setdefault("stamp_inputs", inputs), {"portfolio_snapshot": stamped},
        )[1],
        _build_capital_base_capabilities=lambda inputs: (
            observed.setdefault("capital_inputs", inputs),
            {"capital_base": "capital", "capital_base_binding": "binding"},
        )[1]
    )

    def map_plan(decision, **kwargs):
        observed["decision"] = decision
        observed["map_kwargs"] = kwargs
        return {"mapped": True}

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=runtime,
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={"fixed_param": 0.65},
        available_inputs=("portfolio_snapshot",),
        benchmark_symbol="SOXX",
        signal_text_fn=str,
        translator=str,
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **kwargs: kwargs,
        map_strategy_decision_to_plan_fn=map_plan,
    )
    snapshot = object()

    with patch(
        "application.runtime_strategy_adapters.apply_risk_gate",
        side_effect=lambda decision, **kwargs: (observed.setdefault("risk_kwargs", kwargs), decision)[1],
    ):
        result = adapters.resolve_frozen_rebalance_plan(
            allocation={
                "targets": {"SOXL": 200.0, "BOXX": 800.0},
                "risk_symbols": ["SOXL"],
                "safe_haven_symbols": ["BOXX"],
            },
            execution={
                "signal_date": "2026-09-09",
                "effective_date": "2026-09-10",
                "execution_timing_contract": "next_trading_day",
                "no_order": True,
                "execution_authorized": False,
            },
            snapshot=snapshot,
        )

    assert result == {"mapped": True}
    assert observed["stamp_inputs"] == {"portfolio_snapshot": snapshot}
    assert observed["capital_inputs"] == {"portfolio_snapshot": stamped}
    assert observed["decision"].diagnostics["unrealized_pnl_pct"] == -0.25
    assert observed["decision"].diagnostics["consecutive_losses"] == 6
    assert observed["risk_kwargs"] == {
        "portfolio_snapshot": stamped,
        "max_single_weight": 0.20,
        "enforce_value_target_exposure": True,
        "capital_base": "capital",
        "capital_base_binding": "binding",
    }
    assert [(item.symbol, item.target_value) for item in observed["decision"].positions] == [
        ("BOXX", 800.0),
        ("SOXL", 200.0),
    ]
    assert observed["map_kwargs"]["runtime_metadata"]["execution_annotations"]["signal_date"] == "2026-09-09"
    assert observed["decision"].diagnostics["no_order"] is True
    assert observed["decision"].diagnostics["execution_authorized"] is False


def test_runtime_strategy_adapters_loads_and_reports_plugin_signals():
    observed = {}
    signal = SimpleNamespace(
        plugin="crisis_response_shadow",
        effective_mode="shadow",
        canonical_route="no_action",
        suggested_action="monitor",
    )

    def fake_parse(raw_mounts):
        observed["raw_mounts"] = raw_mounts
        return ("mount-1",)

    def fake_load(mounts, *, strategy_profile):
        observed["load_call"] = (mounts, strategy_profile)
        return (signal,)

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={},
        available_inputs=(),
        benchmark_symbol="SOXX",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **kwargs: {
            "strategy_plugin_line": "plugin={plugin}|mode={mode}|route={route}|action={action}",
            "strategy_plugin_error_line": "plugin-error={reason}|fallback=built-in",
            "strategy_plugin_error_reason_ValueError": "config validation failed",
            "strategy_plugin_consumption_unavailable": "consumption=none",
            "strategy_plugin_name_crisis_response_shadow": "Crisis",
            "strategy_plugin_mode_shadow": "shadow",
            "strategy_plugin_route_no_action": "no action",
            "strategy_plugin_action_monitor": "monitor",
        }.get(key, key).format(**kwargs),
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **_kwargs: {},
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
        build_strategy_plugin_report_payload_fn=lambda signals: {"strategy_plugins": list(signals)},
        load_configured_strategy_plugin_signals_fn=fake_load,
        parse_strategy_plugin_mounts_fn=fake_parse,
    )

    signals, error = adapters.load_strategy_plugin_signals('{"strategy_plugins":[]}')
    report = {}
    adapters.attach_strategy_plugin_report(report, signals=signals, error=error)

    assert error is None
    assert signals == (signal,)
    assert observed["raw_mounts"] == '{"strategy_plugins":[]}'
    assert observed["load_call"] == (("mount-1",), "soxl_soxx_trend_income")
    assert report["summary"]["strategy_plugins"] == [signal]
    assert adapters.build_strategy_plugin_notification_lines(signals) == (
        "plugin=Crisis|mode=shadow|route=no action|action=monitor",
    )
    assert adapters.build_strategy_plugin_error_notification_lines("ValueError: bad config") == (
        "plugin-error=config validation failed|fallback=built-in",
        "consumption=none",
    )
    assert adapters.build_strategy_plugin_alert_messages(signals) == ()


def test_runtime_strategy_adapters_builds_escalated_plugin_alert_message():
    signal = SimpleNamespace(
        plugin="crisis_response_shadow",
        effective_mode="shadow",
        canonical_route="true_crisis",
        suggested_action="defend",
        would_trade_if_enabled=True,
        as_of="2026-05-24",
        source_uri="gs://bucket/latest_signal.json",
    )
    translations = {
        "strategy_plugin_line": "plugin={plugin}|mode={mode}|route={route}|action={action}",
        "strategy_plugin_alert_subject": "alert:{strategy}:{plugin}:{route}",
        "strategy_plugin_alert_title": "alert title",
        "strategy_plugin_alert_strategy": "strategy={strategy}",
        "strategy_plugin_alert_plugin": "plugin={plugin}",
        "strategy_plugin_alert_status": "route={route}",
        "strategy_plugin_alert_action": "action={action}",
        "strategy_plugin_alert_mode": "mode={mode}",
        "strategy_plugin_alert_as_of": "as_of={as_of}",
        "strategy_plugin_name_crisis_response_shadow": "Crisis",
        "strategy_plugin_mode_shadow": "shadow",
        "strategy_plugin_route_true_crisis": "true crisis",
        "strategy_plugin_action_defend": "defend",
    }
    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={},
        available_inputs=(),
        benchmark_symbol="SOXX",
        signal_text_fn=lambda icon: f"signal:{icon}",
        translator=lambda key, **kwargs: translations.get(key, key).format(**kwargs) if kwargs else translations.get(key, key),
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **_kwargs: {},
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
    )

    alerts = adapters.build_strategy_plugin_alert_messages((signal,))

    assert len(alerts) == 1
    assert alerts[0].subject == "alert:soxl_soxx_trend_income:Crisis:true crisis"
    assert "plugin=Crisis" in alerts[0].body
    assert "route=true crisis" in alerts[0].body
    assert "action=defend" in alerts[0].body
    assert "mode=shadow" in alerts[0].body
    assert "would_trade=" not in alerts[0].body
    assert "source=" not in alerts[0].body


def test_completed_session_soxl_overrides_mapper_annotations_on_final_plan(monkeypatch):
    from datetime import datetime, timezone
    from decision_mapper import map_strategy_decision_to_plan
    from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision

    class BrokerAdapters:
        def build_account_state_from_snapshot(self, _snapshot):
            return {
                "available_cash": 1_000.0,
                "market_values": {"SOXL": 0.0},
                "quantities": {"SOXL": 0.0},
                "sellable_quantities": {"SOXL": 0.0},
                "total_strategy_equity": 1_000.0,
            }

    decision = StrategyDecision(
        positions=(PositionTarget(symbol="SOXL", target_value=500.0),),
        diagnostics={
            "execution_annotations": {
                "signal_date": "1999-01-01",
                "effective_date": "1999-01-02",
                "execution_timing_contract": "next_trading_day",
                "trade_threshold_value": 10.0,
                "current_min_trade": 10.0,
                "investable_cash": 1_000.0,
            },
        },
    )
    runtime = SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(decision=decision, metadata={}))
    adapters = build_runtime_strategy_adapters(
        strategy_runtime=runtime,
        strategy_profile="soxl_soxx_trend_income",
        strategy_runtime_config={},
        available_inputs=("portfolio_snapshot", "account_state"),
        benchmark_symbol="SOXX",
        signal_text_fn=str,
        translator=lambda key, **_kwargs: key,
        broker_adapters=BrokerAdapters(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **kwargs: kwargs,
        map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan,
    )
    monkeypatch.setattr(
        "application.runtime_strategy_adapters._next_xnys_session_date",
        lambda value: "2026-07-06" if value == "2026-07-02" else None,
    )
    before = datetime.now(timezone.utc)
    plan = adapters.resolve_rebalance_plan(
        indicators={"completed_session": {"date": "2026-07-02"}},
        snapshot=object(),
    )
    after = datetime.now(timezone.utc)

    assert plan["execution"]["signal_date"] == "2026-07-02"
    assert plan["execution"]["effective_date"] == "2026-07-06"
    assert plan["execution"]["completed_session_date"] == "2026-07-02"
    assert before <= datetime.fromisoformat(plan["execution"]["generated_at"]) <= after


def test_completed_session_does_not_change_non_soxl_mapper_output():
    observed = {}
    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: SimpleNamespace(decision="decision", metadata={})),
        strategy_profile="russell_top50_leader_rotation",
        strategy_runtime_config={},
        available_inputs=("portfolio_snapshot",),
        benchmark_symbol="SOXX",
        signal_text_fn=str,
        translator=lambda key, **_kwargs: key,
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: {},
        build_strategy_evaluation_inputs_fn=lambda **kwargs: kwargs,
        map_strategy_decision_to_plan_fn=lambda _decision, **kwargs: observed.setdefault(
            "plan", {"execution": {"signal_date": "2026-07-06", "effective_date": "2026-07-07"}}
        ),
    )

    plan = adapters.resolve_rebalance_plan(
        indicators={"completed_session": {"date": "2026-07-02"}}, snapshot=object()
    )

    assert plan["execution"] == {"signal_date": "2026-07-06", "effective_date": "2026-07-07"}


def test_completed_xnys_session_respects_close_holiday_and_weekend():
    from datetime import datetime, timezone
    from application.runtime_strategy_adapters import _completed_xnys_session_date

    # 2026-11-27 is the XNYS early-close session after Thanksgiving.
    # At the production 15:45 New York schedule on the Monday after the
    # July 3 holiday, Friday's completed session remains July 2.
    assert _completed_xnys_session_date(datetime(2026, 7, 6, 19, 45, tzinfo=timezone.utc)) == "2026-07-02"
    assert _completed_xnys_session_date(datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)) == "2026-11-25"
    assert _completed_xnys_session_date(datetime(2026, 11, 27, 18, 1, tzinfo=timezone.utc)) == "2026-11-27"
    assert _completed_xnys_session_date(datetime(2026, 11, 29, 20, 0, tzinfo=timezone.utc)) == "2026-11-27"


def test_v7_derived_indicators_fallback_keeps_legacy_rotation_signature():
    observed = {}

    def legacy_rotation_indicators(quote_context, *, trend_window):
        observed["call"] = (quote_context, trend_window)
        return {"derived": True}

    adapters = build_runtime_strategy_adapters(
        strategy_runtime=SimpleNamespace(evaluate=lambda **_kwargs: None),
        strategy_profile=V7_PROFILE,
        strategy_runtime_config={"trend_ma_window": 150},
        available_inputs=("derived_indicators", "portfolio_snapshot"),
        benchmark_symbol="SOXX",
        signal_text_fn=str,
        translator=lambda key, **_kwargs: key,
        broker_adapters=SimpleNamespace(),
        calculate_rotation_indicators_fn=legacy_rotation_indicators,
        build_strategy_evaluation_inputs_fn=lambda **kwargs: kwargs,
        map_strategy_decision_to_plan_fn=lambda *_args, **_kwargs: {},
    )

    assert adapters.calculate_strategy_indicators("v7-quote") == {"derived": True}
    assert observed["call"] == ("v7-quote", 150)
