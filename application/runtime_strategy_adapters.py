"""Builder helpers for LongBridge strategy evaluation adapters."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LongBridgeRuntimeStrategyAdapters:
    strategy_runtime: Any
    strategy_profile: str
    strategy_runtime_config: Mapping[str, Any]
    available_inputs: Collection[str]
    benchmark_symbol: str
    signal_text_fn: Callable[[str], str]
    translator: Callable[..., str]
    broker_adapters: Any
    calculate_rotation_indicators_fn: Callable[..., Any]
    build_strategy_evaluation_inputs_fn: Callable[..., dict[str, Any]]
    map_strategy_decision_to_plan_fn: Callable[..., dict[str, Any]]

    def calculate_strategy_indicators(self, quote_context):
        available_inputs = set(self.available_inputs)
        if "feature_snapshot" in available_inputs and not (
            {"benchmark_history", "qqq_history", "derived_indicators", "indicators"} & available_inputs
        ):
            return {}
        if "market_history" in available_inputs or "benchmark_history" in available_inputs or "qqq_history" in available_inputs:
            market_data_port = self.broker_adapters.build_market_data_port(quote_context)
        if "market_history" in available_inputs:
            market_inputs = {
                "market_history": self.broker_adapters.build_market_history_loader(market_data_port),
            }
            if "benchmark_history" in available_inputs:
                market_inputs["benchmark_history"] = self.broker_adapters.build_price_history(
                    market_data_port,
                    self.benchmark_symbol,
                )
            if "qqq_history" in available_inputs:
                market_inputs["qqq_history"] = self.broker_adapters.build_price_history(
                    market_data_port,
                    self.benchmark_symbol,
                )
            return market_inputs
        if "benchmark_history" in available_inputs or "qqq_history" in available_inputs:
            return self.broker_adapters.build_price_history(market_data_port, self.benchmark_symbol)
        trend_ma_window = int(self.strategy_runtime_config.get("trend_ma_window", 150))
        return self.calculate_rotation_indicators_fn(quote_context, trend_window=trend_ma_window)

    def resolve_rebalance_plan(self, *, indicators, snapshot=None, account_state=None):
        available_inputs = set(self.available_inputs)
        resolved_snapshot = snapshot
        if resolved_snapshot is None and account_state is not None:
            resolved_snapshot = self.broker_adapters.build_portfolio_snapshot_from_account_state(account_state)
        resolved_account_state = account_state
        if resolved_account_state is None and "account_state" in available_inputs and resolved_snapshot is not None:
            resolved_account_state = self.broker_adapters.build_account_state_from_snapshot(resolved_snapshot)
        market_inputs = {
            "market_history": indicators,
            "derived_indicators": indicators,
            "indicators": indicators,
            "benchmark_history": indicators,
            "qqq_history": indicators,
        }
        if isinstance(indicators, dict) and any(
            key in indicators for key in ("market_history", "benchmark_history", "qqq_history")
        ):
            market_inputs.update(indicators)
        evaluation_inputs = self.build_strategy_evaluation_inputs_fn(
            available_inputs=available_inputs,
            market_inputs=market_inputs,
            portfolio_snapshot=resolved_snapshot,
            account_state=resolved_account_state,
            translator=self.translator,
            signal_text_fn=self.signal_text_fn,
        )
        evaluation = self.strategy_runtime.evaluate(**evaluation_inputs)
        return self.map_strategy_decision_to_plan_fn(
            evaluation.decision,
            account_state=resolved_account_state if "account_state" in available_inputs else None,
            snapshot=resolved_snapshot,
            strategy_profile=self.strategy_profile,
            runtime_metadata=getattr(evaluation, "metadata", None),
        )


def build_runtime_strategy_adapters(
    *,
    strategy_runtime: Any,
    strategy_profile: str,
    strategy_runtime_config: Mapping[str, Any],
    available_inputs: Collection[str],
    benchmark_symbol: str,
    signal_text_fn: Callable[[str], str],
    translator: Callable[..., str],
    broker_adapters: Any,
    calculate_rotation_indicators_fn: Callable[..., Any],
    build_strategy_evaluation_inputs_fn: Callable[..., dict[str, Any]],
    map_strategy_decision_to_plan_fn: Callable[..., dict[str, Any]],
) -> LongBridgeRuntimeStrategyAdapters:
    return LongBridgeRuntimeStrategyAdapters(
        strategy_runtime=strategy_runtime,
        strategy_profile=str(strategy_profile),
        strategy_runtime_config=dict(strategy_runtime_config),
        available_inputs=tuple(available_inputs),
        benchmark_symbol=str(benchmark_symbol),
        signal_text_fn=signal_text_fn,
        translator=translator,
        broker_adapters=broker_adapters,
        calculate_rotation_indicators_fn=calculate_rotation_indicators_fn,
        build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs_fn,
        map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan_fn,
    )
