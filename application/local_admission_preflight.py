"""Pure, redacted admission classification for a locally supplied evidence set.

This module never constructs LongPort contexts, calls a provider, reads a
secret, or submits an order.  It only validates the existing runtime-target
and reconciliation receipt formats, then classifies supplied boolean gates.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from application.broker_reconciliation import validate_reconciliation_candidate
from quant_platform_kit.common.runtime_target import resolve_runtime_target_from_env
from strategy_registry import LONGBRIDGE_PLATFORM, resolve_strategy_definition


_PARK_REASONS = (
    ("live_ready", "live_ready_not_confirmed"),
    ("release", "release_not_verified"),
    ("mandate", "mandate_not_confirmed"),
    ("broker_session_token_refresh", "broker_session_token_refresh_not_confirmed"),
    ("data_entitlement", "data_entitlement_not_confirmed"),
    ("ledger", "ledger_not_confirmed"),
    ("unknown_pending_orders", "unknown_pending_orders_present"),
    ("reconciliation", "reconciliation_not_confirmed"),
)


def _strict_true(payload: Mapping[str, object], field: str) -> bool:
    return payload.get(field) is True


def _release_is_verified(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        target = resolve_runtime_target_from_env(
            env={"RUNTIME_TARGET_JSON": json.dumps(dict(value), sort_keys=True)},
            expected_platform_id=LONGBRIDGE_PLATFORM,
        )
        resolve_strategy_definition(target.strategy_profile, platform_id=LONGBRIDGE_PLATFORM)
    except (TypeError, ValueError, EnvironmentError):
        return False
    return target.execution_mode == "live" and target.strategy_release is not None


def _reconciliation_is_verified(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False

    class SuppliedCandidate:
        def to_safe_dict(self) -> dict[str, object]:
            return dict(value)

    try:
        candidate = validate_reconciliation_candidate(SuppliedCandidate())
    except Exception:
        return False
    return (
        candidate.get("permits_active_lkg") is True
        and candidate.get("expected_digests_configured") is True
        and candidate.get("recovery_blockers") == []
    )


def evaluate_local_admission(payload: object) -> dict[str, object]:
    """Return only fixed, sanitized gate state and one terminal disposition.

    Every affirmative assertion except ``unknown_pending_orders`` must be the
    literal boolean ``true``.  The supplied runtime target and reconciliation
    receipt are independently parsed using the existing runtime code.
    """

    source = payload if isinstance(payload, Mapping) else {}
    gates = {
        "live_ready": _strict_true(source, "live_ready"),
        "release": _release_is_verified(source.get("runtime_target")),
        "mandate": _strict_true(source, "mandate"),
        "broker_session_token_refresh": _strict_true(source, "broker_session_token_refresh"),
        "data_entitlement": _strict_true(source, "data_entitlement"),
        "ledger": _strict_true(source, "ledger"),
        "unknown_pending_orders": _strict_true(source, "unknown_pending_orders"),
        "reconciliation": _reconciliation_is_verified(source.get("reconciliation_receipt")),
    }
    for gate, reason_code in _PARK_REASONS:
        if gate == "unknown_pending_orders":
            if gates[gate]:
                return {"disposition": "PARK", "reason_code": reason_code, "gates": gates}
        elif not gates[gate]:
            return {"disposition": "PARK", "reason_code": reason_code, "gates": gates}
    return {"disposition": "READY", "reason_code": "ready", "gates": gates}


__all__ = ["evaluate_local_admission"]
