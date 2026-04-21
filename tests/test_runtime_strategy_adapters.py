import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.runtime_strategy_adapters import build_runtime_strategy_adapters


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
        calculate_rotation_indicators_fn=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected fallback")),
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


def test_runtime_strategy_adapters_fall_back_to_rotation_indicators():
    observed = {}

    def fake_rotation_indicators(quote_context, *, trend_window):
        observed["rotation_call"] = (quote_context, trend_window)
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

    assert observed["rotation_call"] == ("quote-context", 180)
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
            "runtime_metadata": None,
        },
    )
    assert result == {"plan": True}
