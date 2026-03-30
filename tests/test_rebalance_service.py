import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLATFORM_KIT_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(PLATFORM_KIT_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_KIT_SRC))
US_EQUITY_STRATEGIES_SRC = ROOT.parent / "UsEquityStrategies" / "src"
if str(US_EQUITY_STRATEGIES_SRC) not in sys.path:
    sys.path.insert(0, str(US_EQUITY_STRATEGIES_SRC))

from application import rebalance_service
from notifications.telegram import build_translator


class RebalanceServiceNotificationTests(unittest.TestCase):
    def _run_strategy(
        self,
        plan,
        *,
        prices,
        refreshed_plan=None,
        account_states=None,
        estimate_max_purchase_quantity_value=0,
    ):
        sent_messages = []
        observed_account_states = []

        def fake_send_tg_message(message):
            sent_messages.append(message)

        def fake_notify_issue(title, detail):
            fake_send_tg_message(f"{title}\n{detail}")

        def fake_submit_order_with_alert(
            trade_context,
            symbol,
            order_type,
            side,
            quantity,
            logs,
            log_message,
            *,
            submitted_price=None,
        ):
            del trade_context, order_type, side, quantity, submitted_price
            logs.append(f"{log_message} [order_id=test-order]")
            return True

        plan_side_effect = [plan, refreshed_plan or plan]

        account_state_values = list(account_states or [{}, {}])

        def fake_fetch_strategy_account_state(quote_context, trade_context, strategy_assets):
            del quote_context, trade_context, strategy_assets
            if not account_state_values:
                raise AssertionError("unexpected extra fetch_strategy_account_state call")
            value = account_state_values.pop(0)
            observed_account_states.append(value)
            return value

        with patch.object(rebalance_service, "build_rebalance_plan", side_effect=plan_side_effect):
            rebalance_service.run_strategy(
                project_id="project-1",
                secret_name="secret-1",
                trend_ma_window=150,
                token_refresh_threshold_days=30,
                cash_reserve_ratio=0.03,
                min_trade_ratio=0.01,
                min_trade_floor=100.0,
                rebalance_threshold_ratio=0.01,
                limit_sell_discount=0.995,
                limit_buy_premium=1.005,
                small_account_deploy_ratio=0.60,
                mid_account_deploy_ratio=0.57,
                large_account_deploy_ratio=0.50,
                trade_layer_decay_coeff=0.04,
                income_layer_start_usd=150000.0,
                income_layer_max_ratio=0.15,
                income_layer_qqqi_weight=0.70,
                income_layer_spyi_weight=0.30,
                separator="━━━━━━━━━━━━━━━━━━",
                translator=build_translator("zh"),
                with_prefix=lambda message: f"[HK/LongBridgeQuant] {message}",
                send_tg_message=fake_send_tg_message,
                notify_issue=fake_notify_issue,
                fetch_token_from_secret=lambda project_id, secret_name: "refresh-token",
                refresh_token_if_needed=lambda *args, **kwargs: "live-token",
                build_contexts=lambda app_key, app_secret, token: ("quote-context", "trade-context"),
                calculate_rotation_indicators=lambda quote_context, trend_window: {"soxl": {"price": 1, "ma_trend": 2}},
                fetch_strategy_account_state=fake_fetch_strategy_account_state,
                fetch_last_price=lambda quote_context, symbol: prices[symbol],
                estimate_max_purchase_quantity=lambda *args, **kwargs: estimate_max_purchase_quantity_value,
                submit_order_with_alert=fake_submit_order_with_alert,
            )

        return sent_messages, observed_account_states

    def test_sell_then_buy_skip_is_sent_in_single_summary_message(self):
        plan = {
            "strategy_assets": ["SOXL", "SOXX"],
            "targets": {"SOXL": 0.0, "SOXX": 34718.05},
            "market_values": {"SOXL": 31928.30, "SOXX": 0.0},
            "sellable_quantities": {"SOXL": 695, "SOXX": 0},
            "quantities": {"SOXL": 695, "SOXX": 0},
            "current_min_trade": 100.0,
            "limit_order_symbols": ("SOXL", "SOXX"),
            "threshold_value": 100.0,
            "investable_cash": 101.95,
            "market_status": "🛡️ DE-LEVER (SOXX)",
            "deploy_ratio_text": "57.9%",
            "income_ratio_text": "0.0%",
            "income_locked_ratio_text": "38.3%",
            "signal_message": "SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            "available_cash": 101.95,
            "total_strategy_equity": 60000.0,
            "portfolio_rows": (("SOXL", "SOXX"),),
        }
        sent_messages, _ = self._run_strategy(
            plan,
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🔔 【调仓指令】", sent_messages[0])
        self.assertIn("限价卖出", sent_messages[0])
        self.assertIn("买入跳过", sent_messages[0])
        self.assertIn("SOXX.US", sent_messages[0])

    def test_buy_skip_without_orders_is_sent_in_single_heartbeat_message(self):
        plan = {
            "strategy_assets": ["SOXX"],
            "targets": {"SOXX": 34718.05},
            "market_values": {"SOXX": 0.0},
            "sellable_quantities": {"SOXX": 0},
            "quantities": {"SOXX": 0},
            "current_min_trade": 100.0,
            "limit_order_symbols": ("SOXX",),
            "threshold_value": 100.0,
            "investable_cash": 101.95,
            "market_status": "🛡️ DE-LEVER (SOXX)",
            "deploy_ratio_text": "57.9%",
            "income_ratio_text": "0.0%",
            "income_locked_ratio_text": "38.3%",
            "signal_message": "SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            "available_cash": 101.95,
            "total_strategy_equity": 60000.0,
            "portfolio_rows": (("SOXX",),),
        }
        sent_messages, _ = self._run_strategy(
            plan,
            prices={"SOXX.US": 322.74},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("💓 【心跳检测】", sent_messages[0])
        self.assertIn("本轮没有可执行订单", sent_messages[0])
        self.assertIn("跳过项", sent_messages[0])
        self.assertIn("SOXX.US", sent_messages[0])

    def test_refreshes_account_state_after_sell_and_can_place_followup_buy(self):
        initial_plan = {
            "strategy_assets": ["SOXL", "SOXX"],
            "targets": {"SOXL": 0.0, "SOXX": 34718.05},
            "market_values": {"SOXL": 31928.30, "SOXX": 0.0},
            "sellable_quantities": {"SOXL": 695, "SOXX": 0},
            "quantities": {"SOXL": 695, "SOXX": 0},
            "current_min_trade": 100.0,
            "limit_order_symbols": ("SOXL", "SOXX"),
            "threshold_value": 100.0,
            "investable_cash": 101.95,
            "market_status": "🛡️ DE-LEVER (SOXX)",
            "deploy_ratio_text": "57.9%",
            "income_ratio_text": "0.0%",
            "income_locked_ratio_text": "38.3%",
            "signal_message": "SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            "available_cash": 101.95,
            "total_strategy_equity": 60000.0,
            "portfolio_rows": (("SOXL", "SOXX"),),
        }
        refreshed_plan = {
            **initial_plan,
            "market_values": {"SOXL": 0.0, "SOXX": 0.0},
            "sellable_quantities": {"SOXL": 0, "SOXX": 0},
            "quantities": {"SOXL": 0, "SOXX": 0},
            "investable_cash": 40000.0,
            "available_cash": 40000.0,
        }
        sent_messages, observed_account_states = self._run_strategy(
            initial_plan,
            refreshed_plan=refreshed_plan,
            account_states=[{"phase": "before_sell"}, {"phase": "after_sell"}],
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
            estimate_max_purchase_quantity_value=200,
        )

        self.assertEqual(observed_account_states, [{"phase": "before_sell"}, {"phase": "after_sell"}])
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🔔 【调仓指令】", sent_messages[0])
        self.assertIn("限价卖出", sent_messages[0])
        self.assertIn("限价买入", sent_messages[0])
        self.assertNotIn("买入跳过", sent_messages[0])


if __name__ == "__main__":
    unittest.main()
