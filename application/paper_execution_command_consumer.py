"""Paper-only durable-command consumer with reconciled per-symbol evidence.

The module intentionally never imports an execution adapter.  It reads the
current account snapshot and quotes, records what *would* be submitted, and
uses the shared runtime command gate in enforced mode.  A command is only
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
    ExecutionCommandStore,
)
from quant_platform_kit.common.paper_execution_command_consumer import (
    PaperExecutionProposal,
    PaperExecutionReconciliation,
    consume_due_paper_execution_commands as consume_shared_paper_execution_commands,
)
from quant_platform_kit.common.runtime_command_gate import (
    RuntimeCommandExposureEffect,
)
from quant_platform_kit.common.strategy_release import StrategyReleaseIdentity

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


def _reconcile_command(
    command: ExecutionCommand,
    *,
    portfolio: Any,
    market_data_port: Any,
) -> PaperExecutionReconciliation:
    """Adapt LongBridge's value-target evidence to the shared paper contract."""

    proposals, integrity_findings = _build_reconciled_order_proposals(
        command,
        portfolio=portfolio,
        market_data_port=market_data_port,
    )
    return PaperExecutionReconciliation(
        proposals=tuple(
            PaperExecutionProposal(
                symbol=str(proposal["symbol"]),
                exposure_effect=str(proposal["exposure_effect"]),
                details={
                    key: value
                    for key, value in proposal.items()
                    if key not in {"symbol", "exposure_effect"}
                },
            )
            for proposal in proposals
        ),
        integrity_findings=integrity_findings,
    )


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
    """Claim and simulate due paper commands through the shared lifecycle."""

    return consume_shared_paper_execution_commands(
        store=store,
        as_of_session=as_of_session,
        claimant=claimant,
        reconcile_command=lambda command: _reconcile_command(
            command,
            portfolio=portfolio,
            market_data_port=market_data_port,
        ),
        runtime_release_receipt=runtime_release_receipt,
        expected_strategy_release=expected_strategy_release,
    )
