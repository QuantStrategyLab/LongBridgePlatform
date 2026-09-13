"""No-submit paper preview for the frozen SOXL/SOXX V7 candidate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from quant_platform_kit.common.strategy_contracts import (
    StrategyContext,
    build_strategy_context_from_available_inputs,
)
from us_equity_strategies import get_platform_runtime_adapter, get_strategy_entrypoint
from us_equity_strategies.v7_soxl_profile import (
    SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
    V7_CONFIG_SHA256,
    V7_QPK_REVISION,
    V7_REQUIRED_INPUTS,
    V7_SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS,
    V7_UES_REVISION,
)


def _required_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _resolve_context(
    *,
    context: StrategyContext | None,
    available_inputs: Mapping[str, Any] | None,
    as_of: Any | None,
) -> StrategyContext:
    if context is not None and available_inputs is not None:
        raise ValueError("provide context or available_inputs, not both")
    entrypoint = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE)
    if context is not None:
        return context
    if not isinstance(available_inputs, Mapping):
        raise ValueError("V7 preview requires StrategyContext or available_inputs")
    if as_of is None:
        raise ValueError("V7 preview available_inputs requires as_of")
    runtime_adapter = get_platform_runtime_adapter(
        SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
        platform_id="longbridge",
    )
    return build_strategy_context_from_available_inputs(
        entrypoint=entrypoint,
        runtime_adapter=runtime_adapter,
        as_of=as_of,
        available_inputs=available_inputs,
    )


def _serialize_decision(decision) -> dict[str, object]:
    return {
        "positions": [
            {
                "symbol": position.symbol,
                "target_value": position.target_value,
                "target_weight": position.target_weight,
                "role": position.role,
            }
            for position in decision.positions
        ],
        "budgets": [
            {
                "name": budget.name,
                "symbol": budget.symbol,
                "amount": budget.amount,
                "unit": budget.unit,
                "purpose": budget.purpose,
            }
            for budget in decision.budgets
        ],
        "risk_flags": list(decision.risk_flags),
        "diagnostics": dict(decision.diagnostics),
    }


def build_v7_paper_preview_mapping(
    *,
    broker_identity: Mapping[str, object],
    context: StrategyContext | None = None,
    available_inputs: Mapping[str, Any] | None = None,
    as_of: Any | None = None,
    preview_request: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run the named V7 entrypoint and return a pure paper decision preview.

    ``dry_run_only`` is an execution safeguard and is intentionally not used as
    evidence that the broker account is paper. A preview request binds only the
    target account and this fixed candidate; it does not grant account or order
    authority and never calls a broker or scheduler.
    """
    if not isinstance(broker_identity, Mapping):
        raise ValueError("paper broker identity is required")
    if str(broker_identity.get("platform_id") or "").strip().lower() != "longbridge":
        raise ValueError("paper broker identity platform mismatch")
    if str(broker_identity.get("account_mode") or "").strip().lower() != "paper":
        raise ValueError("paper broker identity must be explicit")
    account_scope = _required_text(
        broker_identity.get("account_scope"),
        field_name="paper broker identity account_scope",
    )

    if preview_request is not None:
        if not isinstance(preview_request, Mapping):
            raise ValueError("preview request must be a mapping")
        if str(preview_request.get("platform_id") or "").strip().lower() != "longbridge":
            raise ValueError("preview request platform identity mismatch")
        if str(preview_request.get("account_scope") or "").strip() != account_scope:
            raise ValueError("preview request account mismatch")
        if str(preview_request.get("strategy_profile") or "").strip() != SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE:
            raise ValueError("preview request strategy profile mismatch")
        if str(preview_request.get("candidate_id") or "").strip() != SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE:
            raise ValueError("preview request candidate identity mismatch")
        if str(preview_request.get("config_sha256") or "").strip() != V7_CONFIG_SHA256:
            raise ValueError("preview request candidate identity mismatch")

    entrypoint = get_strategy_entrypoint(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE)
    decision = entrypoint.evaluate(
        _resolve_context(
            context=context,
            available_inputs=available_inputs,
            as_of=as_of,
        )
    )
    return {
        "platform_id": "longbridge",
        "account_scope": account_scope,
        "strategy_profile": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
        "candidate_id": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
        "config_sha256": V7_CONFIG_SHA256,
        "frozen_research_source": {
            "ues_revision": V7_UES_REVISION,
            "qpk_revision": V7_QPK_REVISION,
        },
        "required_inputs": tuple(sorted(V7_REQUIRED_INPUTS)),
        "signal_effective_after_trading_days": V7_SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS,
        "broker_identity": {
            "platform_id": "longbridge",
            "account_mode": "paper",
            "account_scope": account_scope,
        },
        "execution_mode": "paper",
        "preview_only": True,
        "no_order": True,
        "runtime_enabled": False,
        "decision": _serialize_decision(decision),
    }


__all__ = ["build_v7_paper_preview_mapping"]
