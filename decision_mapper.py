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
    account_state: Mapping[str, Any],
    strategy_profile: str,
) -> dict[str, Any]:
    execution_plan = build_value_target_execution_plan(
        decision,
        strategy_profile=strategy_profile,
    )
    annotations = build_value_target_execution_annotations(decision)
    portfolio_plan = build_value_target_portfolio_plan(
        execution_plan,
        market_values=dict(account_state["market_values"]),
        quantities=dict(account_state["quantities"]),
        sellable_quantities=dict(account_state["sellable_quantities"]),
        total_equity=float(account_state["total_strategy_equity"]),
        liquid_cash=float(account_state["available_cash"]),
        strategy_symbols_order="risk_safe_income",
        portfolio_rows_layout=("risk", "income", "safe"),
    )
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
            "current_min_trade": 0.0,
            "investable_cash": portfolio_plan.liquid_cash,
        },
    )
    risk_symbols = list(portfolio_plan.risk_symbols)
    income_symbols = list(portfolio_plan.income_symbols)

    plan.update({
        "strategy_assets": portfolio_plan.strategy_symbols,
        "limit_order_symbols": tuple(risk_symbols + income_symbols),
        "portfolio_rows": portfolio_plan.portfolio_rows,
        "available_cash": portfolio_plan.liquid_cash,
        "market_values": dict(portfolio_plan.market_values),
        "quantities": dict(portfolio_plan.quantities),
        "sellable_quantities": dict(portfolio_plan.sellable_quantities or {}),
        "total_strategy_equity": portfolio_plan.total_equity,
        "current_min_trade": float(annotations.current_min_trade or 0.0),
        "targets": dict(portfolio_plan.target_values),
        "market_status": annotations.status_display or "",
        "signal_message": annotations.signal_display or "",
        "deploy_ratio_text": annotations.deploy_ratio_text or "",
        "income_ratio_text": annotations.income_ratio_text or "",
        "income_locked_ratio_text": annotations.income_locked_ratio_text or "",
        "active_risk_asset": annotations.active_risk_asset,
        "investable_cash": float(annotations.investable_cash or portfolio_plan.liquid_cash),
        "threshold_value": float(annotations.trade_threshold_value),
    })
    return plan
