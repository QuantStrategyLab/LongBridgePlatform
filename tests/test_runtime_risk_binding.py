from __future__ import annotations

import json
import os
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
from runtime_config_support import PlatformRuntimeSettings, load_platform_runtime_settings


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
    def test_loader_policy_authority_is_separate_from_runtime_verification(self):
        """A selected policy can parse successfully and still fail binding."""
        cases = (
            ("verified", None, None, "verified:runtime_risk_limits"),
            ("missing", None, None, "unavailable:runtime_risk_policy"),
            ("policy_shape", None, None, "unavailable:invalid_runtime_risk_policy"),
            ("binding_shape", None, None, "unavailable:invalid_runtime_binding"),
            ("binding", "account_hash", "fake-other-account", "unavailable:runtime_binding_mismatch"),
            ("binding", "account_scope", "FAKE-OTHER-SCOPE", "unavailable:runtime_binding_mismatch"),
            ("binding", "runtime_scope", "fake-other-service", "unavailable:runtime_binding_mismatch"),
            ("binding", "ues_revision", "fake-other-revision", "unavailable:runtime_binding_mismatch"),
            ("binding", "execution_mode", "live", "unavailable:runtime_binding_mismatch"),
            ("binding", "cash_only_execution", False, "unavailable:runtime_binding_mismatch"),
            ("binding", "reserved_cash_ratio", 0.04, "unavailable:runtime_binding_mismatch"),
            ("exit", "trend_exit_buffer", 0.03, "unavailable:runtime_binding_mismatch"),
            ("limits", "max_positions", -1, "unavailable:invalid_runtime_risk_limits"),
        )
        for kind, field, value, expected in cases:
            with self.subTest(kind=kind, field=field):
                policy = _policy(account_hash="fake-account")
                policy["binding"].update({
                    "account_scope": "FAKE-SCOPE",
                    "runtime_scope": "fake-service",
                    "ues_revision": "fake-ues-revision",
                    "execution_mode": "paper",
                })
                target = {
                    "platform_id": "longbridge",
                    "strategy_profile": "soxl_soxx_trend_income",
                    "dry_run_only": True,
                    "account_scope": "FAKE-SCOPE",
                    "service_name": "fake-service",
                    "strategy_release": {
                        "release_id": "fake-release",
                        "manifest_sha256": "a" * 64,
                        "strategy_revision": "fake-ues-revision",
                        "config_sha256": "b" * 64,
                        "risk_policy_sha256": "c" * 64,
                        "evidence_sha256": "d" * 64,
                        "plugin_bundle_sha256": "e" * 64,
                        "effective_session": "2026-01-01",
                    },
                    "runtime_risk_limits": policy,
                }
                if kind == "missing":
                    target.pop("runtime_risk_limits")
                elif kind == "policy_shape":
                    policy["fake_unknown_key"] = True
                elif kind == "binding_shape":
                    policy["binding"].pop("account_hash")
                elif kind == "binding":
                    policy["binding"][field] = value
                elif kind == "exit":
                    policy["exit_parameters"][field] = value
                elif kind == "limits":
                    policy[field] = value
                entrypoint = _SoxlEntrypoint()
                # Neither environment claims nor strategy config may supply the
                # trusted policy omitted from the selected deployment target.
                entrypoint.manifest.default_config["runtime_risk_limits"] = policy
                qsl_target = {**target, "runtime_risk_limits": {"fake_qsl_policy": True}}
                env = {
                    "QSL_RUNTIME_TARGET_JSON": json.dumps(qsl_target),
                    "RUNTIME_TARGET_JSON": json.dumps(target),
                    "RUNTIME_RISK_LIMITS": json.dumps(policy),
                    "QSL_RUNTIME_RISK_LIMITS": json.dumps(policy),
                    "ACCOUNT_PREFIX": "fake-account",
                    "ACCOUNT_REGION": "FAKE-SCOPE",
                    "LONGBRIDGE_RESERVED_CASH_RATIO": "0.03",
                }
                with patch.dict(os.environ, env, clear=True):
                    settings = load_platform_runtime_settings(project_id_resolver=lambda: "fake-project")
                    self.assertEqual(settings.trusted_runtime_risk_policy, target.get("runtime_risk_limits"))
                    self.assertTrue(settings.dry_run_only)
                    self.assertFalse(settings.runtime_target_enabled)
                    runtime = strategy_runtime_module.LoadedStrategyRuntime(
                        entrypoint=entrypoint,
                        runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
                        runtime_settings=settings,
                        merged_runtime_config=dict(entrypoint.manifest.default_config),
                    )
                    with patch.object(strategy_runtime_module, "_installed_ues_revision", return_value="fake-ues-revision"):
                        with patch("us_equity_strategies.signals.resolve_external_market_signal_inputs", return_value={}):
                            result = runtime.evaluate(
                                translator=lambda key, **_k: key,
                                benchmark_history=[{"close": 1.0}],
                                portfolio_snapshot=_snapshot(account_hash="fake-account"),
                            )
                self.assertEqual(result.metadata["runtime_risk_status"], expected)
                self.assertEqual(
                    isinstance(entrypoint.ctx.capabilities["runtime_risk_limits"], RuntimeRiskLimits),
                    kind == "verified",
                )

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

    def test_attaches_small_account_hold_policy(self):
        from quant_platform_kit.risk.contracts import SmallAccountRiskHoldPolicy

        policy = _policy()
        policy["small_account_hold"] = {
            "enabled": True,
            "hold_below_nav": 1000.0,
            "require_cash_only": True,
        }
        entrypoint = _SoxlEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_settings(policy),
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
        hold = entrypoint.ctx.capabilities["small_account_hold_policy"]
        self.assertIsInstance(hold, SmallAccountRiskHoldPolicy)
        self.assertEqual(hold.hold_below_nav, 1000.0)
        self.assertTrue(entrypoint.ctx.capabilities["cash_only_execution"])

    def test_rejects_invalid_small_account_hold(self):
        policy = _policy()
        policy["small_account_hold"] = {"enabled": True, "hold_below_nav": -1}
        entrypoint = _SoxlEntrypoint()
        runtime = strategy_runtime_module.LoadedStrategyRuntime(
            entrypoint=entrypoint,
            runtime_adapter=StrategyRuntimeAdapter(portfolio_input_name="portfolio_snapshot"),
            runtime_settings=_settings(policy),
            merged_runtime_config=dict(entrypoint.manifest.default_config),
        )
        with patch.object(strategy_runtime_module, "_installed_ues_revision", return_value="ues-revision"):
            with patch("us_equity_strategies.signals.resolve_external_market_signal_inputs", return_value={}):
                result = runtime.evaluate(
                    translator=lambda key, **_k: key,
                    benchmark_history=[{"close": 1.0}],
                    portfolio_snapshot=_snapshot(),
                )
        self.assertEqual(result.metadata["runtime_risk_status"], "unavailable:invalid_small_account_hold")
        self.assertNotIn("small_account_hold_policy", entrypoint.ctx.capabilities)

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
