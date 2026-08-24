"""Paper-only durable-command consumer with reconciled per-symbol evidence.

The module intentionally never imports an execution adapter.  It reads the
current account snapshot and quotes, records what *would* be submitted, and
uses the shared runtime command gate in observation mode.  A command is only
marked as paper-filled when every reconciled proposal passes the policy; no
broker order is ever created here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date
from typing import Any

from quant_platform_kit.common.execution_commands import (
    ExecutionCommand,
    ExecutionCommandState,
    ExecutionCommandStore,
)
from quant_platform_kit.common.runtime_command_gate import (
    RuntimeCommandAction,
    RuntimeCommandExposureEffect,
    RuntimeCommandGateEnforcement,
    RuntimeCommandGatePolicy,
    evaluate_runtime_command_gate,
)
from quant_platform_kit.common.strategy_release import (
    StrategyReleaseIdentity,
    build_strategy_release_identity,
)


PAPER_COMMAND_CONSUMER_SCHEMA_VERSION = "longbridge.paper-execution-command-consumer.v1"
PAPER_EXECUTION_INTENT_SCHEMA_VERSION = "longbridge.paper-execution-intent.v1"
_NOTIONAL_TOLERANCE = 0.01


def _normalized_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    return symbol.split(".", 1)[0].strip()


def _normalized_symbols(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {_normalized_symbol(item) for item in value if _normalized_symbol(item)}


def _as_finite_number(value: object, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _command_release_findings(
    command: ExecutionCommand,
    *,
    expected_strategy_release: StrategyReleaseIdentity,
) -> tuple[str, ...]:
    raw_release = command.intent.get("strategy_release")
    if not isinstance(raw_release, Mapping):
        return ("release_identity_mismatch",)
    try:
        command_release = build_strategy_release_identity(raw_release)
    except ValueError:
        return ("release_identity_invalid",)
    if command_release != expected_strategy_release:
        return ("release_identity_mismatch",)
    return ()


def _runtime_release_preflight_reason(
    receipt: Mapping[str, Any] | None,
    *,
    expected_strategy_release: StrategyReleaseIdentity,
) -> str | None:
    """Refuse to claim commands until the runtime has self-attested its release."""
    if not isinstance(receipt, Mapping):
        return "release_receipt_missing"
    if str(receipt.get("attestation_state") or "") != "self_attested":
        return "release_receipt_missing"
    raw_release = receipt.get("strategy_release")
    if not isinstance(raw_release, Mapping):
        return "release_receipt_missing"
    try:
        actual_release = build_strategy_release_identity(raw_release)
    except ValueError:
        return "release_identity_invalid"
    if str(receipt.get("release_id") or "") != actual_release.release_id:
        return "release_identity_invalid"
    if actual_release != expected_strategy_release:
        return "release_identity_mismatch"
    return None


def _build_reconciled_order_proposals(
    command: ExecutionCommand,
    *,
    portfolio: Any,
    market_data_port: Any,
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    """Build dry-run proposals from the immutable target and current holdings.

    Exposure classification compares absolute before/after values for each
    position.  It is therefore based on reconciled state, never merely on an
    order's buy/sell label.
    """
    intent = command.intent
    if str(intent.get("schema_version") or "") != PAPER_EXECUTION_INTENT_SCHEMA_VERSION:
        return (), ("durable_event_history_invalid",)
    if str(intent.get("target_mode") or "") != "value":
        return (), ("durable_event_history_invalid",)
    raw_targets = intent.get("targets")
    if not isinstance(raw_targets, Mapping):
        return (), ("durable_event_history_invalid",)

    try:
        targets = {
            _normalized_symbol(symbol): _as_finite_number(target, field_name=f"targets[{symbol!r}]")
            for symbol, target in raw_targets.items()
            if _normalized_symbol(symbol)
        }
    except ValueError:
        return (), ("durable_event_history_invalid",)
    if any(target < 0.0 for target in targets.values()):
        # LongBridge's current cash-only value-target contract has no safe
        # representation for a short target in this paper consumer.
        return (), ("durable_event_history_invalid",)

    strategy_symbols = _normalized_symbols(intent.get("strategy_symbols"))
    if not strategy_symbols or not set(targets).issubset(strategy_symbols):
        return (), ("durable_event_history_invalid",)

    position_values: dict[str, float] = {}
    position_quantities: dict[str, float] = {}
    findings: list[str] = []
    for position in tuple(getattr(portfolio, "positions", ()) or ()):
        symbol = _normalized_symbol(getattr(position, "symbol", ""))
        if not symbol:
            findings.append("position_reconciliation_mismatch")
            continue
        if symbol not in strategy_symbols:
            findings.append("position_reconciliation_mismatch")
            continue
        try:
            market_value = _as_finite_number(
                getattr(position, "market_value", None),
                field_name=f"position[{symbol}].market_value",
            )
            quantity = _as_finite_number(
                getattr(position, "quantity", None),
                field_name=f"position[{symbol}].quantity",
            )
        except ValueError:
            findings.append("position_reconciliation_mismatch")
            continue
        if abs(quantity) > _NOTIONAL_TOLERANCE and abs(market_value) <= _NOTIONAL_TOLERANCE:
            findings.append("position_reconciliation_mismatch")
        position_values[symbol] = position_values.get(symbol, 0.0) + market_value
        position_quantities[symbol] = position_quantities.get(symbol, 0.0) + quantity

    cash_balance = getattr(portfolio, "cash_balance", None)
    total_equity = getattr(portfolio, "total_equity", None)
    if cash_balance is not None and total_equity is not None:
        try:
            reconciled_total = _as_finite_number(cash_balance, field_name="portfolio.cash_balance") + sum(
                position_values.values()
            )
            expected_total = _as_finite_number(total_equity, field_name="portfolio.total_equity")
            tolerance = max(1.0, abs(expected_total) * 0.005)
            if abs(reconciled_total - expected_total) > tolerance:
                findings.append("position_reconciliation_mismatch")
        except ValueError:
            findings.append("position_reconciliation_mismatch")

    proposals: list[dict[str, object]] = []
    for symbol in sorted(strategy_symbols):
        current_value = position_values.get(symbol, 0.0)
        target_value = targets.get(symbol, 0.0)
        delta_value = target_value - current_value
        if abs(delta_value) <= _NOTIONAL_TOLERANCE:
            continue
        try:
            quote = market_data_port.get_quote(symbol)
            price = _as_finite_number(getattr(quote, "last_price", None), field_name=f"quote[{symbol}].last_price")
            if price <= 0.0:
                raise ValueError("quote price must be positive")
        except Exception:
            findings.append("position_reconciliation_mismatch")
            continue

        before_exposure = abs(current_value)
        after_exposure = abs(target_value)
        exposure_delta = after_exposure - before_exposure
        if exposure_delta < -_NOTIONAL_TOLERANCE:
            exposure_effect = RuntimeCommandExposureEffect.REDUCES
        elif exposure_delta > _NOTIONAL_TOLERANCE:
            exposure_effect = RuntimeCommandExposureEffect.INCREASES
        else:
            exposure_effect = RuntimeCommandExposureEffect.NEUTRAL
        proposals.append(
            {
                "symbol": symbol,
                "side": "buy" if delta_value > 0.0 else "sell",
                "quantity": round(abs(delta_value) / price, 8),
                "reference_price": round(price, 8),
                "current_value": round(current_value, 8),
                "target_value": round(target_value, 8),
                "target_notional_delta": round(delta_value, 8),
                "current_quantity": round(position_quantities.get(symbol, 0.0), 8),
                "exposure_effect": exposure_effect.value,
            }
        )
    return tuple(proposals), tuple(dict.fromkeys(findings))


def _append_or_raise(
    store: ExecutionCommandStore,
    command: ExecutionCommand,
    *,
    next_state: ExecutionCommandState,
    expected_previous_state: ExecutionCommandState,
    details: Mapping[str, object],
) -> None:
    event = store.append_event(
        command,
        next_state=next_state,
        expected_previous_state=expected_previous_state,
        details=details,
    )
    if event is None:
        raise RuntimeError(f"failed to persist paper command event {next_state.value}")


def _attempt_reconciliation_required(
    store: ExecutionCommandStore,
    command: ExecutionCommand,
    *,
    error: Exception,
) -> None:
    try:
        state = store.current_state(command)
        if state not in {
            ExecutionCommandState.CLAIMED,
            ExecutionCommandState.SUBMITTED,
            ExecutionCommandState.ACCEPTED,
            ExecutionCommandState.PARTIALLY_FILLED,
        }:
            return
        store.append_event(
            command,
            next_state=ExecutionCommandState.RECONCILIATION_REQUIRED,
            expected_previous_state=state,
            details={
                "paper_simulation": True,
                "reason": "consumer_exception_requires_manual_reconciliation",
                "error_type": type(error).__name__,
            },
        )
    except Exception:
        # The original error is already captured by the caller's result.  Do
        # not risk masking it with a second storage failure.
        return


def consume_due_paper_execution_commands(
    *,
    store: ExecutionCommandStore | None,
    as_of_session: date | str,
    claimant: str,
    portfolio: Any,
    market_data_port: Any,
    runtime_release_receipt: Mapping[str, Any] | None,
    expected_strategy_release: StrategyReleaseIdentity | Mapping[str, object] | None,
) -> dict[str, object]:
    """Claim and simulate due paper commands; never submit a broker order."""
    if store is None or (not store.cloud_prefix_uri and not store.local_dir):
        raise RuntimeError("paper durable execution command store is required")
    try:
        expected_release = build_strategy_release_identity(expected_strategy_release)
    except ValueError:
        return {
            "schema_version": PAPER_COMMAND_CONSUMER_SCHEMA_VERSION,
            "status": "blocked",
            "reason": "release_identity_invalid",
            "commands": [],
        }
    release_preflight_reason = _runtime_release_preflight_reason(
        runtime_release_receipt,
        expected_strategy_release=expected_release,
    )
    if release_preflight_reason is not None:
        return {
            "schema_version": PAPER_COMMAND_CONSUMER_SCHEMA_VERSION,
            "status": "blocked",
            "reason": release_preflight_reason,
            "commands": [],
        }

    as_of_date = str(as_of_session)[:10]
    commands: list[dict[str, object]] = []
    for command in store.list_due(as_of_date):
        if store.current_state(command) is not ExecutionCommandState.QUEUED:
            continue
        claim = store.claim_due(command, as_of_date=as_of_date, claimant=claimant)
        if claim is None:
            continue
        try:
            integrity_findings = list(
                _command_release_findings(
                    command,
                    expected_strategy_release=expected_release,
                )
            )
            if command.execution_mode != "paper":
                integrity_findings.append("durable_event_history_invalid")
            proposals, reconciliation_findings = _build_reconciled_order_proposals(
                command,
                portfolio=portfolio,
                market_data_port=market_data_port,
            )
            integrity_findings.extend(reconciliation_findings)
            integrity_findings = list(dict.fromkeys(integrity_findings))
            receipts: list[dict[str, object]] = []
            for proposal in proposals:
                decision = evaluate_runtime_command_gate(
                    action=RuntimeCommandAction.SUBMIT,
                    exposure_effect=proposal["exposure_effect"],
                    command=command,
                    command_state=ExecutionCommandState.CLAIMED,
                    as_of_session=as_of_date,
                    runtime_release_receipt=runtime_release_receipt,
                    expected_strategy_release=expected_release,
                    integrity_findings=integrity_findings,
                    policy=RuntimeCommandGatePolicy(
                        enforcement=RuntimeCommandGateEnforcement.OBSERVE,
                    ),
                )
                receipts.append(decision.to_receipt())

            # A no-op command still has to pass the command-level release and
            # timing checks before it can be closed as paper-filled.
            if not proposals:
                decision = evaluate_runtime_command_gate(
                    action=RuntimeCommandAction.SUBMIT,
                    exposure_effect=RuntimeCommandExposureEffect.NEUTRAL,
                    command=command,
                    command_state=ExecutionCommandState.CLAIMED,
                    as_of_session=as_of_date,
                    runtime_release_receipt=runtime_release_receipt,
                    expected_strategy_release=expected_release,
                    integrity_findings=integrity_findings,
                    policy=RuntimeCommandGatePolicy(
                        enforcement=RuntimeCommandGateEnforcement.OBSERVE,
                    ),
                )
                receipts.append(decision.to_receipt())

            details = {
                "paper_simulation": True,
                "claimant": claimant,
                "integrity_findings": integrity_findings,
                "proposals": list(proposals),
                "runtime_command_gate_receipts": receipts,
            }
            if any(not bool(receipt["policy_allows"]) for receipt in receipts):
                _append_or_raise(
                    store,
                    command,
                    next_state=ExecutionCommandState.REJECTED,
                    expected_previous_state=ExecutionCommandState.CLAIMED,
                    details={
                        **details,
                        "reason": "paper_command_gate_would_block",
                    },
                )
                commands.append(
                    {
                        "command_id": command.command_id,
                        "status": ExecutionCommandState.REJECTED.value,
                        "proposals_count": len(proposals),
                        "would_block": True,
                    }
                )
                continue

            _append_or_raise(
                store,
                command,
                next_state=ExecutionCommandState.SUBMITTED,
                expected_previous_state=ExecutionCommandState.CLAIMED,
                details=details,
            )
            _append_or_raise(
                store,
                command,
                next_state=ExecutionCommandState.ACCEPTED,
                expected_previous_state=ExecutionCommandState.SUBMITTED,
                details={"paper_simulation": True, "proposals_count": len(proposals)},
            )
            _append_or_raise(
                store,
                command,
                next_state=ExecutionCommandState.FILLED,
                expected_previous_state=ExecutionCommandState.ACCEPTED,
                details={"paper_simulation": True, "simulated_fill_count": len(proposals)},
            )
            commands.append(
                {
                    "command_id": command.command_id,
                    "status": ExecutionCommandState.FILLED.value,
                    "proposals_count": len(proposals),
                    "would_block": False,
                }
            )
        except Exception as exc:
            _attempt_reconciliation_required(store, command, error=exc)
            commands.append(
                {
                    "command_id": command.command_id,
                    "status": ExecutionCommandState.RECONCILIATION_REQUIRED.value,
                    "error_type": type(exc).__name__,
                }
            )

    return {
        "schema_version": PAPER_COMMAND_CONSUMER_SCHEMA_VERSION,
        "status": "ok",
        "as_of_session": as_of_date,
        "commands": commands,
    }
