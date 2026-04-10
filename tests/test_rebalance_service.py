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

import types


requests_stub = types.ModuleType("requests")
requests_stub.post = lambda *args, **kwargs: None

with patch.dict(sys.modules, {"requests": requests_stub}):
    from application import rebalance_service
    from notifications.telegram import build_translator


def _build_plan(
    *,
    strategy_profile="soxl_soxx_trend_income",
    strategy_symbols,
    risk_symbols=(),
    income_symbols=(),
    safe_haven_symbols=(),
    targets,
    market_values,
    quantities,
    sellable_quantities,
    current_min_trade,
    trade_threshold_value,
    investable_cash,
    market_status,
    deploy_ratio_text,
    income_ratio_text,
    income_locked_ratio_text,
    signal_message,
    available_cash,
    total_strategy_equity,
    portfolio_rows,
    dashboard_text="",
    benchmark_symbol="",
    benchmark_price=0.0,
    long_trend_value=0.0,
    exit_line=0.0,
):
    return {
        "strategy_profile": strategy_profile,
        "allocation": {
            "target_mode": "value",
            "strategy_symbols": tuple(strategy_symbols),
            "risk_symbols": tuple(risk_symbols),
            "income_symbols": tuple(income_symbols),
            "safe_haven_symbols": tuple(safe_haven_symbols),
            "targets": dict(targets),
        },
        "portfolio": {
            "strategy_symbols": tuple(strategy_symbols),
            "portfolio_rows": tuple(portfolio_rows),
            "market_values": dict(market_values),
            "quantities": dict(quantities),
            "sellable_quantities": dict(sellable_quantities),
            "total_equity": float(total_strategy_equity),
            "liquid_cash": float(available_cash),
        },
        "execution": {
            "trade_threshold_value": float(trade_threshold_value),
            "status_display": market_status,
            "signal_display": signal_message,
            "deploy_ratio_text": deploy_ratio_text,
            "income_ratio_text": income_ratio_text,
            "income_locked_ratio_text": income_locked_ratio_text,
            "investable_cash": float(investable_cash),
            "current_min_trade": float(current_min_trade),
            "dashboard_text": dashboard_text,
            "benchmark_symbol": benchmark_symbol,
            "benchmark_price": float(benchmark_price),
            "long_trend_value": float(long_trend_value),
            "exit_line": float(exit_line),
        },
    }


