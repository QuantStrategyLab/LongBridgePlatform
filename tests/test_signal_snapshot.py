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

    def test_includes_snapshot_manifest_input_diagnostics(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            metadata={
                "snapshot_manifest_price_as_of": "2026-06-01",
                "snapshot_manifest_universe_as_of": "2026-05-31",
                "snapshot_manifest_source_input_status": "universe_fallback",
                "snapshot_manifest_source_input_fallback_used": True,
                "snapshot_manifest_source_input_fallback_reason": "RuntimeError: upstream returned HTML",
                "snapshot_manifest_source_input_fallback_streak": 1,
                "snapshot_manifest_source_refresh_run_id": "12345",
            },
        )

        self.assertEqual(snapshot["price_as_of"], "2026-06-01")
        self.assertEqual(snapshot["universe_as_of"], "2026-05-31")
        self.assertEqual(snapshot["source_input_status"], "universe_fallback")
        self.assertIs(snapshot["source_input_fallback_used"], True)
        self.assertEqual(snapshot["source_input_fallback_streak"], 1)
        self.assertEqual(snapshot["source_refresh_run_id"], "12345")

    def test_uses_price_as_of_as_snapshot_date_fallback(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            execution={
                "snapshot_manifest_price_as_of": "2026-06-01",
                "snapshot_manifest_universe_as_of": "2026-05-14",
                "snapshot_manifest_source_input_status": "partial_history_refresh",
                "latest_price_source": "longbridge_candlesticks",
                "signal_display": (
                    "regime=risk_on breadth=68.0% benchmark_trend=up "
                    "target_stock=100.0% realized_stock=100.0% selected=4"
                ),
            },
        )

        self.assertEqual(snapshot["market_date"], "2026-06-01")
        self.assertEqual(snapshot["signal_as_of"], "2026-06-01")
        self.assertEqual(snapshot["price_as_of"], "2026-06-01")
        self.assertEqual(snapshot["universe_as_of"], "2026-05-14")

    def test_includes_soxl_dynamic_volatility_fields(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            strategy_profile="soxl_soxx_trend_income",
            execution={
                "blend_gate_volatility_delever_threshold_mode": "rolling_percentile",
                "blend_gate_volatility_delever_threshold": 0.60,
                "blend_gate_volatility_delever_dynamic_threshold": 0.60,
                "blend_gate_volatility_delever_dynamic_sample_count": 252,
                "blend_gate_volatility_delever_dynamic_percentile": 0.95,
                "blend_gate_volatility_delever_metric": 0.61,
                "blend_gate_volatility_delever_triggered": True,
            },
        )

        self.assertEqual(
            snapshot["indicators"]["blend_gate_volatility_delever_threshold_mode"],
            "rolling_percentile",
        )
        self.assertEqual(
            snapshot["indicators"]["blend_gate_volatility_delever_dynamic_threshold"],
            0.60,
        )
        self.assertEqual(
            snapshot["indicators"][
                "blend_gate_volatility_delever_dynamic_sample_count"
            ],
            252,
        )
        self.assertIs(
            snapshot["indicators"]["blend_gate_volatility_delever_triggered"], True
        )

    def test_includes_tqqq_extended_risk_control_fields(self):
        snapshot = build_signal_snapshot(
            platform="longbridge",
            strategy_profile="tqqq_growth_income",
            execution={
                "dual_drive_volatility_delever_threshold_mode": "rolling_percentile",
                "dual_drive_volatility_delever_threshold": 0.28,
                "dual_drive_volatility_delever_exit_threshold": 0.24,
                "dual_drive_volatility_delever_dynamic_threshold": 0.30,
                "dual_drive_volatility_delever_dynamic_sample_count": 252,
                "dual_drive_volatility_delever_dynamic_percentile": 0.90,
                "dual_drive_volatility_delever_metric": 0.312,
                "dual_drive_volatility_delever_applied": True,
                "dual_drive_volatility_delever_veto_reason": "taco_rebound_context",
                "dual_drive_volatility_delever_taco_veto_enabled": True,
                "dual_drive_volatility_delever_removed_value": 4500.0,
                "dual_drive_macro_risk_governor_applied": True,
                "dual_drive_macro_risk_governor_route": "risk_reduced",
                "dual_drive_crisis_defense_destination": "BOXX",
                "market_regime_control_route": "risk_reduced",
                "market_regime_control_reason_codes": ("macro:vix_crisis_level",),
                "dual_drive_volatility_delever_redirect_symbol": "QQQM",
            },
        )

        indicators = snapshot["indicators"]
        self.assertEqual(indicators["dual_drive_volatility_delever_threshold_mode"], "rolling_percentile")
        self.assertEqual(indicators["dual_drive_volatility_delever_dynamic_threshold"], 0.30)
        self.assertIs(indicators["dual_drive_volatility_delever_applied"], True)
        self.assertEqual(indicators["dual_drive_volatility_delever_veto_reason"], "taco_rebound_context")
        self.assertIs(indicators["dual_drive_volatility_delever_taco_veto_enabled"], True)
        self.assertEqual(indicators["dual_drive_volatility_delever_removed_value"], 4500.0)
        self.assertIs(indicators["dual_drive_macro_risk_governor_applied"], True)
        self.assertEqual(indicators["dual_drive_macro_risk_governor_route"], "risk_reduced")
        self.assertEqual(indicators["dual_drive_crisis_defense_destination"], "BOXX")
        self.assertEqual(indicators["market_regime_control_route"], "risk_reduced")
        self.assertEqual(indicators["market_regime_control_reason_codes"], ["macro:vix_crisis_level"])
        self.assertEqual(indicators["dual_drive_volatility_delever_redirect_symbol"], "QQQM")


if __name__ == "__main__":
    unittest.main()
