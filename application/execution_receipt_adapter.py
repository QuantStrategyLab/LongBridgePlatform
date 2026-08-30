"""Bounded execution-receipt facts derived from LongBridge cycle results."""

from __future__ import annotations

from typing import Any

from quant_platform_kit.common.execution_receipts import (
    attach_runtime_execution_receipt,
    resolve_execution_receipt_fact,
)


def attach_cycle_execution_receipt(
    report: dict[str, Any],
    cycle_result: object,
) -> dict[str, Any]:
    """Attach only facts that the LongBridge cycle result explicitly carries.

    ``action_done`` confirms a local order-submission step, not a broker fill.
    A populated ``pending_orders`` list wins over it and produces a
    reconciliation requirement instead.
    """

    pending_orders = tuple(getattr(cycle_result, "pending_orders", ()) or ())
    outcome, confirmation = resolve_execution_receipt_fact(
        dry_run=bool(report.get("dry_run")),
        submission_attempted=bool(getattr(cycle_result, "action_done", False)),
        reconciliation_required=bool(pending_orders),
    )
    return attach_runtime_execution_receipt(
        report,
        outcome=outcome,
        broker_confirmation=confirmation,
    )


def attach_terminal_fallback_execution_receipt(report: dict[str, Any]) -> dict[str, Any]:
    """Attach a conservative fact when the cycle exits before a result exists."""

    failed = str(report.get("status") or "").strip().lower() == "error"
    outcome, confirmation = resolve_execution_receipt_fact(
        dry_run=bool(report.get("dry_run")),
        submission_attempted=failed,
        failed=failed,
    )
    return attach_runtime_execution_receipt(
        report,
        outcome=outcome,
        broker_confirmation=confirmation,
    )
