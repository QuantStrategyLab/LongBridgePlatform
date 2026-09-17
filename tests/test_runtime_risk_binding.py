from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.runtime_target import build_runtime_target
from quant_platform_kit.common.strategy_contracts import (
    StrategyDecision,
    StrategyManifest,
    StrategyRuntimeAdapter,
)
from quant_platform_kit.risk.contracts import RuntimeRiskLimits
import strategy_runtime as strategy_runtime_module
from runtime_config_support import PlatformRuntimeSettings


class _SoxlEntrypoint:
    def __init__(self):
        self.manifest = StrategyManifest(
            profile="soxl_soxx_trend_income",
            domain="us_equity",
            display_name="SOXL/SOXX Trend Income",
            description="test",
            required_inputs=frozenset({"benchmark_history", "portfolio_snapshot"}),
            default_config={
                "managed_symbols": ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI"),
                "cash_reserve_ratio": 0.03,
                "trend_exit_buffer": 0.02,
                "option_overlay_enabled": False,
                "option_growth_overlay_enabled": False,
                "option_income_overlay_enabled": False,
            },
        )

    def evaluate(self, ctx):
        self.ctx = ctx
        return StrategyDecision(diagnostics={"signal_display": "hold"})


def _policy(*, account_hash: str = "SG") -> dict:
    symbols = ("SOXL", "SOXX", "BOXX", "SCHD", "DGRO", "SGOV", "SPYI", "QQQI")
    return {
        "binding": {
            "account_scope": "SG",
            "runtime_scope": "longbridge-quant-sg-service",
            "account_hash": account_hash,
            "strategy_profile": "soxl_soxx_trend_income",
            "ues_revision": "ues-revision",
            "execution_mode": "live",
            "cash_only_execution": True,
            "reserved_cash_ratio": 0.03,
            "options_enabled": False,
        },
        "allowed_symbols": list(symbols),
        "product_leverage_factors": {"SOXL": 3, **{s: 1 for s in symbols[1:]}},
        "nominal_caps": {"SOXL": 0.679, "SOXX": 0.873, **{s: 0.97 for s in symbols[2:]}},
        "total_nominal_exposure_cap": 0.97,
        "total_effective_exposure_cap": 2.328,
        "max_positions": 8,
        "exit_parameters": {"trend_exit_buffer": 0.02},
    }


def _settings(policy):
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile="soxl_soxx_trend_income",
        dry_run_only=False,
        account_scope="SG",
        service_name="longbridge-quant-sg-service",
        strategy_release={
            "release_id": "soxl-release",
            "manifest_sha256": "a" * 64,
            "strategy_revision": "ues-revision",
            "config_sha256": "b" * 64,
            "risk_policy_sha256": "c" * 64,
            "evidence_sha256": "d" * 64,
            "plugin_bundle_sha256": "e" * 64,
            "effective_session": "2026-09-17",
        },
    )
    return PlatformRuntimeSettings(
        project_id=None,
        secret_name="",
        account_prefix="SG",
        strategy_profile="soxl_soxx_trend_income",
        strategy_display_name="SOXL",
        strategy_domain="us_equity",
        account_region="SG",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        cash_only_execution=True,
        reserved_cash_ratio=0.03,
        trading_currency="USD",
        runtime_target=target,
        trusted_runtime_risk_policy=policy,
    )


def _snapshot(account_hash="SG"):
    now = datetime.now(timezone.utc)
    return PortfolioSnapshot(
        as_of=now,
        total_equity=1000.0,
        metadata={
            "account_hash": account_hash,
            "broker_capital": {
                "net_assets": 1000.0,
                "currency": "USD",
                "observed_at": now,
                "source_digest_sha256": "a" * 64,
            },
        },
    )


class LongBridgeRuntimeRiskBindingTests(unittest.TestCase):
    def test_binds_verified_limits(self):
        entrypoint = _SoxlEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_settings(_policy()),
            merged_runtime_config=dict(entrypoint.manifest.default_config),
        )
        with patch.object(strategy_runtime_module, "_installed_ues_revision", return_value="ues-revision"):
            with patch("us_equity_strategies.signals.resolve_external_market_signal_inputs", return_value={}):
                result = runtime.evaluate(
                    translator=lambda key, **_k: key,
                    benchmark_history=[{"close": 1.0}],
                    portfolio_snapshot=_snapshot(),
                )
        self.assertEqual(result.metadata["runtime_risk_status"], "verified:runtime_risk_limits")
        self.assertEqual(entrypoint.ctx.capabilities["runtime_risk_limits"].max_positions, 8)
        self.assertIsInstance(entrypoint.ctx.capabilities["runtime_risk_limits"], RuntimeRiskLimits)

    def test_rejects_wrong_account_hash(self):
        entrypoint = _SoxlEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_settings(_policy(account_hash="OTHER")),
            merged_runtime_config=dict(entrypoint.manifest.default_config),
        )
        with patch.object(strategy_runtime_module, "_installed_ues_revision", return_value="ues-revision"):
            with patch("us_equity_strategies.signals.resolve_external_market_signal_inputs", return_value={}):
                result = runtime.evaluate(
                    translator=lambda key, **_k: key,
                    benchmark_history=[{"close": 1.0}],
                    portfolio_snapshot=_snapshot(),
                )
        self.assertEqual(result.metadata["runtime_risk_status"], "unavailable:runtime_binding_mismatch")
        self.assertNotIsInstance(entrypoint.ctx.capabilities["runtime_risk_limits"], RuntimeRiskLimits)


if __name__ == "__main__":
    unittest.main()