class RebalanceServiceNotificationTests(unittest.TestCase):
    def test_append_status_lines_localizes_snapshot_guard_text_for_zh(self):
        lines = []
        rebalance_service._append_status_lines(
            lines,
            execution={
                "status_display": "fail_closed | reason=feature_snapshot_path_missing",
                "signal_display": "feature snapshot guard blocked execution",
            },
            translator=build_translator("zh"),
            signal_key="heartbeat_signal",
        )

        self.assertIn("📊 市场状态: 关闭执行 | 原因=缺少特征快照路径", lines)
        self.assertIn("🎯 信号: 特征快照校验阻止执行", lines)

    def _run_strategy(
        self,
        plan,
        *,
        prices,
        refreshed_plan=None,
        account_states=None,
        estimate_max_purchase_quantity_value=0,
        dry_run_only=False,
        strategy_display_name="SOXL/SOXX 半导体趋势收益",
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
        observed_plan_inputs = []

        account_state_values = list(account_states or [{}, {}])

        def fake_fetch_strategy_account_state(quote_context, trade_context):
            del quote_context, trade_context
            if not account_state_values:
                raise AssertionError("unexpected extra fetch_strategy_account_state call")
            value = account_state_values.pop(0)
            observed_account_states.append(value)
            return value

        def fake_resolve_rebalance_plan(*, indicators, account_state):
            observed_plan_inputs.append((indicators, account_state))
            if not plan_side_effect:
                raise AssertionError("unexpected extra resolve_rebalance_plan call")
            return plan_side_effect.pop(0)

        rebalance_service.run_strategy(
            project_id="project-1",
            secret_name="secret-1",
            token_refresh_threshold_days=30,
            limit_sell_discount=0.995,
            limit_buy_premium=1.005,
            separator="━━━━━━━━━━━━━━━━━━",
            translator=build_translator("zh"),
            with_prefix=lambda message: f"[HK/LongBridgeQuant] {message}",
            send_tg_message=fake_send_tg_message,
            notify_issue=fake_notify_issue,
            fetch_token_from_secret=lambda project_id, secret_name: "refresh-token",
            refresh_token_if_needed=lambda *args, **kwargs: "live-token",
            build_contexts=lambda app_key, app_secret, token: ("quote-context", "trade-context"),
            calculate_strategy_indicators=lambda quote_context: {"soxl": {"price": 1, "ma_trend": 2}},
            fetch_strategy_account_state=fake_fetch_strategy_account_state,
            resolve_rebalance_plan=fake_resolve_rebalance_plan,
            fetch_last_price=lambda quote_context, symbol: prices[symbol],
            estimate_max_purchase_quantity=lambda *args, **kwargs: estimate_max_purchase_quantity_value,
            submit_order_with_alert=fake_submit_order_with_alert,
            dry_run_only=dry_run_only,
            strategy_display_name=strategy_display_name,
        )

        return sent_messages, observed_account_states, observed_plan_inputs

    def test_sell_then_buy_skip_is_sent_in_single_summary_message(self):
        plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX"),
            risk_symbols=("SOXL", "SOXX"),
            targets={"SOXL": 0.0, "SOXX": 34718.05},
            market_values={"SOXL": 31928.30, "SOXX": 0.0},
            sellable_quantities={"SOXL": 695, "SOXX": 0},
            quantities={"SOXL": 695, "SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=101.95,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=101.95,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXL", "SOXX"),),
        )
        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🔔 【调仓指令】", sent_messages[0])
        self.assertIn("🧭 策略: SOXL/SOXX 半导体趋势收益", sent_messages[0])
        self.assertIn("限价卖出", sent_messages[0])
        self.assertIn("买入说明", sent_messages[0])
        self.assertIn("SOXX.US", sent_messages[0])

    def test_buy_skip_without_orders_is_sent_in_single_heartbeat_message(self):
        plan = _build_plan(
            strategy_symbols=("SOXX",),
            risk_symbols=("SOXX",),
            targets={"SOXX": 34718.05},
            market_values={"SOXX": 0.0},
            sellable_quantities={"SOXX": 0},
            quantities={"SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=101.95,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=101.95,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXX",),),
        )
        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"SOXX.US": 322.74},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("💓 【心跳检测】", sent_messages[0])
        self.assertIn("本轮没有可执行订单", sent_messages[0])
        self.assertIn("说明", sent_messages[0])
        self.assertIn("可投资现金", sent_messages[0])
        self.assertIn("SOXX.US", sent_messages[0])

    def test_zero_investable_cash_is_silently_skipped(self):
        plan = _build_plan(
            strategy_symbols=("BOXX",),
            safe_haven_symbols=("BOXX",),
            targets={"BOXX": 27316.33},
            market_values={"BOXX": 24880.00},
            sellable_quantities={"BOXX": 214},
            quantities={"BOXX": 214},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=0.0,
            market_status="🚀 RISK-ON (SOXL)",
            deploy_ratio_text="57.7%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="37.6%",
            signal_message="SOXL 站上 150 日均线，持有 SOXL，交易层风险仓位 57.7%",
            available_cash=3065.61,
            total_strategy_equity=103350.09,
            portfolio_rows=(("BOXX",),),
        )

        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"BOXX.US": 116.27},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("账户现金", sent_messages[0])
        self.assertIn("可投资现金: $0.00", sent_messages[0])
        self.assertIn("✅ 无需调仓", sent_messages[0])
        self.assertNotIn("本轮没有可执行订单", sent_messages[0])
        self.assertNotIn("说明", sent_messages[0])
        self.assertNotIn("买入跳过", sent_messages[0])

    def test_cash_limit_zero_mentions_possible_order_hold(self):
        plan = _build_plan(
            strategy_symbols=("SOXX",),
            risk_symbols=("SOXX",),
            targets={"SOXX": 34718.05},
            market_values={"SOXX": 0.0},
            sellable_quantities={"SOXX": 0},
            quantities={"SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=40000.0,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=40000.0,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXX",),),
        )

        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"SOXX.US": 322.74},
            estimate_max_purchase_quantity_value=0,
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("券商估算可买数量为 0", sent_messages[0])
        self.assertIn("可能有未完成挂单", sent_messages[0])

    def test_refreshes_account_state_after_sell_and_can_place_followup_buy(self):
        initial_plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX"),
            risk_symbols=("SOXL", "SOXX"),
            targets={"SOXL": 0.0, "SOXX": 34718.05},
            market_values={"SOXL": 31928.30, "SOXX": 0.0},
            sellable_quantities={"SOXL": 695, "SOXX": 0},
            quantities={"SOXL": 695, "SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=101.95,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=101.95,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXL", "SOXX"),),
        )
        refreshed_plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX"),
            risk_symbols=("SOXL", "SOXX"),
            targets={"SOXL": 0.0, "SOXX": 34718.05},
            market_values={"SOXL": 0.0, "SOXX": 0.0},
            sellable_quantities={"SOXL": 0, "SOXX": 0},
            quantities={"SOXL": 0, "SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=40000.0,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=40000.0,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXL", "SOXX"),),
        )
        sent_messages, observed_account_states, observed_plan_inputs = self._run_strategy(
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
        self.assertEqual(len(observed_plan_inputs), 2)

    def test_dry_run_replaces_real_order_submission_with_summary_lines(self):
        initial_plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX"),
            risk_symbols=("SOXL", "SOXX"),
            targets={"SOXL": 0.0, "SOXX": 34718.05},
            market_values={"SOXL": 31928.30, "SOXX": 0.0},
            sellable_quantities={"SOXL": 695, "SOXX": 0},
            quantities={"SOXL": 695, "SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=101.95,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=101.95,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXL", "SOXX"),),
        )
        refreshed_plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX"),
            risk_symbols=("SOXL", "SOXX"),
            targets={"SOXL": 0.0, "SOXX": 34718.05},
            market_values={"SOXL": 0.0, "SOXX": 0.0},
            sellable_quantities={"SOXL": 0, "SOXX": 0},
            quantities={"SOXL": 0, "SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=40000.0,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=40000.0,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXL", "SOXX"),),
        )
        sent_messages, _, _ = self._run_strategy(
            initial_plan,
            refreshed_plan=refreshed_plan,
            account_states=[{"phase": "before_sell"}, {"phase": "after_sell"}],
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
            estimate_max_purchase_quantity_value=200,
            dry_run_only=True,
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🧪 dry-run 模式", sent_messages[0])
        self.assertIn("🧪 DRY_RUN SELL SOXL.US", sent_messages[0])
        self.assertIn("🧪 DRY_RUN BUY SOXX.US", sent_messages[0])

    def test_heartbeat_accepts_normalized_portfolio_and_execution_sections(self):
        plan = _build_plan(
            strategy_symbols=("SOXX",),
            risk_symbols=("SOXX",),
            targets={"SOXX": 34718.05},
            market_values={"SOXX": 0.0},
            sellable_quantities={"SOXX": 0},
            quantities={"SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=101.95,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=101.95,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXX",),),
        )

        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"SOXX.US": 322.74},
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("💓 【心跳检测】", sent_messages[0])
        self.assertIn("可投资现金", sent_messages[0])
        self.assertIn("SOXX", sent_messages[0])

    def test_hybrid_heartbeat_hides_empty_semiconductor_fields_and_shows_benchmark_line(self):
        plan = _build_plan(
            strategy_profile="tqqq_growth_income",
            strategy_symbols=("TQQQ", "BOXX", "QQQI", "SPYI"),
            risk_symbols=("TQQQ",),
            income_symbols=("QQQI", "SPYI"),
            safe_haven_symbols=("BOXX",),
            targets={"TQQQ": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0},
            market_values={"TQQQ": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0},
            sellable_quantities={"TQQQ": 0, "BOXX": 0, "QQQI": 0, "SPYI": 0},
            quantities={"TQQQ": 0, "BOXX": 0, "QQQI": 0, "SPYI": 0},
            current_min_trade=250.0,
            trade_threshold_value=250.0,
            investable_cash=0.0,
            market_status="",
            deploy_ratio_text="",
            income_ratio_text="",
            income_locked_ratio_text="",
            signal_message="💤 等待信号",
            available_cash=0.0,
            total_strategy_equity=0.0,
            portfolio_rows=(("TQQQ", "BOXX"), ("QQQI", "SPYI")),
            benchmark_symbol="QQQ",
            benchmark_price=588.50,
            long_trend_value=595.25,
            exit_line=573.00,
        )

        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={"TQQQ.US": 50.0, "BOXX.US": 100.0, "QQQI.US": 40.0, "SPYI.US": 45.0},
            dry_run_only=True,
            strategy_display_name="TQQQ 增长收益",
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("💓 【心跳检测】", sent_messages[0])
        self.assertIn("🧭 策略: TQQQ 增长收益", sent_messages[0])
        self.assertIn("🧪 dry-run 模式", sent_messages[0])
        self.assertIn("QQQ: 588.50 | MA200: 595.25 | Exit: 573.00", sent_messages[0])
        self.assertIn("🎯 信号: 💤 等待信号", sent_messages[0])
        self.assertNotIn("📊 市场状态: ", sent_messages[0])
        self.assertNotIn("💼 交易层风险仓位: ", sent_messages[0])
        self.assertNotIn("🏦 收入层锁定占比: ", sent_messages[0])


if __name__ == "__main__":
    unittest.main()
