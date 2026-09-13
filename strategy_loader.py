from __future__ import annotations

import os

from quant_platform_kit.common.platform_runner.loader import (
    load_strategy_definition as _qpk_load_definition,
    load_strategy_entrypoint_for_profile as _qpk_load_entrypoint,
)
from quant_platform_kit.common.strategies import StrategyDefinition
from quant_platform_kit.common.strategy_contracts import (
    StrategyEntrypoint,
    StrategyRuntimeAdapter,
)
from us_equity_strategies import get_strategy_entrypoint

from strategy_registry import LONGBRIDGE_PLATFORM as PLATFORM, PLATFORM_POLICY, STRATEGY_CATALOG, get_platform_runtime_adapter

_V7_PROFILE_NAME = "soxl_soxx_core_only_p2_v7_longterm_compounding_cash_reserve"

def _bound_v7_application(raw_profile: str | None) -> bool:
    """Return true only for the exact paused V7 application binding.

    The normal platform policy remains the default.  This one guarded loader
    path exists so a disabled candidate revision can prove what it loaded;
    it never grants an execution route or changes the global allowlist.
    """

    if str(raw_profile or "").strip() != _V7_PROFILE_NAME:
        return False
    from application.v7_paper_application import load_v7_paper_application_binding

    return load_v7_paper_application_binding(os.environ) is not None


def load_strategy_definition(raw_profile: str | None) -> StrategyDefinition:
    if _bound_v7_application(raw_profile):
        return STRATEGY_CATALOG.definitions[raw_profile.strip()]
    return _qpk_load_definition(
        raw_profile,
        platform_id=PLATFORM,
        strategy_catalog=STRATEGY_CATALOG,
        policy=PLATFORM_POLICY,
    )


def load_strategy_entrypoint_for_profile(raw_profile: str | None) -> StrategyEntrypoint:
    if _bound_v7_application(raw_profile):
        return get_strategy_entrypoint(raw_profile.strip())
    runtime_adapter = load_strategy_runtime_adapter_for_profile(raw_profile)
    return _qpk_load_entrypoint(
        raw_profile,
        platform_id=PLATFORM,
        strategy_catalog=STRATEGY_CATALOG,
        policy=PLATFORM_POLICY,
        runtime_adapter=runtime_adapter,
    )


def load_strategy_runtime_adapter_for_profile(raw_profile: str | None) -> StrategyRuntimeAdapter:
    definition = load_strategy_definition(raw_profile)
    return get_platform_runtime_adapter(
        definition.profile,
        platform_id=PLATFORM,
    )
