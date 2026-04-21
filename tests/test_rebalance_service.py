import sys
import unittest
from pathlib import Path


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

_original_requests_module = sys.modules.get("requests")
sys.modules["requests"] = requests_stub
try:
    from application import rebalance_service
    from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
    from notifications.telegram import build_translator
    from quant_platform_kit.common.models import ExecutionReport, PortfolioSnapshot, Position, QuoteSnapshot
    from quant_platform_kit.common.port_adapters import CallableExecutionPort, CallableMarketDataPort, CallableNotificationPort, CallablePortfolioPort
finally:
    if _original_requests_module is None:
        sys.modules.pop("requests", None)
    else:
        sys.modules["requests"] = _original_requests_module


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
    dashboard_text=None,
    benchmark_symbol="",
    benchmark_price=0.0,
    long_trend_value=0.0,
    exit_line=0.0,
    cash_by_currency=None,
    signal_date="2026-04-21",
    effective_date="2026-04-22",
    execution_timing_contract="next_trading_day",
):
    if dashboard_text is None:
        dashboard_lines = [
            "📌 策略账户概览",
            f"  - 总资产（策略标的+现金）: ${float(total_strategy_equity):,.2f}",
            f"  - 购买力: ${float(available_cash):.2f} | 可投资现金: ${float(investable_cash):.2f}",
        ]
        nonzero_cash = {
            currency: amount
            for currency, amount in dict(cash_by_currency or {}).items()
            if float(amount) != 0.0
        }
        if nonzero_cash and (len(nonzero_cash) > 1 or "USD" not in nonzero_cash):
            formatted_cash = ", ".join(
                f"{currency} {float(nonzero_cash[currency]):,.2f}"
                for currency in sorted(nonzero_cash, key=lambda value: (value != "USD", value))
            )
            dashboard_lines.append(f"  - 各币种现金: {formatted_cash}")
        dashboard_lines.append("💼 策略持仓")
        for row in portfolio_rows:
            for symbol in row:
                dashboard_lines.append(
                    f"  - {symbol}: ${float(market_values[symbol]):,.2f} / {quantities.get(symbol, 0)}股"
                )
        dashboard_text = "\n".join(dashboard_lines)
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
            "cash_by_currency": dict(cash_by_currency or {}),
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
            "signal_date": signal_date,
            "effective_date": effective_date,
            "execution_timing_contract": execution_timing_contract,
        },
    }


def _build_snapshot(plan, *, phase=""):
    portfolio = dict(plan["portfolio"])
    metadata = {"cash_by_currency": dict(portfolio.get("cash_by_currency") or {})}
    if phase:
        metadata["phase"] = phase
    return PortfolioSnapshot(
        as_of="2026-04-21",
        total_equity=float(portfolio["total_equity"]),
        buying_power=float(portfolio["liquid_cash"]),
        cash_balance=float(portfolio["liquid_cash"]),
        positions=tuple(
            Position(
                symbol=symbol,
                quantity=float(portfolio["quantities"].get(symbol, 0)),
                market_value=float(portfolio["market_values"].get(symbol, 0.0)),
            )
            for symbol in portfolio["strategy_symbols"]
        ),
        metadata=metadata,
    )


