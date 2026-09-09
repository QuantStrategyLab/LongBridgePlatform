"""Immutable delayed-execution commands with separate paper and live admission."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from quant_platform_kit.common.execution_commands import (
    EXECUTION_COMMAND_SCHEMA_VERSION,
    EXECUTION_COMMAND_STRATEGY_RELEASE_FIELD,
    ExecutionCommand,
    ExecutionCommandStore,
)
from quant_platform_kit.common.paper_execution_admission import (
    PAPER_RISK_ADMISSION_RECEIPT_INTENT_FIELD,
    PaperRiskAdmissionReceipt,
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
LIVE_EXECUTION_COMMAND_ENABLED_ENV = "LONGBRIDGE_DURABLE_EXECUTION_COMMAND_LIVE_ENABLED"


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


def _sha256_text(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _required_sha256(value: object, *, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return normalized


def build_live_runtime_identity_digest(*, strategy_profile: str, runtime_config: Mapping[str, Any]) -> str:
    packages = {}
    for name in ("quant-platform-kit", "us-equity-strategies"):
        distribution = importlib.metadata.distribution(name)
        try:
            direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
            commit_id = str((direct_url.get("vcs_info") or {}).get("commit_id") or "").lower()
        except (TypeError, ValueError, AttributeError):
            commit_id = ""
        if len(commit_id) != 40 or any(character not in "0123456789abcdef" for character in commit_id):
            raise RuntimeError(f"{name} source commit identity is unavailable")
        packages[name] = {
            "version": distribution.version,
            "source_commit": commit_id,
        }
    identity = {
        "strategy_profile": str(strategy_profile or "").strip(),
        "runtime_config": dict(runtime_config),
        "packages": packages,
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _build_live_execution_intent(
    *,
    physical_account_id: str,
    runtime_identity_digest: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
) -> dict[str, object]:
    account_id = str(physical_account_id or "").strip()
    if not account_id:
        raise ValueError("physical_account_id is required")
    frozen_execution = json.loads(_canonical_json(execution))
    return {
        "kind": "longbridge_next_session_live",
        "physical_account_digest": _sha256_text(account_id),
        "runtime_identity_digest": _required_sha256(
            runtime_identity_digest,
            field_name="runtime_identity_digest",
        ),
        "execution": frozen_execution,
        "allocation": {
            "target_mode": str(allocation.get("target_mode") or "").strip(),
            "targets": _normalized_targets(allocation.get("targets")),
            "strategy_symbols": _normalized_symbols(allocation.get("strategy_symbols")),
            "risk_symbols": _normalized_symbols(allocation.get("risk_symbols")),
            "income_symbols": _normalized_symbols(allocation.get("income_symbols")),
            "safe_haven_symbols": _normalized_symbols(allocation.get("safe_haven_symbols")),
        },
    }


def build_live_execution_command(
    *,
    platform: str,
    account_scope: str,
    strategy_profile: str,
    physical_account_id: str,
    runtime_identity_digest: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
) -> ExecutionCommand:
    """Build one immutable next-session live intent without broker authority."""
    intent = _build_live_execution_intent(
        physical_account_id=physical_account_id,
        runtime_identity_digest=runtime_identity_digest,
        execution=execution,
        allocation=allocation,
    )
    return ExecutionCommand.from_decision(
        platform=platform,
        account_scope=account_scope,
        strategy_profile=strategy_profile,
        execution_mode="live",
        signal_date=execution.get("signal_date"),
        effective_date=execution.get("effective_date"),
        execution_timing_contract=execution.get("execution_timing_contract"),
        decision_digest=hashlib.sha256(_canonical_json(intent).encode("utf-8")).hexdigest(),
        intent=intent,
    )


def enqueue_live_execution_command(
    *,
    enabled: bool,
    dry_run_only: bool,
    store: ExecutionCommandStore | None,
    platform: str,
    account_scope: str,
    strategy_profile: str,
    physical_account_id: str,
    runtime_identity_digest: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
) -> tuple[ExecutionCommand, bool] | None:
    if not enabled:
        return None
    if dry_run_only:
        raise RuntimeError("durable live execution command is live-only")
    if store is None or (not store.cloud_prefix_uri and not store.local_dir):
        raise RuntimeError("durable live execution command store is required")
    command = build_live_execution_command(
        platform=platform,
        account_scope=account_scope,
        strategy_profile=strategy_profile,
        physical_account_id=physical_account_id,
        runtime_identity_digest=runtime_identity_digest,
        execution=execution,
        allocation=allocation,
    )
    return command, bool(store.enqueue(command))


def list_live_execution_commands(store: ExecutionCommandStore) -> tuple[ExecutionCommand, ...]:
    """Read all durable live commands so unresolved prior sessions stay blocking."""
    if store.cloud_prefix_uri:
        prefix = "/".join(
            (
                str(store.cloud_prefix_uri).rstrip("/"),
                store.namespace,
                EXECUTION_COMMAND_SCHEMA_VERSION,
            )
        )
        object_store = store.object_store or store._object_store()  # noqa: SLF001
        locations = tuple(object_store.list(prefix))
        read_text = object_store.read_text
    elif store.local_dir:
        root = Path(store.local_dir) / store.namespace / EXECUTION_COMMAND_SCHEMA_VERSION
        locations = tuple(root.glob("*/*/command.json")) if root.exists() else ()

        def read_text(location):
            return Path(location).read_text(encoding="utf-8")
    else:
        raise RuntimeError("execution command store has no durable backend")
    commands = (
        ExecutionCommand.from_dict(json.loads(read_text(location)))
        for location in locations
        if str(location).endswith("/command.json") or Path(str(location)).name == "command.json"
    )
    return tuple(
        sorted(
            (item for item in commands if item.execution_mode == "live"),
            key=lambda item: item.command_id,
        )
    )


def _build_paper_execution_decision_intent(
    *,
    allocation: Mapping[str, Any],
    strategy_release: Any = None,
) -> dict[str, object]:
    intent: dict[str, object] = {
        "schema_version": PAPER_EXECUTION_INTENT_SCHEMA_VERSION,
        "target_mode": str(allocation.get("target_mode") or "").strip(),
        "targets": _normalized_targets(allocation.get("targets")),
        "strategy_symbols": _normalized_symbols(allocation.get("strategy_symbols")),
        "risk_symbols": _normalized_symbols(allocation.get("risk_symbols")),
        "safe_haven_symbols": _normalized_symbols(allocation.get("safe_haven_symbols")),
    }
    if strategy_release is not None:
        intent[EXECUTION_COMMAND_STRATEGY_RELEASE_FIELD] = build_strategy_release_identity(
            strategy_release
        ).to_dict()
    return intent


def build_paper_execution_decision_digest(
    *,
    allocation: Mapping[str, Any],
    strategy_release: Any = None,
) -> str:
    """Digest the immutable strategy decision before its risk receipt is added.

    The later command ID content-addresses the full intent, including the
    receipt.  Keeping this decision digest receipt-free avoids a circular hash
    while still binding both the strategy decision and risk evidence.
    """
    intent = _build_paper_execution_decision_intent(
        allocation=allocation,
        strategy_release=strategy_release,
    )
    return hashlib.sha256(_canonical_json(intent).encode("utf-8")).hexdigest()


def build_paper_execution_command(
    *,
    platform: str,
    account_scope: str,
    strategy_profile: str,
    execution: Mapping[str, Any],
    allocation: Mapping[str, Any],
    strategy_release: Any = None,
    paper_risk_admission_receipt: Mapping[str, object] | None = None,
) -> ExecutionCommand:
    """Bind one paper-only command to immutable timing and target intent."""
    execution = dict(execution or {})
    allocation = dict(allocation or {})
    intent = _build_paper_execution_decision_intent(
        allocation=allocation,
        strategy_release=strategy_release,
    )
    decision_digest = hashlib.sha256(_canonical_json(intent).encode("utf-8")).hexdigest()
    if paper_risk_admission_receipt is not None:
        # Strictly normalize before it becomes part of the command's
        # content-addressed intent.  A consumer subsequently verifies that
        # this receipt binds the same pre-receipt decision digest.
        intent[PAPER_RISK_ADMISSION_RECEIPT_INTENT_FIELD] = PaperRiskAdmissionReceipt.from_dict(
            paper_risk_admission_receipt
        ).to_dict()
    return ExecutionCommand.from_decision(
        platform=platform,
        account_scope=account_scope,
        strategy_profile=strategy_profile,
        execution_mode="paper",
        signal_date=execution.get("signal_date"),
        effective_date=execution.get("effective_date"),
        execution_timing_contract=execution.get("execution_timing_contract"),
        decision_digest=decision_digest,
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
    paper_risk_admission_receipt: Mapping[str, object] | None = None,
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
        paper_risk_admission_receipt=paper_risk_admission_receipt,
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


def resolve_live_execution_command_enabled(*, env_reader, dry_run_only: bool) -> bool:
    raw_value = str(env_reader(LIVE_EXECUTION_COMMAND_ENABLED_ENV, "") or "").strip().lower()
    enabled = raw_value in {"1", "true", "t", "yes", "y", "on"}
    if enabled and dry_run_only:
        raise RuntimeError("durable live execution command is live-only")
    return enabled
