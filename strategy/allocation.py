"""Allocation and plan helpers for LongBridgePlatform."""

from __future__ import annotations

import numpy as np


def get_dynamic_allocation(
    total_equity_usd,
    *,
    small_account_deploy_ratio,
    mid_account_deploy_ratio,
    large_account_deploy_ratio,
    trade_layer_decay_coeff,
):
    if total_equity_usd <= 10000:
        return small_account_deploy_ratio

    if total_equity_usd <= 80000:
        return float(
            np.interp(
                total_equity_usd,
                [10000, 80000],
                [small_account_deploy_ratio, mid_account_deploy_ratio],
            )
        )

    if total_equity_usd <= 180000:
        return float(
            np.interp(
                total_equity_usd,
                [80000, 180000],
                [mid_account_deploy_ratio, large_account_deploy_ratio],
            )
        )

    decayed_ratio = large_account_deploy_ratio - (
        trade_layer_decay_coeff * np.log10(total_equity_usd / 180000)
    )
    return max(0.0, decayed_ratio)


def get_income_layer_ratio(
    total_equity_usd,
    *,
    income_layer_start_usd,
    income_layer_max_ratio,
):
    if total_equity_usd <= income_layer_start_usd:
        return 0.0

    if total_equity_usd <= (income_layer_start_usd * 2):
        return float(
            np.interp(
                total_equity_usd,
                [income_layer_start_usd, income_layer_start_usd * 2],
                [0.0, income_layer_max_ratio],
            )
        )

    return income_layer_max_ratio


def build_rebalance_plan(
    indicators,
    account_state,
    *,
    trend_ma_window,
    translator,
    cash_reserve_ratio,
    min_trade_ratio,
    min_trade_floor,
    rebalance_threshold_ratio,
    small_account_deploy_ratio,
    mid_account_deploy_ratio,
    large_account_deploy_ratio,
    trade_layer_decay_coeff,
    income_layer_start_usd,
    income_layer_max_ratio,
    income_layer_qqqi_weight,
    income_layer_spyi_weight,
):
    strategy_assets = ["SOXL", "SOXX", "BOXX", "QQQI", "SPYI"]
    available_cash = account_state["available_cash"]
    market_values = account_state["market_values"]
    quantities = account_state["quantities"]
    sellable_quantities = account_state["sellable_quantities"]
    total_strategy_equity = account_state["total_strategy_equity"]
    current_min_trade = max(min_trade_floor, total_strategy_equity * min_trade_ratio)

    current_income_layer_value = market_values["QQQI"] + market_values["SPYI"]
    income_layer_ratio = get_income_layer_ratio(
        total_strategy_equity,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
    )
    desired_income_layer_value = total_strategy_equity * income_layer_ratio
    locked_income_layer_value = max(current_income_layer_value, desired_income_layer_value)
    income_layer_add_value = max(0.0, locked_income_layer_value - current_income_layer_value)
    core_equity = max(0.0, total_strategy_equity - locked_income_layer_value)
    deploy_ratio = get_dynamic_allocation(
        core_equity,
        small_account_deploy_ratio=small_account_deploy_ratio,
        mid_account_deploy_ratio=mid_account_deploy_ratio,
        large_account_deploy_ratio=large_account_deploy_ratio,
        trade_layer_decay_coeff=trade_layer_decay_coeff,
    )
    deployed_capital = core_equity * deploy_ratio
    deploy_ratio_text = f"{deploy_ratio * 100:.1f}%"
    income_ratio_text = f"{income_layer_ratio * 100:.1f}%"
    income_locked_ratio_text = (
        f"{(locked_income_layer_value / total_strategy_equity) * 100:.1f}%"
        if total_strategy_equity > 0
        else "0.0%"
    )

    soxl_price = indicators["soxl"]["price"]
    soxl_ma_trend = indicators["soxl"]["ma_trend"]
    active_risk_asset = "SOXL" if soxl_price > soxl_ma_trend else "SOXX"
    market_status = (
        f"🚀 RISK-ON ({active_risk_asset})"
        if active_risk_asset == "SOXL"
        else "🛡️ DE-LEVER (SOXX)"
    )
    signal_message = (
        translator("signal_risk_on", window=trend_ma_window, ratio=deploy_ratio_text)
        if active_risk_asset == "SOXL"
        else translator("signal_delever", window=trend_ma_window, ratio=deploy_ratio_text)
    )

    targets = {
        "SOXL": deployed_capital if active_risk_asset == "SOXL" else 0.0,
        "SOXX": deployed_capital if active_risk_asset == "SOXX" else 0.0,
        "QQQI": market_values["QQQI"] + (income_layer_add_value * income_layer_qqqi_weight),
        "SPYI": market_values["SPYI"] + (income_layer_add_value * income_layer_spyi_weight),
        "BOXX": max(0.0, core_equity - deployed_capital),
    }

    return {
        "strategy_assets": strategy_assets,
        "available_cash": available_cash,
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": sellable_quantities,
        "total_strategy_equity": total_strategy_equity,
        "current_min_trade": current_min_trade,
        "targets": targets,
        "market_status": market_status,
        "signal_message": signal_message,
        "deploy_ratio_text": deploy_ratio_text,
        "income_ratio_text": income_ratio_text,
        "income_locked_ratio_text": income_locked_ratio_text,
        "active_risk_asset": active_risk_asset,
        "investable_cash": max(0, available_cash - (total_strategy_equity * cash_reserve_ratio)),
        "threshold_value": total_strategy_equity * rebalance_threshold_ratio,
    }

