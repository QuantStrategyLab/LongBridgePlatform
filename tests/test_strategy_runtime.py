import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import strategy_runtime as strategy_runtime_module
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.strategy_contracts import (
    StrategyDecision,
    StrategyManifest,
    StrategyRuntimeAdapter,
    StrategyRuntimePolicy,
)
from runtime_config_support import PlatformRuntimeSettings


class _TqqqEntrypoint:
    manifest = StrategyManifest(
        profile="tqqq_growth_income",
        domain="us_equity",
        display_name="TQQQ Growth Income",
        description="test entrypoint",
        required_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
        default_config={
            "benchmark_symbol": "QQQ",
            "managed_symbols": ("TQQQ", "QQQ", "BOXX", "SPYI", "QQQI"),
            "income_threshold_usd": 1_000_000_000.0,
            "qqqi_income_ratio": 0.5,
        },
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "tqqq"})


class _SemiconductorEntrypoint:
    def __init__(self):
        self.manifest = StrategyManifest(
            profile="soxl_soxx_trend_income",
            domain="us_equity",
            display_name="SOXL/SOXX Semiconductor Trend Income",
            description="test entrypoint",
            required_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
            default_config={"managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI")},
        )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_message": "ok"})


class _TechEntrypoint:
    manifest = StrategyManifest(
        profile="tech_communication_pullback_enhancement",
        domain="us_equity",
        display_name="Tech/Communication Pullback Enhancement",
        description="test entrypoint",
        required_inputs=frozenset({"feature_snapshot"}),
        default_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "risk on"})


class _RussellEntrypoint:
    manifest = StrategyManifest(
        profile="russell_1000_multi_factor_defensive",
        domain="us_equity",
        display_name="Russell 1000 Multi-Factor",
        description="test entrypoint",
        required_inputs=frozenset({"feature_snapshot"}),
        default_config={"safe_haven": "BOXX", "benchmark_symbol": "SPY"},
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "broad risk on"})


class _DynamicMegaLeveragedEntrypoint:
    manifest = StrategyManifest(
        profile="dynamic_mega_leveraged_pullback",
        domain="us_equity",
        display_name="Dynamic Mega Leveraged Pullback",
        description="test entrypoint",
        required_inputs=frozenset({"feature_snapshot", "market_history", "benchmark_history", "portfolio_snapshot"}),
        default_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "leveraged pullback"})


def _build_runtime_settings(
    profile: str,
    *,
    feature_snapshot_path: str | None = None,
    income_threshold_usd: float | None = None,
    qqqi_income_ratio: float | None = None,
) -> PlatformRuntimeSettings:
    return PlatformRuntimeSettings(
        project_id=None,
        secret_name="longport_token_hk",
        account_prefix="HK",
        strategy_profile=profile,
        strategy_display_name=(
            "Tech/Communication Pullback Enhancement" if profile == "tech_communication_pullback_enhancement" else "SOXL/SOXX Semiconductor Trend Income"
        ),
        strategy_domain="us_equity",
        account_region="HK",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        income_threshold_usd=income_threshold_usd,
        qqqi_income_ratio=qqqi_income_ratio,
        feature_snapshot_path=feature_snapshot_path,
        feature_snapshot_manifest_path=None,
        strategy_config_path=None,
        strategy_config_source=None,
    )


