from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quant_platform_kit.strategy_contracts import StrategyDecision


def _target_values(decision: StrategyDecision) -> dict[str, float]:
    target_values: dict[str, float] = {}
    for position in decision.positions:
        if position.target_value is None:
            raise ValueError(
                "LongBridge decision mapper requires target_value positions; "
                f"position {position.symbol!r} is missing target_value"
            )
        target_values[position.symbol] = float(position.target_value)
    return target_values


def _symbols_by_role(decision: StrategyDecision) -> tuple[list[str], list[str], list[str]]:
    risk_symbols: list[str] = []
    income_symbols: list[str] = []
    safe_haven_symbols: list[str] = []
    target_values = _target_values(decision)
    for position in decision.positions:
        if position.role == "safe_haven":
            safe_haven_symbols.append(position.symbol)
        elif position.role == "income":
            income_symbols.append(position.symbol)
        else:
            risk_symbols.append(position.symbol)
    risk_symbols = sorted(dict.fromkeys(risk_symbols))
    income_symbols = sorted(
        dict.fromkeys(income_symbols),
        key=lambda symbol: (-target_values.get(symbol, 0.0), symbol),
    )
    safe_haven_symbols = sorted(dict.fromkeys(safe_haven_symbols))
    return risk_symbols, income_symbols, safe_haven_symbols


def map_strategy_decision_to_plan(
    decision: StrategyDecision,
    *,
    account_state: Mapping[str, Any],
    strategy_profile: str,
) -> dict[str, Any]:
    diagnostics = dict(decision.diagnostics)
    target_values = _target_values(decision)
    risk_symbols, income_symbols, safe_haven_symbols = _symbols_by_role(decision)
    strategy_assets = tuple(risk_symbols + safe_haven_symbols + income_symbols)
    portfolio_rows = tuple(
        row
        for row in (
            tuple(risk_symbols),
            tuple(income_symbols),
            tuple(safe_haven_symbols),
        )
        if row
    )

    return {
        "strategy_profile": strategy_profile,
        "strategy_assets": strategy_assets,
        "limit_order_symbols": tuple(risk_symbols + income_symbols),
        "portfolio_rows": portfolio_rows,
        "available_cash": float(account_state["available_cash"]),
        "market_values": dict(account_state["market_values"]),
        "quantities": dict(account_state["quantities"]),
        "sellable_quantities": dict(account_state["sellable_quantities"]),
        "total_strategy_equity": float(account_state["total_strategy_equity"]),
        "current_min_trade": float(diagnostics["current_min_trade"]),
        "targets": target_values,
        "market_status": diagnostics["market_status"],
        "signal_message": diagnostics["signal_message"],
        "deploy_ratio_text": diagnostics["deploy_ratio_text"],
        "income_ratio_text": diagnostics["income_ratio_text"],
        "income_locked_ratio_text": diagnostics["income_locked_ratio_text"],
        "active_risk_asset": diagnostics.get("active_risk_asset"),
        "investable_cash": float(diagnostics["investable_cash"]),
        "threshold_value": float(diagnostics["threshold_value"]),
    }
