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

        for profile in SUPPORTED_STRATEGY_PROFILES:
            self.assertNotEqual(zh_name(profile), profile)
            self.assertNotEqual(en_name(profile), profile)

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
