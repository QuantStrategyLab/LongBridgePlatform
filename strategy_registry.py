from __future__ import annotations

from us_equity_strategies.platform_registry_support import (
    build_platform_profile_matrix,
    get_enabled_profiles_for_platform,
    resolve_platform_strategy_definition,
)

from quant_platform_kit.common.strategies import StrategyDefinition, US_EQUITY_DOMAIN

LONGBRIDGE_PLATFORM = "longbridge"

DEFAULT_STRATEGY_PROFILE = "semiconductor_rotation_income"
ROLLBACK_STRATEGY_PROFILE = DEFAULT_STRATEGY_PROFILE

LONGBRIDGE_ENABLED_PROFILES = frozenset({"semiconductor_rotation_income"})

PLATFORM_SUPPORTED_DOMAINS: dict[str, frozenset[str]] = {
    LONGBRIDGE_PLATFORM: frozenset({US_EQUITY_DOMAIN}),
}

SUPPORTED_STRATEGY_PROFILES = LONGBRIDGE_ENABLED_PROFILES


def get_supported_profiles_for_platform(platform_id: str) -> frozenset[str]:
    return get_enabled_profiles_for_platform(
        platform_id,
        expected_platform_id=LONGBRIDGE_PLATFORM,
        enabled_profiles=LONGBRIDGE_ENABLED_PROFILES,
    )


def get_platform_profile_matrix() -> list[dict[str, object]]:
    return build_platform_profile_matrix(
        platform_id=LONGBRIDGE_PLATFORM,
        enabled_profiles=LONGBRIDGE_ENABLED_PROFILES,
        default_profile=DEFAULT_STRATEGY_PROFILE,
        rollback_profile=ROLLBACK_STRATEGY_PROFILE,
    )


def resolve_strategy_definition(
    raw_value: str | None,
    *,
    platform_id: str,
) -> StrategyDefinition:
    return resolve_platform_strategy_definition(
        raw_value,
        platform_id=platform_id,
        expected_platform_id=LONGBRIDGE_PLATFORM,
        enabled_profiles=LONGBRIDGE_ENABLED_PROFILES,
        platform_supported_domains=PLATFORM_SUPPORTED_DOMAINS,
        default_profile=DEFAULT_STRATEGY_PROFILE,
    )
