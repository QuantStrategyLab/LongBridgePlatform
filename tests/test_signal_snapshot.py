import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.signal_snapshot import build_signal_snapshot


class SignalSnapshotTests(unittest.TestCase):
    def test_derives_snapshot_date_from_zh_status_display(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            strategy_profile="mega_cap_leader_rotation_top50_balanced",
            execution={
                "status_display": "不执行 | 原因=当前不在月度执行窗口 | 快照日期=2026-06-01 | 允许日期=2026-06-02",
                "signal_display": "月度快照节奏 | 等待进入执行窗口",
                "latest_price_source": "longbridge_candlesticks",
            },
        )

        self.assertEqual(snapshot["market_date"], "2026-06-01")
        self.assertEqual(snapshot["signal_as_of"], "2026-06-01")

    def test_derives_snapshot_date_from_runtime_diagnostic_status_display(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            execution={
                "status_display": "no-op | reason=outside_monthly_execution_window snapshot=2026-04-10 allowed=2026-04-13",
                "latest_price_source": "longbridge_candlesticks",
            },
        )

        self.assertEqual(snapshot["market_date"], "2026-04-10")
        self.assertEqual(snapshot["signal_as_of"], "2026-04-10")

    def test_prefers_structured_market_date_over_status_text(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            execution={
                "market_date": "2026-06-02",
                "status_display": "no-op | snapshot=2026-06-01 allowed=2026-06-02",
            },
        )

        self.assertEqual(snapshot["market_date"], "2026-06-02")
        self.assertEqual(snapshot["signal_as_of"], "2026-06-01")


if __name__ == "__main__":
    unittest.main()
