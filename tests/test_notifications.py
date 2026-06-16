import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications.telegram import (
    build_issue_notifier,
    build_prefixer,
    build_sender,
    build_strategy_display_name,
    build_translator,
)
from notifications.renderers import render_heartbeat_notification
from strategy_registry import SUPPORTED_STRATEGY_PROFILES


class FakeRequests:
    def __init__(self):
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json, timeout))
        return object()


class NotificationTests(unittest.TestCase):
    def test_build_translator_supports_chinese(self):
        translate = build_translator("zh")
        self.assertEqual(translate("equity", value="123.45"), "💰 净值: $123.45")
        self.assertEqual(
            translate("cash_summary", available="1.00", investable="2.00"),
            "💵 资金\n  - 账户现金: $1.00\n  - 可投资现金: $2.00",
        )
        self.assertEqual(translate("order_logs_title"), "🧾 执行明细")
        self.assertEqual(translate("benchmark_title", symbol="QQQ"), "📈 QQQ 基准")
        self.assertEqual(translate("market_status_blend_gate_risk_on", asset="SOXX+SOXL"), "🚀 风险开启（SOXX+SOXL）")
        self.assertEqual(
            translate(
                "signal_blend_gate_risk_on",
                trend_symbol="SOXX",
                window=140,
                soxl_ratio="70.0%",
                soxx_ratio="20.0%",
            ),
            "SOXX 站上 140 日门槛线，持有 SOXL 70.0% + SOXX 20.0%",
        )
        self.assertEqual(
            translate("market_status_blend_gate_overlay_capped", asset="SOXX"),
            "🧯 风控降档（SOXX）",
        )
        self.assertEqual(
            translate(
                "signal_blend_gate_overlay_capped",
                trend_symbol="SOXX",
                window=140,
                reasons="RSI 超阈值 + 突破布林上轨",
                allocation_text="SOXX 15.0%",
            ),
            "SOXX 仍在 140 日门槛线上方，但触发风控降档（RSI 超阈值 + 突破布林上轨），目标仓位 SOXX 15.0%",
        )
        self.assertEqual(
            translate(
                "blend_gate_reason_volatility_delever",
                symbol="SOXX",
                window=10,
                volatility="55.0%",
                threshold="50.0%",
                redirect_symbol="SOXX",
            ),
            "SOXX 10 日年化波动率 55.0% 高于 50.0%，SOXL 转向 SOXX",
        )
        self.assertEqual(
            translate(
                "blend_gate_reason_volatility_delever_dynamic",
                symbol="SOXX",
                window=10,
                volatility="61.0%",
                threshold="60.0%",
                threshold_detail=translate(
                    "blend_gate_volatility_threshold_detail_dynamic",
                    percentile="p95",
                    lookback="252",
                    floor="50.0%",
                    cap="75.0%",
                    sample_count="252",
                ),
                redirect_symbol="SOXX",
            ),
            "SOXX 10 日年化波动率 61.0% 高于实际阈值 60.0%（动态 p95，252日窗口，范围 50.0%-75.0%，样本 252），SOXL 转向 SOXX",
        )
        self.assertEqual(
            translate(
                "strategy_plugin_line",
                plugin=translate("strategy_plugin_name_crisis_response_shadow"),
                mode=translate("strategy_plugin_mode_shadow"),
                route=translate("strategy_plugin_route_no_action"),
                action=translate("strategy_plugin_action_monitor"),
            ),
            "🧩 插件：危机观察通知 | 状态：未触发 | 提醒：持续观察",
        )
        self.assertEqual(
            translate(
                "strategy_plugin_line",
                plugin=translate("strategy_plugin_name_market_regime_control"),
                mode=translate("strategy_plugin_mode_shadow"),
                route=translate("strategy_plugin_route_risk_reduced"),
                action=translate("strategy_plugin_action_delever"),
            ),
            "🧩 插件：市场状态控制 | 状态：风险降低 | 提醒：降杠杆",
        )
        self.assertIn("策略侧已批准", translate("strategy_plugin_guidance_market_regime_control_risk_reduced_delever"))
        self.assertEqual(
            translate(
                "small_account_warning_note",
                portfolio_equity="$0",
                min_recommended_equity="$1,000",
                reason=translate(
                    "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication"
                ),
            ),
            "小账户提示：净值 $0 低于建议 $1,000；整数股和最小仓位限制可能导致实盘无法完全复现回测",
        )

    def test_build_strategy_display_name_supports_i18n(self):
        zh_translate = build_translator("zh")
        en_translate = build_translator("en")
        zh_name = build_strategy_display_name(zh_translate)("soxl_soxx_trend_income")
        en_name = build_strategy_display_name(en_translate)("soxl_soxx_trend_income")
        self.assertEqual(zh_name, "SOXL/SOXX 半导体趋势收益")
        self.assertEqual(en_name, "SOXL/SOXX Semiconductor Trend Income")

    def test_supported_strategy_profiles_have_translated_names(self):
        zh_name = build_strategy_display_name(build_translator("zh"))
        en_name = build_strategy_display_name(build_translator("en"))
        self.assertEqual(zh_name("global_etf_confidence_vol_gate"), "全球 ETF 置信波动门控")
        self.assertEqual(en_name("global_etf_confidence_vol_gate"), "Global ETF Confidence Vol Gate")
        self.assertEqual(zh_name("hk_global_etf_tactical_rotation"), "港股全球 ETF 战术轮动")
        self.assertEqual(en_name("hk_global_etf_tactical_rotation"), "HK Global ETF Tactical Rotation")
        self.assertEqual(zh_name("hk_dividend_gold_defensive_rotation"), "港股股息黄金防守轮动")
        self.assertEqual(en_name("hk_dividend_gold_defensive_rotation"), "HK Dividend-Gold Defensive Rotation")

        for profile in SUPPORTED_STRATEGY_PROFILES:
            self.assertNotEqual(zh_name(profile), profile)
            self.assertNotEqual(en_name(profile), profile)

    def test_heartbeat_signal_snapshot_localizes_price_source(self):
        rendered = render_heartbeat_notification(
            execution={
                "signal_snapshot": {
                    "market_date": "2026-05-28",
                    "latest_price_source": "longbridge_candlesticks",
                    "quote_overlay_used": None,
                    "price_as_of": "2026-05-28",
                    "universe_as_of": "2026-04-30",
                    "source_input_status": "universe_fallback",
                    "source_input_fallback_used": True,
                    "source_input_fallback_streak": 1,
                },
                "status_display": "🚀 风险开启（SOXX+SOXL）",
                "signal_display": "SOXX 站上 140 日门槛线，持有 SOXL 70.0% + SOXX 20.0%",
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("zh"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="SOXL/SOXX 半导体趋势收益",
            dry_run_only=False,
        )

        self.assertIn("数据源 LongBridge 日线K线", rendered.compact_text)
        self.assertIn("🧩 输入状态: 价格 2026-05-28 | 股票池 2026-04-30 | 股票池复用 连续1次", rendered.compact_text)
        self.assertNotIn("报价覆盖", rendered.compact_text)
        self.assertIn("📊 市场状态: 🚀 风险开启（SOXX+SOXL）", rendered.compact_text)
        self.assertNotIn("longbridge_candlesticks", rendered.compact_text)

    def test_heartbeat_renders_tqqq_volatility_delever_risk_control(self):
        zh_rendered = render_heartbeat_notification(
            execution={
                "signal_display": "🚀 入场信号",
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_window": 5,
                "dual_drive_volatility_delever_metric": 0.312,
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_lookback": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_dynamic_min_periods": 126,
                "dual_drive_volatility_delever_dynamic_floor": 0.24,
                "dual_drive_volatility_delever_dynamic_cap": 0.36,
                "dual_drive_volatility_delever_redirect_symbol": "QQQ",
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("zh"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="TQQQ 增长收益",
            dry_run_only=False,
        )
        en_rendered = render_heartbeat_notification(
            execution={
                "signal_display": "Entry signal",
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_window": 5,
                "dual_drive_volatility_delever_metric": 0.312,
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_lookback": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_dynamic_min_periods": 126,
                "dual_drive_volatility_delever_dynamic_floor": 0.24,
                "dual_drive_volatility_delever_dynamic_cap": 0.36,
                "dual_drive_volatility_delever_redirect_symbol": "QQQ",
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("en"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="TQQQ Growth Income",
            dry_run_only=False,
        )

        self.assertIn(
            "🛡️ 风控: QQQ 5 日年化波动率 31.2% 高于实际阈值 30.0%（动态 p90，252日窗口，范围 24.0%-36.0%，样本 252），TQQQ 转向 QQQ",
            zh_rendered.compact_text,
        )
        self.assertIn(
            "🛡️ Risk control: QQQ 5d annualized volatility 31.2% is above effective threshold 30.0% (dynamic p90, 252d lookback, bounded 24.0%-36.0%, samples 252); TQQQ redirects to QQQ",
            en_rendered.compact_text,
        )

    def test_heartbeat_renders_tqqq_volatility_delever_hysteresis_risk_control(self):
        zh_rendered = render_heartbeat_notification(
            execution={
                "signal_display": "🚀 入场信号",
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_window": 5,
                "dual_drive_volatility_delever_metric": 0.262,
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_exit_threshold": 0.24,
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_lookback": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_dynamic_min_periods": 126,
                "dual_drive_volatility_delever_dynamic_floor": 0.24,
                "dual_drive_volatility_delever_dynamic_cap": 0.36,
                "dual_drive_volatility_delever_trigger_reason": "hysteresis_hold",
                "dual_drive_volatility_delever_redirect_symbol": "QQQM",
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("zh"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="TQQQ 增长收益",
            dry_run_only=False,
        )
        en_rendered = render_heartbeat_notification(
            execution={
                "signal_display": "Entry signal",
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_window": 5,
                "dual_drive_volatility_delever_metric": 0.262,
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_exit_threshold": 0.24,
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_lookback": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_dynamic_min_periods": 126,
                "dual_drive_volatility_delever_dynamic_floor": 0.24,
                "dual_drive_volatility_delever_dynamic_cap": 0.36,
                "dual_drive_volatility_delever_trigger_reason": "hysteresis_hold",
                "dual_drive_volatility_delever_redirect_symbol": "QQQM",
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("en"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="TQQQ Growth Income",
            dry_run_only=False,
        )

        self.assertIn(
            "🛡️ 风控: QQQ 5 日年化波动率 26.2% 仍高于退出阈值 24.0%；入场实际阈值 30.0%（动态 p90，252日窗口，范围 24.0%-36.0%，样本 252），维持 TQQQ 转向 QQQM",
            zh_rendered.compact_text,
        )
        self.assertIn(
            "🛡️ Risk control: QQQ 5d annualized volatility 26.2% remains above exit threshold 24.0%; entry effective threshold 30.0% (dynamic p90, 252d lookback, bounded 24.0%-36.0%, samples 252); keep TQQQ redirected to QQQM",
            en_rendered.compact_text,
        )

    def test_heartbeat_localizes_strategy_diagnostics_and_source_input_status(self):
        rendered = render_heartbeat_notification(
            execution={
                "dashboard_text": (
                    "📌 策略账户概览\n"
                    "🎯 信号: regime=risk_on breadth=68.0% benchmark_trend=up "
                    "target_stock=100.0% realized_stock=100.0% selected=4 "
                    "top=MU(4.07), INTC(2.23), AMD(1.96)"
                ),
                "signal_snapshot": {
                    "market_date": "",
                    "latest_price_source": "longbridge_candlesticks",
                    "price_as_of": "2026-06-01",
                    "universe_as_of": "2026-05-14",
                    "source_input_status": "partial_history_refresh",
                },
                "status_display": "regime=risk_on",
                "signal_display": (
                    "regime=risk_on breadth=68.0% benchmark_trend=up "
                    "target_stock=100.0% realized_stock=100.0% selected=4 "
                    "top=MU(4.07), INTC(2.23), AMD(1.96)"
                ),
            },
            skip_logs=(),
            note_logs=(),
            translator=build_translator("zh"),
            separator="━━━━━━━━━━━━━━━━━━",
            strategy_display_name="Mega Cap Top50 平衡龙头轮动",
            dry_run_only=False,
        )

        self.assertIn("🧩 输入状态: 价格 2026-06-01 | 股票池 2026-05-14 | 状态 部分行情刷新", rendered.compact_text)
        self.assertIn(
            "🎯 信号: 市场阶段=进攻 市场宽度=68.0% 基准趋势=向上 "
            "目标股票仓位=100.0% 实际股票仓位=100.0% 入选标的数=4 "
            "前排标的=MU(4.07), INTC(2.23), AMD(1.96)",
            rendered.compact_text,
        )
        self.assertIn("📊 市场状态: 市场阶段=进攻", rendered.compact_text)
        self.assertIn(
            "🎯 信号: 市场阶段=进攻 市场宽度=68.0% 基准趋势=向上 "
            "目标股票仓位=100.0% 实际股票仓位=100.0% 入选标的数=4 "
            "前排标的=MU(4.07), INTC(2.23), AMD(1.96)",
            rendered.compact_text,
        )
        self.assertNotIn("regime=risk_on", rendered.compact_text)
        self.assertNotIn("benchmark_trend=up", rendered.compact_text)
        self.assertNotIn("target_stock=", rendered.compact_text)
        self.assertNotIn("partial_history_refresh", rendered.compact_text)

    def test_build_prefixer_prefers_account_prefix_only(self):
        with_prefix = build_prefixer("HK", "longbridge-quant-semiconductor-rotation-income-hk")
        self.assertEqual(with_prefix("hello"), "[HK] hello")

    def test_build_prefixer_falls_back_to_service_name(self):
        with_prefix = build_prefixer("", "longbridge-quant-hk")
        self.assertEqual(with_prefix("hello"), "[longbridge-quant-hk] hello")

    def test_build_sender_posts_prefixed_message(self):
        fake_requests = FakeRequests()
        sender = build_sender(
            "token-1",
            "chat-1",
            with_prefix_fn=build_prefixer("HK", "longbridge-quant-semiconductor-rotation-income-hk"),
            requests_module=fake_requests,
        )
        sender("hello")
        self.assertEqual(len(fake_requests.calls), 1)
        url, payload, timeout = fake_requests.calls[0]
        self.assertIn("token-1", url)
        self.assertEqual(payload["chat_id"], "chat-1")
        self.assertEqual(payload["text"], "[HK] hello")
        self.assertEqual(timeout, 10)

    def test_build_issue_notifier_logs_and_sends(self):
        sent = []
        notifier = build_issue_notifier(
            with_prefix_fn=build_prefixer("SG", "longbridge-quant-semiconductor-rotation-income-sg"),
            send_tg_message_fn=sent.append,
        )
        notifier("Problem", "details")
        self.assertEqual(sent, ["Problem\ndetails"])


if __name__ == "__main__":
    unittest.main()
