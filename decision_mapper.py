from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quant_platform_kit.strategy_contracts import (
    StrategyDecision,
    build_value_target_execution_annotations,
    build_value_target_execution_plan,
    build_value_target_plan_payload,
    build_value_target_portfolio_plan,
)


def map_strategy_decision_to_plan(
    decision: StrategyDecision,
    *,
    account_state: Mapping[str, Any] | None = None,
    snapshot: Any | None = None,
    strategy_profile: str,
) -> dict[str, Any]:
    execution_plan = build_value_target_execution_plan(
        decision,
        strategy_profile=strategy_profile,
    )
    annotations = build_value_target_execution_annotations(decision)
    if account_state is not None:
        market_values = dict(account_state["market_values"])
        quantities = dict(account_state["quantities"])
        sellable_quantities = dict(account_state["sellable_quantities"])
        total_equity = float(account_state["total_strategy_equity"])
        liquid_cash = float(account_state["available_cash"])
    elif snapshot is not None:
        strategy_symbols = tuple(execution_plan.strategy_symbols_risk_safe_income)
        market_values = {symbol: 0.0 for symbol in strategy_symbols}
        quantities = {symbol: 0 for symbol in strategy_symbols}
        sellable_quantities = {symbol: 0 for symbol in strategy_symbols}
        for position in getattr(snapshot, "positions", ()):
            if position.symbol not in market_values:
                continue
            market_values[position.symbol] = float(position.market_value)
            quantities[position.symbol] = int(position.quantity)
            sellable_quantities[position.symbol] = int(position.quantity)
        total_equity = float(snapshot.total_equity)
        liquid_cash = float(snapshot.buying_power or snapshot.cash_balance or 0.0)
    else:
        raise ValueError("LongBridge plan mapping requires account_state or snapshot")

    portfolio_plan = build_value_target_portfolio_plan(
        execution_plan,
        market_values=market_values,
        quantities=quantities,
        sellable_quantities=sellable_quantities,
        total_equity=total_equity,
        liquid_cash=liquid_cash,
        strategy_symbols_order="risk_safe_income",
        portfolio_rows_layout=("risk", "income", "safe"),
    )
    investable_cash = annotations.investable_cash
    if investable_cash is None:
        investable_cash = max(0.0, liquid_cash - annotations.reserved_cash)
    current_min_trade = annotations.current_min_trade
    if current_min_trade is None:
        current_min_trade = annotations.trade_threshold_value
    plan = build_value_target_plan_payload(
        strategy_profile=strategy_profile,
        portfolio_plan=portfolio_plan,
        annotations=annotations,
        include_sellable_quantities=True,
        execution_fields=(
            "trade_threshold_value",
            "signal_display",
            "status_display",
            "deploy_ratio_text",
            "income_ratio_text",
            "income_locked_ratio_text",
            "active_risk_asset",
            "investable_cash",
            "current_min_trade",
        ),
        execution_defaults={
            "signal_display": "",
            "status_display": "",
            "deploy_ratio_text": "",
            "income_ratio_text": "",
            "income_locked_ratio_text": "",
            "current_min_trade": current_min_trade,
            "investable_cash": investable_cash,
        },
    )
    return plan
