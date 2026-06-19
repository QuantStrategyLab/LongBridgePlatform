import unittest
from datetime import datetime, timezone

from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.strategy_contracts import PositionTarget, StrategyDecision

from decision_mapper import map_strategy_decision_to_plan


class DecisionMapperTests(unittest.TestCase):
    def test_maps_semiconductor_strategy_decision_to_execution_plan(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="SOXL", target_value=30000.0),
                PositionTarget(symbol="SOXX", target_value=0.0),
                PositionTarget(symbol="BOXX", target_value=15000.0, role="safe_haven"),
                PositionTarget(symbol="QQQI", target_value=3500.0, role="income"),
                PositionTarget(symbol="SPYI", target_value=1500.0, role="income"),
            ),
            diagnostics={
                "market_status": "🚀 RISK-ON (SOXX+SOXL)",
                "signal_message": "signal",
                "deploy_ratio_text": "60.0%",
                "income_ratio_text": "10.0%",
                "income_locked_ratio_text": "10.0%",
                "active_risk_asset": "SOXX+SOXL",
                "investable_cash": 9000.0,
                "threshold_value": 500.0,
                "current_min_trade": 100.0,
                "total_strategy_equity": 50000.0,
            },
        )
        account_state = {
            "available_cash": 10000.0,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0, "BOXX": 5000.0, "QQQI": 1000.0, "SPYI": 1000.0},
            "quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0, "BOXX": 50, "QQQI": 10, "SPYI": 10},
            "total_strategy_equity": 50000.0,
            "cash_by_currency": {"USD": 10000.0, "SGD": 350.0},
        }

        plan = map_strategy_decision_to_plan(
            decision,
            account_state=account_state,
            strategy_profile="soxl_soxx_trend_income",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["strategy_symbols"], ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["allocation"]["targets"]["BOXX"], 15000.0)
        self.assertEqual(plan["portfolio"]["portfolio_rows"], (("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)))
        self.assertEqual(plan["portfolio"]["cash_by_currency"], {"USD": 10000.0, "SGD": 350.0})
        self.assertEqual(plan["portfolio"]["sellable_quantities"]["BOXX"], 50)
        self.assertEqual(plan["execution"]["trade_threshold_value"], 500.0)
        self.assertEqual(plan["execution"]["investable_cash"], 9000.0)
        self.assertNotIn("strategy_assets", plan)
        self.assertNotIn("targets", plan)
        self.assertNotIn("threshold_value", plan)

    def test_prefers_normalized_execution_annotations_when_present(self):
        decision = StrategyDecision(
            positions=(PositionTarget(symbol="SOXL", target_value=30000.0),),
            diagnostics={
                "execution_annotations": {
                    "trade_threshold_value": 250.0,
                    "signal_display": "signal",
                    "status_display": "risk-on",
                    "dashboard_text": "strategy dashboard",
                    "deploy_ratio_text": "60.0%",
                    "income_ratio_text": "10.0%",
                    "income_locked_ratio_text": "10.0%",
                    "active_risk_asset": "SOXL",
                    "investable_cash": 9000.0,
                    "current_min_trade": 100.0,
                    "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
                    "blend_gate_volatility_delever_threshold": 0.60,
                    "blend_gate_volatility_delever_dynamic_threshold": 0.60,
                    "blend_gate_volatility_delever_dynamic_sample_count": 252,
                    "blend_gate_volatility_delever_dynamic_lookback": 252,
                    "blend_gate_volatility_delever_dynamic_percentile": 0.95,
                    "blend_gate_volatility_delever_dynamic_min_periods": 126,
                    "blend_gate_volatility_delever_dynamic_floor": 0.50,
                    "blend_gate_volatility_delever_dynamic_cap": 0.75,
                    "blend_gate_volatility_delever_metric": 0.61,
                    "blend_gate_volatility_delever_triggered": True,
                }
            },
        )
        account_state = {
            "available_cash": 10000.0,
            "market_values": {"SOXL": 0.0},
            "quantities": {"SOXL": 0},
            "sellable_quantities": {"SOXL": 0},
            "total_strategy_equity": 50000.0,
        }

        plan = map_strategy_decision_to_plan(
            decision,
            account_state=account_state,
            strategy_profile="soxl_soxx_trend_income",
        )

        self.assertEqual(plan["execution"]["trade_threshold_value"], 250.0)
        self.assertEqual(plan["execution"]["status_display"], "risk-on")
        self.assertEqual(plan["execution"]["signal_display"], "signal")
        self.assertEqual(plan["execution"]["dashboard_text"], "strategy dashboard")
        self.assertEqual(plan["execution"]["investable_cash"], 9000.0)
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_threshold_mode"], "rolling_percentile")
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_threshold"], 0.60)
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_dynamic_threshold"], 0.60)
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_dynamic_sample_count"], 252)
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_dynamic_percentile"], 0.95)
        self.assertEqual(plan["execution"]["blend_gate_volatility_delever_metric"], 0.61)
        self.assertIs(plan["execution"]["blend_gate_volatility_delever_triggered"], True)

    def test_maps_hybrid_decision_from_snapshot_source(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="TQQQ", target_value=10000.0),
                PositionTarget(symbol="BOXX", target_value=8000.0, role="safe_haven"),
                PositionTarget(symbol="SPYI", target_value=1500.0, role="income"),
                PositionTarget(symbol="QQQI", target_value=1500.0, role="income"),
            ),
            diagnostics={
                "execution_annotations": {
                    "trade_threshold_value": 250.0,
                    "reserved_cash": 1000.0,
                    "signal_display": "entry",
                    "benchmark_symbol": "QQQ",
                    "benchmark_price": 500.0,
                    "long_trend_value": 480.0,
                    "exit_line": 470.0,
                    "dual_drive_volatility_delever_applied": True,
                    "dual_drive_volatility_delever_window": 5,
                    "dual_drive_volatility_delever_metric": 0.312,
                    "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                    "dual_drive_volatility_delever_threshold": 0.28,
                    "dual_drive_volatility_delever_exit_threshold": 0.24,
                    "dual_drive_volatility_delever_dynamic_threshold": 0.28,
                    "dual_drive_volatility_delever_dynamic_sample_count": 252,
                    "dual_drive_volatility_delever_dynamic_lookback": 252,
                    "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                    "dual_drive_volatility_delever_dynamic_min_periods": 126,
                    "dual_drive_volatility_delever_dynamic_floor": 0.24,
                    "dual_drive_volatility_delever_dynamic_cap": 0.36,
                    "dual_drive_volatility_delever_trigger_reason": "entry_threshold",
                    "dual_drive_volatility_delever_veto_reason": "taco_rebound_context",
                    "dual_drive_volatility_delever_taco_veto_enabled": True,
                    "dual_drive_volatility_delever_taco_rebound_context_active": False,
                    "dual_drive_volatility_delever_true_crisis_active": False,
                    "dual_drive_volatility_delever_redirect_symbol": "QQQ",
                    "dual_drive_volatility_delever_removed_value": 4500.0,
                    "dual_drive_macro_risk_governor_enabled": True,
                    "dual_drive_macro_risk_governor_found": True,
                    "dual_drive_macro_risk_governor_route": "risk_reduced",
                    "dual_drive_macro_risk_governor_active": True,
                    "dual_drive_macro_risk_governor_applied": True,
                    "dual_drive_macro_risk_governor_leverage_scalar": 0.5,
                    "dual_drive_macro_risk_governor_risk_asset_scalar": 0.75,
                    "dual_drive_macro_risk_governor_removed_value": 2500.0,
                    "dual_drive_macro_risk_governor_redirected_to_unlevered": 1500.0,
                    "dual_drive_crisis_defense_enabled": True,
                    "dual_drive_crisis_defense_triggered": True,
                    "dual_drive_crisis_defense_applied": False,
                    "dual_drive_crisis_defense_destination": "BOXX",
                    "dual_drive_crisis_defense_removed_value": 0.0,
                    "market_regime_control_enabled": True,
                    "market_regime_control_found": True,
                    "market_regime_control_route": "risk_reduced",
                    "market_regime_control_active": True,
                    "market_regime_control_risk_budget_scalar": 0.5,
                    "market_regime_control_reason_codes": ("macro:vix_crisis_level",),
                }
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=25000.0,
            buying_power=6000.0,
            positions=(
                Position(symbol="TQQQ", quantity=10, market_value=5000.0),
                Position(symbol="BOXX", quantity=20, market_value=2000.0),
            ),
            metadata={"account_hash": "longbridge-test"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="tqqq_growth_income",
        )

        self.assertEqual(plan["allocation"]["strategy_symbols"], ("TQQQ", "BOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["execution"]["trade_threshold_value"], 250.0)
        self.assertEqual(plan["execution"]["investable_cash"], 5000.0)
        self.assertEqual(plan["execution"]["benchmark_symbol"], "QQQ")
        self.assertEqual(plan["execution"]["benchmark_price"], 500.0)
        self.assertEqual(plan["execution"]["long_trend_value"], 480.0)
        self.assertEqual(plan["execution"]["exit_line"], 470.0)
        self.assertIs(plan["execution"]["dual_drive_volatility_delever_applied"], True)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_window"], 5)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_metric"], 0.312)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_threshold_mode"], "rolling_percentile")
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_threshold"], 0.28)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_exit_threshold"], 0.24)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_threshold"], 0.28)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_sample_count"], 252)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_lookback"], 252)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_percentile"], 0.90)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_min_periods"], 126)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_floor"], 0.24)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_dynamic_cap"], 0.36)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_trigger_reason"], "entry_threshold")
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_veto_reason"], "taco_rebound_context")
        self.assertIs(plan["execution"]["dual_drive_volatility_delever_taco_veto_enabled"], True)
        self.assertIs(plan["execution"]["dual_drive_volatility_delever_taco_rebound_context_active"], False)
        self.assertIs(plan["execution"]["dual_drive_volatility_delever_true_crisis_active"], False)
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_redirect_symbol"], "QQQ")
        self.assertEqual(plan["execution"]["dual_drive_volatility_delever_removed_value"], 4500.0)
        self.assertIs(plan["execution"]["dual_drive_macro_risk_governor_enabled"], True)
        self.assertIs(plan["execution"]["dual_drive_macro_risk_governor_found"], True)
        self.assertEqual(plan["execution"]["dual_drive_macro_risk_governor_route"], "risk_reduced")
        self.assertIs(plan["execution"]["dual_drive_macro_risk_governor_active"], True)
        self.assertIs(plan["execution"]["dual_drive_macro_risk_governor_applied"], True)
        self.assertEqual(plan["execution"]["dual_drive_macro_risk_governor_leverage_scalar"], 0.5)
        self.assertEqual(plan["execution"]["dual_drive_macro_risk_governor_risk_asset_scalar"], 0.75)
        self.assertEqual(plan["execution"]["dual_drive_macro_risk_governor_removed_value"], 2500.0)
        self.assertEqual(plan["execution"]["dual_drive_macro_risk_governor_redirected_to_unlevered"], 1500.0)
        self.assertIs(plan["execution"]["dual_drive_crisis_defense_enabled"], True)
        self.assertIs(plan["execution"]["dual_drive_crisis_defense_triggered"], True)
        self.assertIs(plan["execution"]["dual_drive_crisis_defense_applied"], False)
        self.assertEqual(plan["execution"]["dual_drive_crisis_defense_destination"], "BOXX")
        self.assertEqual(plan["execution"]["dual_drive_crisis_defense_removed_value"], 0.0)
        self.assertIs(plan["execution"]["market_regime_control_enabled"], True)
        self.assertIs(plan["execution"]["market_regime_control_found"], True)
        self.assertEqual(plan["execution"]["market_regime_control_route"], "risk_reduced")
        self.assertIs(plan["execution"]["market_regime_control_active"], True)
        self.assertEqual(plan["execution"]["market_regime_control_risk_budget_scalar"], 0.5)
        self.assertEqual(plan["execution"]["market_regime_control_reason_codes"], ("macro:vix_crisis_level",))
        self.assertEqual(plan["portfolio"]["market_values"]["TQQQ"], 5000.0)

    def test_translates_weight_decision_for_snapshot_strategy(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="AAPL", target_weight=0.35),
                PositionTarget(symbol="MSFT", target_weight=0.35),
                PositionTarget(symbol="BOXX", target_weight=0.30, role="safe_haven"),
            ),
            diagnostics={
                "signal_description": "risk on",
                "status_description": "regime=soft_defense | breadth=55.0%",
                "dashboard": "tech dashboard",
                "benchmark_symbol": "QQQ",
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=20000.0,
            buying_power=4000.0,
            positions=(
                Position(symbol="AAPL", quantity=10, market_value=1500.0),
                Position(symbol="BOXX", quantity=30, market_value=3000.0),
            ),
            metadata={"account_hash": "longbridge-mega"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="russell_top50_leader_rotation",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["targets"]["AAPL"], 7000.0)
        self.assertEqual(plan["allocation"]["targets"]["BOXX"], 6000.0)
        self.assertEqual(plan["execution"]["signal_display"], "risk on")
        self.assertEqual(plan["execution"]["status_display"], "regime=soft_defense | breadth=55.0%")
        self.assertEqual(plan["execution"]["dashboard_text"], "tech dashboard")
        self.assertEqual(plan["execution"]["benchmark_symbol"], "QQQ")
        self.assertEqual(plan["portfolio"]["portfolio_rows"], (("AAPL", "MSFT"), ("BOXX",)))

    def test_applies_platform_reserved_cash_policy_to_weight_decision(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="AAPL", target_weight=0.5),
                PositionTarget(symbol="MSFT", target_weight=0.5),
            ),
            diagnostics={"signal_description": "risk on"},
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=20000.0,
            buying_power=4000.0,
            positions=(Position(symbol="AAPL", quantity=10, market_value=1500.0),),
            metadata={"account_hash": "longbridge-reserve"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="russell_top50_leader_rotation",
            runtime_metadata={
                "longbridge_execution_policy": {
                    "reserved_cash_floor_usd": 1500.0,
                    "reserved_cash_ratio": 0.03,
                }
            },
        )

        self.assertEqual(plan["execution"]["reserved_cash"], 1500.0)
        self.assertEqual(plan["execution"]["investable_cash"], 2500.0)

    def test_zero_equity_weight_targets_no_execute_instead_of_translation_error(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="AAPL", target_weight=0.5),
                PositionTarget(symbol="MSFT", target_weight=0.5),
            ),
            diagnostics={"signal_description": "risk on"},
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=0.0,
            buying_power=0.0,
            positions=(),
            metadata={"account_hash": "longbridge-zero"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="russell_top50_leader_rotation",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["targets"], {"AAPL": 0.0, "MSFT": 0.0})
        self.assertEqual(plan["portfolio"]["total_equity"], 0.0)
        self.assertEqual(plan["execution"]["trade_threshold_value"], 100.0)
        self.assertEqual(plan["execution"]["investable_cash"], 0.0)

    def test_carries_snapshot_manifest_diagnostics_to_execution(self):
        decision = StrategyDecision(
            positions=(),
            risk_flags=("no_execute",),
            diagnostics={"signal_description": "monthly cadence"},
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=10000.0,
            buying_power=10000.0,
            positions=(),
            metadata={"account_hash": "longbridge-snapshot"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="russell_top50_leader_rotation",
            runtime_metadata={
                "snapshot_manifest_price_as_of": "2026-06-01",
                "snapshot_manifest_universe_as_of": "2026-04-29",
                "snapshot_manifest_source_input_status": "universe_fallback",
                "snapshot_manifest_source_input_fallback_used": True,
                "snapshot_manifest_source_input_fallback_streak": 1,
                "snapshot_manifest_source_refresh_run_id": "26785047433",
            },
        )

        self.assertEqual(plan["execution"]["snapshot_manifest_price_as_of"], "2026-06-01")
        self.assertEqual(plan["execution"]["snapshot_manifest_universe_as_of"], "2026-04-29")
        self.assertEqual(plan["execution"]["snapshot_manifest_source_input_status"], "universe_fallback")
        self.assertIs(plan["execution"]["snapshot_manifest_source_input_fallback_used"], True)
        self.assertEqual(plan["execution"]["snapshot_manifest_source_input_fallback_streak"], 1)
        self.assertEqual(plan["execution"]["snapshot_manifest_source_refresh_run_id"], "26785047433")

    def test_platform_reserved_cash_policy_does_not_lower_strategy_reserve(self):
        decision = StrategyDecision(
            positions=(PositionTarget(symbol="TQQQ", target_value=5000.0),),
            diagnostics={
                "execution_annotations": {
                    "trade_threshold_value": 100.0,
                    "reserved_cash": 1200.0,
                }
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=10000.0,
            buying_power=3000.0,
            positions=(),
            metadata={"account_hash": "longbridge-reserve"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="tqqq_growth_income",
            runtime_metadata={
                "longbridge_execution_policy": {
                    "reserved_cash_floor_usd": 150.0,
                    "reserved_cash_ratio": 0.03,
                }
            },
        )

        self.assertEqual(plan["execution"]["reserved_cash"], 1200.0)
        self.assertEqual(plan["execution"]["investable_cash"], 1800.0)

    def test_keeps_cash_by_currency_from_snapshot_metadata(self):
        decision = StrategyDecision(
            positions=(PositionTarget(symbol="SOXL", target_value=0.0),),
            diagnostics={
                "execution_annotations": {
                    "trade_threshold_value": 100.0,
                    "investable_cash": 0.0,
                }
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=0.0,
            buying_power=0.0,
            positions=(),
            metadata={
                "account_hash": "longbridge-sg",
                "cash_by_currency": {"USD": 0.0, "SGD": 350.0},
            },
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="soxl_soxx_trend_income",
        )

        self.assertEqual(plan["portfolio"]["cash_by_currency"], {"USD": 0.0, "SGD": 350.0})

    def test_translates_weight_decision_for_global_etf_rotation(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="VGK", target_weight=0.5),
                PositionTarget(symbol="EWJ", target_weight=0.3),
                PositionTarget(symbol="BIL", target_weight=0.2, role="safe_haven"),
            ),
            diagnostics={
                "signal_description": "quarterly",
                "canary_status": "SPY:✅, EFA:✅",
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=20000.0,
            buying_power=4000.0,
            positions=(
                Position(symbol="VOO", quantity=10, market_value=1500.0),
                Position(symbol="BIL", quantity=30, market_value=3000.0),
            ),
            metadata={"account_hash": "longbridge-global"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="global_etf_rotation",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["targets"]["VGK"], 10000.0)
        self.assertEqual(plan["allocation"]["targets"]["EWJ"], 6000.0)
        self.assertEqual(plan["allocation"]["targets"]["BIL"], 4000.0)
        self.assertEqual(plan["execution"]["signal_display"], "quarterly")
        self.assertEqual(plan["execution"]["status_display"], "SPY:✅, EFA:✅")

    def test_translates_weight_decision_for_russell_strategy(self):
        decision = StrategyDecision(
            positions=(
                PositionTarget(symbol="AAPL", target_weight=0.30),
                PositionTarget(symbol="MSFT", target_weight=0.30),
                PositionTarget(symbol="NVDA", target_weight=0.20),
                PositionTarget(symbol="BOXX", target_weight=0.20, role="safe_haven"),
            ),
            diagnostics={
                "signal_description": "risk on",
                "status_description": "breadth=62.0% | regime=risk_on | benchmark=up",
                "benchmark_symbol": "SPY",
            },
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=20000.0,
            buying_power=4000.0,
            positions=(
                Position(symbol="AAPL", quantity=10, market_value=1500.0),
                Position(symbol="BOXX", quantity=30, market_value=3000.0),
            ),
            metadata={"account_hash": "longbridge-russell"},
        )

        plan = map_strategy_decision_to_plan(
            decision,
            snapshot=snapshot,
            strategy_profile="russell_top50_leader_rotation",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["targets"]["AAPL"], 6000.0)
        self.assertEqual(plan["allocation"]["targets"]["BOXX"], 4000.0)
        self.assertEqual(plan["execution"]["signal_display"], "risk on")
        self.assertEqual(
            plan["execution"]["status_display"],
            "breadth=62.0% | regime=risk_on | benchmark=up",
        )


if __name__ == "__main__":
    unittest.main()
