"""Verify and represent a frozen Phase 5 funding proposal for offline LongBridge research."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Mapping

SYMBOLS = ("TQQQ", "QQQM", "BOXX")
OWNERS = {"TQQQ": "tqqq_core", "QQQM": "outer", "BOXX": "outer"}
CANDIDATE_IDS = frozenset({
    "qqqm_tqqq_guard_boxx_fixed_research_v3",
    "qqqm_boxx_matched_defense_research_v3",
    "qqqm_tqqq_guard_boxx_fixed_scale_research_v1",
    "qqqm_boxx_matched_defense_scale_research_v1",
})
POLICY_VERSION = "fixed_45_05_49_01_vs_50_00_49_01_restricted_paid_cash_v3"
TOLERANCE_USD = 1e-6


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field}: invalid number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field}: must be finite and nonnegative")
    return number


def _integer(value: Any, field: str) -> int:
    number = _number(value, field)
    if number != int(number):
        raise ValueError(f"{field}: whole shares required")
    return int(number)


def _same_money(actual: Any, expected: float, field: str) -> None:
    if abs(_number(actual, field) - expected) > TOLERANCE_USD:
        raise ValueError(f"{field}: funded proposal mismatch")


def _same_symbols(actual: Mapping[str, Any], expected: Mapping[str, int], field: str) -> None:
    if set(actual) != set(SYMBOLS) or any(
        _integer(actual[symbol], f"{field}.{symbol}") != expected[symbol]
        for symbol in SYMBOLS
    ):
        raise ValueError(f"{field}: funded proposal mismatch")


def convert_funded_phase5_proposal(plan: Mapping[str, Any], funded: Mapping[str, Any]) -> dict:
    """Check a frozen funded research step before making LongBridge-shaped evidence.

    No broker object, market-data port, release receipt, or order object is
    accepted. The returned rows are offline research representations only.
    """
    if plan.get("candidate_id") not in CANDIDATE_IDS or plan.get("budget_policy_version") != POLICY_VERSION:
        raise ValueError("PHASE5_IDENTITY")
    if funded.get("candidate_id") != plan["candidate_id"]:
        raise ValueError("PHASE5_FUNDED_IDENTITY")
    signal = date.fromisoformat(str(plan["signal_date"]))
    execution = date.fromisoformat(str(plan["execution_date"]))
    quote = date.fromisoformat(str(plan["quote_date"]))
    if not signal < execution or quote != execution:
        raise ValueError("PHASE5_TIME_CAUSALITY")
    if funded.get("signal_date") != signal.isoformat() or funded.get("execution_date") != execution.isoformat():
        raise ValueError("PHASE5_FUNDED_TIME")
    if plan.get("company_actions_applied") is not True:
        raise ValueError("PHASE5_COMPANY_ACTIONS")
    if funded.get("scope") != "offline_research_open_price_simulation_only":
        raise ValueError("PHASE5_FUNDED_SCOPE")
    if funded.get("status") not in {"FEASIBLE_RESEARCH_PROPOSAL", "FUNDED_SHORTFALL"}:
        raise ValueError("PHASE5_FUNDED_STATUS")
    if type(plan.get("session_index")) is not int or plan["session_index"] < 0:
        raise ValueError("PHASE5_SESSION_INDEX")
    for field in ("option_liability_usd", "collateral_usd", "external_cash_flow_usd"):
        if _number(plan.get(field, 0), field):
            raise ValueError(f"PHASE5_UNSUPPORTED_{field.upper()}")

    prices = {symbol: _number(plan["open_prices_usd"][symbol], f"open_prices_usd.{symbol}")
              for symbol in SYMBOLS}
    if set(plan["open_prices_usd"]) != set(SYMBOLS) or any(not price for price in prices.values()):
        raise ValueError("PHASE5_PRICES")
    if set(plan["account_shares"]) != set(SYMBOLS):
        raise ValueError("PHASE5_ACCOUNT_SYMBOLS")
    before = {symbol: _integer(plan["account_shares"][symbol], f"account_shares.{symbol}")
              for symbol in SYMBOLS}
    targets_by_owner = plan["target_usd_by_owner"]
    positions_by_owner = plan["positions_shares_by_owner"]
    if set(targets_by_owner) != set(OWNERS.values()) or set(positions_by_owner) != set(OWNERS.values()):
        raise ValueError("PHASE5_OWNER_SET")
    targets = {}
    target_shares = {}
    for owner in ("tqqq_core", "outer"):
        if set(targets_by_owner[owner]) - set(SYMBOLS) or set(positions_by_owner[owner]) - set(SYMBOLS):
            raise ValueError("PHASE5_OWNER_SYMBOLS")
        targets[owner] = {symbol: _number(targets_by_owner[owner].get(symbol, 0),
                                           f"targets.{owner}.{symbol}") for symbol in SYMBOLS}
        target_shares[owner] = {symbol: math.floor(targets[owner][symbol] / prices[symbol])
                                for symbol in SYMBOLS}
        for symbol in SYMBOLS:
            member_before = _integer(positions_by_owner[owner].get(symbol, 0),
                                     f"positions.{owner}.{symbol}")
            if owner != OWNERS[symbol] and (member_before or targets[owner][symbol]):
                raise ValueError("PHASE5_UNSUPPORTED_MEMBER_OWNERSHIP")
    if any(before[symbol] != sum(_integer(positions_by_owner[owner].get(symbol, 0),
                                          f"positions.{owner}.{symbol}")
                                        for owner in ("tqqq_core", "outer")) for symbol in SYMBOLS):
        raise ValueError("PHASE5_MEMBER_ACCOUNT_IDENTITY")
    if funded.get("target_usd_by_owner") != targets or funded.get("target_shares_by_owner") != target_shares:
        raise ValueError("PHASE5_FUNDED_TARGET")
    total_target = {symbol: sum(target_shares[owner][symbol] for owner in ("tqqq_core", "outer"))
                    for symbol in SYMBOLS}
    fee_bps = _number(plan["cost_bps_per_side"], "cost_bps_per_side")
    if fee_bps not in {5, 10, 15}:
        raise ValueError("PHASE5_UNSUPPORTED_COST")
    rate = fee_bps / 10_000.0
    budget = _number(plan["tqqq_member_budget_usd"], "tqqq_member_budget_usd")
    cash_target = _number(plan["outer_cash_target_usd"], "outer_cash_target_usd")
    if targets["tqqq_core"]["TQQQ"] > budget + TOLERANCE_USD:
        raise ValueError("PHASE5_MEMBER_TARGET_BUDGET")
    cash = _number(plan["settled_cash_usd"], "settled_cash_usd")
    restricted = _number(plan["restricted_paid_cash_usd"], "restricted_paid_cash_usd")
    if restricted > cash + TOLERANCE_USD:
        raise ValueError("PHASE5_RESTRICTED_CASH")
    if plan["pending_sales"] or plan["unpaid_receivables"]:
        raise ValueError("PHASE5_UNSUPPORTED_PENDING_OR_RECEIVABLE")
    if any(before.values()):
        raise ValueError("PHASE5_UNSUPPORTED_EXISTING_HOLDINGS")

    # This first-open slice verifies a previously funded proposal. It does not
    # recreate the strategy's budget or state machine.
    expected_rows = []
    shares = dict(before)
    costs = {symbol: 0.0 for symbol in SYMBOLS}
    shortfalls = {}
    for symbol in SYMBOLS:
        gap = total_target[symbol] - shares[symbol]
        reserve = 0.0 if symbol == "TQQQ" else max(0.0, budget - shares["TQQQ"] * prices["TQQQ"])
        available = max(0.0, cash - restricted - cash_target - reserve)
        affordable = math.floor((available + 1e-9) / (prices[symbol] * (1.0 + rate)))
        fill = min(gap, affordable)
        if fill:
            cost = fill * prices[symbol] * rate
            expected_rows.append({"symbol": symbol, "side": "buy", "shares": fill,
                                  "owner": OWNERS[symbol], "open_price_usd": prices[symbol],
                                  "research_cost_usd": cost})
            cash -= fill * prices[symbol] + cost
            costs[symbol] = cost
            shares[symbol] += fill
        if fill < gap:
            shortfalls[symbol] = {"target_shares": total_target[symbol],
                                  "proposed_shares": shares[symbol],
                                  "reason": "insufficient_free_settled_cash"}
    actual_rows = tuple(funded["simulated_trades"])
    if len(actual_rows) != len(expected_rows):
        raise ValueError("PHASE5_FUNDED_TRADES")
    for actual, expected in zip(actual_rows, expected_rows, strict=True):
        if type(actual.get("shares")) is not int or actual["shares"] <= 0:
            raise ValueError("PHASE5_FUNDED_WHOLE_SHARES")
        if set(actual) != set(expected) or any(
            actual[key] != expected[key] for key in ("symbol", "side", "shares", "owner")
        ):
            raise ValueError("PHASE5_FUNDED_TRADES")
        _same_money(actual["open_price_usd"], expected["open_price_usd"], "trade.open_price_usd")
        _same_money(actual["research_cost_usd"], expected["research_cost_usd"],
                    "trade.research_cost_usd")
    if funded.get("unfilled_target_shares") != shortfalls or funded["status"] != (
        "FUNDED_SHORTFALL" if shortfalls else "FEASIBLE_RESEARCH_PROPOSAL"
    ):
        raise ValueError("PHASE5_FUNDED_SHORTFALL")
    state = funded["simulated_post_open_state"]
    _same_symbols(state["account_shares"], shares, "post.account_shares")
    for owner in ("tqqq_core", "outer"):
        expected_member = {symbol: shares[symbol] if OWNERS[symbol] == owner else 0
                           for symbol in SYMBOLS}
        _same_symbols(state["positions_shares_by_owner"][owner], expected_member,
                      f"post.positions.{owner}")
    if set(state["positions_shares_by_owner"]) != {"tqqq_core", "outer"}:
        raise ValueError("PHASE5_FUNDED_OWNER_SET")
    if state["pending_sales"] or state["unpaid_receivables"]:
        raise ValueError("PHASE5_FUNDED_UNSUPPORTED_STATE")
    _same_money(state["settled_cash_usd"], cash, "post.settled_cash_usd")
    _same_money(state["restricted_paid_cash_usd"], restricted, "post.restricted_paid_cash_usd")
    if set(funded["net_account_proposed_deltas"]) != set(SYMBOLS) or any(
        _integer(funded["net_account_proposed_deltas"][symbol], f"delta.{symbol}") != shares[symbol]
        for symbol in SYMBOLS
    ):
        raise ValueError("PHASE5_FUNDED_ACCOUNT_DELTA")
    if set(funded["research_cost_usd_by_symbol"]) != set(SYMBOLS):
        raise ValueError("PHASE5_FUNDED_COST_SYMBOLS")
    for symbol in SYMBOLS:
        _same_money(funded["research_cost_usd_by_symbol"][symbol], costs[symbol], f"cost.{symbol}")
    total_cost = math.fsum(costs.values())
    _same_money(funded["research_total_cost_usd"], total_cost, "research_total_cost_usd")
    reserve_after = max(0.0, budget - shares["TQQQ"] * prices["TQQQ"])
    _same_money(funded["member_reserved_cash_after_usd"], reserve_after,
                "member_reserved_cash_after_usd")
    _same_money(funded["outer_free_settled_cash_after_usd"],
                max(0.0, cash - restricted - reserve_after), "outer_free_settled_cash_after_usd")
    prior_nav = _number(plan["settled_cash_usd"], "settled_cash_usd")
    after_nav = cash + math.fsum(shares[symbol] * prices[symbol] for symbol in SYMBOLS)
    _same_money(funded["pre_open_account_nav_usd"], prior_nav, "pre_open_account_nav_usd")
    _same_money(funded["simulated_post_open_account_nav_usd"], after_nav,
                "simulated_post_open_account_nav_usd")
    identity_error = abs(prior_nav - total_cost - after_nav)
    _same_money(funded["account_identity_error_usd"], identity_error,
                "account_identity_error_usd")
    _same_money(funded["released_pending_sale_usd"], 0.0, "released_pending_sale_usd")
    if identity_error > TOLERANCE_USD:
        raise ValueError("PHASE5_ACCOUNT_IDENTITY")

    rows = tuple({"symbol": row["symbol"], "platform_symbol": f"{row['symbol']}.US",
                  "side": "buy", "quantity": row["shares"], "quantity_step": 1,
                  "owner": row["owner"], "reference_open_price_usd": row["open_price_usd"],
                  "reference_notional_usd": row["shares"] * row["open_price_usd"],
                  "research_cost_usd": row["research_cost_usd"],
                  "before_shares": before[row["symbol"]], "proposed_after_shares": shares[row["symbol"]],
                  "exposure_effect": "increases"} for row in expected_rows)
    return {"status": funded["status"], "candidate_id": plan["candidate_id"],
            "signal_date": signal.isoformat(), "execution_date": execution.isoformat(),
            "target_usd_by_owner": targets, "target_shares_by_owner": target_shares,
            "offline_proposals": rows, "unfilled_target_shares": shortfalls,
            "research_total_cost_usd": total_cost,
            "simulated_post_open_state": {"account_shares": shares, "settled_cash_usd": cash,
                                          "member_reserved_cash_usd": reserve_after,
                                          "restricted_paid_cash_usd": restricted},
            "account_identity_error_usd": identity_error,
            "scope": "longbridge_offline_research_conversion_only"}
