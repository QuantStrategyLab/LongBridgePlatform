"""Paper-only producer for immutable delayed-execution command evidence."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from quant_platform_kit.common.execution_commands import (
    EXECUTION_COMMAND_STRATEGY_RELEASE_FIELD,
    ExecutionCommand,
    ExecutionCommandStore,
)
from quant_platform_kit.common.execution_commands import (
    build_execution_command_store_from_env as _build_execution_command_store_from_env,
)
from quant_platform_kit.common.runtime_command_gate import (
    RuntimeCommandExposureEffect,
    RuntimeCommandGateEnforcement,
    RuntimeCommandGatePolicy,
    evaluate_runtime_command_gate,
)
from quant_platform_kit.common.strategy_release import build_strategy_release_identity

PAPER_EXECUTION_INTENT_SCHEMA_VERSION = "longbridge.paper-execution-intent.v1"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _normalized_symbols(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(symbol or "").strip().upper() for symbol in value if str(symbol or "").strip()})


def _normalized_targets(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, float] = {}
    for symbol, target in value.items():
        key = str(symbol or "").strip().upper()
        if not key:
            continue
        try:
            normalized[key] = float(target)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid paper execution target for {key}") from exc
    return {symbol: normalized[symbol] for symbol in sorted(normalized)}


def build_paper_execution_command(
    *,
    platform: str,
    account_scope: str,
    strategy_profile: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
    strategy_release: Any = None,
) -> ExecutionCommand:
    """Bind one paper-only command to immutable timing and target intent."""
    execution = dict(execution or {})
    allocation = dict(allocation or {})
    intent = {
        "schema_version": PAPER_EXECUTION_INTENT_SCHEMA_VERSION,
        "target_mode": str(allocation.get("target_mode") or "").strip(),
        "targets": _normalized_targets(allocation.get("targets")),
        "strategy_symbols": _normalized_symbols(allocation.get("strategy_symbols")),
        "risk_symbols": _normalized_symbols(allocation.get("risk_symbols")),
        "safe_haven_symbols": _normalized_symbols(allocation.get("safe_haven_symbols")),
    }
    if strategy_release is not None:
        # The command is content-addressed, so including the release identity
        # binds a delayed paper command to the exact decision release.  The
        # future consumer compares it with its self-attested runtime release
        # before it simulates even a single order.
        intent[EXECUTION_COMMAND_STRATEGY_RELEASE_FIELD] = build_strategy_release_identity(
            strategy_release
        ).to_dict()
    intent_json = _canonical_json(intent)
    return ExecutionCommand.from_decision(
        platform=platform,
        account_scope=account_scope,
        strategy_profile=strategy_profile,
        execution_mode="paper",
        signal_date=execution.get("signal_date"),
        effective_date=execution.get("effective_date"),
        execution_timing_contract=execution.get("execution_timing_contract"),
        decision_digest=hashlib.sha256(intent_json.encode("utf-8")).hexdigest(),
        intent=intent,
    )


def enqueue_paper_execution_command(
    *,
    enabled: bool,
    dry_run_only: bool,
    store: ExecutionCommandStore | None,
    platform: str,
    account_scope: str,
    strategy_profile: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
    runtime_release_receipt: Mapping[str, Any] | None = None,
    expected_strategy_release: Any = None,
) -> dict[str, object] | None:
    """Create one command only; this phase never claims or routes it."""
    if not enabled:
        return None
    if not dry_run_only:
        raise RuntimeError("durable execution command producer is paper-only")
    if store is None or (not store.cloud_prefix_uri and not store.local_dir):
        raise RuntimeError("paper durable execution command store is required")
    command = build_paper_execution_command(
        platform=platform,
        account_scope=account_scope,
        strategy_profile=strategy_profile,
        execution=execution,
        allocation=allocation,
        strategy_release=expected_strategy_release,
    )
    created = store.enqueue(command)
    gate_decision = evaluate_runtime_command_gate(
        action="submit",
        # A target-allocation command cannot safely infer the net exposure of
        # each future order. The future consumer must reconcile positions and
        # re-evaluate per order before it ever switches to enforcement.
        exposure_effect=RuntimeCommandExposureEffect.UNKNOWN,
        command=command,
        command_state="queued",
        as_of_session=command.effective_date,
        runtime_release_receipt=runtime_release_receipt,
        expected_strategy_release=expected_strategy_release,
        policy=RuntimeCommandGatePolicy(
            enforcement=RuntimeCommandGateEnforcement.OBSERVE,
        ),
    )
    return {
        "schema_version": "longbridge.paper-execution-command-observation.v1",
        "command_id": command.command_id,
        "decision_digest": command.decision_digest,
        "effective_date": command.effective_date,
        "status": "QUEUED" if created else "ALREADY_QUEUED",
        "consumer_authorized": False,
        "runtime_command_gate": gate_decision.to_receipt(),
    }


def build_execution_command_store_from_env(
    *,
    env_reader,
    gcp_project_id: str | None = None,
) -> ExecutionCommandStore:
    return _build_execution_command_store_from_env(
        platform_env_prefix="LONGBRIDGE",
        env_reader=env_reader,
        project_id=gcp_project_id,
    )


def resolve_paper_execution_command_producer_enabled(*, env_reader, dry_run_only: bool) -> bool:
    raw_value = str(env_reader("LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_ENABLED", "") or "").strip().lower()
    enabled = raw_value in {"1", "true", "t", "yes", "y", "on"}
    if enabled and not dry_run_only:
        raise RuntimeError("durable execution command producer is paper-only and cannot be enabled live")
    return enabled


def resolve_paper_execution_command_consumer_enabled(*, env_reader, dry_run_only: bool) -> bool:
    """Resolve the opt-in paper consumer flag and reject any live runtime."""
    raw_value = str(
        env_reader("LONGBRIDGE_DURABLE_EXECUTION_COMMAND_PAPER_CONSUMER_ENABLED", "") or ""
    ).strip().lower()
    enabled = raw_value in {"1", "true", "t", "yes", "y", "on"}
    if enabled and not dry_run_only:
        raise RuntimeError("durable execution command consumer is paper-only and cannot be enabled live")
    return enabled
