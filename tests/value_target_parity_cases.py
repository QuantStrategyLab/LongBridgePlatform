"""Frozen inputs for the B12 value-target facade parity test (LongBridge).

``tests/fixtures/value_target_plan_parity.json`` was produced by running
``map_strategy_decision_to_plan`` on these inputs with the legacy
``build_value_target_runtime_plan`` + mixed ``ValueTargetExecutionAnnotations``
path (LongBridgePlatform main@fe16142, QPK@350dd38). The migrated
``semantics=`` / ``display=`` / ``presentation=`` path must reproduce it exactly
after canonical JSON encoding.

Regenerate only for a reviewed, intentional behavior change:
    PYTHONPATH=. python tests/value_target_parity_cases.py --write
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.common.strategy_contracts import PositionTarget, StrategyDecision

GOLDEN_PATH = Path(__file__).with_name("fixtures") / "value_target_plan_parity.json"
_AS_OF = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)

_FULL_ANNOTATIONS = {
    "trade_threshold_value": 250.0,
    "reserved_cash": 300.0,
    "signal_display": "signal",
    "status_display": "risk-on",
    "dashboard_text": "dash",
    "separator": "---",
    "signal_date": "2026-10-08",
    "effective_date": "2026-10-09",
    "execution_timing_contract": "next_session_close",
    "execution_calendar_source": "xnys",
    "signal_effective_after_trading_days": 1,
    "benchmark_symbol": "SOXX",
    "benchmark_price": 251.5,
    "long_trend_value": 240.25,
    "exit_line": 230.0,
    "deploy_ratio_text": "60.0%",
    "income_ratio_text": "10.0%",
    "income_locked_ratio_text": "5.0%",
    "active_risk_asset": "SOXL",
    "current_min_trade": 120.0,
    "investable_cash": 9000.0,
}


def _account_state(**overrides: Any) -> dict[str, Any]:
    state = {
        "available_cash": 10000.0,
        "market_values": {"SOXL": 2000.0, "SOXX": 0.0, "BOXX": 5000.0, "QQQI": 1000.0, "SPYI": 1000.0},
        "quantities": {"SOXL": 20, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
        "sellable_quantities": {"SOXL": 20, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
        "total_strategy_equity": 19000.0,
        "cash_by_currency": {"usd": 10000.0, "SGD": 350.0, "": 1.0},
    }
    state.update(overrides)
    return state


def _snapshot(total_equity: float, buying_power: float, positions=(), metadata=None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        as_of=_AS_OF,
        total_equity=total_equity,
        buying_power=buying_power,
        positions=tuple(Position(symbol=s, quantity=q, market_value=v) for s, q, v in positions),
        metadata=dict(metadata or {}),
    )


def build_cases() -> list[tuple[str, dict[str, Any]]]:
    soxl_positions = (
        PositionTarget(symbol="SOXL", target_value=6000.0),
        PositionTarget(symbol="SOXX", target_value=0.0),
        PositionTarget(symbol="BOXX", target_value=8000.0, role="safe_haven"),
        PositionTarget(symbol="QQQI", target_value=2500.0, role="income"),
        PositionTarget(symbol="SPYI", target_value=1500.0, role="income"),
    )
    return [
        (
            "soxl_value_full_annotations_policy",
            dict(
                decision=StrategyDecision(
                    positions=soxl_positions,
                    diagnostics={
                        "execution_annotations": dict(_FULL_ANNOTATIONS),
                        "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
                        "blend_gate_volatility_delever_metric": 0.61,
                        "market_regime_control_route": "risk_reduced",
                        "snapshot_manifest_price_as_of": "2026-10-08",
                    },
                ),
                account_state=_account_state(),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata={
                    "longbridge_execution_policy": {"reserved_cash_floor_usd": 200.0, "reserved_cash_ratio": 0.05},
                    "execution_annotations": {"signal_date": "2026-10-07", "dashboard_text": "runtime dash"},
                },
            ),
        ),
        (
            "soxl_value_legacy_keys",
            dict(
                decision=StrategyDecision(
                    positions=soxl_positions,
                    diagnostics={
                        "market_status": "RISK-ON",
                        "signal_message": "legacy",
                        "deploy_ratio_text": "60.0%",
                        "income_ratio_text": "10.0%",
                        "income_locked_ratio_text": "10.0%",
                        "active_risk_asset": "SOXX+SOXL",
                        "threshold_value": 500.0,
                        "reserved": 40.0,
                        "qqq_price": 500.0,
                        "ma200": 450.0,
                    },
                ),
                account_state=_account_state(),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata=None,
            ),
        ),
        (
            "tqqq_value_layout_defaults",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="TQQQ", target_value=5000.0),
                        PositionTarget(symbol="QQQM", target_value=1000.0),
                        PositionTarget(symbol="BOXX", target_value=3000.0, role="safe_haven"),
                        PositionTarget(symbol="QQQI", target_value=500.0, role="income"),
                    ),
                    diagnostics={
                        "execution_annotations": {"trade_threshold_value": 100.0},
                        "dual_drive_volatility_delever_applied": True,
                        "dual_drive_crisis_defense_destination": "BOXX",
                    },
                ),
                account_state=_account_state(
                    market_values={"TQQQ": 3000.0, "BOXX": 2000.0},
                    quantities={"TQQQ": 30, "BOXX": 20},
                    sellable_quantities={"TQQQ": 30, "BOXX": 20},
                    total_strategy_equity=12000.0,
                    available_cash=7000.0,
                ),
                strategy_profile="tqqq_growth_income",
                runtime_metadata={"longbridge_execution_policy": {"reserved_cash_ratio": 0.02}},
            ),
        ),
        (
            "russell_weight_translation_snapshot",
            dict(
                decision=StrategyDecision(
                    positions=(
                        PositionTarget(symbol="AAPL", target_weight=0.5),
                        PositionTarget(symbol="MSFT", target_weight=0.3),
                        PositionTarget(symbol="BOXX", target_weight=0.2, role="safe_haven"),
                    ),
                    diagnostics={
                        "signal_description": "risk on",
                        "status_description": "breadth ok",
                        "benchmark_symbol": "spy",
                        "benchmark_price": 600.0,
                        "long_trend_value": 580.0,
                        "exit_line": 560.0,
                        "snapshot_as_of": "2026-10-08",
                        "execution_annotations": {
                            "effective_date": "2026-10-09",
                            "execution_timing_contract": "same_day",
                            "signal_effective_after_trading_days": 0,
                        },
                    },
                ),
                snapshot=_snapshot(20000.0, 4000.0, (("AAPL", 5.0, 1000.0),), {"cash_by_currency": {"USD": 4000.0}}),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={"longbridge_execution_policy": {"reserved_cash_floor_usd": 1500.0}},
            ),
        ),
        (
            "weight_translation_not_cash_only",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="AAPL", target_weight=0.6), PositionTarget(symbol="NVDA", target_weight=0.4)),
                    diagnostics={"signal_display": "fallback", "canary_status": "canary"},
                ),
                snapshot=_snapshot(15000.0, 500.0, (("AAPL", 50.0, 9000.0), ("NVDA", 30.0, 5700.0))),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={"longbridge_execution_policy": {"cash_only_execution": False}},
            ),
        ),
        (
            "weight_zero_equity_blocked",
            dict(
                decision=StrategyDecision(positions=(PositionTarget(symbol="AAPL", target_weight=1.0),), diagnostics={}),
                snapshot=_snapshot(0.0, 0.0),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata=None,
            ),
        ),
        (
            "weight_rejected_risk_gate_holds_current",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="AAPL", target_weight=1.0),),
                    risk_flags=("rejected:stale_inputs",),
                    diagnostics={"risk_gate": "REJECT", "no_order": True, "signal_message": "blocked"},
                ),
                snapshot=_snapshot(10000.0, 2000.0, (("AAPL", 10.0, 8000.0),)),
                strategy_profile="russell_top50_leader_rotation",
                runtime_metadata={"longbridge_execution_policy": {"reserved_cash_floor_usd": 100.0}},
            ),
        ),
        (
            "value_no_execute_keeps_value_positions",
            dict(
                decision=StrategyDecision(
                    positions=soxl_positions,
                    risk_flags=("no_execute",),
                    diagnostics={"execution_authorized": False, "execution_annotations": {"trade_threshold_value": 100.0}},
                ),
                account_state=_account_state(),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata=None,
            ),
        ),
        (
            "invalid_risk_flags_rejected",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="SOXL", target_value=1000.0),),
                    risk_flags=["not-a-tuple"],
                    diagnostics={"execution_annotations": {"trade_threshold_value": 100.0}},
                ),
                account_state=_account_state(),
                strategy_profile="soxl_soxx_trend_income",
                runtime_metadata=None,
            ),
        ),
        (
            "global_etf_value_policy_caps_investable",
            dict(
                decision=StrategyDecision(
                    positions=(PositionTarget(symbol="VTI", target_value=4000.0), PositionTarget(symbol="BIL", target_value=2000.0)),
                    diagnostics={"execution_annotations": {"trade_threshold_value": 100.0, "investable_cash": 50000.0}},
                ),
                account_state=_account_state(
                    market_values={"VTI": 1000.0},
                    quantities={"VTI": 3},
                    sellable_quantities={"VTI": 3},
                    total_strategy_equity=8000.0,
                    available_cash=7000.0,
                    cash_by_currency=None,
                ),
                strategy_profile="global_etf_rotation",
                runtime_metadata={"longbridge_execution_policy": {"reserved_cash_floor_usd": 500.0, "reserved_cash_ratio": 0.10}},
            ),
        ),
    ]


def canonical(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=_default))


def _default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    return repr(value)


def render_all() -> dict[str, Any]:
    from decision_mapper import map_strategy_decision_to_plan

    return {name: canonical(map_strategy_decision_to_plan(**kwargs)) for name, kwargs in build_cases()}


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    text = json.dumps(render_all(), indent=2, sort_keys=True) + "\n"
    if "--write" in sys.argv:
        GOLDEN_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {GOLDEN_PATH}")
    else:
        sys.stdout.write(text)
