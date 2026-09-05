import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import strategy_runtime as strategy_runtime_module
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import (
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
            "managed_symbols": ("TQQQ", "QQQM", "BOXX", "SPYI", "QQQI"),
            "income_threshold_usd": 1_000_000_000.0,
            "qqqi_income_ratio": 0.5,
        },
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "tqqq"})


class _NasdaqSp500DcaEntrypoint:
    manifest = StrategyManifest(
        profile="nasdaq_sp500_smart_dca",
        domain="us_equity",
        display_name="Nasdaq 100 / S&P 500 DCA",
        description="test entrypoint",
        required_inputs=frozenset({"market_history", "portfolio_snapshot"}),
        default_config={
            "managed_symbols": ("QQQM", "SPLG"),
            "investment_amount_mode": "fixed",
            "smart_multiplier_enabled": False,
            "base_investment_usd": 1000.0,
        },
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "dca"})


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
        profile="russell_top50_leader_rotation",
        domain="us_equity",
        display_name="Russell Top50 Leader Rotation",
        description="test entrypoint",
        required_inputs=frozenset({"feature_snapshot"}),
        default_config={"safe_haven": "BOXX", "benchmark_symbol": "SPY"},
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "broad risk on"})


class _MegaCapTop50Entrypoint:
    manifest = StrategyManifest(
        profile="russell_top50_leader_rotation",
        domain="us_equity",
        display_name="Russell Top50 Leader Rotation",
        description="test entrypoint",
        required_inputs=frozenset({"feature_snapshot"}),
        default_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
    )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_description": "top50 balanced"})


def _build_runtime_settings(
    profile: str,
    *,
    feature_snapshot_path: str | None = None,
    income_threshold_usd: float | None = None,
    qqqi_income_ratio: float | None = None,
    income_layer_enabled: bool | None = None,
    income_layer_start_usd: float | None = None,
    income_layer_max_ratio: float | None = None,
    dca_mode: str | None = None,
    dca_base_investment_usd: float | None = None,
    ibit_zscore_exit_enabled: bool | None = None,
    ibit_zscore_exit_mode: str | None = None,
    ibit_zscore_exit_parking_symbol: str | None = None,
    ibit_zscore_exit_risk_reduced_exposure: float | None = None,
    ibit_zscore_exit_risk_off_exposure: float | None = None,
    ibit_zscore_exit_allow_outside_execution_window: bool | None = None,
    runtime_execution_window_trading_days: int | None = None,
    reserved_cash_floor_usd: float = 0.0,
    reserved_cash_ratio: float = 0.0,
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
        reserved_cash_floor_usd=reserved_cash_floor_usd,
        reserved_cash_ratio=reserved_cash_ratio,
        income_threshold_usd=income_threshold_usd,
        qqqi_income_ratio=qqqi_income_ratio,
        income_layer_enabled=income_layer_enabled,
        income_layer_start_usd=income_layer_start_usd,
        income_layer_max_ratio=income_layer_max_ratio,
        dca_mode=dca_mode,
        dca_base_investment_usd=dca_base_investment_usd,
        ibit_zscore_exit_enabled=ibit_zscore_exit_enabled,
        ibit_zscore_exit_mode=ibit_zscore_exit_mode,
        ibit_zscore_exit_parking_symbol=ibit_zscore_exit_parking_symbol,
        ibit_zscore_exit_risk_reduced_exposure=ibit_zscore_exit_risk_reduced_exposure,
        ibit_zscore_exit_risk_off_exposure=ibit_zscore_exit_risk_off_exposure,
        ibit_zscore_exit_allow_outside_execution_window=(
            ibit_zscore_exit_allow_outside_execution_window
        ),
        runtime_execution_window_trading_days=runtime_execution_window_trading_days,
        feature_snapshot_path=feature_snapshot_path,
        feature_snapshot_manifest_path=None,
        strategy_config_path=None,
        strategy_config_source=None,
    )


