"""Fail-closed, read-only LongBridge reconciliation evidence."""

from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from quant_platform_kit.common.broker_reconciliation import (
    BrokerReconciliationEvidence,
    BrokerReconciliationFinding,
    build_broker_reconciliation_evidence,
    calculate_broker_observation_sha256,
    evaluate_broker_reconciliation_recovery,
)
from quant_platform_kit.common.execution_state import build_execution_marker_store_from_env


ENABLED_ENV_NAME = "LONGBRIDGE_BROKER_RECONCILIATION_ENABLED"
EXPECTED_DIGESTS_ENV_NAME = "LONGBRIDGE_RECONCILIATION_EXPECTED_DIGESTS_JSON"
SUPPORTED_ACCOUNT_SCOPES = frozenset({"PAPER", "HK", "SG"})
_EXPECTED_DIGEST_KEYS = (
    "account_scope_sha256",
    "positions_sha256",
    "cash_sha256",
    "open_orders_sha256",
    "recent_executions_sha256",
    "local_execution_ledger_sha256",
)
_SAFE_CANDIDATE_KEYS = frozenset(
    {
        "schema_version",
        "permits_active_lkg",
        "expected_digests_configured",
        "execution_ledger_records_count",
        "recovery_blockers",
        "evidence",
    }
)
_TERMINAL_ORDER_STATUSES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class LongBridgeReconciliationReadError(RuntimeError):
    """A required read-only LongBridge surface was unavailable or incomplete."""


def reconciliation_enabled(env_reader: Callable[[str, str], str | None]) -> bool:
    """Require an explicit exact boolean; missing or malformed values stay off."""

    return str(env_reader(ENABLED_ENV_NAME, "") or "").strip().lower() == "true"


def _text(value: object) -> str:
    return str(value or "").strip()


