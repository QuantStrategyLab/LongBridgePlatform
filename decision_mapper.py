from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from us_equity_strategies.catalog import resolve_canonical_profile

from quant_platform_kit.strategy_contracts import (
    PositionTarget,
    StrategyDecision,
    ValueTargetExecutionAnnotations,
    build_value_target_execution_annotations,
    build_value_target_portfolio_inputs_from_account_state,
    build_value_target_portfolio_inputs_from_snapshot,
    build_value_target_runtime_plan,
    resolve_decision_target_mode,
    translate_decision_to_target_mode,
)

_SAFE_HAVEN_SYMBOLS = frozenset({"BOXX", "BIL"})
_INCOME_SYMBOLS = frozenset({"QQQI", "SPYI"})
_DEFAULT_MIN_TRADE_FLOOR = 100.0
_DEFAULT_REBALANCE_THRESHOLD_RATIO = 0.01


def _build_portfolio_inputs(
    *,
    account_state: Mapping[str, Any] | None,
    snapshot: Any | None,
):
    if account_state is not None:
        return build_value_target_portfolio_inputs_from_account_state(account_state)
    if snapshot is not None:
        return build_value_target_portfolio_inputs_from_snapshot(
            snapshot,
            include_sellable_quantities=True,
            liquid_cash=float(snapshot.buying_power or snapshot.cash_balance or 0.0),
        )
    raise ValueError("LongBridge plan mapping requires account_state or snapshot")


def _cash_by_currency_from_account_state(
    account_state: Mapping[str, Any] | None,
) -> dict[str, float]:
    if account_state is None:
        return {}
    raw_cash = account_state.get("cash_by_currency")
    if not isinstance(raw_cash, Mapping):
        return {}
    cash_by_currency: dict[str, float] = {}
    for currency, amount in raw_cash.items():
        normalized_currency = str(currency or "").strip().upper()
        if not normalized_currency:
            continue
        cash_by_currency[normalized_currency] = float(amount)
    return cash_by_currency