class StrategyRuntimeTests(unittest.TestCase):
    def _capital_runtime(self, *, currency="USD", entrypoint=None):
        entrypoint = entrypoint or _SemiconductorEntrypoint()
        settings = replace(
            _build_runtime_settings(entrypoint.manifest.profile),
            trading_currency=currency,
            runtime_target=SimpleNamespace(
                platform_id="longbridge", account_scope="HK",
                strategy_profile=entrypoint.manifest.profile,
                service_name="test-runtime", deployment_selector=None,
            ),
        )
        return strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=settings,
        )

    def _capital_snapshot(self, **overrides):
        capital = {
            "net_assets": 2000.0, "currency": "USD", "observed_at": datetime.now(timezone.utc),
            "source_digest_sha256": "a" * 64,
            **overrides,
        }
        return PortfolioSnapshot(
            as_of=datetime.now(timezone.utc), total_equity=200.0,
            buying_power=100.0, cash_balance=100.0,
            metadata={"account_hash": "HK", "broker_capital": capital},
        )

    def test_capital_context_uses_broker_denominator_and_real_qpk_gate(self):
        from quant_platform_kit.common.strategy_contracts import PositionTarget
        from quant_platform_kit.risk.gate import apply_risk_gate

        class Entrypoint(_SemiconductorEntrypoint):
            def evaluate(self, ctx):
                self.ctx = ctx
                return apply_risk_gate(
                    StrategyDecision(positions=(PositionTarget(symbol="SPY", target_value=50.0),)),
                    portfolio_snapshot=ctx.portfolio, product_leverage_factors={"SPY": 1},
                    enforce_value_target_exposure=True, **ctx.capabilities,
                )

        entrypoint = Entrypoint()
        runtime = self._capital_runtime(entrypoint=entrypoint)
        with patch.object(runtime.__class__, "_stamp_portfolio_risk_metadata", side_effect=lambda inputs: dict(inputs)):
            result = runtime.evaluate(translator=str, derived_indicators={}, portfolio_snapshot=self._capital_snapshot())
        self.assertEqual(result.decision.diagnostics["risk_gate"], "APPROVE")
        self.assertEqual(entrypoint.ctx.capabilities["capital_base"].reported_equity, 2000.0)
        self.assertEqual(entrypoint.ctx.portfolio.total_equity, 200.0)

    def test_capital_context_withholds_stale_future_wrong_currency_or_account(self):
        from quant_platform_kit.common.capital_base import validate_capital_base

        runtime = self._capital_runtime()
        self.assertFalse(runtime._build_capital_base_capabilities({}))
        self.assertFalse(runtime._build_capital_base_capabilities({"portfolio_snapshot": replace(self._capital_snapshot(), metadata={})}))
        for overrides in (
            {"currency": "HKD"}, {"net_assets": float("nan")}, {"net_assets": 0},
            {"source_digest_sha256": ""}, {"observed_at": None},
            {"observed_at": datetime.now(timezone.utc) - timedelta(seconds=301)},
            {"observed_at": datetime.now(timezone.utc) + timedelta(seconds=30)},
        ):
            with self.subTest(overrides=overrides):
                caps = runtime._build_capital_base_capabilities({"portfolio_snapshot": self._capital_snapshot(**overrides)})
                self.assertFalse(caps)
        snapshot = self._capital_snapshot()
        snapshot = replace(snapshot, metadata={**snapshot.metadata, "account_hash": "different"})
        self.assertFalse(runtime._build_capital_base_capabilities({"portfolio_snapshot": snapshot}))
        caps = runtime._build_capital_base_capabilities({"portfolio_snapshot": self._capital_snapshot()})
        self.assertTrue(validate_capital_base(caps["capital_base"], binding=caps["capital_base_binding"]).is_valid)
        for change in ({"account_scope": "other"}, {"runtime_scope": "other"}, {"strategy_scope": "other"}):
            self.assertFalse(validate_capital_base(caps["capital_base"], binding=replace(caps["capital_base_binding"], **change)).is_valid)

    def test_capital_context_supports_other_currency_and_feature_snapshot_profile(self):
        entrypoint = _TechEntrypoint()
        runtime = self._capital_runtime(currency="HKD", entrypoint=entrypoint)
        snapshot = self._capital_snapshot(currency="HKD")
        request = SimpleNamespace(
            entrypoint=entrypoint, runtime_adapter=runtime.runtime_adapter,
            as_of=datetime.now(timezone.utc),
            available_inputs={"feature_snapshot": [], "portfolio_snapshot": snapshot},
            runtime_config={},
        )
        ctx = runtime._build_feature_snapshot_context(request)
        self.assertEqual(ctx.capabilities["capital_base_binding"].target_currency, "HKD")
        self.assertEqual(ctx.capabilities["capital_base_binding"].strategy_scope, entrypoint.manifest.profile)
        self.assertEqual(ctx.capabilities["capital_base"].fx_rate_to_target, 1.0)
        self.assertIs(ctx.portfolio, snapshot)

    def test_actual_pinned_soxl_keeps_risk_rejection_after_capital_wiring(self):
        from application.longbridge_portfolio import fetch_strategy_account_state
        from application.runtime_broker_adapters import build_runtime_broker_adapters
        from us_equity_strategies import get_strategy_entrypoint

        runtime = self._capital_runtime(entrypoint=get_strategy_entrypoint("soxl_soxx_trend_income"))
        indicators = {
            "soxl": {"price": 80.0, "ma_trend": 75.0},
            "soxx": {
                "price": 80.0, "ma_trend": 75.0, "realized_volatility_10": 0.20,
                "realized_volatility_10_dynamic_threshold": 0.50,
                "realized_volatility_10_dynamic_sample_count": 252.0,
            },
        }
        trade = SimpleNamespace(
            account_balance=lambda: [SimpleNamespace(
                currency="USD", net_assets=2000.0,
                cash_infos=[SimpleNamespace(currency="USD", available_cash=200.0, frozen_cash=1800.0)],
            )],
            stock_positions=lambda: SimpleNamespace(channels=[]),
        )
        adapters = build_runtime_broker_adapters(
            strategy_symbols=("SOXL", "SOXX", "BOXX"), account_hash="HK",
            fetch_last_price_fn=lambda *_args: None,
            fetch_strategy_account_state_fn=lambda quote, broker: fetch_strategy_account_state(
                quote, broker, ["SOXL", "SOXX", "BOXX"],
            ),
            submit_order_fn=lambda *_args: self.fail("synthetic risk test must not submit"),
        )
        snapshot = adapters.build_portfolio_port(None, trade).get_portfolio_snapshot()
        with (
            patch.object(runtime.__class__, "_stamp_portfolio_risk_metadata", side_effect=lambda inputs: dict(inputs)),
            patch("us_equity_strategies.entrypoints.record_strategy_decision"),
        ):
            missing = runtime.evaluate(
                translator=lambda key, **kwargs: key, derived_indicators=indicators,
                portfolio_snapshot=replace(snapshot, metadata={}),
            ).decision
            connected = runtime.evaluate(
                translator=lambda key, **kwargs: key, derived_indicators=indicators, portfolio_snapshot=snapshot,
            ).decision
        self.assertEqual(missing.risk_flags, ("rejected:capital_base",))
        self.assertNotIn("rejected:capital_base", connected.risk_flags)
        self.assertEqual(connected.diagnostics["value_target_exposure_policy"], "enforced")
        self.assertEqual(connected.diagnostics["risk_gate"], "REJECT")
        self.assertEqual(connected.positions, ())

    def test_market_history_runtime_loads_loader_into_context(self):
        class _FixedDatetime:
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 1, tzinfo=tz or timezone.utc)

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
            runtime_adapter=StrategyRuntimeAdapter(
                portfolio_input_name="portfolio_snapshot",
                runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=1),
            ),
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
        with patch.object(strategy_runtime_module, "datetime", _FixedDatetime):
            result = runtime.evaluate(
                market_history=market_history_loader,
                portfolio_snapshot=snapshot,
                translator=lambda key, **_kwargs: key,
            )

        self.assertIs(entrypoint.ctx.market_data["market_history"], market_history_loader)
        self.assertIs(entrypoint.ctx.portfolio, snapshot)
        self.assertEqual(entrypoint.ctx.runtime_config["signal_effective_after_trading_days"], 1)
        self.assertEqual(result.metadata["strategy_profile"], "global_etf_rotation")
        self.assertEqual(result.metadata["signal_date"], "2026-04-01")
        self.assertEqual(result.metadata["effective_date"], "2026-04-02")
        self.assertEqual(result.metadata["execution_timing_contract"], "next_trading_day")

    def test_runtime_exposes_managed_symbols_and_injects_translator(self):
        class _FixedDatetime:
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 4, 1, tzinfo=tz or timezone.utc)

        entrypoint = _SemiconductorEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                portfolio_input_name="portfolio_snapshot",
                runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=1),
            ),
            runtime_settings=_build_runtime_settings("soxl_soxx_trend_income"),
            merged_runtime_config={"managed_symbols": ("SOXL", "SOXX", "BOXX", "QQQI", "SPYI")},
        )

        with patch.object(strategy_runtime_module, "datetime", _FixedDatetime):
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
        self.assertEqual(entrypoint.ctx.runtime_config["signal_effective_after_trading_days"], 1)
        self.assertEqual(result.metadata["strategy_profile"], "soxl_soxx_trend_income")
        self.assertEqual(result.metadata["strategy_display_name"], "SOXL/SOXX Semiconductor Trend Income")
        self.assertEqual(result.metadata["signal_date"], "2026-04-01")
        self.assertEqual(result.metadata["effective_date"], "2026-04-02")
        self.assertEqual(result.metadata["execution_timing_contract"], "next_trading_day")

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
                        income_layer_enabled=False,
                        income_layer_start_usd=250000.0,
                        income_layer_max_ratio=0.25,
                    ),
                )

        self.assertEqual(runtime.runtime_overrides["income_threshold_usd"], 100000.0)
        self.assertEqual(runtime.runtime_overrides["qqqi_income_ratio"], 0.5)
        self.assertFalse(runtime.runtime_overrides["income_layer_enabled"])
        self.assertEqual(runtime.runtime_overrides["income_layer_start_usd"], 250000.0)
        self.assertEqual(runtime.runtime_overrides["income_layer_max_ratio"], 0.25)
        self.assertEqual(runtime.merged_runtime_config["income_threshold_usd"], 100000.0)
        self.assertEqual(runtime.merged_runtime_config["qqqi_income_ratio"], 0.5)
        self.assertFalse(runtime.merged_runtime_config["income_layer_enabled"])
        self.assertEqual(runtime.merged_runtime_config["income_layer_start_usd"], 250000.0)
        self.assertEqual(runtime.merged_runtime_config["income_layer_max_ratio"], 0.25)

    def test_load_strategy_runtime_applies_dca_overrides_from_settings(self):
        entrypoint = _NasdaqSp500DcaEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint):
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime(
                    "nasdaq_sp500_smart_dca",
                    runtime_settings=_build_runtime_settings(
                        "nasdaq_sp500_smart_dca",
                        dca_mode="smart",
                        dca_base_investment_usd=500.0,
                    ),
                )

        self.assertEqual(runtime.runtime_overrides["investment_amount_mode"], "fixed")
        self.assertTrue(runtime.runtime_overrides["smart_multiplier_enabled"])
        self.assertEqual(runtime.runtime_overrides["base_investment_usd"], 500.0)
        self.assertEqual(runtime.merged_runtime_config["investment_amount_mode"], "fixed")
        self.assertTrue(runtime.merged_runtime_config["smart_multiplier_enabled"])
        self.assertEqual(runtime.merged_runtime_config["base_investment_usd"], 500.0)

    def test_ibit_zscore_exit_overrides_apply_to_runtime_config(self):
        settings = _build_runtime_settings(
            "ibit_smart_dca",
            ibit_zscore_exit_enabled=True,
            ibit_zscore_exit_mode="live",
            ibit_zscore_exit_parking_symbol="BOXX",
            ibit_zscore_exit_risk_reduced_exposure=0.5,
            ibit_zscore_exit_risk_off_exposure=0.25,
            ibit_zscore_exit_allow_outside_execution_window=True,
        )

        self.assertEqual(
            strategy_runtime_module._build_runtime_overrides("ibit_smart_dca", settings),
            {
                "ibit_zscore_exit_enabled": True,
                "ibit_zscore_exit_mode": "live",
                "ibit_zscore_exit_parking_symbol": "BOXX",
                "ibit_zscore_exit_risk_reduced_exposure": 0.5,
                "ibit_zscore_exit_risk_off_exposure": 0.25,
                "ibit_zscore_exit_allow_outside_execution_window": True,
            },
        )

    def test_load_strategy_runtime_applies_reserved_cash_policy_overrides_from_settings(self):
        entrypoint = _SemiconductorEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint):
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime(
                    "soxl_soxx_trend_income",
                    runtime_settings=_build_runtime_settings(
                        "soxl_soxx_trend_income",
                        reserved_cash_floor_usd=150.0,
                        reserved_cash_ratio=0.03,
                    ),
                )

        self.assertEqual(runtime.runtime_overrides["reserved_cash_floor_usd"], 150.0)
        self.assertEqual(runtime.runtime_overrides["reserved_cash_ratio"], 0.03)
        self.assertEqual(runtime.merged_runtime_config["reserved_cash_floor_usd"], 150.0)
        self.assertEqual(runtime.merged_runtime_config["reserved_cash_ratio"], 0.03)

    def test_load_strategy_runtime_applies_tech_execution_window_overrides_from_settings(self):
        entrypoint = _TechEntrypoint()

        with patch.object(strategy_runtime_module, "load_strategy_entrypoint_for_profile", return_value=entrypoint):
            with patch.object(
                strategy_runtime_module,
                "load_strategy_runtime_adapter_for_profile",
                return_value=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            ):
                runtime = strategy_runtime_module.load_strategy_runtime(
                    "tech_communication_pullback_enhancement",
                    runtime_settings=_build_runtime_settings(
                        "tech_communication_pullback_enhancement",
                        runtime_execution_window_trading_days=31,
                    ),
                )

        self.assertEqual(runtime.merged_runtime_config["runtime_execution_window_trading_days"], 31)

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
        self.assertIsNone(entrypoint.ctx.runtime_config["run_as_of"].tzinfo)
        self.assertEqual(entrypoint.ctx.runtime_config["run_as_of"].date(), entrypoint.ctx.as_of.date())
        self.assertEqual(entrypoint.ctx.runtime_config["runtime_execution_window_trading_days"], 1)
        self.assertIn(load_snapshot.call_args.kwargs["run_as_of"].tzinfo, (None, timezone.utc))
        self.assertEqual(load_snapshot.call_args.kwargs["run_as_of"].date(), entrypoint.ctx.as_of.date())
        self.assertEqual(result.metadata["managed_symbols"], ("AAPL", "MSFT", "BOXX"))
        self.assertEqual(result.metadata["status_icon"], "🧲")

    def test_feature_snapshot_runtime_loads_russell_top50_snapshot_into_context(self):
        entrypoint = _RussellEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                status_icon="👑",
                required_feature_columns=frozenset({"symbol", "sector", "mom_6_1", "mom_12_1", "sma200_gap", "vol_63", "maxdd_126"}),
                managed_symbols_extractor=lambda *_args, **_kwargs: ("AAPL", "MSFT", "BOXX"),
                portfolio_input_name="portfolio_snapshot",
            ),
            runtime_settings=_build_runtime_settings(
                "russell_top50_leader_rotation",
                feature_snapshot_path="gs://bucket/russell-top50.csv",
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
        self.assertEqual(result.metadata["status_icon"], "👑")

    def test_feature_snapshot_runtime_loads_mega_cap_top50_snapshot_into_context(self):
        entrypoint = _MegaCapTop50Entrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                status_icon="👑",
                required_feature_columns=frozenset({"symbol", "sector", "close"}),
                managed_symbols_extractor=lambda *_args, **_kwargs: ("NVDA", "META", "BOXX"),
                portfolio_input_name="portfolio_snapshot",
            ),
            runtime_settings=_build_runtime_settings(
                "russell_top50_leader_rotation",
                feature_snapshot_path="gs://bucket/top50.csv",
            ),
            merged_runtime_config={"safe_haven": "BOXX", "benchmark_symbol": "QQQ"},
            logger=lambda _message: None,
        )

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
                            "symbol": "NVDA",
                            "sector": "Technology",
                            "close": 880.0,
                        }
                    ],
                    "metadata": {"snapshot_guard_decision": "proceed", "snapshot_as_of": "2026-04-08"},
                },
            )(),
        ):
            result = runtime.evaluate(
                portfolio_snapshot=portfolio,
                translator=lambda key, **_kwargs: key,
            )

        self.assertEqual(entrypoint.ctx.market_data["feature_snapshot"][0]["symbol"], "NVDA")
        self.assertEqual(entrypoint.ctx.portfolio.total_equity, portfolio.total_equity)
        self.assertEqual(entrypoint.ctx.portfolio.buying_power, portfolio.buying_power)
        # No live performance history was supplied in this fixture. The shared
        # lifecycle helper must not fabricate a zero-loss record from missing
        # evidence; a later runtime gate can classify unavailable evidence.
        self.assertNotIn("consecutive_losses", entrypoint.ctx.portfolio.metadata)
        self.assertEqual(result.metadata["managed_symbols"], ("NVDA", "META", "BOXX"))
        self.assertEqual(result.metadata["status_icon"], "👑")

    def test_evaluate_stamps_consecutive_losses_on_portfolio_snapshot(self):
        from quant_platform_kit.common.models import PortfolioSnapshot

        class _GlobalEntrypoint:
            def __init__(self):
                self.manifest = StrategyManifest(
                    profile="global_etf_rotation",
                    domain="us_equity",
                    display_name="Global ETF Rotation",
                    description="test",
                    required_inputs=frozenset({"market_history", "portfolio_snapshot"}),
                )
                self.ctx = None

            def evaluate(self, ctx):
                self.ctx = ctx
                return StrategyDecision()

        entrypoint = _GlobalEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(
                portfolio_input_name="portfolio_snapshot",
                runtime_policy=StrategyRuntimePolicy(signal_effective_after_trading_days=0),
            ),
            runtime_settings=_build_runtime_settings("global_etf_rotation"),
            logger=lambda _message: None,
        )
        snapshot = PortfolioSnapshot(
            as_of=datetime.now(timezone.utc),
            total_equity=10_000.0,
            positions=(),
            metadata={},
        )
        stamped = PortfolioSnapshot(
            as_of=snapshot.as_of,
            total_equity=snapshot.total_equity,
            positions=(),
            metadata={"consecutive_losses": 4},
        )

        with patch(
            "quant_platform_kit.strategy_lifecycle.live_equity.stamp_consecutive_losses_on_snapshot",
            return_value=stamped,
        ) as stamp:
            result = runtime.evaluate(
                market_history=lambda *_args, **_kwargs: [1.0, 2.0],
                portfolio_snapshot=snapshot,
                translator=lambda key, **_kwargs: key,
            )

        stamp.assert_called_once()
        self.assertIs(entrypoint.ctx.portfolio, stamped)
        self.assertEqual(entrypoint.ctx.portfolio.metadata["consecutive_losses"], 4)
        self.assertEqual(result.metadata["strategy_profile"], "global_etf_rotation")


if __name__ == "__main__":
    unittest.main()