def _decimal_text(value: object, *, field_name: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LongBridgeReconciliationReadError(
            f"LongBridge reconciliation is missing {field_name}."
        ) from exc
    if not number.is_finite():
        raise LongBridgeReconciliationReadError(
            f"LongBridge reconciliation has non-finite {field_name}."
        )
    normalized = format(number.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _enum_name(value: object) -> str:
    return _text(getattr(value, "__name__", "") or value).upper()


def _canonical_records(records: list[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    return tuple(sorted((dict(record) for record in records), key=lambda record: json.dumps(record, sort_keys=True)))


def _read_collection(context: Any, name: str, **kwargs: object) -> tuple[Any, ...]:
    reader = getattr(context, name, None)
    if not callable(reader):
        raise LongBridgeReconciliationReadError(
            f"LongBridge reconciliation requires read-only {name} support."
        )
    try:
        value = reader(**kwargs)
    except Exception as exc:
        raise LongBridgeReconciliationReadError(
            f"LongBridge reconciliation could not read {name}."
        ) from exc
    if not isinstance(value, (list, tuple)):
        raise LongBridgeReconciliationReadError(
            f"LongBridge reconciliation received invalid {name} data."
        )
    return tuple(value)


def _normalize_position(position: Any, *, account_channel: str) -> dict[str, object]:
    symbol = _text(getattr(position, "symbol", "")).upper()
    currency = _text(getattr(position, "currency", "")).upper()
    if not symbol or not currency:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation received an incomplete position.")
    return {
        "account_channel": account_channel,
        "symbol": symbol,
        "currency": currency,
        "quantity": _decimal_text(
            getattr(position, "quantity", None), field_name="position quantity"
        ),
    }


def _normalize_cash(balance: Any) -> dict[str, object]:
    cash_infos = getattr(balance, "cash_infos", None)
    if not isinstance(cash_infos, (list, tuple)) or not cash_infos:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation received incomplete cash data.")
    cash_detail = []
    for cash_info in cash_infos:
        cash_currency = _text(getattr(cash_info, "currency", "")).upper()
        if not cash_currency:
            raise LongBridgeReconciliationReadError("LongBridge reconciliation received cash without currency.")
        cash_detail.append(
            {
                "currency": cash_currency,
                "available_cash": _decimal_text(
                    getattr(cash_info, "available_cash", None), field_name="available cash"
                ),
                "frozen_cash": _decimal_text(
                    getattr(cash_info, "frozen_cash", None), field_name="frozen cash"
                ),
                "settling_cash": _decimal_text(
                    getattr(cash_info, "settling_cash", None), field_name="settling cash"
                ),
            }
        )
    return {"cash_infos": list(_canonical_records(cash_detail))}


def _normalize_order(order: Any) -> dict[str, object]:
    order_id = _text(getattr(order, "order_id", ""))
    status = _enum_name(getattr(order, "status", ""))
    symbol = _text(getattr(order, "symbol", "")).upper()
    currency = _text(getattr(order, "currency", "")).upper()
    if not order_id or not status or not symbol or not currency:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation received an incomplete order.")
    return {
        "order_id": order_id,
        "status": status,
        "symbol": symbol,
        "currency": currency,
        "side": _enum_name(getattr(order, "side", "")),
        "order_type": _enum_name(getattr(order, "order_type", "")),
        "quantity": _decimal_text(getattr(order, "quantity", None), field_name="order quantity"),
        "executed_quantity": _decimal_text(
            getattr(order, "executed_quantity", None), field_name="order executed quantity"
        ),
        "submitted_at": _text(getattr(order, "submitted_at", "")),
        "updated_at": _text(getattr(order, "updated_at", "")),
    }


def _normalize_execution(execution: Any) -> dict[str, object]:
    order_id = _text(getattr(execution, "order_id", ""))
    trade_id = _text(getattr(execution, "trade_id", ""))
    symbol = _text(getattr(execution, "symbol", "")).upper()
    if not order_id or not trade_id or not symbol:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation received an incomplete execution.")
    return {
        "order_id": order_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "trade_done_at": _text(getattr(execution, "trade_done_at", "")),
        "quantity": _decimal_text(
            getattr(execution, "quantity", None), field_name="execution quantity"
        ),
        "price": _decimal_text(getattr(execution, "price", None), field_name="execution price"),
    }


@dataclass(frozen=True)
class LongBridgeReconciliationObservations:
    """Sensitive in-memory broker observations; never serialize them to HTTP."""

    account_scope: Mapping[str, object]
    account_identity_match: bool
    positions: tuple[Mapping[str, object], ...]
    cash: tuple[Mapping[str, object], ...]
    open_orders: tuple[Mapping[str, object], ...]
    recent_executions: tuple[Mapping[str, object], ...]
    positions_complete: bool
    cash_complete: bool
    open_orders_complete: bool
    recent_executions_complete: bool


@dataclass(frozen=True)
class LongBridgeReconciliationCandidate:
    evidence: BrokerReconciliationEvidence
    recovery_blockers: tuple[BrokerReconciliationFinding, ...]
    expected_digests_configured: bool
    execution_ledger_records_count: int

    @property
    def permits_active_lkg(self) -> bool:
        return not self.recovery_blockers

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": "longbridge_reconciliation_candidate.v1",
            "permits_active_lkg": self.permits_active_lkg,
            "expected_digests_configured": self.expected_digests_configured,
            "execution_ledger_records_count": self.execution_ledger_records_count,
            "recovery_blockers": [finding.value for finding in self.recovery_blockers],
            "evidence": self.evidence.to_dict(),
        }


def collect_read_only_reconciliation_observations(
    _quote_context: Any,
    trade_context: Any,
    *,
    account_scope: str,
    now: datetime | None = None,
    lookback: timedelta = timedelta(days=7),
) -> LongBridgeReconciliationObservations:
    """Read only documented SDK surfaces; never construct or invoke an order port.

    LongBridge exposes only partial account-channel evidence and no documented
    all-open-orders query. The resulting candidate records both limitations
    as unmatched, so this reader cannot unlock a frozen baseline.
    """

    scope = _text(account_scope).upper()
    if scope not in SUPPORTED_ACCOUNT_SCOPES or lookback <= timedelta(0):
        raise LongBridgeReconciliationReadError("LongBridge reconciliation scope is invalid.")
    reference_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    balances = _read_collection(trade_context, "account_balance")
    positions_reader = getattr(trade_context, "stock_positions", None)
    if not callable(positions_reader):
        raise LongBridgeReconciliationReadError("LongBridge reconciliation requires read-only stock_positions support.")
    try:
        stock_positions = positions_reader()
    except Exception as exc:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation could not read stock_positions.") from exc
    channels = getattr(stock_positions, "channels", None)
    if not isinstance(channels, (list, tuple)) or not channels:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation received incomplete positions.")
    orders = _read_collection(
        trade_context,
        "history_orders",
        start_at=reference_now - lookback,
        end_at=reference_now,
    )
    executions = _read_collection(
        trade_context,
        "history_executions",
        start_at=reference_now - lookback,
        end_at=reference_now,
    )
    # The history endpoint excludes today's trades; the SDK hides has_more.
    today_executions = _read_collection(trade_context, "today_executions")
    normalized_executions: dict[str, dict[str, object]] = {}
    for execution in (*executions, *today_executions):
        traded_at = getattr(execution, "trade_done_at", None)
        if not isinstance(traded_at, datetime):
            raise LongBridgeReconciliationReadError("LongBridge reconciliation received an invalid execution time.")
        # longport 3.0.23 converts Unix timestamps to local naive datetimes.
        traded_at = traded_at.astimezone(timezone.utc)
        if not reference_now - lookback <= traded_at <= reference_now:
            continue
        record = _normalize_execution(execution)
        record["trade_done_at"] = traded_at.isoformat()
        trade_id = str(record["trade_id"])
        previous = normalized_executions.get(trade_id)
        if previous is not None and previous != record:
            raise LongBridgeReconciliationReadError("LongBridge reconciliation received conflicting executions.")
        normalized_executions[trade_id] = record
    account_channels: list[str] = []
    normalized_positions: list[dict[str, object]] = []
    for channel in channels:
        account_channel = _text(getattr(channel, "account_channel", "")).lower()
        raw_positions = getattr(channel, "positions", None)
        if not account_channel or not isinstance(raw_positions, (list, tuple)):
            raise LongBridgeReconciliationReadError("LongBridge reconciliation received an incomplete position channel.")
        account_channels.append(account_channel)
        normalized_positions.extend(
            _normalize_position(position, account_channel=account_channel) for position in raw_positions
        )
    normalized_orders = [_normalize_order(order) for order in orders]
    open_orders = [order for order in normalized_orders if order["status"] not in _TERMINAL_ORDER_STATUSES]
    return LongBridgeReconciliationObservations(
        account_scope={"configured_scope": scope, "account_channels": sorted(set(account_channels))},
        # The SDK exposes account channels, not a stable account identity or paper/live marker.
        account_identity_match=False,
        positions=_canonical_records(normalized_positions),
        cash=_canonical_records([_normalize_cash(balance) for balance in balances]),
        open_orders=_canonical_records(open_orders),
        recent_executions=_canonical_records(list(normalized_executions.values())),
        positions_complete=True,
        cash_complete=True,
        # A bounded history query cannot prove every long-lived active order is present.
        open_orders_complete=False,
        recent_executions_complete=len(executions) < 1000,
    )


def _expected_digests(
    *, env_reader: Callable[[str, str | None], str | None] = os.getenv
) -> Mapping[str, str] | None:
    raw = _text(env_reader(EXPECTED_DIGESTS_ENV_NAME, None))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation expected digests are invalid.") from exc
    if not isinstance(value, Mapping) or set(value) != set(_EXPECTED_DIGEST_KEYS):
        raise LongBridgeReconciliationReadError("LongBridge reconciliation expected digests are incomplete.")
    normalized = {key: _text(value[key]).lower().removeprefix("sha256:") for key in _EXPECTED_DIGEST_KEYS}
    if any(not _SHA256_PATTERN.fullmatch(digest) for digest in normalized.values()):
        raise LongBridgeReconciliationReadError("LongBridge reconciliation expected digests are invalid.")
    return normalized


def _continuity_fields(runtime_target: Any) -> tuple[str, str, str]:
    continuity = getattr(runtime_target, "live_continuity", None)
    baseline_id = _text(getattr(continuity, "baseline_id", ""))
    baseline_target_sha256 = _text(getattr(continuity, "baseline_target_sha256", "")).lower()
    if (
        _text(getattr(continuity, "state", "")).upper() != "RECONCILE_ONLY"
        or not baseline_id
        or not _SHA256_PATTERN.fullmatch(baseline_target_sha256)
    ):
        raise LongBridgeReconciliationReadError("LongBridge reconciliation requires a frozen runtime target.")
    return baseline_id, baseline_target_sha256, baseline_target_sha256


def build_reconciliation_candidate(
    *,
    observations: LongBridgeReconciliationObservations,
    runtime_target: Any,
    project_id: str | None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
    observed_at: datetime | None = None,
) -> LongBridgeReconciliationCandidate:
    """Build an immutable redacted candidate; no baseline always remains blocked."""

    expected = _expected_digests(env_reader=env_reader)
    platform_id = _text(getattr(runtime_target, "platform_id", ""))
    strategy_profile = _text(getattr(runtime_target, "strategy_profile", ""))
    account_scope = _text(getattr(runtime_target, "account_scope", ""))
    if platform_id != "longbridge" or not strategy_profile or account_scope.upper() not in SUPPORTED_ACCOUNT_SCOPES:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation runtime target is incomplete.")
    baseline_id, baseline_target_sha256, runtime_target_sha256 = _continuity_fields(runtime_target)
    digests = {
        "account_scope_sha256": calculate_broker_observation_sha256(observations.account_scope),
        "positions_sha256": calculate_broker_observation_sha256(observations.positions),
        "cash_sha256": calculate_broker_observation_sha256(observations.cash),
        "open_orders_sha256": calculate_broker_observation_sha256(observations.open_orders),
        "recent_executions_sha256": calculate_broker_observation_sha256(observations.recent_executions),
    }
    marker_store = build_execution_marker_store_from_env(
        platform_env_prefix="LONGBRIDGE", env_reader=env_reader, project_id=project_id
    )
    ledger_digest, records_count = marker_store.calculate_recent_ledger_digest(
        platform=platform_id,
        strategy_profile=strategy_profile,
        account_scope=account_scope,
        execution_mode=_text(getattr(runtime_target, "execution_mode", "")),
    )
    digests["local_execution_ledger_sha256"] = ledger_digest
    timestamp = observed_at or datetime.now(timezone.utc)

    def matches(key: str, complete: bool = True) -> bool:
        return bool(complete and expected is not None and expected[key] == digests[key])

    evidence = build_broker_reconciliation_evidence(
        platform_id=platform_id,
        strategy_profile=strategy_profile,
        account_scope_sha256=digests["account_scope_sha256"],
        baseline_id=baseline_id,
        baseline_target_sha256=baseline_target_sha256,
        runtime_target_sha256=runtime_target_sha256,
        observed_at=timestamp,
        broker_connected=True,
        account_identity_match=observations.account_identity_match,
        positions_match=matches("positions_sha256", observations.positions_complete),
        cash_match=matches("cash_sha256", observations.cash_complete),
        open_orders_match=matches("open_orders_sha256", observations.open_orders_complete),
        recent_executions_match=matches(
            "recent_executions_sha256", observations.recent_executions_complete
        ),
        local_execution_ledger_match=matches("local_execution_ledger_sha256"),
        **{key: value for key, value in digests.items() if key != "account_scope_sha256"},
    )
    blockers = evaluate_broker_reconciliation_recovery(
        evidence,
        now=timestamp,
        expected_platform_id=platform_id,
        expected_strategy_profile=strategy_profile,
        expected_account_scope_sha256=(expected or {}).get("account_scope_sha256"),
        expected_baseline_id=baseline_id,
        expected_runtime_target_sha256=runtime_target_sha256,
        expected_positions_sha256=(expected or {}).get("positions_sha256"),
        expected_cash_sha256=(expected or {}).get("cash_sha256"),
        expected_open_orders_sha256=(expected or {}).get("open_orders_sha256"),
        expected_recent_executions_sha256=(expected or {}).get("recent_executions_sha256"),
        expected_local_execution_ledger_sha256=(expected or {}).get("local_execution_ledger_sha256"),
    )
    return LongBridgeReconciliationCandidate(
        evidence=evidence,
        recovery_blockers=blockers,
        expected_digests_configured=expected is not None,
        execution_ledger_records_count=records_count,
    )


def validate_reconciliation_candidate(candidate: object) -> dict[str, object]:
    """Accept only the canonical public-safe QPK receipt."""

    try:
        payload = candidate.to_safe_dict()
        evidence = BrokerReconciliationEvidence.from_dict(payload["evidence"])
    except Exception as exc:
        raise LongBridgeReconciliationReadError("LongBridge reconciliation receipt is invalid.") from exc
    if set(payload) != _SAFE_CANDIDATE_KEYS or payload.get("schema_version") != "longbridge_reconciliation_candidate.v1":
        raise LongBridgeReconciliationReadError("LongBridge reconciliation receipt is invalid.")
    if evidence.platform_id != "longbridge":
        raise LongBridgeReconciliationReadError("LongBridge reconciliation receipt is invalid.")
    normalized = dict(payload)
    normalized["evidence"] = evidence.to_dict()
    return normalized


def run_read_only_broker_reconciliation(
    *,
    enabled: bool,
    account_scope: object,
    runtime_target: object,
    strategy_profile: str,
    project_id: str | None,
    build_read_only_contexts: Callable[[], tuple[Any, Any]],
    collect_evidence: Callable[..., LongBridgeReconciliationObservations] | None,
    env_reader: Callable[[str, str | None], str | None] = os.getenv,
) -> tuple[dict[str, object], int]:
    """Collect one explicit redacted receipt without creating an execution port."""

    scope = _text(account_scope).upper()
    if not enabled:
        return {"status": "blocked", "reason": "broker_reconciliation_disabled"}, 503
    if scope not in SUPPORTED_ACCOUNT_SCOPES:
        return {"status": "blocked", "reason": "broker_reconciliation_account_scope_unsupported"}, 503
    if not callable(collect_evidence):
        return {"status": "blocked", "reason": "broker_reconciliation_collector_unavailable"}, 503
    if not callable(build_read_only_contexts):
        return {"status": "blocked", "reason": "broker_reconciliation_context_builder_unavailable"}, 503
    if runtime_target is None:
        return {"status": "blocked", "reason": "broker_reconciliation_runtime_target_unavailable"}, 503
    if (
        _text(getattr(runtime_target, "platform_id", "")).lower() != "longbridge"
        or _text(getattr(runtime_target, "account_scope", "")).upper() != scope
        or _text(getattr(runtime_target, "strategy_profile", "")) != _text(strategy_profile)
    ):
        return {"status": "blocked", "reason": "broker_reconciliation_runtime_target_unavailable"}, 503
    try:
        _continuity_fields(runtime_target)
    except LongBridgeReconciliationReadError:
        return {"status": "blocked", "reason": "broker_reconciliation_runtime_target_unavailable"}, 503
    try:
        quote_context, trade_context = build_read_only_contexts()
        observations = collect_evidence(quote_context, trade_context, account_scope=scope)
        candidate = build_reconciliation_candidate(
            observations=observations,
            runtime_target=runtime_target,
            project_id=project_id,
            env_reader=env_reader,
        )
        return validate_reconciliation_candidate(candidate), 200
    except Exception:
        return {"status": "blocked", "reason": "broker_reconciliation_collection_failed"}, 503


__all__ = [
    "ENABLED_ENV_NAME",
    "EXPECTED_DIGESTS_ENV_NAME",
    "LongBridgeReconciliationCandidate",
    "LongBridgeReconciliationObservations",
    "LongBridgeReconciliationReadError",
    "build_reconciliation_candidate",
    "collect_read_only_reconciliation_observations",
    "reconciliation_enabled",
    "run_read_only_broker_reconciliation",
    "validate_reconciliation_candidate",
]