class StrategyRuntimeTests(unittest.TestCase):
    def test_market_history_runtime_loads_loader_into_context(self):
        class _GlobalEntrypoint:
            manifest = StrategyManifest(
                profile="global_etf_rotation",
                domain="us_equity",
                display_name="Global ETF Rotation",
                description="test entrypoint",
                required_inputs=frozenset({"market_history"}),
                default_config={"safe_haven": "BIL", "ranking_pool": ("VOO", "VGK")},
            )

            def evaluate(self, ctx):
                self.ctx = ctx
                return StrategyDecision(diagnostics={"signal_description": "quarterly"})

        entrypoint = _GlobalEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_build_runtime_settings("global_etf_rotation"),
            merged_runtime_config={"safe_haven": "BIL", "ranking_pool": ("VOO", "VGK")},
        )

        def market_history_loader(*_args, **_kwargs):
            return [1.0, 2.0, 3.0]

        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=1000.0,
            buying_power=200.0,
            positions=(),
        )
        result = runtime.evaluate(
            market_history=market_history_loader,
            portfolio_snapshot=snapshot,
            translator=lambda key, **_kwargs: key,
        )

        self.assertIs(entrypoint.ctx.market_data["market_history"], market_history_loader)
        self.assertIs(entrypoint.ctx.portfolio, snapshot)
        self.assertEqual(result.metadata["strategy_profile"], "global_etf_rotation")

    def test_runtime_exposes_managed_symbols_and_injects_translator(self):
        entrypoint = _SemiconductorEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_build_runtime_settings("soxl_soxx_trend_income"),
            merged_runtime_config={"managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI")},
        )

        result = runtime.evaluate(
            derived_indicators={"soxl": {"price": 1.0, "ma_trend": 2.0}},
            portfolio_snapshot=PortfolioSnapshot(
                as_of=datetime.now(timezone.utc),
                total_equity=100.0,
                buying_power=100.0,
                positions=(),
            ),
            translator=lambda key, **_kwargs: key,
            signal_text_fn=lambda icon: f"signal:{icon}",
        )

        self.assertEqual(runtime.managed_symbols, ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))
        self.assertEqual(entrypoint.ctx.market_data["derived_indicators"]["soxl"]["price"], 1.0)
        self.assertEqual(entrypoint.ctx.portfolio.total_equity, 100.0)
        self.assertIn("translator", entrypoint.ctx.runtime_config)
        self.assertEqual(entrypoint.ctx.runtime_config["signal_text_fn"]("idle"), "signal:idle")
        self.assertEqual(result.metadata["strategy_profile"], "soxl_soxx_trend_income")
        self.assertEqual(result.metadata["strategy_display_name"], "SOXL/SOXX Semiconductor Trend Income")

    def test_load_strategy_runtime_uses_entrypoint_default_config(self):
        entrypoint = _SemiconductorEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint) as mock_loader:
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime(
                    "soxl_soxx_trend_income",
                    runtime_settings=_build_runtime_settings("soxl_soxx_trend_income"),
                )

        mock_loader.assert_called_once_with("soxl_soxx_trend_income")
        self.assertIs(runtime.entrypoint, entrypoint)
        self.assertEqual(runtime.managed_symbols, ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"))

    def test_load_strategy_runtime_applies_tqqq_income_overrides_from_settings(self):
        entrypoint = _TqqqEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint):
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime(
                    "tqqq_growth_income",
                    runtime_settings=_build_runtime_settings(
                        "tqqq_growth_income",
                        income_threshold_usd=100000.0,
                        qqqi_income_ratio=0.5,
                    ),
                )

        self.assertEqual(runtime.runtime_overrides["income_threshold_usd"], 100000.0)
        self.assertEqual(runtime.runtime_overrides["qqqi_income_ratio"], 0.5)
        self.assertEqual(runtime.merged_runtime_config["income_threshold_usd"], 100000.0)
        self.assertEqual(runtime.merged_runtime_config["qqqi_income_ratio"], 0.5)

    def test_feature_snapshot_runtime_loads_snapshot_into_context(self):
        entrypoint = _TechEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                status_icon="🧲",
                required_feature_columns=frozenset({"symbol", "close", "as_of"}),
                snapshot_date_columns=("as_of",),
                require_snapshot_manifest=False,
                managed_symbols_extractor=lambda *_args, **_kwargs: ("AAPL", "MSFT", "BOXX"),
                portfolio_input_name="portfolio_snapshot",
                runtime_policy=StrategyRuntimePolicy(runtime_execution_window_trading_days=1),
            ),
            runtime_settings=_build_runtime_settings(
                "tech_communication_pullback_enhancement",
                feature_snapshot_path="gs://bucket/tech.csv",
            ),
            merged_runtime_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
            logger=lambda _message: None,
        )

        with patch.object(
            strategy_runtime_module,
            "load_feature_snapshot_guarded",
            return_value=type(
                "GuardResult",
                (),
                {
                    "frame": [
                        {"as_of": "2026-04-08", "symbol": "AAPL", "close": 100.0},
                        {"as_of": "2026-04-08", "symbol": "MSFT", "close": 200.0},
                    ],
                    "metadata": {"snapshot_guard_decision": "proceed", "snapshot_as_of": "2026-04-08"},
                },
            )(),
        ) as load_snapshot:
            result = runtime.evaluate(
                portfolio_snapshot=PortfolioSnapshot(
                    as_of=datetime.now(timezone.utc),
                    total_equity=1000.0,
                    buying_power=200.0,
                    positions=(),
                ),
                translator=lambda key, **_kwargs: key,
            )

        self.assertEqual(entrypoint.ctx.market_data["feature_snapshot"][0]["symbol"], "AAPL")
        self.assertEqual(entrypoint.ctx.portfolio.total_equity, 1000.0)
        self.assertIn("run_as_of", entrypoint.ctx.runtime_config)
        self.assertEqual(entrypoint.ctx.runtime_config["run_as_of"], entrypoint.ctx.as_of)
        self.assertEqual(entrypoint.ctx.runtime_config["runtime_execution_window_trading_days"], 1)
        self.assertEqual(load_snapshot.call_args.kwargs["run_as_of"], entrypoint.ctx.as_of)
        self.assertEqual(result.metadata["managed_symbols"], ("AAPL", "MSFT", "BOXX"))
        self.assertEqual(result.metadata["status_icon"], "🧲")

    def test_feature_snapshot_runtime_loads_russell_snapshot_into_context(self):
        entrypoint = _RussellEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                status_icon="📏",
                required_feature_columns=frozenset({"symbol", "sector", "mom_6_1", "mom_12_1", "sma200_gap", "vol_63", "maxdd_126"}),
                managed_symbols_extractor=lambda *_args, **_kwargs: ("AAPL", "MSFT", "BOXX"),
                portfolio_input_name="portfolio_snapshot",
            ),
            runtime_settings=_build_runtime_settings(
                "russell_1000_multi_factor_defensive",
                feature_snapshot_path="gs://bucket/russell.csv",
            ),
            merged_runtime_config={"safe_haven": "BOXX", "benchmark_symbol": "SPY"},
            logger=lambda _message: None,
        )

        with patch.object(
            strategy_runtime_module,
            "load_feature_snapshot_guarded",
            return_value=type(
                "GuardResult",
                (),
                {
                    "frame": [
                        {"symbol": "SPY", "sector": "Benchmark", "mom_6_1": 0.1, "mom_12_1": 0.2, "sma200_gap": 0.03, "vol_63": 0.15, "maxdd_126": -0.12},
                        {"symbol": "AAPL", "sector": "Technology", "mom_6_1": 0.3, "mom_12_1": 0.4, "sma200_gap": 0.08, "vol_63": 0.20, "maxdd_126": -0.10},
                    ],
                    "metadata": {"snapshot_guard_decision": "proceed", "snapshot_as_of": "2026-04-08"},
                },
            )(),
        ):
            result = runtime.evaluate(
                portfolio_snapshot=PortfolioSnapshot(
                    as_of=datetime.now(timezone.utc),
                    total_equity=1000.0,
                    buying_power=100.0,
                    positions=(),
                ),
                translator=lambda key, **_kwargs: key,
            )

        self.assertEqual(entrypoint.ctx.market_data["feature_snapshot"][1]["symbol"], "AAPL")
        self.assertNotIn("runtime_execution_window_trading_days", entrypoint.ctx.runtime_config)
        self.assertEqual(result.metadata["managed_symbols"], ("AAPL", "MSFT", "BOXX"))
        self.assertEqual(result.metadata["status_icon"], "📏")

    def test_feature_snapshot_runtime_keeps_hybrid_inputs_for_dynamic_mega_leveraged_pullback(self):
        entrypoint = _DynamicMegaLeveragedEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                status_icon="2x",
                required_feature_columns=frozenset({"symbol", "underlying_symbol", "sector", "candidate_rank", "product_leverage", "product_available"}),
                managed_symbols_extractor=lambda *_args, **_kwargs: ("AAPU", "BOXX"),
                portfolio_input_name="portfolio_snapshot",
            ),
            runtime_settings=_build_runtime_settings(
                "dynamic_mega_leveraged_pullback",
                feature_snapshot_path="gs://bucket/dynamic.csv",
            ),
            merged_runtime_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
            logger=lambda _message: None,
        )

        def market_history_loader(*_args, **_kwargs):
            return [1.0, 2.0, 3.0]

        portfolio = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=1000.0,
            buying_power=200.0,
            positions=(),
        )

        with patch.object(
            strategy_runtime_module,
            "load_feature_snapshot_guarded",
            return_value=type(
                "GuardResult",
                (),
                {
                    "frame": [
                        {
                            "symbol": "AAPU",
                            "underlying_symbol": "AAPL",
                            "sector": "Technology",
                            "candidate_rank": 1,
                            "product_leverage": 2.0,
                            "product_available": True,
                        }
                    ],
                    "metadata": {"snapshot_guard_decision": "proceed", "snapshot_as_of": "2026-04-08"},
                },
            )(),
        ):
            result = runtime.evaluate(
                market_history=market_history_loader,
                benchmark_history=[{"close": 1.0, "high": 1.0, "low": 1.0}],
                portfolio_snapshot=portfolio,
                translator=lambda key, **_kwargs: key,
            )

        self.assertEqual(entrypoint.ctx.market_data["feature_snapshot"][0]["symbol"], "AAPU")
        self.assertIs(entrypoint.ctx.market_data["market_history"], market_history_loader)
        self.assertEqual(entrypoint.ctx.market_data["benchmark_history"][0]["close"], 1.0)
        self.assertIs(entrypoint.ctx.portfolio, portfolio)
        self.assertEqual(result.metadata["managed_symbols"], ("AAPU", "BOXX"))
        self.assertEqual(result.metadata["status_icon"], "2x")


if __name__ == "__main__":
    unittest.main()
