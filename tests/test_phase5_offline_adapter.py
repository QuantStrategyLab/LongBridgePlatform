"""Direct checks for the bounded LongBridge Phase 5 offline research conversion."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from research.phase5_offline_adapter import POLICY_VERSION, convert_funded_phase5_proposal


def _plan(**changes):
    plan = {
        "candidate_id": "qqqm_tqqq_guard_boxx_fixed_research_v3",
        "budget_policy_version": POLICY_VERSION,
        "signal_date": "2023-03-27", "execution_date": "2023-03-28",
        "quote_date": "2023-03-28", "session_index": 0,
        "company_actions_applied": True,
        "open_prices_usd": {"TQQQ": 100, "QQQM": 50, "BOXX": 100},
        "target_usd_by_owner": {"tqqq_core": {"TQQQ": 200},
                                "outer": {"QQQM": 250, "BOXX": 300}},
        "positions_shares_by_owner": {"tqqq_core": {}, "outer": {}},
        "account_shares": {"TQQQ": 0, "QQQM": 0, "BOXX": 0},
        "settled_cash_usd": 1000, "restricted_paid_cash_usd": 0,
        "pending_sales": [], "unpaid_receivables": [],
        "tqqq_member_budget_usd": 300, "outer_cash_target_usd": 10,
        "cost_bps_per_side": 10,
        "option_liability_usd": 0, "collateral_usd": 0,
        "external_cash_flow_usd": 0,
    }
    plan.update(changes)
    return plan


def _funded():
    trades = (
        {"symbol": "TQQQ", "side": "buy", "shares": 2, "owner": "tqqq_core",
         "open_price_usd": 100.0, "research_cost_usd": 0.2},
        {"symbol": "QQQM", "side": "buy", "shares": 5, "owner": "outer",
         "open_price_usd": 50.0, "research_cost_usd": 0.25},
        {"symbol": "BOXX", "side": "buy", "shares": 3, "owner": "outer",
         "open_price_usd": 100.0, "research_cost_usd": 0.3},
    )
    return {
        "status": "FEASIBLE_RESEARCH_PROPOSAL",
        "candidate_id": "qqqm_tqqq_guard_boxx_fixed_research_v3",
        "signal_date": "2023-03-27", "execution_date": "2023-03-28",
        "target_usd_by_owner": {"tqqq_core": {"TQQQ": 200.0, "QQQM": 0.0, "BOXX": 0.0},
                                "outer": {"TQQQ": 0.0, "QQQM": 250.0, "BOXX": 300.0}},
        "target_shares_by_owner": {"tqqq_core": {"TQQQ": 2, "QQQM": 0, "BOXX": 0},
                                   "outer": {"TQQQ": 0, "QQQM": 5, "BOXX": 3}},
        "simulated_trades": trades, "unfilled_target_shares": {},
        "research_cost_usd_by_symbol": {"TQQQ": 0.2, "QQQM": 0.25, "BOXX": 0.3},
        "research_total_cost_usd": 0.75,
        "net_account_proposed_deltas": {"TQQQ": 2, "QQQM": 5, "BOXX": 3},
        "member_reserved_cash_after_usd": 100.0,
        "outer_free_settled_cash_after_usd": 149.25,
        "released_pending_sale_usd": 0.0,
        "pre_open_account_nav_usd": 1000.0,
        "simulated_post_open_account_nav_usd": 999.25,
        "account_identity_error_usd": 0.0,
        "simulated_post_open_state": {
            "account_shares": {"TQQQ": 2, "QQQM": 5, "BOXX": 3},
            "positions_shares_by_owner": {
                "tqqq_core": {"TQQQ": 2, "QQQM": 0, "BOXX": 0},
                "outer": {"TQQQ": 0, "QQQM": 5, "BOXX": 3}},
            "settled_cash_usd": 249.25, "restricted_paid_cash_usd": 0.0,
            "pending_sales": (), "unpaid_receivables": (),
        },
        "scope": "offline_research_open_price_simulation_only",
    }


def test_whole_share_longbridge_representation_and_account_identity():
    plan, funded = _plan(), _funded()
    before = copy.deepcopy((plan, funded))
    first = convert_funded_phase5_proposal(plan, funded)
    assert first == convert_funded_phase5_proposal(plan, funded)
    assert (plan, funded) == before
    assert first["status"] == "FEASIBLE_RESEARCH_PROPOSAL"
    assert [row["platform_symbol"] for row in first["offline_proposals"]] == [
        "TQQQ.US", "QQQM.US", "BOXX.US"]
    assert [row["quantity"] for row in first["offline_proposals"]] == [2, 5, 3]
    assert all(row["quantity_step"] == 1 and row["exposure_effect"] == "increases"
               for row in first["offline_proposals"])
    assert first["simulated_post_open_state"]["settled_cash_usd"] == pytest.approx(249.25)
    assert first["simulated_post_open_state"]["member_reserved_cash_usd"] == 100
    assert first["account_identity_error_usd"] == 0


@pytest.mark.parametrize("path,value", [
    (("simulated_trades", 0, "shares"), 2.5),
    (("simulated_trades", 0, "owner"), "outer"),
    (("simulated_trades", 0, "open_price_usd"), 101),
    (("simulated_trades", 0, "research_cost_usd"), 0),
    (("simulated_trades", 0, "side"), "sell"),
    (("simulated_post_open_state", "settled_cash_usd"), 250),
    (("simulated_post_open_state", "account_shares", "BOXX"), 4),
    (("research_total_cost_usd",), 0),
    (("member_reserved_cash_after_usd",), 0),
    (("unfilled_target_shares",), {"BOXX": {"target_shares": 3}}),
    (("status",), "FUNDED_SHORTFALL"),
    (("candidate_id",), "wrong"),
    (("signal_date",), "2023-03-28"),
])
def test_rejects_tampered_funded_proposal(path, value):
    funded = copy.deepcopy(_funded())
    target = funded
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises((ValueError, TypeError)):
        convert_funded_phase5_proposal(_plan(), funded)


def test_rejects_unsupported_or_noncausal_input():
    funded = _funded()
    variants = (
        _plan(quote_date="2023-03-29"),
        _plan(open_prices_usd={"TQQQ": 101, "QQQM": 50, "BOXX": 100}),
        _plan(account_shares={"TQQQ": 1, "QQQM": 0, "BOXX": 0}),
        _plan(pending_sales=[{"symbol": "TQQQ", "net_amount_usd": 100,
                              "release_session_index": 2}]),
        _plan(unpaid_receivables=[{"symbol": "QQQM", "amount_usd": 1}]),
        _plan(option_liability_usd=1),
        _plan(target_usd_by_owner={"tqqq_core": {"TQQQ": 200},
                                   "outer": {"TQQQ": 1, "QQQM": 250, "BOXX": 300}}),
    )
    for plan in variants:
        with pytest.raises((ValueError, TypeError)):
            convert_funded_phase5_proposal(plan, funded)


def test_zero_share_and_funded_shortfall_are_explicit():
    plan = _plan(target_usd_by_owner={"tqqq_core": {"TQQQ": 0},
                                     "outer": {"QQQM": 250, "BOXX": 300}})
    funded = copy.deepcopy(_funded())
    funded["target_usd_by_owner"]["tqqq_core"]["TQQQ"] = 0.0
    funded["target_shares_by_owner"]["tqqq_core"]["TQQQ"] = 0
    funded["simulated_trades"] = funded["simulated_trades"][1:]
    funded["research_cost_usd_by_symbol"]["TQQQ"] = 0.0
    funded["research_total_cost_usd"] = 0.55
    funded["net_account_proposed_deltas"]["TQQQ"] = 0
    funded["member_reserved_cash_after_usd"] = 300.0
    funded["outer_free_settled_cash_after_usd"] = 149.45
    funded["simulated_post_open_state"]["account_shares"]["TQQQ"] = 0
    funded["simulated_post_open_state"]["positions_shares_by_owner"]["tqqq_core"]["TQQQ"] = 0
    funded["simulated_post_open_state"]["settled_cash_usd"] = 449.45
    funded["simulated_post_open_account_nav_usd"] = 999.45
    result = convert_funded_phase5_proposal(plan, funded)
    assert [row["symbol"] for row in result["offline_proposals"]] == ["QQQM", "BOXX"]


@pytest.mark.skipif(not all(os.environ.get(name) for name in (
    "QSL_PHASE5_PRIVATE_ROOT", "QSL_PHASE5_UES_ROOT", "QSL_PHASE5_SCHWAB_ROOT")),
    reason="approved local frozen research inputs not attached")
def test_eight_frozen_first_open_paths_preserve_funded_ledger():
    private_root = Path(os.environ["QSL_PHASE5_PRIVATE_ROOT"])
    ues_root = Path(os.environ["QSL_PHASE5_UES_ROOT"])
    schwab_root = Path(os.environ["QSL_PHASE5_SCHWAB_ROOT"])
    sys.path.insert(0, str(ues_root / "docs/research/first_compounding_20260925"))
    try:
        from boxx_outer_cash_compare import _load
        from phase5_locked_inventory_audit import (COST_BPS, PATHS, PRINCIPALS,
                                                   SCALE_SUMMARY_SHA, _ledger, _sha)
    finally:
        sys.path.pop(0)
    source = schwab_root / "research/phase5_offline_adapter.py"
    spec = importlib.util.spec_from_file_location("schwab_phase5_frozen_funding", source)
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    summary_path = private_root / "phase5_capital_scale_v1/phase5_capital_scale_summary.v1.json"
    assert _sha(summary_path) == SCALE_SUMMARY_SHA
    study = json.loads(summary_path.read_text())
    _, _, _, rows, _ = _load(private_root)
    first = next(row for row in rows if row["date"] == "2023-03-28")
    prices = {symbol: float(first[symbol.lower() + "_open"])
              for symbol in ("TQQQ", "QQQM", "BOXX")}
    count = shortfall_count = 0
    for principal in PRINCIPALS:
        for path in PATHS:
            day = _ledger(private_root, principal, path, study)[0]
            target = day["target_usd"]
            plan = _plan(candidate_id=day["candidate_id"],
                         signal_date=day["signal_date"], execution_date=day["date"],
                         quote_date=day["date"], open_prices_usd=prices,
                         target_usd_by_owner={"tqqq_core": {"TQQQ": target["TQQQ"]},
                                              "outer": {"QQQM": target["QQQM"],
                                                        "BOXX": target["BOXX"]}},
                         settled_cash_usd=principal,
                         tqqq_member_budget_usd=day["member_budget_usd"],
                         outer_cash_target_usd=day["cash_target_usd"],
                         cost_bps_per_side=COST_BPS)
            funded = upstream.adapt_phase5_first_platform_offline(plan)
            result = convert_funded_phase5_proposal(plan, funded)
            assert {row["symbol"]: row["quantity"] for row in result["offline_proposals"]} == {
                symbol: int(day["trade_shares"][symbol]) for symbol in ("TQQQ", "QQQM", "BOXX")
                if int(day["trade_shares"][symbol]) > 0}
            assert result["simulated_post_open_state"]["settled_cash_usd"] == pytest.approx(
                day["settled_cash_usd"], abs=1e-6)
            assert result["research_total_cost_usd"] == pytest.approx(
                sum(day["trade_cost_usd"].values()), abs=1e-6)
            assert result["simulated_post_open_state"]["member_reserved_cash_usd"] == pytest.approx(
                day["member_reserved_cash_at_open_usd"], abs=1e-6)
            shortfall_count += result["status"] == "FUNDED_SHORTFALL"
            count += 1
    assert count == 8 and shortfall_count == 1
    print(f"longbridge_first_open_paths={count} funded_shortfalls={shortfall_count} "
          "share_cash_cost_reserve_mismatches=0")
