import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPO_ROOT = ROOT.parent.parent if ROOT.parent.name == ".worktrees" else ROOT
QPK_PIN_WORKTREE_SRC = (
    REPO_ROOT.parent / "QuantPlatformKit" / ".worktrees" / "pin-d51bb79" / "src"
)
QPK_DRIFT_WORKTREE_SRC = (
    REPO_ROOT.parent / "QuantPlatformKit" / ".worktrees" / "drift-to-new-risk-a" / "src"
)
QPK_SRC = REPO_ROOT.parent / "QuantPlatformKit" / "src"
for qpk_src in (QPK_DRIFT_WORKTREE_SRC, QPK_SRC, QPK_PIN_WORKTREE_SRC):
    if (qpk_src / "quant_platform_kit").exists() and str(qpk_src) not in sys.path:
        sys.path.insert(0, str(qpk_src))

from application.account_new_risk_gate_support import (
    ACCOUNT_NEW_RISK_GATE_ENV,
    apply_combined_scale,
    build_account_new_risk_snapshot,
    build_snapshot_from_portfolio,
    evaluate_portfolio_new_risk_admission,
    maybe_inject_production_drift_status,
    new_risk_buy_prohibited,
    set_cycle_snapshot,
)
from application.execution_service import execute_rebalance_cycle
from application.longbridge_execution import submit_order
from notifications.telegram import build_translator
from quant_platform_kit.common.models import ExecutionReport, QuoteSnapshot
from quant_platform_kit.common.port_adapters import CallableExecutionPort, CallableMarketDataPort
from quant_platform_kit.risk.account_new_risk_gate import NewRiskDisposition


class AccountNewRiskGateSupportTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_cycle_snapshot(None)
        os.environ.pop(ACCOUNT_NEW_RISK_GATE_ENV, None)

    def test_missing_equity_prohibits_fail_closed(self) -> None:
        portfolio = {"market_values": {"SOXL": 0.0}, "liquid_cash": 100.0}
        self.assertEqual(
            build_account_new_risk_snapshot(portfolio),
            {
                "observation_status": "UNAVAILABLE",
                "reconciliation_status": "UNVERIFIED",
                "circuit_breaker_state": "CLOSED",
                "equity_usd": None,
            },
        )
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(result))
        self.assertIn("EQUITY_UNKNOWN_FAIL_CLOSED", result.reason_codes)

    def test_healthy_equity_without_explicit_snapshot_allows_new_risk(self) -> None:
        """Cycle-health axes derive from resolved equity; no injected snapshot required."""
        portfolio = {"total_strategy_equity": 50_000.0}
        self.assertEqual(
            build_account_new_risk_snapshot(portfolio),
            {
                "observation_status": "COMPLETE",
                "reconciliation_status": "VERIFIED",
                "circuit_breaker_state": "CLOSED",
                "equity_usd": 50_000.0,
            },
        )
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

    def test_snapshot_maps_production_drift_status_from_account_new_risk_snapshot(self) -> None:
        snapshot = build_snapshot_from_portfolio(
            {
                "total_strategy_equity": 10_000.0,
                "account_new_risk_snapshot": {"production_drift_status": "review"},
            }
        )
        self.assertEqual(snapshot.production_drift_status, "review")

    def test_production_drift_review_prohibits_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"production_drift_status": "review"},
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("PRODUCTION_DRIFT_REVIEW", result.reason_codes)

    def test_production_drift_critical_prohibits_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"production_drift_status": "critical"},
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("PRODUCTION_DRIFT_CRITICAL", result.reason_codes)

    def test_absent_production_drift_status_still_allows_when_healthy(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

    def test_maybe_inject_fills_production_drift_from_store_resolver(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        with patch(
            "quant_platform_kit.risk.production_drift_new_risk.resolve_production_drift_status_from_store",
            return_value="review",
        ):
            injected = maybe_inject_production_drift_status(
                portfolio,
                strategy_profile="demo_profile",
                domain="us_equity",
            )
        self.assertEqual(
            injected["account_new_risk_snapshot"]["production_drift_status"],
            "review",
        )
        result = evaluate_portfolio_new_risk_admission(injected)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("PRODUCTION_DRIFT_REVIEW", result.reason_codes)

    def test_maybe_inject_does_not_overwrite_explicit_status(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"production_drift_status": "critical"},
        }
        with patch(
            "quant_platform_kit.risk.production_drift_new_risk.resolve_production_drift_status_from_store",
            return_value="review",
        ) as resolve:
            injected = maybe_inject_production_drift_status(
                portfolio,
                strategy_profile="demo_profile",
                domain="us_equity",
            )
        resolve.assert_not_called()
        self.assertEqual(
            injected["account_new_risk_snapshot"]["production_drift_status"],
            "critical",
        )

    def test_maybe_inject_leaves_absent_when_resolver_returns_none(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        with patch(
            "quant_platform_kit.risk.production_drift_new_risk.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            injected = maybe_inject_production_drift_status(
                portfolio,
                strategy_profile="demo_profile",
                domain="us_equity",
            )
        self.assertNotIn("account_new_risk_snapshot", injected)
        result = evaluate_portfolio_new_risk_admission(injected)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

    def test_maybe_inject_leaves_absent_when_resolver_raises(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        with patch(
            "quant_platform_kit.risk.production_drift_new_risk.resolve_production_drift_status_from_store",
            side_effect=RuntimeError("store unavailable"),
        ):
            injected = maybe_inject_production_drift_status(
                portfolio,
                strategy_profile="demo_profile",
                domain="us_equity",
            )
        self.assertNotIn("account_new_risk_snapshot", injected)

    def test_unknown_pending_orders_prohibits_new_risk(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0, "unknown_pending_orders": True}
        snapshot = build_account_new_risk_snapshot(portfolio)
        self.assertEqual(snapshot["reconciliation_status"], "UNVERIFIED")
        self.assertEqual(snapshot["circuit_breaker_state"], "OPEN")
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(result))

    def test_unknown_pending_orders_from_metadata_prohibits_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "metadata": {"unknown_pending_orders": True},
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(result))

    def test_durable_breaker_open_prohibits_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "durable_circuit_breaker_state": "OPEN",
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(result))

    def test_durable_breaker_absent_does_not_trip_breaker(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        snapshot = build_account_new_risk_snapshot(portfolio)
        self.assertEqual(snapshot["circuit_breaker_state"], "CLOSED")

    def test_drawdown_brake_prohibits_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 85_000.0,
            "account_new_risk_snapshot": {
                "peak_equity_usd": 100_000.0,
            },
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(result))
        self.assertIn("DRAWDOWN_BRAKE_TRIPPED", result.reason_codes)

    def test_equity_without_snapshot_allows_new_risk(self) -> None:
        """Healthy resolved equity alone now derives cycle-health axes (ALLOW)."""
        portfolio = {"total_strategy_equity": 50_000.0}
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

    def test_explicit_healthy_snapshot_allows_new_risk(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {
                "observation_status": "COMPLETE",
                "reconciliation_status": "VERIFIED",
                "circuit_breaker_state": "CLOSED",
            },
        }
        result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)
        self.assertFalse(result.live_authority_granted)

    def test_missing_combined_scale_is_no_op(self) -> None:
        self.assertEqual(apply_combined_scale(4.0, None), 4.0)

    def test_submit_order_blocks_buy_when_equity_missing(self) -> None:
        set_cycle_snapshot(build_snapshot_from_portfolio({}))
        attempts = {"count": 0}

        def fake_submit(*_args, **_kwargs):
            attempts["count"] += 1
            return ExecutionReport(symbol="SOXL", side="buy", quantity=1.0, status="submitted")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit):
            report = submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="buy",
                quantity=1.0,
            )

        self.assertEqual(attempts["count"], 0)
        self.assertEqual(report.status, "rejected")
        self.assertEqual(report.raw_payload.get("detail"), "account_new_risk_gate")

    def test_submit_order_halves_buy_quantity_for_half_scale(self) -> None:
        set_cycle_snapshot(
            build_snapshot_from_portfolio(
                {
                    "total_strategy_equity": 40_000.0,
                    "account_new_risk_snapshot": {
                        "observation_status": "COMPLETE",
                        "reconciliation_status": "VERIFIED",
                        "circuit_breaker_state": "CLOSED",
                        "drawdown_from_peak": 0.075,
                    },
                }
            )
        )
        submitted = {}

        def fake_submit(*_args, **kwargs):
            submitted.update(kwargs)
            return ExecutionReport(symbol="SOXL", side="buy", quantity=kwargs["quantity"], status="submitted")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit):
            submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="buy",
                quantity=4.0,
            )

        self.assertEqual(submitted["quantity"], 2.0)

    def test_submit_order_allows_sell_when_buy_prohibited(self) -> None:
        set_cycle_snapshot(build_snapshot_from_portfolio({}))
        submitted = {}

        def fake_submit(*_args, **kwargs):
            submitted.update(kwargs)
            return ExecutionReport(symbol="SOXL", side="sell", quantity=kwargs["quantity"], status="submitted")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit):
            report = submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="sell",
                quantity=1.0,
            )

        self.assertEqual(submitted["quantity"], 1.0)
        self.assertEqual(report.status, "submitted")

    def test_submit_order_allows_buy_when_healthy(self) -> None:
        set_cycle_snapshot(
            build_snapshot_from_portfolio(
                {
                    "total_strategy_equity": 50_000.0,
                    "account_new_risk_snapshot": {
                        "observation_status": "COMPLETE",
                        "reconciliation_status": "VERIFIED",
                        "circuit_breaker_state": "CLOSED",
                    },
                }
            )
        )
        attempts = {"count": 0}

        def fake_submit(*_args, **_kwargs):
            attempts["count"] += 1
            return ExecutionReport(symbol="SOXL", side="buy", quantity=1.0, status="submitted")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit):
            report = submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="buy",
                quantity=1.0,
            )

        self.assertEqual(attempts["count"], 1)
        self.assertEqual(report.status, "submitted")

    def test_gate_disabled_via_env_skips_buy_block(self) -> None:
        os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "0"
        set_cycle_snapshot(build_snapshot_from_portfolio({}))
        attempts = {"count": 0}

        def fake_submit(*_args, **_kwargs):
            attempts["count"] += 1
            return ExecutionReport(symbol="SOXL", side="buy", quantity=1.0, status="submitted")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit):
            report = submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="buy",
                quantity=1.0,
            )

        self.assertEqual(attempts["count"], 1)
        self.assertEqual(report.status, "submitted")


class AccountNewRiskGateExecutionCycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_cycle_snapshot(None)
        os.environ.pop(ACCOUNT_NEW_RISK_GATE_ENV, None)

    def _execution_port(self, submitted_orders):
        def _submit(order_intent):
            submitted_orders.append(order_intent)
            return ExecutionReport(
                symbol=order_intent.symbol,
                side=order_intent.side,
                quantity=order_intent.quantity,
                status="submitted",
                broker_order_id="lb-order-pending",
            )

        return CallableExecutionPort(_submit)

    def _run_buy_cycle(self, *, portfolio_overrides=None, execution_overrides=None, dry_run_only=False):
        submitted_orders = []
        plan = {
            "strategy_profile": "soxl_soxx_trend_income",
            "allocation": {
                "target_mode": "value",
                "strategy_symbols": ("SOXL",),
                "risk_symbols": ("SOXL",),
                "income_symbols": (),
                "safe_haven_symbols": (),
                "targets": {"SOXL": 400.0},
            },
            "portfolio": {
                "strategy_symbols": ("SOXL",),
                "portfolio_rows": (("SOXL",),),
                "market_values": {"SOXL": 0.0},
                "quantities": {"SOXL": 0},
                "sellable_quantities": {"SOXL": 0},
                "total_equity": 50_000.0,
                "total_strategy_equity": 50_000.0,
                "liquid_cash": 500.0,
                "cash_sweep_symbol": None,
                "cash_by_currency": {},
            },
            "execution": {
                "current_min_trade": 10.0,
                "trade_threshold_value": 10.0,
                "investable_cash": 500.0,
                "signal_date": "2026-04-21",
                "effective_date": "2026-04-21",
            },
        }
        if portfolio_overrides:
            plan["portfolio"].update(portfolio_overrides)
        if execution_overrides:
            plan["execution"].update(execution_overrides)

        result = execute_rebalance_cycle(
            trade_context=object(),
            plan=plan,
            portfolio=plan["portfolio"],
            execution=plan["execution"],
            allocation=plan["allocation"],
            fetch_replanned_state=lambda: (
                plan,
                plan["portfolio"],
                plan["execution"],
                plan["allocation"],
            ),
            market_data_port=CallableMarketDataPort(
                quote_loader=lambda symbol: QuoteSnapshot(
                    symbol=symbol,
                    as_of="2026-08-24",
                    last_price=100.0,
                )
            ),
            estimate_max_purchase_quantity=lambda *_args, **_kwargs: 5,
            execution_port=self._execution_port(submitted_orders),
            notify_issue=lambda _title, _detail: None,
            translator=build_translator("en"),
            with_prefix=lambda message: message,
            limit_sell_discount=0.995,
            limit_buy_premium=1.0,
            dry_run_only=dry_run_only,
        )
        return result, submitted_orders

    def test_execution_cycle_blocks_buys_when_equity_missing(self) -> None:
        result, submitted_orders = self._run_buy_cycle(
            portfolio_overrides={
                "total_equity": None,
                "total_strategy_equity": None,
            }
        )
        self.assertFalse(result.action_done)
        self.assertEqual(submitted_orders, [])
        self.assertEqual(
            result.portfolio["account_new_risk_snapshot"]["observation_status"],
            "UNAVAILABLE",
        )
        self.assertTrue(any("Account new-risk gate" in note for note in result.note_logs))

    def test_execution_cycle_allows_buys_without_explicit_snapshot(self) -> None:
        """Healthy resolved equity alone now derives cycle-health axes (ALLOW)."""
        result, submitted_orders = self._run_buy_cycle()
        self.assertTrue(result.action_done)
        self.assertEqual(len(submitted_orders), 1)

    def test_execution_cycle_blocks_buys_when_unknown_pending_orders(self) -> None:
        result, submitted_orders = self._run_buy_cycle(
            portfolio_overrides={"unknown_pending_orders": True},
        )
        self.assertFalse(result.action_done)
        self.assertEqual(submitted_orders, [])

    def test_execution_cycle_allows_buys_when_healthy(self) -> None:
        result, submitted_orders = self._run_buy_cycle(
            portfolio_overrides={
                "account_new_risk_snapshot": {
                    "observation_status": "COMPLETE",
                    "reconciliation_status": "VERIFIED",
                    "circuit_breaker_state": "CLOSED",
                },
            }
        )
        self.assertTrue(result.action_done)
        self.assertEqual(len(submitted_orders), 1)
        self.assertEqual(str(getattr(submitted_orders[0], "side", "")).lower(), "buy")

    def test_execution_cycle_allows_sell_when_buy_prohibited(self) -> None:
        submitted_orders = []
        plan = {
            "strategy_profile": "soxl_soxx_trend_income",
            "allocation": {
                "target_mode": "value",
                "strategy_symbols": ("SOXL",),
                "risk_symbols": ("SOXL",),
                "income_symbols": (),
                "safe_haven_symbols": (),
                "targets": {"SOXL": 0.0},
            },
            "portfolio": {
                "strategy_symbols": ("SOXL",),
                "portfolio_rows": (("SOXL",),),
                "market_values": {"SOXL": 400.0},
                "quantities": {"SOXL": 4},
                "sellable_quantities": {"SOXL": 4},
                "total_equity": None,
                "total_strategy_equity": None,
                "liquid_cash": 100.0,
                "cash_sweep_symbol": None,
                "cash_by_currency": {},
            },
            "execution": {
                "current_min_trade": 10.0,
                "trade_threshold_value": 10.0,
                "investable_cash": 100.0,
                "signal_date": "2026-04-21",
                "effective_date": "2026-04-21",
            },
        }
        result = execute_rebalance_cycle(
            trade_context=object(),
            plan=plan,
            portfolio=plan["portfolio"],
            execution=plan["execution"],
            allocation=plan["allocation"],
            fetch_replanned_state=lambda: (
                plan,
                plan["portfolio"],
                plan["execution"],
                plan["allocation"],
            ),
            market_data_port=CallableMarketDataPort(
                quote_loader=lambda symbol: QuoteSnapshot(
                    symbol=symbol,
                    as_of="2026-08-24",
                    last_price=100.0,
                )
            ),
            estimate_max_purchase_quantity=lambda *_args, **_kwargs: 5,
            execution_port=self._execution_port(submitted_orders),
            notify_issue=lambda _title, _detail: None,
            translator=build_translator("en"),
            with_prefix=lambda message: message,
            limit_sell_discount=0.995,
            limit_buy_premium=1.0,
        )
        self.assertTrue(result.action_done)
        self.assertEqual(len(submitted_orders), 1)
        self.assertEqual(str(getattr(submitted_orders[0], "side", "")).lower(), "sell")

    def test_dry_run_emits_gate_axes_even_when_no_execute(self) -> None:
        result, submitted_orders = self._run_buy_cycle(
            portfolio_overrides={
                "account_new_risk_snapshot": {
                    "observation_status": "COMPLETE",
                    "reconciliation_status": "VERIFIED",
                    "circuit_breaker_state": "CLOSED",
                },
            },
            execution_overrides={"no_execute": True, "risk_flags": ("no_execute",)},
            dry_run_only=True,
        )
        self.assertFalse(result.action_done)
        self.assertEqual(submitted_orders, [])
        self.assertTrue(result.execution.get("no_execute"))
        self.assertTrue(
            any(
                "disposition=ALLOW_NEW_RISK" in note
                and "observation=COMPLETE" in note
                and "reconciliation=VERIFIED" in note
                and "breaker=CLOSED" in note
                for note in result.note_logs
            )
        )


if __name__ == "__main__":
    unittest.main()
