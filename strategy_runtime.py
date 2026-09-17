from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from quant_platform_kit.common.feature_snapshot import load_feature_snapshot_guarded
from quant_platform_kit.common.capital_base import (
    CapitalBaseBinding,
    CapitalScope,
    CapitalValuationBasis,
    build_capital_base_snapshot,
    validate_capital_base,
)
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.feature_snapshot_runtime import (
    FeatureSnapshotRuntimeSettings,
    evaluate_feature_snapshot_strategy,
)
from quant_platform_kit.common.strategy_contracts import (
    StrategyDecision,
    StrategyEntrypoint,
    StrategyRuntimeAdapter,
    apply_runtime_policy_to_runtime_config,
    build_execution_timing_metadata,
    build_strategy_context_from_available_inputs,
)
from quant_platform_kit.risk.contracts import RuntimeRiskLimits, SmallAccountRiskHoldPolicy
from runtime_config_support import PlatformRuntimeSettings

from strategy_loader import (
    load_strategy_entrypoint_for_profile,
    load_strategy_runtime_adapter_for_profile,
)


_FEATURE_SNAPSHOT_INPUT = "feature_snapshot"
_SOXL_PROFILE = "soxl_soxx_trend_income"


def _parse_small_account_hold_policy(raw: Any) -> SmallAccountRiskHoldPolicy | None:
    """Parse optional deployment hold policy; invalid shapes return None."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return None
    try:
        return SmallAccountRiskHoldPolicy(
            enabled=raw["enabled"],
            hold_below_nav=raw["hold_below_nav"],
            require_cash_only=raw.get("require_cash_only", True),
        )
    except (KeyError, TypeError, ValueError):
        return None

def _installed_ues_revision() -> str | None:
    """Read the VCS revision of the installed UES distribution."""
    try:
        distribution = importlib_metadata.distribution("us-equity-strategies")
        raw_direct_url = distribution.read_text("direct_url.json")
        if not raw_direct_url:
            return None
        payload = json.loads(raw_direct_url)
        revision = payload.get("vcs_info", {}).get("commit_id")
    except (ImportError, OSError, TypeError, ValueError, AttributeError):
        return None
    if not isinstance(revision, str) or not revision.strip():
        return None
    return revision.strip()


DCA_PROFILES = frozenset({"nasdaq_sp500_smart_dca", "ibit_smart_dca"})
IBIT_ZSCORE_EXIT_PROFILE = "ibit_smart_dca"


@dataclass(frozen=True)
class StrategyEvaluationResult:
    decision: StrategyDecision
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedStrategyRuntime:
    entrypoint: StrategyEntrypoint
    runtime_adapter: StrategyRuntimeAdapter
    runtime_settings: PlatformRuntimeSettings
    execution_entrypoint: StrategyEntrypoint | None = None
    execution_materials: Mapping[str, Any] = field(default_factory=dict)
    runtime_overrides: Mapping[str, Any] = field(default_factory=dict)
    runtime_config: Mapping[str, Any] = field(default_factory=dict)
    merged_runtime_config: Mapping[str, Any] = field(default_factory=dict)
    logger: Callable[[str], None] = print

    @property
    def profile(self) -> str:
        return self.entrypoint.manifest.profile

    @property
    def display_name(self) -> str:
        return str(self.entrypoint.manifest.display_name)

    @property
    def managed_symbols(self) -> tuple[str, ...]:
        configured = self.merged_runtime_config.get("managed_symbols", ())
        return tuple(str(symbol) for symbol in configured)

    @property
    def uses_evidence_execution(self) -> bool:
        return self.execution_entrypoint is not None and bool(self.execution_materials)

    def _stamp_portfolio_risk_metadata(self, available_inputs: Mapping[str, Any]) -> dict[str, Any]:
        resolved = dict(available_inputs)
        snapshot = resolved.get("portfolio_snapshot")
        if snapshot is None:
            return resolved
        from quant_platform_kit.strategy_lifecycle.live_equity import stamp_consecutive_losses_on_snapshot

        resolved["portfolio_snapshot"] = stamp_consecutive_losses_on_snapshot(
            snapshot,
            strategy_profile=self.profile,
            logger=self.logger,
        )
        return resolved

    def _build_capital_base_capabilities(self, available_inputs: Mapping[str, Any]) -> dict[str, Any]:
        capabilities = {
            key: value
            for key, value in self.execution_materials.items()
            if key not in {"capital_base", "capital_base_binding"}
        }
        snapshot = available_inputs.get("portfolio_snapshot")
        target = self.runtime_settings.runtime_target
        metadata = getattr(snapshot, "metadata", None)
        if target is None or not isinstance(metadata, Mapping):
            return capabilities
        source = metadata.get("broker_capital")
        if not isinstance(source, Mapping):
            return capabilities
        settings = self.runtime_settings
        if (
            target.platform_id != "longbridge"
            or target.account_scope != settings.account_region
            or target.strategy_profile != self.profile
            or metadata.get("account_hash") != (settings.account_prefix or settings.account_region)
            or source.get("currency") != settings.trading_currency
        ):
            return capabilities
        try:
            binding = CapitalBaseBinding(
                account_scope=target.account_scope,
                runtime_scope=target.service_name or target.deployment_selector,
                strategy_scope=self.profile,
                target_currency=settings.trading_currency,
                capital_scope=CapitalScope.ACCOUNT,
                valuation_basis=CapitalValuationBasis.BROKER_ACCOUNT_NET_LIQUIDATION,
            )
            # Keep the managed portfolio/sizing equity unchanged. Only the risk
            # denominator uses the broker's independently reported net assets.
            capital = build_capital_base_snapshot(
                PortfolioSnapshot(as_of=source.get("observed_at"), total_equity=source.get("net_assets")),
                account_scope=binding.account_scope,
                runtime_scope=binding.runtime_scope,
                strategy_scope=binding.strategy_scope,
                reported_currency=source.get("currency"),
                target_currency=binding.target_currency,
                fx_rate_to_target=1.0,
                source_digest_sha256=source.get("source_digest_sha256"),
                capital_scope=binding.capital_scope,
                valuation_basis=binding.valuation_basis,
            )
            if not validate_capital_base(capital, binding=binding).is_valid:
                return capabilities
        except (TypeError, ValueError, AttributeError):
            return capabilities
        capabilities.update({"capital_base": capital, "capital_base_binding": binding})
        return capabilities

    def _build_runtime_risk_capabilities(
        self,
        available_inputs: Mapping[str, Any],
        capabilities: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str]:
        """Bind explicit limits to the deployed account and installed UES."""
        if self.profile != _SOXL_PROFILE:
            return dict(capabilities), "unavailable:profile_not_supported"
        policy = self.runtime_settings.trusted_runtime_risk_policy
        runtime_target = self.runtime_settings.runtime_target
        snapshot = available_inputs.get("portfolio_snapshot")
        binding = capabilities.get("capital_base_binding")
        if not isinstance(policy, Mapping):
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:runtime_risk_policy"
        if runtime_target is None or snapshot is None or binding is None:
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:runtime_binding"
        expected_policy_keys = {
            "binding",
            "allowed_symbols",
            "product_leverage_factors",
            "nominal_caps",
            "total_nominal_exposure_cap",
            "total_effective_exposure_cap",
            "max_positions",
            "exit_parameters",
        }
        optional_policy_keys = {"small_account_hold", "max_daily_loss_usd"}
        policy_keys = set(policy)
        if (
            not expected_policy_keys.issubset(policy_keys)
            or (policy_keys - expected_policy_keys - optional_policy_keys)
            or not isinstance(policy.get("binding"), Mapping)
        ):
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:invalid_runtime_risk_policy"

        target_release = runtime_target.strategy_release
        policy_binding = policy["binding"]
        expected_binding_keys = {
            "account_scope",
            "runtime_scope",
            "account_hash",
            "strategy_profile",
            "ues_revision",
            "execution_mode",
            "cash_only_execution",
            "reserved_cash_ratio",
            "options_enabled",
        }
        if set(policy_binding) != expected_binding_keys:
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:invalid_runtime_binding"
        metadata = getattr(snapshot, "metadata", {})
        account_scope = str(runtime_target.account_scope or "").strip()
        runtime_scope = str(runtime_target.service_name or runtime_target.deployment_selector or "").strip()
        actual_account_hash = str(metadata.get("account_hash") or "").strip() if isinstance(metadata, Mapping) else ""
        actual_ues_revision = _installed_ues_revision()
        actual_exit_buffer = self.merged_runtime_config.get("trend_exit_buffer")
        if (
            not account_scope
            or not runtime_scope
            or not actual_account_hash
            or target_release is None
            or str(policy_binding["account_scope"]).strip() != account_scope
            or str(policy_binding["runtime_scope"]).strip() != runtime_scope
            or str(policy_binding["account_hash"]).strip() != actual_account_hash
            or str(policy_binding["strategy_profile"]).strip() != self.profile
            or str(policy_binding["ues_revision"]).strip() != str(target_release.strategy_revision).strip()
            or actual_ues_revision is None
            or actual_ues_revision != str(policy_binding["ues_revision"]).strip()
            or str(policy_binding["execution_mode"]).strip().lower() != runtime_target.execution_mode
            or policy_binding["cash_only_execution"] is not True
            or self.runtime_settings.cash_only_execution is not True
            or policy_binding["reserved_cash_ratio"] != self.merged_runtime_config.get("cash_reserve_ratio")
            or policy_binding["reserved_cash_ratio"] != self.runtime_settings.reserved_cash_ratio
            or policy_binding["reserved_cash_ratio"] != 0.03
            or policy_binding["options_enabled"] is not False
            or any(
                self.merged_runtime_config.get(key) is not False
                for key in (
                    "option_overlay_enabled",
                    "option_growth_overlay_enabled",
                    "option_income_overlay_enabled",
                )
            )
            or not isinstance(policy.get("exit_parameters"), Mapping)
            or actual_exit_buffer is None
            or actual_exit_buffer != 0.02
            or dict(policy["exit_parameters"]) != {"trend_exit_buffer": 0.02}
            or dict(policy["exit_parameters"]) != {"trend_exit_buffer": actual_exit_buffer}
        ):
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:runtime_binding_mismatch"
        try:
            daily_loss_kwargs: dict[str, Any] = {}
            if "max_daily_loss_usd" in policy:
                daily_loss_kwargs["max_daily_loss_usd"] = policy.get("max_daily_loss_usd")
            limits = RuntimeRiskLimits(
                allowed_symbols=tuple(policy["allowed_symbols"]),
                product_leverage_factors=policy["product_leverage_factors"],
                nominal_caps=policy["nominal_caps"],
                total_nominal_exposure_cap=policy["total_nominal_exposure_cap"],
                total_effective_exposure_cap=policy["total_effective_exposure_cap"],
                max_positions=policy["max_positions"],
                **daily_loss_kwargs,
            )
        except (TypeError, ValueError):
            return {**capabilities, "runtime_risk_limits": object()}, "unavailable:invalid_runtime_risk_limits"
        capability_payload: dict[str, Any] = {
            **capabilities,
            "runtime_risk_limits": limits,
            "cash_only_execution": bool(self.runtime_settings.cash_only_execution),
        }
        hold_policy = _parse_small_account_hold_policy(policy.get("small_account_hold"))
        if isinstance(policy.get("small_account_hold"), Mapping) and hold_policy is None:
            return {
                **capabilities,
                "runtime_risk_limits": object(),
            }, "unavailable:invalid_small_account_hold"
        if hold_policy is not None:
            if hold_policy.require_cash_only and self.runtime_settings.cash_only_execution is not True:
                return {
                    **capabilities,
                    "runtime_risk_limits": object(),
                }, "unavailable:small_account_hold_cash_only"
            capability_payload["small_account_hold_policy"] = hold_policy
        return capability_payload, "verified:runtime_risk_limits"

    def _build_feature_snapshot_context(self, request):
        return build_strategy_context_from_available_inputs(
            entrypoint=request.entrypoint,
            runtime_adapter=request.runtime_adapter,
            as_of=request.as_of,
            available_inputs=request.available_inputs,
            runtime_config=request.runtime_config,
            capabilities=self._build_capital_base_capabilities(request.available_inputs),
        )

    def evaluate(
        self,
        *,
        translator: Callable[[str], str],
        signal_text_fn: Callable[[str], str] | None = None,
        **available_inputs,
    ) -> StrategyEvaluationResult:
        runtime_config = dict(self.runtime_overrides)
        runtime_config.setdefault("translator", translator)
        if signal_text_fn is not None:
            runtime_config.setdefault("signal_text_fn", signal_text_fn)
        apply_runtime_policy_to_runtime_config(runtime_config, self.runtime_adapter)

        active_entrypoint = self.execution_entrypoint or self.entrypoint
        if _FEATURE_SNAPSHOT_INPUT in frozenset(active_entrypoint.manifest.required_inputs):
            return self._evaluate_feature_snapshot_strategy(
                runtime_config=runtime_config,
                available_inputs=self._stamp_portfolio_risk_metadata(available_inputs),
            )

        as_of = datetime.now(timezone.utc)
        resolved_available_inputs = self._stamp_portfolio_risk_metadata(available_inputs)
        from us_equity_strategies.signals import resolve_external_market_signal_inputs
        resolved_available_inputs.update(
            resolve_external_market_signal_inputs(
                strategy_profile=self.profile,
                available_inputs=self.runtime_adapter.available_inputs or self.entrypoint.manifest.required_inputs,
                runtime_settings=self.runtime_settings,
                as_of=as_of,
                logger=self.logger,
            )
        )
        capabilities = self._build_capital_base_capabilities(resolved_available_inputs)
        capabilities, runtime_risk_status = self._build_runtime_risk_capabilities(
            resolved_available_inputs,
            capabilities,
        )
        ctx = build_strategy_context_from_available_inputs(
            entrypoint=active_entrypoint,
            runtime_adapter=self.runtime_adapter,
            as_of=as_of,
            available_inputs=resolved_available_inputs,
            runtime_config=runtime_config,
            capabilities=capabilities,
        )
        decision = active_entrypoint.evaluate(ctx)
        return StrategyEvaluationResult(
            decision=decision,
            metadata={
                "strategy_profile": self.profile,
                "strategy_display_name": self.display_name,
                "runtime_risk_status": runtime_risk_status,
                **build_execution_timing_metadata(
                    signal_date=as_of,
                    signal_effective_after_trading_days=(
                        self.runtime_adapter.runtime_policy.signal_effective_after_trading_days
                    ),
                ),
            },
        )

    def _evaluate_feature_snapshot_strategy(
        self,
        *,
        runtime_config: Mapping[str, Any],
        available_inputs: Mapping[str, Any],
    ) -> StrategyEvaluationResult:
        runtime_config = dict(runtime_config)
        runtime_config.setdefault("run_as_of", datetime.now(timezone.utc).replace(tzinfo=None))
        result = evaluate_feature_snapshot_strategy(
            entrypoint=self.entrypoint,
            runtime_adapter=self.runtime_adapter,
            runtime_settings=FeatureSnapshotRuntimeSettings(
                feature_snapshot_path=self.runtime_settings.feature_snapshot_path,
                feature_snapshot_manifest_path=self.runtime_settings.feature_snapshot_manifest_path,
                feature_snapshot_fallback_mode=self.runtime_settings.feature_snapshot_fallback_mode,
                feature_snapshot_fallback_cache_dir=self.runtime_settings.feature_snapshot_fallback_cache_dir,
                feature_snapshot_fallback_max_stale_days=(
                    self.runtime_settings.feature_snapshot_fallback_max_stale_days
                ),
                strategy_config_path=self.runtime_settings.strategy_config_path,
                strategy_config_source=self.runtime_settings.strategy_config_source,
                dry_run_only=self.runtime_settings.dry_run_only,
            ),
            runtime_config=runtime_config,
            merged_runtime_config=self.merged_runtime_config,
            available_inputs=available_inputs,
            base_managed_symbols=self.managed_symbols,
            include_strategy_display_name=True,
            set_run_as_of=True,
            snapshot_loader=load_feature_snapshot_guarded,
            context_builder=self._build_feature_snapshot_context,
        )
        return StrategyEvaluationResult(
            decision=result.decision,
            metadata=result.metadata,
        )

    def load_runtime_parameters(self) -> dict[str, Any]:
        runtime_loader = self.runtime_adapter.runtime_parameter_loader
        if not callable(runtime_loader):
            return {}
        return dict(
            runtime_loader(
                config_path=self.runtime_settings.strategy_config_path,
                logger=self.logger,
            )
            or {}
        )


def _default_runtime_settings(profile: str, display_name: str) -> PlatformRuntimeSettings:
    return PlatformRuntimeSettings(
        project_id=None,
        secret_name="",
        account_prefix="DEFAULT",
        strategy_profile=profile,
        strategy_display_name=display_name,
        strategy_domain="us_equity",
        account_region="DEFAULT",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        dry_run_only=False,
        debug_position_snapshot=False,
        feature_snapshot_path=None,
        feature_snapshot_manifest_path=None,
        strategy_config_path=None,
        strategy_config_source=None,
    )


def _build_runtime_overrides(profile: str, runtime_settings: PlatformRuntimeSettings) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    reserved_cash_floor_usd = getattr(runtime_settings, "reserved_cash_floor_usd", 0.0)
    reserved_cash_ratio = getattr(runtime_settings, "reserved_cash_ratio", None)
    if float(reserved_cash_floor_usd or 0.0) > 0.0:
        overrides["reserved_cash_floor_usd"] = float(reserved_cash_floor_usd)
    if reserved_cash_ratio is not None and float(reserved_cash_ratio or 0.0) > 0.0:
        overrides["reserved_cash_ratio"] = float(reserved_cash_ratio)
        overrides["cash_reserve_ratio"] = float(reserved_cash_ratio)
    if profile == _SOXL_PROFILE and bool(getattr(runtime_settings, "cash_only_execution", True)):
        overrides["option_overlay_enabled"] = False
        overrides["option_growth_overlay_enabled"] = False
        overrides["option_income_overlay_enabled"] = False
    income_layer_enabled = getattr(runtime_settings, "income_layer_enabled", None)
    income_layer_start_usd = getattr(runtime_settings, "income_layer_start_usd", None)
    income_layer_max_ratio = getattr(runtime_settings, "income_layer_max_ratio", None)
    if income_layer_enabled is not None:
        overrides["income_layer_enabled"] = income_layer_enabled
    if income_layer_start_usd is not None:
        overrides["income_layer_start_usd"] = income_layer_start_usd
    if income_layer_max_ratio is not None:
        overrides["income_layer_max_ratio"] = income_layer_max_ratio
    _apply_dca_runtime_overrides(profile, runtime_settings, overrides)
    _apply_ibit_zscore_exit_runtime_overrides(profile, runtime_settings, overrides)
    if profile == "tqqq_growth_income":
        if runtime_settings.income_threshold_usd is not None:
            overrides["income_threshold_usd"] = runtime_settings.income_threshold_usd
        if runtime_settings.qqqi_income_ratio is not None:
            overrides["qqqi_income_ratio"] = runtime_settings.qqqi_income_ratio
    if profile == "tech_communication_pullback_enhancement":
        if runtime_settings.runtime_execution_window_trading_days is not None:
            overrides["runtime_execution_window_trading_days"] = (
                runtime_settings.runtime_execution_window_trading_days
            )
    return overrides


def _apply_dca_runtime_overrides(
    profile: str,
    runtime_settings: PlatformRuntimeSettings,
    overrides: dict[str, Any],
) -> None:
    if profile not in DCA_PROFILES:
        return
    dca_mode = getattr(runtime_settings, "dca_mode", None)
    dca_base_investment_usd = getattr(runtime_settings, "dca_base_investment_usd", None)
    if dca_mode is not None:
        overrides["investment_amount_mode"] = "fixed"
        overrides["smart_multiplier_enabled"] = dca_mode == "smart"
    if dca_base_investment_usd is not None:
        overrides["base_investment_usd"] = dca_base_investment_usd


def _apply_ibit_zscore_exit_runtime_overrides(
    profile: str,
    runtime_settings: PlatformRuntimeSettings,
    overrides: dict[str, Any],
) -> None:
    if profile != IBIT_ZSCORE_EXIT_PROFILE:
        return
    for setting_name, override_name in (
        ("ibit_zscore_exit_enabled", "ibit_zscore_exit_enabled"),
        ("ibit_zscore_exit_mode", "ibit_zscore_exit_mode"),
        ("ibit_zscore_exit_parking_symbol", "ibit_zscore_exit_parking_symbol"),
        ("ibit_zscore_exit_risk_reduced_exposure", "ibit_zscore_exit_risk_reduced_exposure"),
        ("ibit_zscore_exit_risk_off_exposure", "ibit_zscore_exit_risk_off_exposure"),
        (
            "ibit_zscore_exit_allow_outside_execution_window",
            "ibit_zscore_exit_allow_outside_execution_window",
        ),
    ):
        value = getattr(runtime_settings, setting_name, None)
        if value is not None:
            overrides[override_name] = value


def load_strategy_runtime(
    raw_profile: str | None,
    *,
    runtime_settings: PlatformRuntimeSettings | None = None,
    runtime_overrides: Mapping[str, Any] | None = None,
    execution_entrypoint: StrategyEntrypoint | None = None,
    execution_materials: Mapping[str, Any] | None = None,
    logger: Callable[[str], None] = print,
) -> LoadedStrategyRuntime:
    entrypoint = load_strategy_entrypoint_for_profile(raw_profile)
    runtime_adapter = load_strategy_runtime_adapter_for_profile(raw_profile)
    resolved_runtime_settings = runtime_settings or _default_runtime_settings(
        entrypoint.manifest.profile,
        entrypoint.manifest.display_name,
    )
    resolved_execution_materials = dict(execution_materials or {})
    if not resolved_execution_materials and entrypoint.manifest.profile == "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve":
        try:
            from application.v7_paper_application import load_v7_paper_application_binding

            binding = load_v7_paper_application_binding(os.environ)
            if binding is not None:
                resolved_execution_materials = dict(binding.get("execution_materials") or {})
        except (ImportError, ValueError):
            resolved_execution_materials = {}
    resolved_execution_entrypoint = execution_entrypoint
    if (
        resolved_execution_entrypoint is None
        and resolved_execution_materials
        and entrypoint.manifest.profile == "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve"
        and bool(getattr(resolved_runtime_settings, "runtime_target_enabled", False))
        and getattr(getattr(resolved_runtime_settings, "runtime_target", None), "strategy_release", None) is not None
    ):
        from strategy_loader import load_strategy_execution_entrypoint_for_profile

        resolved_execution_entrypoint = load_strategy_execution_entrypoint_for_profile(
            entrypoint.manifest.profile,
            execution_materials=resolved_execution_materials,
        )
    overrides = _build_runtime_overrides(entrypoint.manifest.profile, resolved_runtime_settings)
    overrides.update(runtime_overrides or {})
    runtime = LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=resolved_runtime_settings,
        execution_entrypoint=resolved_execution_entrypoint,
        execution_materials=resolved_execution_materials,
        runtime_overrides=overrides,
        logger=logger,
    )
    runtime_config = runtime.load_runtime_parameters()
    merged_runtime_config = dict(entrypoint.manifest.default_config)
    merged_runtime_config.update(runtime_config)
    merged_runtime_config.update(overrides)
    return LoadedStrategyRuntime(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        runtime_settings=resolved_runtime_settings,
        execution_entrypoint=resolved_execution_entrypoint,
        execution_materials=resolved_execution_materials,
        runtime_overrides=overrides,
        runtime_config=runtime_config,
        merged_runtime_config=merged_runtime_config,
        logger=logger,
    )
