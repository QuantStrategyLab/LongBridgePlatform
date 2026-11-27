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

from longbridge_platform.application.account_new_risk_gate_support import (
    ACCOUNT_NEW_RISK_GATE_ENV,
    apply_combined_scale_to_allocation_targets,
    build_account_new_risk_snapshot,
    build_snapshot_from_portfolio,
    evaluate_portfolio_new_risk_admission,
    maybe_publish_attention_for_admission,
    new_risk_buy_prohibited,
    reset_attention_sent_keys_for_tests,
    resolve_max_daily_loss_usd,
    set_cycle_snapshot,
)
from longbridge_platform.application.execution_service import execute_rebalance_cycle
from longbridge_platform.application.longbridge_execution import submit_order
from longbridge_platform.notifications.telegram import build_translator
from quant_platform_kit.common.models import ExecutionReport, QuoteSnapshot
from quant_platform_kit.common.port_adapters import CallableExecutionPort, CallableMarketDataPort
from quant_platform_kit.risk.account_new_risk_gate import NewRiskDisposition


class AccountNewRiskGateSupportTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_cycle_snapshot(None)
        reset_attention_sent_keys_for_tests()
        os.environ.pop(ACCOUNT_NEW_RISK_GATE_ENV, None)
        os.environ.pop("LONGBRIDGE_MAX_DAILY_LOSS_USD", None)
        os.environ.pop("MAX_DAILY_LOSS_USD", None)
        os.environ.pop("RUNTIME_TARGET_JSON", None)

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
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
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

    def test_explicit_daily_loss_at_limit_prohibits_buy(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {
                "daily_loss_usd": 100.0,
                "max_daily_loss_usd": 100.0,
            },
        }
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            snapshot = build_snapshot_from_portfolio(portfolio)
            result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(snapshot.daily_loss_usd, 100.0)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("DAILY_LOSS_LIMIT_EXCEEDED", result.reason_codes)
        self.assertTrue(new_risk_buy_prohibited(result))

    def test_unconfigured_daily_loss_limit_omits_axis(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            # daily_loss fact absent / invalid must not invent a prohibit when
            # no max_daily_loss_usd is configured.
            "account_new_risk_snapshot": {"daily_loss_usd": float("nan")},
        }
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)
        self.assertNotIn("DAILY_LOSS_UNKNOWN_FAIL_CLOSED", result.reason_codes)
        self.assertNotIn("DAILY_LOSS_LIMIT_EXCEEDED", result.reason_codes)

    def test_configured_limit_without_daily_loss_fact_fails_closed(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"max_daily_loss_usd": 100.0},
        }
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("DAILY_LOSS_UNKNOWN_FAIL_CLOSED", result.reason_codes)

    def test_invalid_primary_daily_loss_env_falls_back_to_valid_secondary(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"daily_loss_usd": 300.0},
        }
        with patch.dict(
            os.environ,
            {"LONGBRIDGE_MAX_DAILY_LOSS_USD": "0", "MAX_DAILY_LOSS_USD": "250"},
            clear=False,
        ), patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            self.assertEqual(resolve_max_daily_loss_usd(portfolio), 250.0)
            result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("DAILY_LOSS_LIMIT_EXCEEDED", result.reason_codes)

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

    def test_invalid_production_drift_status_prohibits_new_risk(self) -> None:
        result = evaluate_portfolio_new_risk_admission(
            {
                "total_strategy_equity": 50_000.0,
                "account_new_risk_snapshot": {"production_drift_status": "historical_simulation"},
            }
        )
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)

    def test_absent_production_drift_status_still_allows_when_healthy(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
            result = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertEqual(result.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

    def test_store_critical_production_drift_prohibits_when_status_absent(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0}
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value="critical",
        ) as store_resolver:
            result = evaluate_portfolio_new_risk_admission(portfolio)
        store_resolver.assert_called_once()
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("PRODUCTION_DRIFT_CRITICAL", result.reason_codes)

    def test_explicit_production_drift_status_skips_store_lookup(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"production_drift_status": "critical"},
        }
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value="review",
        ) as store_resolver:
            result = evaluate_portfolio_new_risk_admission(portfolio)
        store_resolver.assert_not_called()
        self.assertEqual(result.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("PRODUCTION_DRIFT_CRITICAL", result.reason_codes)

    def test_explicit_snapshot_status_wins_over_portfolio_status(self) -> None:
        portfolio = {
            "total_strategy_equity": 50_000.0,
            "account_new_risk_snapshot": {"production_drift_status": "critical"},
            "production_drift_status": "review",
        }
        snapshot = build_snapshot_from_portfolio(portfolio)
        self.assertEqual(snapshot.production_drift_status, "critical")
        self.assertTrue(new_risk_buy_prohibited(evaluate_portfolio_new_risk_admission(portfolio)))

    def test_portfolio_status_is_used_when_snapshot_omits_it(self) -> None:
        portfolio = {"total_strategy_equity": 50_000.0, "production_drift_status": "review"}
        snapshot = build_snapshot_from_portfolio(portfolio)
        self.assertEqual(snapshot.production_drift_status, "review")
        self.assertTrue(new_risk_buy_prohibited(evaluate_portfolio_new_risk_admission(portfolio)))

    def test_rebalance_service_has_no_research_store_drift_injection(self) -> None:
        source = (
            ROOT / "src" / "longbridge_platform" / "application" / "rebalance_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("maybe_inject_production_drift_status", source)
        self.assertNotIn("resolve_production_drift_status_from_store", source)

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
        with patch(
            "longbridge_platform.application.account_new_risk_gate_support.resolve_production_drift_status_from_store",
            return_value=None,
        ):
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

    def test_combined_scale_halves_allocation_targets(self) -> None:
        scaled = apply_combined_scale_to_allocation_targets(
            {"targets": {"2800.HK": 0.5, "2828.HK": 0.5}},
            0.5,
        )
        self.assertEqual(scaled["targets"], {"2800.HK": 0.25, "2828.HK": 0.25})

    def test_missing_combined_scale_leaves_targets(self) -> None:
        allocation = {"targets": {"2800.HK": 0.5}}
        self.assertEqual(
            apply_combined_scale_to_allocation_targets(allocation, None)["targets"],
            {"2800.HK": 0.5},
        )

    def test_attention_notify_on_new_risk_prohibit_dedupes(self) -> None:
        reset_attention_sent_keys_for_tests()
        portfolio = {
            "total_equity": 50_000.0,
            "strategy_profile": "soxl_soxx_trend_income",
            "account_id": "00827",
            "account_new_risk_snapshot": {"production_drift_status": "critical"},
        }
        admission = evaluate_portfolio_new_risk_admission(portfolio)
        self.assertTrue(new_risk_buy_prohibited(admission))
        snapshot = build_snapshot_from_portfolio(portfolio)
        payloads: list[str] = []

        def _sender(*, text: str, alert_key: str | None = None, **_kwargs) -> bool:
            payloads.append(text)
            return True

        counts = maybe_publish_attention_for_admission(
            admission,
            portfolio=portfolio,
            snapshot=snapshot,
            telegram_sender=_sender,
            log_message=lambda *_a, **_k: None,
        )
        self.assertEqual(counts.get("sent"), 1)
        counts2 = maybe_publish_attention_for_admission(
            admission,
            portfolio=portfolio,
            snapshot=snapshot,
            telegram_sender=_sender,
            log_message=lambda *_a, **_k: None,
        )
        self.assertEqual(counts2.get("sent"), 0)
        self.assertEqual(counts2.get("skipped"), 1)
        self.assertEqual(len(payloads), 1)


    def test_submit_order_blocks_buy_when_equity_missing(self) -> None:
        set_cycle_snapshot(build_snapshot_from_portfolio({}))
        attempts = {"count": 0}

        def fake_submit(*_args, **_kwargs):
            attempts["count"] += 1
            return ExecutionReport(symbol="SOXL", side="buy", quantity=1.0, status="submitted")

        with patch("longbridge_platform.application.longbridge_execution._qpk_submit_order", fake_submit):
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

    def test_submit_order_does_not_scale_buy_quantity(self) -> None:
        """Envelope scale applies to allocation targets, not submit-time quantity."""
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

        with patch("longbridge_platform.application.longbridge_execution._qpk_submit_order", fake_submit):
            submit_order(
                object(),
                "SOXL.US",
                order_kind="market",
                side="buy",
                quantity=4.0,
            )

        self.assertEqual(submitted["quantity"], 4.0)

    def test_submit_order_allows_sell_when_buy_prohibited(self) -> None:
        set_cycle_snapshot(build_snapshot_from_portfolio({}))
        submitted = {}

        def fake_submit(*_args, **kwargs):
            submitted.update(kwargs)
            return ExecutionReport(symbol="SOXL", side="sell", quantity=kwargs["quantity"], status="submitted")

        with patch("longbridge_platform.application.longbridge_execution._qpk_submit_order", fake_submit):
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

        with patch("longbridge_platform.application.longbridge_execution._qpk_submit_order", fake_submit):
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

        with patch("longbridge_platform.application.longbridge_execution._qpk_submit_order", fake_submit):
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
                "risk_gate": "APPROVE",
                "risk_flags": ("risk_gate:passed",),
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
                "risk_gate": "APPROVE",
                "risk_flags": ("risk_gate:passed",),
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


def _synthetic_fuse_record(trade_date: str = "2026-12-09", *, fuse_latched: bool = False) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "session_allowed": True,
        "session_state": "regular",
        "fuse_latched": fuse_latched,
        "fuse_level": "L1" if fuse_latched else "CLEAR",
        "blocks_orders": fuse_latched,
        "blocks_eligibility": fuse_latched,
        "calendar_version": "synthetic-calendar-v1",
        "spec_digest": "synthetic-spec-digest-001",
    }


class Fuse2SharedReadbackTests(unittest.TestCase):
    _ENV_KEYS = (
        ACCOUNT_NEW_RISK_GATE_ENV,
        "FUSE_READBACK_MODE",
        "FUSE_TRADE_DATE",
        "FUSE_OBSERVE_UNTIL",
        "LONGBRIDGE_MARKET",
        "LONGBRIDGE_MARKET_TIMEZONE",
        "MARKET",
        "MARKET_TIMEZONE",
        "ACCOUNT_REGION",
        "ACCOUNT_PREFIX",
        "LONGBRIDGE_MAX_DAILY_LOSS_USD",
        "MAX_DAILY_LOSS_USD",
        "RUNTIME_TARGET_JSON",
        "QSL_RUNTIME_TARGET_JSON",
        "TELEGRAM_TOKEN",
        "GLOBAL_TELEGRAM_CHAT_ID",
        "QSL_GLOBAL_TELEGRAM_CHAT_ID",
    )

    def setUp(self) -> None:
        saved = {key: os.environ.get(key) for key in self._ENV_KEYS}

        def _restore() -> None:
            set_cycle_snapshot(None)
            reset_attention_sent_keys_for_tests()
            for key, old_val in saved.items():
                if old_val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_val

        self.addCleanup(_restore)
        for key in self._ENV_KEYS:
            os.environ.pop(key, None)
        set_cycle_snapshot(None)
        reset_attention_sent_keys_for_tests()

    def test_fuse_readback_enforce_observe_and_legacy_off_hardening(self) -> None:
        from longbridge_platform.application import account_new_risk_gate_support as gate

        base_portfolio = {
            "total_equity": 50_000.0,
            "peak_equity_usd": 50_000.0,
            "account_new_risk_snapshot": {
                "observation_status": "COMPLETE",
                "reconciliation_status": "VERIFIED",
                "circuit_breaker_state": "CLOSED",
            },
        }
        with patch.object(gate, "resolve_production_drift_status_from_store", return_value=None):
            with patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"):
                allow_res = evaluate_portfolio_new_risk_admission(
                    {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-09")}
                )
                self.assertEqual(allow_res.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

                latched_res = evaluate_portfolio_new_risk_admission(
                    {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True)}
                )
                self.assertEqual(latched_res.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
                self.assertIn("FUSE_LATCHED_BLOCKED", latched_res.reason_codes)

                missing_res = evaluate_portfolio_new_risk_admission({**base_portfolio, "fuse_readback": None})
                self.assertIn("FUSE_READBACK_MISSING_FAIL_CLOSED", missing_res.reason_codes)

                bad_record_res = evaluate_portfolio_new_risk_admission(
                    {**base_portfolio, "fuse_readback": {"trade_date": "2026-12-09", "fuse_latched": "yes"}}
                )
                self.assertIn("FUSE_READBACK_INVALID_FAIL_CLOSED", bad_record_res.reason_codes)

                # Observe mode emits non-blocking telemetry when ACCOUNT_NEW_RISK_GATE=1.
                os.environ["FUSE_READBACK_MODE"] = "observe"
                os.environ["FUSE_OBSERVE_UNTIL"] = "2026-12-10"
                obs_res = evaluate_portfolio_new_risk_admission(
                    {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-08")}
                )
                self.assertEqual(obs_res.disposition, NewRiskDisposition.ALLOW_NEW_RISK)
                telemetry = gate.fuse_telemetry_from_admission(obs_res)
                self.assertEqual(telemetry["fuse_readback_effective_mode"], "observe")
                self.assertTrue(telemetry["fuse_readback_would_block"])
                self.assertIn("FUSE_READBACK_STALE_FAIL_CLOSED", telemetry["fuse_readback_reason_codes"])

                # ACCOUNT_NEW_RISK_GATE=0 still blocks tighter during observe mode.
                os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "0"
                obs_legacy_off = evaluate_portfolio_new_risk_admission(
                    {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-08")}
                )
                self.assertEqual(obs_legacy_off.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
                self.assertIn("LEGACY_NEW_RISK_SWITCH_DISABLED", obs_legacy_off.reason_codes)

                # Expired observe window promotes to enforce.
                os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "1"
                with patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-12"):
                    expired_res = evaluate_portfolio_new_risk_admission(
                        {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-11")}
                    )
                self.assertEqual(expired_res.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
                expired_tel = gate.fuse_telemetry_from_admission(expired_res)
                self.assertEqual(expired_tel["fuse_readback_effective_mode"], "enforce")
                self.assertTrue(expired_tel["fuse_readback_observe_window_expired"])

                # submit_order enforces FUSE even when ACCOUNT_NEW_RISK_GATE=0.
                os.environ["FUSE_READBACK_MODE"] = "enforce"
                set_cycle_snapshot(
                    build_snapshot_from_portfolio(
                        {**base_portfolio, "fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True)}
                    ),
                    portfolio=base_portfolio,
                )
                calls = {"n": 0}
                with patch(
                    "longbridge_platform.application.longbridge_execution._qpk_submit_order",
                    side_effect=lambda *_a, **_kw: calls.__setitem__("n", calls["n"] + 1),
                ):
                    blocked_report = submit_order(
                        object(), "SOXL.US", order_kind="market", side="buy", quantity=1.0
                    )
                self.assertEqual(calls["n"], 0)
                self.assertEqual(blocked_report.status, "rejected")
                self.assertEqual(blocked_report.raw_payload.get("detail"), "account_new_risk_gate")

    def test_hk_us_and_invalid_market_timezone_resolution(self) -> None:
        from datetime import datetime as _real_dt
        from zoneinfo import ZoneInfo
        from longbridge_platform.application import account_new_risk_gate_support as gate

        fixed_hk_next_day = _real_dt(2026, 12, 10, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        with patch.object(gate, "datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: fixed_hk_next_day.astimezone(tz) if tz else fixed_hk_next_day
            os.environ["QSL_RUNTIME_TARGET_JSON"] = '{"market": "hk"}'
            self.assertEqual(gate._current_fuse_trade_date(), "2026-12-10")
            os.environ["QSL_RUNTIME_TARGET_JSON"] = '{"market": "us"}'
            self.assertEqual(gate._current_fuse_trade_date(), "2026-12-09")
            os.environ.pop("QSL_RUNTIME_TARGET_JSON", None)
            self.assertEqual(gate._current_fuse_trade_date({"market": "HK_EQUITY"}), "2026-12-10")

        # Invalid market_timezone fails closed and ignores FUSE_TRADE_DATE env fallback.
        os.environ["FUSE_TRADE_DATE"] = "2026-11-25"
        with patch.object(gate, "resolve_production_drift_status_from_store", return_value=None):
            res_bad_tz = evaluate_portfolio_new_risk_admission(
                {
                    "total_equity": 50_000.0,
                    "peak_equity_usd": 50_000.0,
                    "market_timezone": "Mars/Olympus_Mons",
                    "fuse_readback": _synthetic_fuse_record("2026-11-25"),
                }
            )
        self.assertEqual(res_bad_tz.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("FUSE_READBACK_STALE_FAIL_CLOSED", res_bad_tz.reason_codes)

    def test_snapshot_transport_telemetry_post_sell_and_cash_sweep_blocking(self) -> None:
        from longbridge_platform import http_routes
        from longbridge_platform.application import account_new_risk_gate_support as gate
        from longbridge_platform.application import execution_service
        from longbridge_platform.application import rebalance_service
        from longbridge_platform.application.runtime_broker_adapters import build_runtime_broker_adapters
        from longbridge_platform.application.runtime_dependencies import (
            LongBridgeRebalanceConfig,
            LongBridgeRebalanceRuntime,
        )

        # 1. Runtime adapter preserves FUSE fields into PortfolioSnapshot.metadata and through rebalance telemetry + http summary.
        adapters = build_runtime_broker_adapters(
            strategy_symbols=("SOXL",),
            account_hash="lb-demo",
            fetch_last_price_fn=lambda *_a: 100.0,
            fetch_strategy_account_state_fn=lambda *_a: {},
            submit_order_fn=lambda *_a, **_kw: None,
        )
        snap = adapters.build_portfolio_snapshot_from_account_state(
            {
                "available_cash": 1000.0,
                "market_values": {"SOXL": 0.0},
                "quantities": {"SOXL": 0.0},
                "sellable_quantities": {"SOXL": 0.0},
                "total_strategy_equity": 50_000.0,
                "total_equity": 50_000.0,
                "fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True),
                "market": "US",
            }
        )
        self.assertIn("fuse_readback", snap.metadata)
        self.assertEqual(snap.metadata.get("market"), "US")

        plan_e2e = {
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
                "liquid_cash": 1000.0,
                "cash_sweep_symbol": None,
                "cash_by_currency": {},
            },
            "execution": {
                "risk_gate": "APPROVE",
                "risk_flags": ("risk_gate:passed",),
                "current_min_trade": 10.0,
                "trade_threshold_value": 10.0,
                "investable_cash": 1000.0,
                "signal_date": "2026-12-09",
                "effective_date": "2026-12-09",
            },
        }
        os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "0"
        submitted: list[object] = []
        recorded: list[dict[str, object]] = []
        from quant_platform_kit.common.port_adapters import (
            CallableNotificationPort,
            CallablePortfolioPort,
        )
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                rebalance_service,
                "try_record_platform_execution",
                side_effect=lambda _p, payload, **_kw: recorded.append(payload),
            ),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            e2e_result = rebalance_service.run_strategy(
                runtime=LongBridgeRebalanceRuntime(
                    bootstrap=lambda: (None, None, {}),
                    resolve_rebalance_plan=lambda **_kw: {
                        "strategy_profile": plan_e2e["strategy_profile"],
                        "allocation": dict(plan_e2e["allocation"]),
                        "portfolio": dict(plan_e2e["portfolio"]),
                        "execution": dict(plan_e2e["execution"]),
                    },
                    market_data_port_factory=lambda _q: CallableMarketDataPort(
                        quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                    ),
                    estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                    notifications=CallableNotificationPort(lambda _msg: None),
                    notify_issue=lambda *_a: None,
                    portfolio_port_factory=lambda _q, _t: CallablePortfolioPort(lambda: snap),
                    execution_port_factory=lambda _t: CallableExecutionPort(submitted.append),
                ),
                config=LongBridgeRebalanceConfig(
                    translator=build_translator("en"),
                    with_prefix=lambda m: m,
                    separator="---",
                    limit_sell_discount=0.995,
                    limit_buy_premium=1.0,
                    dry_run_only=False,
                    strategy_profile="demo_profile",
                    notify_no_trade_cycles=False,
                ),
            )
        self.assertEqual(submitted, [])
        self.assertEqual(e2e_result.execution.get("execution_status"), "blocked")
        self.assertIn("FUSE_LATCHED_BLOCKED", e2e_result.execution["fuse_readback_reason_codes"])
        self.assertEqual(len(recorded), 1)
        self.assertIn("FUSE_LATCHED_BLOCKED", recorded[0]["fuse_readback_reason_codes"])
        http_summary = http_routes._summarize_cycle_result_for_report(e2e_result, dry_run=False)
        self.assertEqual(http_summary["execution_status"], "blocked")
        self.assertIn("FUSE_LATCHED_BLOCKED", http_summary["fuse_readback_reason_codes"])

        # 2. Post-sell refresh: attempt 1 healthy & attempt 2 latched (lower/equal cash) blocks cash-sweep & normal buy.
        os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "1"
        plan_sell = {
            "strategy_profile": "soxl_soxx_trend_income",
            "allocation": {
                "target_mode": "value",
                "strategy_symbols": ("SOXL", "BOXX"),
                "risk_symbols": ("SOXL",),
                "income_symbols": (),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 0.0, "BOXX": 1000.0},
            },
            "portfolio": {
                "strategy_symbols": ("SOXL", "BOXX"),
                "portfolio_rows": (("SOXL",), ("BOXX",)),
                "market_values": {"SOXL": 400.0, "BOXX": 0.0},
                "quantities": {"SOXL": 4, "BOXX": 0},
                "sellable_quantities": {"SOXL": 4, "BOXX": 0},
                "total_equity": 50_000.0,
                "total_strategy_equity": 50_000.0,
                "liquid_cash": 100.0,
                "cash_sweep_symbol": "BOXX",
                "cash_by_currency": {},
                "fuse_readback": _synthetic_fuse_record("2026-12-09"),
            },
            "execution": {
                "risk_gate": "APPROVE",
                "risk_flags": ("risk_gate:passed",),
                "current_min_trade": 10.0,
                "trade_threshold_value": 10.0,
                "investable_cash": 50.0,
                "signal_date": "2026-12-09",
                "effective_date": "2026-12-09",
            },
        }
        refreshes = [
            {**plan_sell["portfolio"], "market_values": {"SOXL": 0.0, "BOXX": 0.0}, "quantities": {"SOXL": 0, "BOXX": 0}, "liquid_cash": 40.0, "fuse_readback": _synthetic_fuse_record("2026-12-09")},
            {**plan_sell["portfolio"], "market_values": {"SOXL": 0.0, "BOXX": 0.0}, "quantities": {"SOXL": 0, "BOXX": 0}, "liquid_cash": 40.0, "fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True)},
        ]
        idx = {"n": 0}
        submitted_sell: list[object] = []
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            res_refresh = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_sell,
                portfolio=dict(plan_sell["portfolio"]),
                execution=dict(plan_sell["execution"]),
                allocation=dict(plan_sell["allocation"]),
                fetch_replanned_state=lambda: (
                    lambda p: (
                        {**plan_sell, "portfolio": p, "execution": {**plan_sell["execution"], "investable_cash": p["liquid_cash"]}},
                        p,
                        {**plan_sell["execution"], "investable_cash": p["liquid_cash"]},
                        dict(plan_sell["allocation"]),
                    )
                )(dict(refreshes[min(idx.__setitem__("n", idx["n"] + 1) or idx["n"] - 1, 1)])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port(submitted_sell),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
                post_sell_refresh_attempts=2,
                post_sell_refresh_interval_sec=0.0,
            )
        self.assertEqual([o.side for o in submitted_sell], ["sell"])
        self.assertIn("FUSE_LATCHED_BLOCKED", res_refresh.execution["fuse_readback_reason_codes"])

        # 3. Refreshed snapshot setting fuse_enabled=False and dropping fuse_readback keeps cycle FUSE active & fails closed.
        dropped = {**{k: v for k, v in plan_sell["portfolio"].items() if k != "fuse_readback"}, "fuse_enabled": False}
        submitted_drop: list[object] = []
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            res_drop = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_sell,
                portfolio=dict(plan_sell["portfolio"]),
                execution=dict(plan_sell["execution"]),
                allocation=dict(plan_sell["allocation"]),
                fetch_replanned_state=lambda: (
                    {**plan_sell, "portfolio": dict(dropped), "execution": {**plan_sell["execution"], "investable_cash": 1000.0}},
                    dict(dropped),
                    {**plan_sell["execution"], "investable_cash": 1000.0},
                    dict(plan_sell["allocation"]),
                ),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port(submitted_drop),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertEqual([o.side for o in submitted_drop], ["sell"])
        self.assertIn("FUSE_READBACK_MISSING_FAIL_CLOSED", res_drop.execution["fuse_readback_reason_codes"])

        # 4. Pre-submit mid-cycle FUSE rollover block (including cash-sweep) records zero pending/dry-run orders & blocked status.
        os.environ["FUSE_READBACK_MODE"] = "enforce"
        day_calls = {"n": 0}
        def _rollover_day(*_a, **_kw):
            day_calls["n"] += 1
            return "2026-12-09" if day_calls["n"] <= 2 else "2026-12-10"

        submitted_rollover: list[object] = []
        plan_rollover = {
            **plan_e2e,
            "portfolio": {
                **plan_e2e["portfolio"],
                "fuse_readback": _synthetic_fuse_record("2026-12-09"),
            },
        }
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", side_effect=_rollover_day),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            res_rollover = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_rollover,
                portfolio=dict(plan_rollover["portfolio"]),
                execution=dict(plan_rollover["execution"]),
                allocation=dict(plan_rollover["allocation"]),
                fetch_replanned_state=lambda: (plan_rollover, dict(plan_rollover["portfolio"]), dict(plan_rollover["execution"]), dict(plan_rollover["allocation"])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port(submitted_rollover),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertEqual(submitted_rollover, [])
        self.assertEqual(res_rollover.pending_orders, ())
        self.assertEqual(res_rollover.dry_run_orders, ())
        self.assertEqual(res_rollover.execution.get("execution_status"), "blocked")
        self.assertIn("FUSE_READBACK_STALE_FAIL_CLOSED", res_rollover.execution["fuse_readback_reason_codes"])

    def test_astra_r1_hk_region_daily_loss_limit_no_duplicate_heartbeat_and_frozen_command(self) -> None:
        from datetime import datetime as _real_dt
        from zoneinfo import ZoneInfo
        from longbridge_platform.application import account_new_risk_gate_support as gate
        from longbridge_platform.application import execution_service
        from longbridge_platform.application import rebalance_service

        # 1. HK region/prefix/HONG_KONG aliases resolve Asia/Hong_Kong aligned with runtime_config_support.
        fixed_hk_next_day = _real_dt(2026, 12, 10, 1, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
        with patch.object(gate, "datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz=None: fixed_hk_next_day.astimezone(tz) if tz else fixed_hk_next_day
            for env_key, env_val in (
                ("ACCOUNT_REGION", "HK"),
                ("ACCOUNT_PREFIX", "HK"),
                ("LONGBRIDGE_MARKET", "HONG_KONG"),
            ):
                os.environ[env_key] = env_val
                self.assertEqual(gate._current_fuse_trade_date(), "2026-12-10")
                os.environ.pop(env_key, None)

        # 2. Submit-time check preserves cycle portfolio daily-loss limit (100 overrides env 50 when loss=75).
        os.environ["MAX_DAILY_LOSS_USD"] = "50"
        plan_dl = {
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
                "liquid_cash": 1000.0,
                "cash_sweep_symbol": None,
                "cash_by_currency": {},
                "daily_loss_usd": 75.0,
                "max_daily_loss_usd": 100.0,
                "fuse_readback": _synthetic_fuse_record("2026-12-09"),
            },
            "execution": {
                "risk_gate": "APPROVE",
                "risk_flags": ("risk_gate:passed",),
                "current_min_trade": 10.0,
                "trade_threshold_value": 10.0,
                "investable_cash": 1000.0,
                "signal_date": "2026-12-09",
                "effective_date": "2026-12-09",
            },
        }
        submitted_dl: list[object] = []
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            res_dl = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_dl,
                portfolio=dict(plan_dl["portfolio"]),
                execution=dict(plan_dl["execution"]),
                allocation=dict(plan_dl["allocation"]),
                fetch_replanned_state=lambda: (plan_dl, dict(plan_dl["portfolio"]), dict(plan_dl["execution"]), dict(plan_dl["allocation"])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port(submitted_dl),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertEqual(len(submitted_dl), 1)
        self.assertFalse(res_dl.execution.get("execution_status") == "blocked")

        # 3. Initial FUSE block with confirmed attention suppresses duplicate per-cycle heartbeat,
        #    even when cash-sweep hits the already-blocked initial gate; failed/unconfigured still alerts.
        os.environ.pop("MAX_DAILY_LOSS_USD", None)
        plan_blk = {
            **plan_dl,
            "portfolio": {
                **plan_dl["portfolio"],
                "daily_loss_usd": None,
                "cash_sweep_symbol": "BOXX",
                "fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True),
            },
            "allocation": {
                **plan_dl["allocation"],
                "strategy_symbols": ("SOXL", "BOXX"),
                "safe_haven_symbols": ("BOXX",),
                "targets": {"SOXL": 400.0, "BOXX": 0.0},
            },
        }
        plan_blk["portfolio"]["market_values"]["BOXX"] = 0.0
        plan_blk["portfolio"]["quantities"]["BOXX"] = 0
        plan_blk["portfolio"]["sellable_quantities"]["BOXX"] = 0
        plan_blk["portfolio"].pop("max_daily_loss_usd", None)
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 1, "skipped": 0, "failed": 0},
            ),
        ):
            res_blk = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_blk,
                portfolio=dict(plan_blk["portfolio"]),
                execution=dict(plan_blk["execution"]),
                allocation=dict(plan_blk["allocation"]),
                fetch_replanned_state=lambda: (plan_blk, dict(plan_blk["portfolio"]), dict(plan_blk["execution"]), dict(plan_blk["allocation"])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port([]),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertEqual(res_blk.execution.get("execution_status"), "blocked")
        self.assertFalse(
            rebalance_service._has_notification_attention(
                config=type("C", (), {"notification_attention_reason_codes": ()})(),
                result=res_blk,
            )
        )

        # Unified skipped=1 (ambiguous failed/suppressed) does NOT mark delivery confirmed.
        with (
            patch("longbridge_platform.notifications.unified.enabled", return_value=True),
            patch(
                "longbridge_platform.notifications.unified.publish_attention",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
        ):
            from quant_platform_kit.risk.account_new_risk_gate import NewRiskAdmissionResult

            adm = NewRiskAdmissionResult(
                disposition=NewRiskDisposition.NEW_RISK_PROHIBITED,
                reason_codes=("FUSE_LATCHED_BLOCKED",),
            )
            gate.maybe_publish_attention_for_admission(adm, portfolio=plan_blk["portfolio"])
            self.assertFalse(gate.last_attention_delivery_confirmed())

        # Cash-sweep-only buy attempt (ordinary target below threshold) marks blocked + REJECTED without losing confirmed dedup.
        plan_sweep_only = {
            **plan_blk,
            "portfolio": {
                **plan_blk["portfolio"],
                "market_values": {"SOXL": 400.0, "BOXX": 0.0},
            },
            "execution": {
                **plan_blk["execution"],
                "investable_cash": 1000.0,
            },
        }
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 1, "skipped": 0, "failed": 0},
            ),
        ):
            res_sweep_only = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_sweep_only,
                portfolio=dict(plan_sweep_only["portfolio"]),
                execution=dict(plan_sweep_only["execution"]),
                allocation=dict(plan_sweep_only["allocation"]),
                fetch_replanned_state=lambda: (plan_sweep_only, dict(plan_sweep_only["portfolio"]), dict(plan_sweep_only["execution"]), dict(plan_sweep_only["allocation"])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port([]),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertEqual(res_sweep_only.execution.get("execution_status"), "blocked")
        self.assertIn("FUSE_LATCHED_BLOCKED", res_sweep_only.execution.get("account_new_risk_reason_codes", []))
        self.assertFalse(
            rebalance_service._has_notification_attention(
                config=type("C", (), {"notification_attention_reason_codes": ()})(),
                result=res_sweep_only,
            )
        )

        # Failed attention delivery (or unconfigured telegram) retains blocked heartbeat attention.
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 0, "failed": 1},
            ),
        ):
            res_blk_failed = execute_rebalance_cycle(
                trade_context=object(),
                plan=plan_blk,
                portfolio=dict(plan_blk["portfolio"]),
                execution=dict(plan_blk["execution"]),
                allocation=dict(plan_blk["allocation"]),
                fetch_replanned_state=lambda: (plan_blk, dict(plan_blk["portfolio"]), dict(plan_blk["execution"]), dict(plan_blk["allocation"])),
                market_data_port=CallableMarketDataPort(
                    quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                ),
                estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                execution_port=AccountNewRiskGateExecutionCycleTests()._execution_port([]),
                notify_issue=lambda *_a: None,
                translator=build_translator("en"),
                with_prefix=lambda m: m,
                limit_sell_discount=0.995,
                limit_buy_premium=1.0,
            )
        self.assertTrue(
            rebalance_service._has_notification_attention(
                config=type("C", (), {"notification_attention_reason_codes": ()})(),
                result=res_blk_failed,
            )
        )

        # Runtime LONGBRIDGE_MARKET_TIMEZONE beats stale portfolio["market"], and None limit falls through to env.
        os.environ["LONGBRIDGE_MARKET_TIMEZONE"] = "Asia/Hong_Kong"
        with patch.object(gate, "datetime") as mock_dt2:
            mock_dt2.now.side_effect = lambda tz=None: fixed_hk_next_day.astimezone(tz) if tz else fixed_hk_next_day
            self.assertEqual(gate._current_fuse_trade_date({"market": "US"}), "2026-12-10")
        os.environ.pop("LONGBRIDGE_MARKET_TIMEZONE", None)

        os.environ["MAX_DAILY_LOSS_USD"] = "50"
        with patch.object(gate, "resolve_production_drift_status_from_store", return_value=None):
            res_null_limit = evaluate_portfolio_new_risk_admission(
                {
                    "total_equity": 50_000.0,
                    "peak_equity_usd": 50_000.0,
                    "daily_loss_usd": 75.0,
                    "max_daily_loss_usd": None,
                }
            )
        self.assertEqual(res_null_limit.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
        self.assertIn("DAILY_LOSS_LIMIT_EXCEEDED", res_null_limit.reason_codes)
        os.environ.pop("MAX_DAILY_LOSS_USD", None)

        # 4. Frozen live command initial selection carries initial_snapshot.metadata fuse_readback into run_strategy.
        from tempfile import TemporaryDirectory
        from quant_platform_kit.common.execution_commands import ExecutionCommandState
        from quant_platform_kit.common.models import PortfolioSnapshot
        from quant_platform_kit.common.port_adapters import (
            CallableExecutionPort,
            CallableNotificationPort,
            CallablePortfolioPort,
        )
        from longbridge_platform.application.durable_execution_commands import (
            ExecutionCommandStore,
            build_live_execution_command,
        )
        from longbridge_platform.application.runtime_dependencies import (
            LongBridgeRebalanceConfig,
            LongBridgeRebalanceRuntime,
        )

        cmd_store = ExecutionCommandStore(local_dir=self.enterContext(TemporaryDirectory()))
        due_cmd = build_live_execution_command(
            platform="longbridge",
            account_scope="SG",
            strategy_profile="russell_top50_leader_rotation",
            physical_account_id="lb-sg-001",
            runtime_identity_digest="a" * 64,
            execution={
                **plan_dl["execution"],
                "signal_date": "2026-12-08",
                "effective_date": "2026-12-09",
                "execution_timing_contract": "next_trading_day",
            },
            allocation=plan_dl["allocation"],
        )
        self.assertTrue(cmd_store.enqueue(due_cmd))
        latched_snap = PortfolioSnapshot(
            as_of="2026-12-09",
            total_equity=50_000.0,
            buying_power=1000.0,
            cash_balance=1000.0,
            positions=(),
            metadata={"fuse_readback": _synthetic_fuse_record("2026-12-09", fuse_latched=True)},
        )
        frozen_plan = {
            **plan_dl,
            "strategy_profile": "russell_top50_leader_rotation",
            "portfolio": {
                **plan_dl["portfolio"],
                "daily_loss_usd": None,
            },
            "execution": {
                **plan_dl["execution"],
                "signal_date": "2026-12-09",
                "effective_date": "2026-12-10",
                "execution_timing_contract": "next_trading_day",
            },
        }
        frozen_plan["portfolio"].pop("max_daily_loss_usd", None)
        frozen_plan["portfolio"].pop("fuse_readback", None)
        submitted_frozen: list[object] = []
        with (
            patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
            patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            patch.object(
                rebalance_service,
                "try_record_platform_execution",
                return_value=None,
            ),
            patch.object(
                execution_service,
                "maybe_publish_attention_for_admission",
                return_value={"sent": 0, "skipped": 1, "failed": 0},
            ),
        ):
            frozen_res = rebalance_service.run_strategy(
                runtime=LongBridgeRebalanceRuntime(
                    bootstrap=lambda: ("q", "t", {}),
                    resolve_rebalance_plan=lambda **_kw: frozen_plan,
                    resolve_frozen_rebalance_plan=lambda **_kw: {
                        **frozen_plan,
                        "portfolio": dict(frozen_plan["portfolio"]),
                        "execution": dict(frozen_plan["execution"]),
                        "allocation": dict(frozen_plan["allocation"]),
                    },
                    market_data_port_factory=lambda _q: CallableMarketDataPort(
                        quote_loader=lambda s: QuoteSnapshot(symbol=s, as_of="2026-12-09", last_price=100.0)
                    ),
                    estimate_max_purchase_quantity=lambda *_a, **_kw: 5,
                    notifications=CallableNotificationPort(lambda _m: None),
                    notify_issue=lambda *_a: None,
                    portfolio_port_factory=lambda _q, _t: CallablePortfolioPort(lambda: latched_snap),
                    execution_port_factory=lambda _t: CallableExecutionPort(submitted_frozen.append),
                ),
                config=LongBridgeRebalanceConfig(
                    translator=build_translator("en"),
                    with_prefix=lambda m: m,
                    separator="---",
                    limit_sell_discount=0.995,
                    limit_buy_premium=1.0,
                    dry_run_only=False,
                    notify_no_trade_cycles=False,
                    strategy_profile="russell_top50_leader_rotation",
                    execution_state_account_scope="SG",
                    physical_account_id="lb-sg-001",
                    durable_execution_command_live_enabled=True,
                    durable_live_execution_session_authorized=True,
                    durable_execution_runtime_identity_digest="a" * 64,
                    execution_command_store=cmd_store,
                ),
            )
        self.assertEqual(submitted_frozen, [])
        self.assertIn("FUSE_LATCHED_BLOCKED", frozen_res.execution["fuse_readback_reason_codes"])
        self.assertEqual(frozen_res.execution.get("execution_status"), "blocked")
        self.assertIs(cmd_store.current_state(due_cmd), ExecutionCommandState.REJECTED)
        cmd_events = cmd_store.events(due_cmd)
        self.assertIn("FUSE_LATCHED_BLOCKED", cmd_events[-1].details.get("reason_codes", []))

    def test_fuse3_state_path_readback_allow_and_fail_closed(self) -> None:
        import json
        import tempfile
        from longbridge_platform.application import account_new_risk_gate_support as gate

        base_portfolio = {
            "total_equity": 50_000.0,
            "peak_equity_usd": 50_000.0,
            "account_new_risk_snapshot": {
                "observation_status": "COMPLETE",
                "reconciliation_status": "VERIFIED",
                "circuit_breaker_state": "CLOSED",
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "fuse_readback.json"
            state_file.write_text(json.dumps(_synthetic_fuse_record("2026-12-09")), encoding="utf-8")
            with (
                patch.dict(os.environ, {"FUSE_READBACK_STATE_PATH": str(state_file)}, clear=False),
                patch.object(gate, "resolve_production_drift_status_from_store", return_value=None),
                patch.object(gate, "_current_fuse_trade_date", return_value="2026-12-09"),
            ):
                allowed = evaluate_portfolio_new_risk_admission(base_portfolio)
                self.assertEqual(allowed.disposition, NewRiskDisposition.ALLOW_NEW_RISK)

                state_file.unlink()
                os.environ[ACCOUNT_NEW_RISK_GATE_ENV] = "0"
                blocked = evaluate_portfolio_new_risk_admission(base_portfolio)
                self.assertEqual(blocked.disposition, NewRiskDisposition.NEW_RISK_PROHIBITED)
                self.assertIn("FUSE_READBACK_MISSING_FAIL_CLOSED", blocked.reason_codes)


if __name__ == "__main__":
    unittest.main()