class RebalanceServiceNotificationTests(unittest.TestCase):
    def test_run_strategy_prefers_portfolio_port_runtime_path(self):
        sent_messages = []
        observed = {}
        snapshot = PortfolioSnapshot(
            as_of="2026-04-21",
            total_equity=60000.0,
            buying_power=101.95,
            cash_balance=101.95,
            positions=(
                Position(symbol="SOXX", quantity=0, market_value=0.0),
            ),
            metadata={"cash_by_currency": {"USD": 101.95}},
        )
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

        rebalance_service.run_strategy(
            runtime=LongBridgeRebalanceRuntime(
                bootstrap=lambda: ("quote-context", "trade-context", {"soxl": {"price": 1.0}}),
                resolve_rebalance_plan=lambda *, indicators, snapshot=None, account_state=None: (
                    observed.setdefault("indicators", indicators),
                    observed.setdefault("snapshot", snapshot),
                    observed.setdefault("account_state", account_state),
                    plan,
                )[-1],
                market_data_port_factory=lambda _quote_context: CallableMarketDataPort(
                    quote_loader=lambda symbol: QuoteSnapshot(
                        symbol=symbol,
                        as_of="2026-04-21",
                        last_price=322.74,
                    )
                ),
                estimate_max_purchase_quantity=lambda *args, **kwargs: 0,
                notifications=CallableNotificationPort(sent_messages.append),
                notify_issue=lambda title, detail: sent_messages.append(f"{title}\n{detail}"),
                portfolio_port_factory=lambda _quote_context, _trade_context: CallablePortfolioPort(
                    lambda: snapshot
                ),
                execution_port_factory=lambda _trade_context: CallableExecutionPort(
                    lambda _order_intent: (_ for _ in ()).throw(AssertionError("unexpected order submit"))
                ),
            ),
            config=LongBridgeRebalanceConfig(
                limit_sell_discount=0.995,
                limit_buy_premium=1.005,
                separator="━━━━━━━━━━━━━━━━━━",
                translator=build_translator("zh"),
                with_prefix=lambda message: f"[HK/LongBridgeQuant] {message}",
                strategy_display_name="SOXL/SOXX 半导体趋势收益",
            ),
        )

        self.assertIs(observed["snapshot"], snapshot)
        self.assertIsNone(observed["account_state"])
        self.assertEqual(observed["indicators"], {"soxl": {"price": 1.0}})
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("【心跳", sent_messages[0])

    def test_run_strategy_supports_execution_port_runtime_path(self):
        sent_messages = []
        observed_orders = []
        observed_post_submit = []
        snapshot = PortfolioSnapshot(
            as_of="2026-04-21",
            total_equity=60000.0,
            buying_power=50000.0,
            cash_balance=50000.0,
            positions=(Position(symbol="SOXX", quantity=0, market_value=0.0),),
            metadata={"cash_by_currency": {"USD": 50000.0}},
        )
        plan = _build_plan(
            strategy_symbols=("SOXX",),
            risk_symbols=("SOXX",),
            targets={"SOXX": 34718.05},
            market_values={"SOXX": 0.0},
            sellable_quantities={"SOXX": 0},
            quantities={"SOXX": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=50000.0,
            market_status="🛡️ DE-LEVER (SOXX)",
            deploy_ratio_text="57.9%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="38.3%",
            signal_message="SOXL 跌破 150 日均线，切换至 SOXX，交易层风险仓位 57.9%",
            available_cash=50000.0,
            total_strategy_equity=60000.0,
            portfolio_rows=(("SOXX",),),
        )

        rebalance_service.run_strategy(
            runtime=LongBridgeRebalanceRuntime(
                bootstrap=lambda: ("quote-context", "trade-context", {"soxl": {"price": 1.0}}),
                resolve_rebalance_plan=lambda *, indicators, snapshot=None: plan,
                market_data_port_factory=lambda _quote_context: CallableMarketDataPort(
                    quote_loader=lambda symbol: QuoteSnapshot(
                        symbol=symbol,
                        as_of="2026-04-21",
                        last_price=322.74,
                    )
                ),
                estimate_max_purchase_quantity=lambda *args, **kwargs: 200,
                notifications=CallableNotificationPort(sent_messages.append),
                notify_issue=lambda title, detail: sent_messages.append(f"{title}\n{detail}"),
                portfolio_port_factory=lambda _quote_context, _trade_context: CallablePortfolioPort(
                    lambda: snapshot
                ),
                execution_port_factory=lambda _trade_context: CallableExecutionPort(
                    lambda order_intent: (
                        observed_orders.append(order_intent),
                        ExecutionReport(
                            symbol=order_intent.symbol,
                            side=order_intent.side,
                            quantity=order_intent.quantity,
                            status="submitted",
                            broker_order_id="lb-order-1",
                        ),
                    )[-1]
                ),
                post_submit_order=lambda trade_context, order_intent, report: observed_post_submit.append(
                    (trade_context, order_intent.symbol, report.broker_order_id)
                ),
            ),
            config=LongBridgeRebalanceConfig(
                limit_sell_discount=0.995,
                limit_buy_premium=1.005,
                separator="━━━━━━━━━━━━━━━━━━",
                translator=build_translator("zh"),
                with_prefix=lambda message: f"[HK/LongBridgeQuant] {message}",
                strategy_display_name="SOXL/SOXX 半导体趋势收益",
            ),
        )

        self.assertEqual(len(observed_orders), 1)
        self.assertEqual(observed_orders[0].symbol, "SOXX.US")
        self.assertEqual(observed_orders[0].order_type, "limit")
        self.assertEqual(observed_post_submit, [("trade-context", "SOXX.US", "lb-order-1")])
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("【调仓", sent_messages[0])

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

        self.assertIn("📊 市场状态: 关闭执行", lines)
        self.assertIn("  - 原因=缺少特征快照路径", lines)
        self.assertIn("🎯 信号: 特征快照校验阻止执行", lines)

    def test_append_status_lines_localizes_qqq_tech_diagnostics_for_zh(self):
        lines = []
        rebalance_service._append_status_lines(
            lines,
            execution={
                "status_display": "regime=soft_defense | breadth=41.2% | target_stock=60.0% | realized_stock=60.0%",
                "signal_display": (
                    "regime=soft_defense breadth=41.2% benchmark_trend=down "
                    "target_stock=60.0% realized_stock=60.0% selected=8 top=CIEN(0.92)"
                ),
            },
            translator=build_translator("zh"),
            signal_key="signal",
        )

        self.assertIn(
            "📊 市场状态: 市场阶段=软防御",
            lines,
        )
        self.assertIn("  - 市场宽度=41.2%", lines)
        self.assertIn("  - 目标股票仓位=60.0%", lines)
        self.assertIn("  - 实际股票仓位=60.0%", lines)
        self.assertIn(
            "🎯 触发信号: 市场阶段=软防御 市场宽度=41.2% 基准趋势=向下 "
            "目标股票仓位=60.0% 实际股票仓位=60.0% 入选标的数=8 前排标的=CIEN(0.92)",
            lines,
        )

    def test_append_status_lines_localizes_runtime_diagnostic_tail_for_zh(self):
        lines = []
        rebalance_service._append_status_lines(
            lines,
            execution={
                "status_display": (
                    "no-op | reason=outside_monthly_execution_window "
                    "snapshot=2026-04-10 allowed=2026-04-13"
                ),
                "signal_display": (
                    "monthly snapshot cadence | waiting inside execution window | "
                    "small_account_warning=true portfolio_equity=$0 "
                    "min_recommended_equity=$1,000 "
                    "reason=integer_shares_min_position_value_may_prevent_backtest_replication"
                ),
            },
            translator=build_translator("zh"),
            signal_key="heartbeat_signal",
        )

        self.assertIn(
            "📊 市场状态: 不执行",
            lines,
        )
        self.assertIn("  - 原因=当前不在月度执行窗口", lines)
        self.assertIn("  - 快照日期=2026-04-10", lines)
        self.assertIn("  - 允许日期=2026-04-13", lines)
        self.assertIn("🎯 信号: 月度快照节奏", lines)
        self.assertIn("  - 等待进入执行窗口", lines)
        self.assertIn("  - 小账户提示=是", lines)
        self.assertIn("  - 净值=$0", lines)
        self.assertIn("  - 建议最低净值=$1,000", lines)
        self.assertIn("  - 原因=整数股和最小仓位限制可能导致实盘无法完全复现回测", lines)

    def _run_strategy(
        self,
        plan,
        *,
        prices,
        refreshed_plan=None,
        portfolio_snapshots=None,
        estimate_max_purchase_quantity_value=0,
        dry_run_only=False,
        strategy_display_name="SOXL/SOXX 半导体趋势收益",
        post_sell_refresh_attempts=1,
    ):
        sent_messages = []
        observed_snapshots = []
        observed_orders = []
        observed_sleeps = []

        if isinstance(refreshed_plan, (list, tuple)):
            plan_side_effect = [plan, *refreshed_plan]
        else:
            plan_side_effect = [plan, refreshed_plan or plan]
        observed_plan_inputs = []

        snapshot_values = list(
            portfolio_snapshots
            or [
                _build_snapshot(plan, phase="before_cycle"),
                _build_snapshot(refreshed_plan or plan, phase="after_cycle"),
            ]
        )

        def fake_load_snapshot():
            if not snapshot_values:
                raise AssertionError("unexpected extra portfolio snapshot refresh")
            value = snapshot_values.pop(0)
            observed_snapshots.append(value)
            return value

        def fake_resolve_rebalance_plan(*, indicators, snapshot):
            observed_plan_inputs.append((indicators, snapshot))
            if not plan_side_effect:
                raise AssertionError("unexpected extra resolve_rebalance_plan call")
            return plan_side_effect.pop(0)

        rebalance_service.run_strategy(
            runtime=LongBridgeRebalanceRuntime(
                bootstrap=lambda: ("quote-context", "trade-context", {"soxl": {"price": 1, "ma_trend": 2}}),
                resolve_rebalance_plan=fake_resolve_rebalance_plan,
                market_data_port_factory=lambda _quote_context: CallableMarketDataPort(
                    quote_loader=lambda symbol: QuoteSnapshot(
                        symbol=symbol,
                        as_of="2026-04-21",
                        last_price=float(prices[symbol]),
                    )
                ),
                estimate_max_purchase_quantity=lambda *args, **kwargs: estimate_max_purchase_quantity_value,
                notifications=CallableNotificationPort(sent_messages.append),
                notify_issue=lambda title, detail: sent_messages.append(f"{title}\n{detail}"),
                portfolio_port_factory=lambda _quote_context, _trade_context: CallablePortfolioPort(fake_load_snapshot),
                execution_port_factory=lambda _trade_context: CallableExecutionPort(
                    lambda order_intent: (
                        observed_orders.append(order_intent),
                        ExecutionReport(
                            symbol=order_intent.symbol,
                            side=order_intent.side,
                            quantity=order_intent.quantity,
                            status="submitted",
                            broker_order_id="test-order",
                        ),
                    )[-1]
                ),
            ),
            config=LongBridgeRebalanceConfig(
                limit_sell_discount=0.995,
                limit_buy_premium=1.005,
                separator="━━━━━━━━━━━━━━━━━━",
                translator=build_translator("zh"),
                with_prefix=lambda message: f"[HK/LongBridgeQuant] {message}",
                strategy_display_name=strategy_display_name,
                dry_run_only=dry_run_only,
                post_sell_refresh_attempts=post_sell_refresh_attempts,
                post_sell_refresh_interval_sec=0.0,
                sleeper=observed_sleeps.append,
            ),
        )

        return sent_messages, observed_snapshots, observed_plan_inputs

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
        self.assertIn("⏱ 执行时点: 2026-04-21 -> 2026-04-22 (next_trading_day)", sent_messages[0])
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
        self.assertIn("⏱ 执行时点: 2026-04-21 -> 2026-04-22 (next_trading_day)", sent_messages[0])
        self.assertIn("本轮没有可执行订单", sent_messages[0])
        self.assertIn("说明", sent_messages[0])
        self.assertIn("可投资现金", sent_messages[0])
        self.assertIn("SOXX.US", sent_messages[0])

    def test_zero_investable_cash_reports_buying_power_without_trade_note(self):
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
        self.assertNotIn("账户现金", sent_messages[0])
        self.assertIn("购买力: $3065.61 | 可投资现金: $0.00", sent_messages[0])
        self.assertIn("BOXX: $24,880.00 / 214股", sent_messages[0])
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

    def test_non_usd_cash_is_reported_when_usd_cash_is_zero(self):
        plan = _build_plan(
            strategy_symbols=("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
            risk_symbols=("SOXL", "SOXX"),
            income_symbols=("QQQI", "SPYI"),
            safe_haven_symbols=("BOXX",),
            targets={"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0},
            market_values={"SOXL": 0.0, "SOXX": 0.0, "BOXX": 0.0, "QQQI": 0.0, "SPYI": 0.0},
            sellable_quantities={"SOXL": 0, "SOXX": 0, "BOXX": 0, "QQQI": 0, "SPYI": 0},
            quantities={"SOXL": 0, "SOXX": 0, "BOXX": 0, "QQQI": 0, "SPYI": 0},
            current_min_trade=100.0,
            trade_threshold_value=100.0,
            investable_cash=0.0,
            market_status="🚀 RISK-ON (SOXX+SOXL)",
            deploy_ratio_text="90.0%",
            income_ratio_text="0.0%",
            income_locked_ratio_text="0.0%",
            signal_message="SOXX 站上 140 日门槛线，持有 SOXL 70.0% + SOXX 20.0%",
            available_cash=0.0,
            total_strategy_equity=0.0,
            portfolio_rows=(("SOXL", "SOXX"), ("QQQI", "SPYI"), ("BOXX",)),
            cash_by_currency={"USD": 0.0, "SGD": 350.0},
        )

        sent_messages, _, _ = self._run_strategy(
            plan,
            prices={},
            dry_run_only=True,
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("各币种现金: SGD 350.00", sent_messages[0])
        self.assertIn("检测到非 USD 现金", sent_messages[0])
        self.assertIn("本轮没有可执行订单", sent_messages[0])
        self.assertNotIn("✅ 无需调仓", sent_messages[0])

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
        before_sell_snapshot = _build_snapshot(initial_plan, phase="before_sell")
        after_sell_snapshot = _build_snapshot(refreshed_plan, phase="after_sell")
        sent_messages, observed_snapshots, observed_plan_inputs = self._run_strategy(
            initial_plan,
            refreshed_plan=refreshed_plan,
            portfolio_snapshots=[before_sell_snapshot, after_sell_snapshot],
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
            estimate_max_purchase_quantity_value=200,
        )

        self.assertEqual(observed_snapshots, [before_sell_snapshot, after_sell_snapshot])
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🔔 【调仓指令】", sent_messages[0])
        self.assertIn("限价卖出", sent_messages[0])
        self.assertIn("限价买入", sent_messages[0])
        self.assertNotIn("买入跳过", sent_messages[0])
        self.assertEqual(len(observed_plan_inputs), 2)

    def test_retries_account_refresh_after_sell_until_buying_power_updates(self):
        initial_plan = _build_plan(
            strategy_profile="tqqq_growth_income",
            strategy_symbols=("TQQQ", "BOXX"),
            risk_symbols=("TQQQ",),
            safe_haven_symbols=("BOXX",),
            targets={"TQQQ": 900.0, "BOXX": 100.0},
            market_values={"TQQQ": 0.0, "BOXX": 1000.0},
            sellable_quantities={"TQQQ": 0, "BOXX": 10},
            quantities={"TQQQ": 0, "BOXX": 10},
            current_min_trade=10.0,
            trade_threshold_value=10.0,
            investable_cash=101.95,
            market_status="",
            deploy_ratio_text="",
            income_ratio_text="",
            income_locked_ratio_text="",
            signal_message="🚀 入场信号",
            available_cash=101.95,
            total_strategy_equity=1200.0,
            portfolio_rows=(("TQQQ", "BOXX"),),
        )
        stale_refreshed_plan = _build_plan(
            strategy_profile="tqqq_growth_income",
            strategy_symbols=("TQQQ", "BOXX"),
            risk_symbols=("TQQQ",),
            safe_haven_symbols=("BOXX",),
            targets={"TQQQ": 900.0, "BOXX": 100.0},
            market_values={"TQQQ": 0.0, "BOXX": 1000.0},
            sellable_quantities={"TQQQ": 0, "BOXX": 10},
            quantities={"TQQQ": 0, "BOXX": 10},
            current_min_trade=10.0,
            trade_threshold_value=10.0,
            investable_cash=101.95,
            market_status="",
            deploy_ratio_text="",
            income_ratio_text="",
            income_locked_ratio_text="",
            signal_message="🚀 入场信号",
            available_cash=101.95,
            total_strategy_equity=1200.0,
            portfolio_rows=(("TQQQ", "BOXX"),),
        )
        settled_refreshed_plan = _build_plan(
            strategy_profile="tqqq_growth_income",
            strategy_symbols=("TQQQ", "BOXX"),
            risk_symbols=("TQQQ",),
            safe_haven_symbols=("BOXX",),
            targets={"TQQQ": 900.0, "BOXX": 100.0},
            market_values={"TQQQ": 0.0, "BOXX": 100.0},
            sellable_quantities={"TQQQ": 0, "BOXX": 1},
            quantities={"TQQQ": 0, "BOXX": 1},
            current_min_trade=10.0,
            trade_threshold_value=10.0,
            investable_cash=1001.95,
            market_status="",
            deploy_ratio_text="",
            income_ratio_text="",
            income_locked_ratio_text="",
            signal_message="🚀 入场信号",
            available_cash=1001.95,
            total_strategy_equity=1200.0,
            portfolio_rows=(("TQQQ", "BOXX"),),
        )

        before_sell_snapshot = _build_snapshot(initial_plan, phase="before_sell")
        stale_snapshot = _build_snapshot(stale_refreshed_plan, phase="stale_after_sell")
        settled_snapshot = _build_snapshot(settled_refreshed_plan, phase="settled_after_sell")
        sent_messages, observed_snapshots, observed_plan_inputs = self._run_strategy(
            initial_plan,
            refreshed_plan=[stale_refreshed_plan, settled_refreshed_plan],
            portfolio_snapshots=[
                before_sell_snapshot,
                stale_snapshot,
                settled_snapshot,
            ],
            prices={"TQQQ.US": 50.0, "BOXX.US": 100.0},
            estimate_max_purchase_quantity_value=200,
            strategy_display_name="TQQQ 增长收益",
            post_sell_refresh_attempts=2,
        )

        self.assertEqual(
            observed_snapshots,
            [before_sell_snapshot, stale_snapshot, settled_snapshot],
        )
        self.assertEqual(len(observed_plan_inputs), 3)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("市价卖出", sent_messages[0])
        self.assertIn("限价买入", sent_messages[0])
        self.assertIn("TQQQ", sent_messages[0])
        self.assertNotIn("买入说明", sent_messages[0])

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
            portfolio_snapshots=[
                _build_snapshot(initial_plan, phase="before_sell"),
                _build_snapshot(refreshed_plan, phase="after_sell"),
            ],
            prices={"SOXL.US": 45.94, "SOXX.US": 322.74},
            estimate_max_purchase_quantity_value=200,
            dry_run_only=True,
        )

        self.assertEqual(len(sent_messages), 1)
        self.assertIn("🧪 模拟运行模式", sent_messages[0])
        self.assertIn("🧪 模拟限价卖出 SOXL.US", sent_messages[0])
        self.assertIn("🧪 模拟限价买入 SOXX.US", sent_messages[0])

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
        self.assertNotIn("💵 资金\n  - 账户现金:", sent_messages[0])
        self.assertIn("📌 策略账户概览", sent_messages[0])
        self.assertIn("总资产（策略标的+现金）: $60,000.00", sent_messages[0])
        self.assertIn("购买力: $101.95 | 可投资现金: $101.95", sent_messages[0])
        self.assertIn("SOXX: $0.00 / 0股", sent_messages[0])

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
        self.assertIn("🧪 模拟运行模式", sent_messages[0])
        self.assertIn("📌 策略账户概览", sent_messages[0])
        self.assertIn("TQQQ: $0.00 / 0股", sent_messages[0])
        self.assertIn("BOXX: $0.00 / 0股", sent_messages[0])
        self.assertIn("QQQI: $0.00 / 0股", sent_messages[0])
        self.assertIn("SPYI: $0.00 / 0股", sent_messages[0])
        self.assertNotIn("📈 QQQ 基准\n  - QQQ: 588.50\n  - MA200: 595.25\n  - 退出线: 573.00", sent_messages[0])
        self.assertIn("🎯 信号: 💤 等待信号", sent_messages[0])
        self.assertNotIn("账户现金: $0.00 | 可投资现金", sent_messages[0])
        self.assertNotIn("TQQQ: $0.00  BOXX", sent_messages[0])
        self.assertNotIn("📊 市场状态: ", sent_messages[0])
        self.assertNotIn("💼 交易层风险仓位: ", sent_messages[0])
        self.assertNotIn("🏦 收入层锁定占比: ", sent_messages[0])


if __name__ == "__main__":
    unittest.main()
