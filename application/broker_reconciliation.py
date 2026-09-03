"""Fail-closed LongBridge broker-reconciliation entrypoint groundwork."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from quant_platform_kit.common.broker_reconciliation import BrokerReconciliationEvidence


ENABLED_ENV_NAME = "LONGBRIDGE_BROKER_RECONCILIATION_ENABLED"
SUPPORTED_ACCOUNT_SCOPES = frozenset({"PAPER", "HK", "SG"})


def reconciliation_enabled(env_reader: Callable[[str, str], str | None]) -> bool:
    """Require an explicit exact boolean; missing or malformed values stay off."""

    return str(env_reader(ENABLED_ENV_NAME, "") or "").strip().lower() == "true"


def run_read_only_broker_reconciliation(
    *,
    enabled: bool,
    account_scope: object,
    build_read_only_contexts: Callable[[], tuple[Any, Any]],
    collect_evidence: Callable[..., BrokerReconciliationEvidence | Mapping[str, object]] | None,
) -> tuple[dict[str, object], int]:
    """Collect one redacted QPK receipt without constructing an execution port.

    The production collector is intentionally absent in this groundwork slice.
    Both the feature flag and a concrete collector must exist before broker
    contexts are built.
    """

    scope = str(account_scope or "").strip().upper()
    if not enabled:
        return {"status": "blocked", "reason": "broker_reconciliation_disabled"}, 503
    if scope not in SUPPORTED_ACCOUNT_SCOPES:
        return {
            "status": "blocked",
            "reason": "broker_reconciliation_account_scope_unsupported",
        }, 503
    if not callable(collect_evidence):
        return {
            "status": "blocked",
            "reason": "broker_reconciliation_collector_unavailable",
        }, 503
    if not callable(build_read_only_contexts):
        return {
            "status": "blocked",
            "reason": "broker_reconciliation_context_builder_unavailable",
        }, 503

    try:
        quote_context, trade_context = build_read_only_contexts()
        evidence = collect_evidence(
            quote_context,
            trade_context,
            account_scope=scope,
        )
        normalized = (
            evidence
            if isinstance(evidence, BrokerReconciliationEvidence)
            else BrokerReconciliationEvidence.from_dict(evidence)
        )
        if normalized.platform_id != "longbridge":
            raise ValueError("unexpected reconciliation platform")
    except Exception:
        return {
            "status": "blocked",
            "reason": "broker_reconciliation_collection_failed",
        }, 503
    return normalized.to_dict(), 200
