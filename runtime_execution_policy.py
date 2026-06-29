from __future__ import annotations

from quant_platform_kit.common.execution_capabilities import (
    FRACTIONAL_SHARE_EXECUTION_SKIP_REASON,
    definition_requires_fractional_share_execution,
    fractional_share_execution_unsupported_reason,
    notional_buy_compat_mode_enabled,
)
from quant_platform_kit.common.strategies import normalize_profile_name
from strategy_registry import PLATFORM_CAPABILITY_MATRIX, STRATEGY_CATALOG

FRACTIONAL_BUY_QUANTITY_STEP = 0.0001


def dca_execution_unsupported_reason(strategy_profile: str) -> str | None:
    return fractional_share_execution_unsupported_reason(
        strategy_profile,
        strategy_catalog=STRATEGY_CATALOG,
        capability_matrix=PLATFORM_CAPABILITY_MATRIX,
    )


def dca_compat_mode_enabled(strategy_profile: str) -> bool:
    """True when the strategy wants fractional but the platform lacks it.

    The execution layer should convert notional buys to minimum whole-share
    (US) or whole-lot (HK) orders instead of skipping the strategy entirely.
    """
    return notional_buy_compat_mode_enabled(
        strategy_profile,
        strategy_catalog=STRATEGY_CATALOG,
        capability_matrix=PLATFORM_CAPABILITY_MATRIX,
    )


def fractional_buy_execution_enabled(strategy_profile: str) -> bool:
    if dca_execution_unsupported_reason(strategy_profile) is not None:
        return False
    normalized_profile = normalize_profile_name(strategy_profile)
    definition = STRATEGY_CATALOG.definitions.get(normalized_profile)
    if definition is None:
        return False
    return definition_requires_fractional_share_execution(definition)


__all__ = (
    "FRACTIONAL_BUY_QUANTITY_STEP",
    "FRACTIONAL_SHARE_EXECUTION_SKIP_REASON",
    "dca_compat_mode_enabled",
    "dca_execution_unsupported_reason",
    "fractional_buy_execution_enabled",
)
