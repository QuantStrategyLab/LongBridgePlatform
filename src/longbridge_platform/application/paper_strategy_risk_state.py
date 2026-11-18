"""Paper-only persistence adapter for strategy-owned risk-state transitions.

The strategy calculates its own state transition before this module is called.
LongBridge only validates its local binding and appends that immutable record to
a dedicated store.  It has no broker import and no authority to modify a plan.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateStore,
    StrategyRiskStateTransition,
    build_strategy_risk_state_store_from_env as _build_strategy_risk_state_store_from_env,
)


PAPER_STRATEGY_RISK_STATE_OBSERVATION_SCHEMA_VERSION = "longbridge.paper-strategy-risk-state.v1"


def _enabled(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def resolve_paper_strategy_risk_state_enabled(*, env_reader, dry_run_only: bool) -> bool:
    """Resolve an explicit paper-only opt-in; live use is always rejected."""

    enabled = _enabled(env_reader("LONGBRIDGE_STRATEGY_RISK_STATE_PAPER_ENABLED", ""))
    if enabled and not dry_run_only:
        raise RuntimeError("strategy risk state persistence is paper-only and cannot be enabled live")
    return enabled


def build_strategy_risk_state_store_from_env(
    *,
    env_reader,
    gcp_project_id: str | None = None,
) -> StrategyRiskStateStore:
    """Build the dedicated risk-state store, never the execution-command store."""

    return _build_strategy_risk_state_store_from_env(
        platform_env_prefix="LONGBRIDGE",
        env_reader=env_reader,
        project_id=gcp_project_id,
    )


def record_paper_strategy_risk_state_transition(
    *,
    enabled: bool,
    dry_run_only: bool,
    store: StrategyRiskStateStore | None,
    transition_payload: Mapping[str, Any] | None,
    expected_strategy_profile: object,
    expected_account_scope: object,
) -> dict[str, object] | None:
    """Append one precomputed paper transition and return a redacted receipt.

    An enabled adapter without a dedicated store or transition is an error;
    silently proceeding would let a restarted runtime forget its cooldown.
    """

    if not enabled:
        return None
    if not dry_run_only:
        raise RuntimeError("strategy risk state persistence is paper-only and cannot be enabled live")
    if store is None or (not store.cloud_prefix_uri and not store.local_dir):
        raise RuntimeError("paper strategy risk state store is required")
    if not isinstance(transition_payload, Mapping):
        raise RuntimeError("paper strategy risk state transition is required")
    transition = StrategyRiskStateTransition.from_dict(transition_payload)
    expected_profile = str(expected_strategy_profile or "").strip().lower()
    expected_scope = str(expected_account_scope or "").strip().lower()
    if transition.identity.strategy_profile != expected_profile:
        raise RuntimeError("paper strategy risk state strategy profile does not match this runtime")
    if transition.identity.account_scope != expected_scope:
        raise RuntimeError("paper strategy risk state account scope does not match this runtime")
    result = store.append(transition)
    return {
        "schema_version": PAPER_STRATEGY_RISK_STATE_OBSERVATION_SCHEMA_VERSION,
        **result.to_dict(),
        "consumer_authorized": False,
    }


__all__ = [
    "PAPER_STRATEGY_RISK_STATE_OBSERVATION_SCHEMA_VERSION",
    "build_strategy_risk_state_store_from_env",
    "record_paper_strategy_risk_state_transition",
    "resolve_paper_strategy_risk_state_enabled",
]
