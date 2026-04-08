import unittest

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
                "market_status": "🚀 RISK-ON (SOXL)",
                "signal_message": "signal",
                "deploy_ratio_text": "60.0%",
                "income_ratio_text": "10.0%",
                "income_locked_ratio_text": "10.0%",
                "active_risk_asset": "SOXL",
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
        }

        plan = map_strategy_decision_to_plan(
            decision,
            account_state=account_state,
            strategy_profile="semiconductor_rotation_income",
        )

        self.assertEqual(plan["allocation"]["target_mode"], "value")
        self.assertEqual(plan["allocation"]["strategy_symbols"], ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))
        self.assertEqual(plan["allocation"]["targets"]["BOXX"], 15000.0)
        self.assertEqual(plan["portfolio"]["portfolio_rows"], (("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)))
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
                    "deploy_ratio_text": "60.0%",
                    "income_ratio_text": "10.0%",
                    "income_locked_ratio_text": "10.0%",
                    "active_risk_asset": "SOXL",
                    "investable_cash": 9000.0,
                    "current_min_trade": 100.0,
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
            strategy_profile="semiconductor_rotation_income",
        )

        self.assertEqual(plan["execution"]["trade_threshold_value"], 250.0)
        self.assertEqual(plan["execution"]["status_display"], "risk-on")
        self.assertEqual(plan["execution"]["signal_display"], "signal")
        self.assertEqual(plan["execution"]["investable_cash"], 9000.0)


if __name__ == "__main__":
    unittest.main()