def _cash_by_currency_from_snapshot(snapshot: Any | None) -> dict[str, float]:
    metadata = getattr(snapshot, "metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        return {}
    raw_cash = metadata.get("cash_by_currency")
    if not isinstance(raw_cash, Mapping):
        return {}
    cash_by_currency: dict[str, float] = {}
    for currency, amount in raw_cash.items():
        normalized_currency = str(currency or "").strip().upper()
        if not normalized_currency:
            continue
        cash_by_currency[normalized_currency] = float(amount)
    return cash_by_currency


def _symbol_role(symbol: str) -> str | None:
    normalized = str(symbol or "").strip().upper()
    if normalized in _SAFE_HAVEN_SYMBOLS:
        return "safe_haven"
    if normalized in _INCOME_SYMBOLS:
        return "income"
    return None


def _default_threshold_value(total_equity: float) -> float:
    return max(_DEFAULT_MIN_TRADE_FLOOR, float(total_equity) * _DEFAULT_REBALANCE_THRESHOLD_RATIO)


def _build_weight_translation_annotations(
    decision: StrategyDecision,
    *,
    total_equity: float,
    liquid_cash: float,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> ValueTargetExecutionAnnotations:
    diagnostics = {**dict(runtime_metadata or {}), **dict(decision.diagnostics)}
    execution_annotations: dict[str, Any] = {}
    raw_runtime_annotations = runtime_metadata.get("execution_annotations") if isinstance(runtime_metadata, Mapping) else None
    if isinstance(raw_runtime_annotations, Mapping):
        execution_annotations.update(raw_runtime_annotations)
    raw_annotations = diagnostics.get("execution_annotations")
    if isinstance(raw_annotations, Mapping):
        execution_annotations.update(raw_annotations)
    threshold_value = _default_threshold_value(total_equity)
    signal_display = str(
        diagnostics.get("signal_description")
        or diagnostics.get("signal_display")
        or diagnostics.get("signal_message")
        or ""
    ).strip() or None
    status_display = str(
        diagnostics.get("status_description")
        or diagnostics.get("market_status")
        or diagnostics.get("canary_status")
        or ""
    ).strip() or None
    dashboard_text = str(
        execution_annotations.get("dashboard_text")
        or diagnostics.get("dashboard")
        or ""
    ).strip() or None
    benchmark_symbol = str(diagnostics.get("benchmark_symbol") or "").strip().upper() or None
    return ValueTargetExecutionAnnotations(
        trade_threshold_value=threshold_value,
        reserved_cash=0.0,
        signal_display=signal_display,
        status_display=status_display,
        dashboard_text=dashboard_text,
        benchmark_symbol=benchmark_symbol,
        benchmark_price=(
            float(diagnostics["benchmark_price"])
            if diagnostics.get("benchmark_price") is not None
            else None
        ),
        long_trend_value=(
            float(diagnostics["long_trend_value"])
            if diagnostics.get("long_trend_value") is not None
            else None
        ),
        exit_line=(
            float(diagnostics["exit_line"])
            if diagnostics.get("exit_line") is not None
            else None
        ),
        signal_date=(
            str(execution_annotations.get("signal_date") or diagnostics.get("signal_date") or "").strip() or None
        ),
        effective_date=(
            str(execution_annotations.get("effective_date") or diagnostics.get("effective_date") or "").strip()
            or None
        ),
        execution_timing_contract=(
            str(
                execution_annotations.get("execution_timing_contract")
                or diagnostics.get("execution_timing_contract")
                or ""
            ).strip()
            or None
        ),
        execution_calendar_source=(
            str(
                execution_annotations.get("execution_calendar_source")
                or diagnostics.get("execution_calendar_source")
                or ""
            ).strip()
            or None
        ),
        signal_effective_after_trading_days=(
            int(signal_delay)
            if (
                signal_delay := execution_annotations.get(
                    "signal_effective_after_trading_days",
                    diagnostics.get("signal_effective_after_trading_days"),
                )
            )
            is not None
            else None
        ),
        current_min_trade=threshold_value,
        investable_cash=max(0.0, float(liquid_cash)),
    )


def _build_hold_current_value_decision(portfolio_inputs) -> StrategyDecision:
    positions: list[PositionTarget] = []
    for symbol, market_value in sorted(portfolio_inputs.market_values.items()):
        positions.append(
            PositionTarget(
                symbol=str(symbol),
                target_value=float(market_value),
                role=_symbol_role(str(symbol)),
            )
        )
    return StrategyDecision(positions=tuple(positions))


def _normalize_to_value_target_decision(
    decision: StrategyDecision,
    *,
    portfolio_inputs,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> tuple[StrategyDecision, ValueTargetExecutionAnnotations | None]:
    target_mode = resolve_decision_target_mode(decision)
    no_execute = "no_execute" in set(decision.risk_flags)

    if target_mode == "value" and not no_execute:
        return decision, None

    if target_mode == "weight" and not no_execute:
        translated = translate_decision_to_target_mode(
            decision,
            target_mode="value",
            total_equity=float(portfolio_inputs.total_equity),
        )
        return translated, _build_weight_translation_annotations(
            decision,
            total_equity=float(portfolio_inputs.total_equity),
            liquid_cash=float(portfolio_inputs.liquid_cash),
            runtime_metadata=runtime_metadata,
        )

    synthetic = _build_hold_current_value_decision(portfolio_inputs)
    synthetic_annotations = _build_weight_translation_annotations(
        decision,
        total_equity=float(portfolio_inputs.total_equity),
        liquid_cash=float(portfolio_inputs.liquid_cash),
        runtime_metadata=runtime_metadata,
    )
    return synthetic, synthetic_annotations


def _resolve_layout(strategy_profile: str) -> tuple[str, tuple[str, ...], tuple[str, ...], dict[str, Any]]:
    strategy_profile = resolve_canonical_profile(strategy_profile)
    if strategy_profile == "tqqq_growth_income":
        return (
            "risk_safe_income",
            ("risk_safe", "income"),
            (
                "trade_threshold_value",
                "reserved_cash",
                "signal_display",
                "status_display",
                "dashboard_text",
                "signal_date",
                "effective_date",
                "execution_timing_contract",
                "execution_calendar_source",
                "signal_effective_after_trading_days",
                "benchmark_symbol",
                "benchmark_price",
                "long_trend_value",
                "exit_line",
                "current_min_trade",
                "investable_cash",
            ),
            {
                "reserved_cash": 0.0,
                "signal_display": "",
                "status_display": "",
                "dashboard_text": "",
                "signal_date": "",
                "effective_date": "",
                "execution_timing_contract": "",
                "execution_calendar_source": "",
                "signal_effective_after_trading_days": None,
                "benchmark_symbol": "QQQ",
                "benchmark_price": 0.0,
                "long_trend_value": 0.0,
                "exit_line": 0.0,
                "current_min_trade": 0.0,
                "investable_cash": 0.0,
            },
        )
    if strategy_profile in {"tech_communication_pullback_enhancement", "qqq_tech_enhancement"}:
        return (
            "risk_safe_income",
            ("risk_safe",),
            (
                "trade_threshold_value",
                "reserved_cash",
                "signal_display",
                "status_display",
                "dashboard_text",
                "signal_date",
                "effective_date",
                "execution_timing_contract",
                "execution_calendar_source",
                "signal_effective_after_trading_days",
                "benchmark_symbol",
                "benchmark_price",
                "long_trend_value",
                "exit_line",
                "current_min_trade",
                "investable_cash",
            ),
            {
                "reserved_cash": 0.0,
                "signal_display": "",
                "status_display": "",
                "dashboard_text": "",
                "signal_date": "",
                "effective_date": "",
                "execution_timing_contract": "",
                "execution_calendar_source": "",
                "signal_effective_after_trading_days": None,
                "benchmark_symbol": "QQQ",
                "benchmark_price": 0.0,
                "long_trend_value": 0.0,
                "exit_line": 0.0,
                "current_min_trade": 0.0,
                "investable_cash": 0.0,
            },
        )
    return (
        "risk_safe_income",
        ("risk", "income", "safe"),
        (
            "trade_threshold_value",
            "signal_display",
            "status_display",
            "dashboard_text",
            "signal_date",
            "effective_date",
            "execution_timing_contract",
            "execution_calendar_source",
            "signal_effective_after_trading_days",
            "benchmark_symbol",
            "benchmark_price",
            "long_trend_value",
            "exit_line",
            "deploy_ratio_text",
            "income_ratio_text",
            "income_locked_ratio_text",
            "active_risk_asset",
            "investable_cash",
            "current_min_trade",
        ),
        {
            "signal_display": "",
            "status_display": "",
            "dashboard_text": "",
            "signal_date": "",
            "effective_date": "",
            "execution_timing_contract": "",
            "execution_calendar_source": "",
            "signal_effective_after_trading_days": None,
            "deploy_ratio_text": "",
            "income_ratio_text": "",
            "income_locked_ratio_text": "",
            "current_min_trade": 0.0,
            "investable_cash": 0.0,
        },
    )


def map_strategy_decision_to_plan(
    decision: StrategyDecision,
    *,
    account_state: Mapping[str, Any] | None = None,
    snapshot: Any | None = None,
    strategy_profile: str,
    runtime_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_profile = resolve_canonical_profile(strategy_profile)
    portfolio_inputs = _build_portfolio_inputs(account_state=account_state, snapshot=snapshot)
    normalized_decision, normalized_annotations = _normalize_to_value_target_decision(
        decision,
        portfolio_inputs=portfolio_inputs,
        runtime_metadata=runtime_metadata,
    )
    annotations = normalized_annotations
    if annotations is None:
        merged_diagnostics = {**dict(runtime_metadata or {}), **dict(normalized_decision.diagnostics)}
        merged_execution_annotations: dict[str, Any] = {}
        raw_runtime_annotations = runtime_metadata.get("execution_annotations") if isinstance(runtime_metadata, Mapping) else None
        if isinstance(raw_runtime_annotations, Mapping):
            merged_execution_annotations.update(raw_runtime_annotations)
        raw_decision_annotations = merged_diagnostics.get("execution_annotations")
        if isinstance(raw_decision_annotations, Mapping):
            merged_execution_annotations.update(raw_decision_annotations)
        merged_decision = StrategyDecision(
            positions=normalized_decision.positions,
            budgets=normalized_decision.budgets,
            risk_flags=normalized_decision.risk_flags,
            diagnostics={
                **merged_diagnostics,
                "execution_annotations": merged_execution_annotations,
            },
        )
        annotations = build_value_target_execution_annotations(merged_decision)
        investable_cash = annotations.investable_cash
        if investable_cash is None:
            investable_cash = max(
                0.0,
                portfolio_inputs.liquid_cash - annotations.reserved_cash,
            )
        current_min_trade = annotations.current_min_trade
        if current_min_trade is None:
            current_min_trade = annotations.trade_threshold_value
        annotations = ValueTargetExecutionAnnotations(
            trade_threshold_value=annotations.trade_threshold_value,
            reserved_cash=annotations.reserved_cash,
            signal_display=annotations.signal_display,
            status_display=annotations.status_display,
            dashboard_text=annotations.dashboard_text,
            separator=annotations.separator,
            benchmark_symbol=annotations.benchmark_symbol,
            benchmark_price=annotations.benchmark_price,
            long_trend_value=annotations.long_trend_value,
            exit_line=annotations.exit_line,
            signal_date=annotations.signal_date,
            effective_date=annotations.effective_date,
            execution_timing_contract=annotations.execution_timing_contract,
            execution_calendar_source=annotations.execution_calendar_source,
            signal_effective_after_trading_days=annotations.signal_effective_after_trading_days,
            deploy_ratio_text=annotations.deploy_ratio_text,
            income_ratio_text=annotations.income_ratio_text,
            income_locked_ratio_text=annotations.income_locked_ratio_text,
            active_risk_asset=annotations.active_risk_asset,
            current_min_trade=current_min_trade,
            investable_cash=investable_cash,
        )

    strategy_symbols_order, portfolio_rows_layout, execution_fields, execution_defaults = _resolve_layout(
        canonical_profile
    )
    plan = build_value_target_runtime_plan(
        normalized_decision,
        strategy_profile=canonical_profile,
        portfolio_inputs=portfolio_inputs,
        annotations=annotations,
        strategy_symbols_order=strategy_symbols_order,
        portfolio_rows_layout=portfolio_rows_layout,
        include_sellable_quantities=True,
        execution_fields=execution_fields,
        execution_defaults=execution_defaults,
    )
    cash_by_currency = _cash_by_currency_from_account_state(account_state)
    if not cash_by_currency:
        cash_by_currency = _cash_by_currency_from_snapshot(snapshot)
    if cash_by_currency:
        plan["portfolio"]["cash_by_currency"] = cash_by_currency
    return plan
