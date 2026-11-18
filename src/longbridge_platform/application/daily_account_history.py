"""Bounded daily account snapshot history producer helpers."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from longbridge_platform.application.account_financing import (
    FinancingPayloadError,
    validate_financing_payload,
)
from longbridge_platform.application.account_snapshot import project_broker_cost_price

HISTORY_SCHEMA = "longbridge_account_snapshot_history.v1"
SNAPSHOT_SCHEMA = "longbridge_account_snapshot.v1"
SOURCE_KIND = "deployment_scope_token_version"
OBSERVATION_WINDOW = timedelta(minutes=15)

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_TARGET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_BINDING_ID = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_POSITION_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9./ -]{0,31}$")
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_FORBIDDEN_PREFIX_PARTS = ("execution-report", "execution_report", "runtime-report")
_BALANCE_FIELDS = ("currency", "net_assets", "total_cash")
_CASH_FIELDS = ("currency", "available_cash", "frozen_cash", "settling_cash")
_PRODUCER_WRITE_TIMEOUT_SECONDS = 20.0
_PRODUCER_DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_HISTORY_POSITION_FIELDS = frozenset(
    {
        "broker_reported_positions",
        "broker_reported_positions_scope",
        "broker_reported_positions_complete",
    }
)


@dataclass(frozen=True)
class DailyAccountRecordResult:
    """Result of evaluating or persisting a daily account snapshot record."""

    status: str
    category: str = ""
    publish_status: str = "disabled"
    publish_category: str = ""


class _Rejected(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


@dataclass(frozen=True)
class _ProducerConfig:
    prefix: str
    target_id: str
    account_scope: str
    project_id: str


def record_projected_daily_account(
    env: Mapping[str, str],
    *,
    account_scope: str,
    source_binding: Mapping[str, Any],
    balances: Any,
    cash: Any,
    started: datetime,
    finished: datetime,
    open_store: Callable[[str], Any],
    financing: Any = None,
    positions: Any = None,
) -> DailyAccountRecordResult:
    """Create one bounded history object from an already-read broker response."""
    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return DailyAccountRecordResult("skipped", "disabled")
    if str(account_scope or "").strip() not in {"HK", "SG"}:
        return DailyAccountRecordResult("skipped", "scope")
    try:
        config = _producer_config(env)
        if account_scope != config.account_scope:
            raise _Rejected("scope")
        body, uri = _producer_history_object(
            config,
            source_binding,
            balances,
            cash,
            started,
            finished,
            financing=financing,
            positions=positions,
        )
    except _Rejected as rejected:
        return DailyAccountRecordResult("skipped", rejected.category)
    try:
        created = _producer_create_bounded(open_store(config.project_id), uri, body)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        return DailyAccountRecordResult("error", "store_unknown")
    if created is True:
        return DailyAccountRecordResult("recorded")
    return DailyAccountRecordResult(
        "error",
        "object_conflict" if created is False else "store_unknown",
    )


def _producer_config(env: Mapping[str, str]) -> _ProducerConfig:
    prefix = _producer_gcs_prefix(str(env.get("ACCOUNT_HISTORY_GCS_PREFIX") or ""))
    scope = str(env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") or "").strip()
    target = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    project = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if (
        scope not in {"HK", "SG"}
        or target != scope.lower()
        or not _TARGET_ID.fullmatch(target)
        or not _PROJECT_ID.fullmatch(project)
    ):
        raise _Rejected("config_invalid")
    return _ProducerConfig(prefix, target, scope, project)


def _producer_gcs_prefix(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("config_invalid") from None
    segments = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "gs"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port is not None
        or _BUCKET.fullmatch(parsed.netloc or "") is None
        or not segments
        or segments[-1] != "account_snapshots"
        or any(part in {".", ".."} for part in segments)
        or any(denied in part.lower() for part in segments for denied in _FORBIDDEN_PREFIX_PARTS)
    ):
        raise _Rejected("config_invalid")
    return f"gs://{parsed.netloc}/{'/'.join(segments)}"


def _independent_position_rows(value: object) -> list[dict[str, Any]]:
    """Retain optional native configured cost separately from accounting average cost."""
    if not isinstance(value, list) or len(value) > 64:
        raise _Rejected("snapshot_invalid")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("snapshot_invalid")
        symbol, currency, quantity = (
            item.get("symbol"),
            item.get("currency"),
            item.get("quantity"),
        )
        if (
            not isinstance(symbol, str)
            or _POSITION_SYMBOL.fullmatch(symbol) is None
            or not isinstance(currency, str)
            or _CURRENCY.fullmatch(currency) is None
            or isinstance(quantity, bool)
            or not isinstance(quantity, str)
            or _DECIMAL_TEXT.fullmatch(quantity) is None
        ):
            raise _Rejected("snapshot_invalid")
        whole, _, fraction = quantity.lstrip("-").partition(".")
        if len(whole) > 15 or len(fraction) > 8 or not Decimal(quantity).is_finite():
            raise _Rejected("snapshot_invalid")
        if symbol in seen:
            raise _Rejected("snapshot_invalid")
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "currency": currency,
                "market_value": None,
            }
        )
        if "broker_cost_price" in item or "broker_cost_method" in item:
            cost = item.get("broker_cost_price")
            if (
                not isinstance(cost, str)
                or project_broker_cost_price(cost) is None
                or item.get("broker_cost_method") != "broker_configured"
            ):
                raise _Rejected("snapshot_invalid")
            rows[-1].update(broker_cost_price=cost, broker_cost_method="broker_configured")
    return sorted(rows, key=lambda row: (row["currency"], row["symbol"]))


def _history_positions_valid(payload: Mapping[str, Any]) -> bool:
    present = set(payload) & _HISTORY_POSITION_FIELDS
    if not present:
        return True
    if (
        present != _HISTORY_POSITION_FIELDS
        or payload.get("broker_reported_positions_scope") != "stocks_only"
        or payload.get("broker_reported_positions_complete") is not True
    ):
        return False
    try:
        return payload["broker_reported_positions"] == _independent_position_rows(
            payload["broker_reported_positions"]
        )
    except _Rejected:
        return False


def _producer_history_object(
    config: _ProducerConfig,
    source_binding: Mapping[str, Any],
    balances: Any,
    cash: Any,
    started: datetime,
    finished: datetime,
    financing: Any = None,
    positions: Any = None,
) -> tuple[str, str]:
    if (
        not isinstance(source_binding, Mapping)
        or source_binding.get("kind") != SOURCE_KIND
        or source_binding.get("status") != "bound"
        or not isinstance(source_binding.get("id"), str)
        or not _BINDING_ID.fullmatch(source_binding["id"])
    ):
        raise _Rejected("source_unbound")
    if any(
        not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None
        for value in (started, finished)
    ):
        raise _Rejected("observation_invalid")
    started, finished = started.astimezone(timezone.utc), finished.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if finished < started or finished > now or started < now - OBSERVATION_WINDOW:
        raise _Rejected("observation_invalid")
    balance_rows = _producer_money_rows(balances, _BALANCE_FIELDS)
    record: dict[str, Any] = {
        "schema_version": HISTORY_SCHEMA,
        "snapshot_schema_version": SNAPSHOT_SCHEMA,
        "account_scope": config.account_scope,
        "target_id": config.target_id,
        "source_binding": {"kind": SOURCE_KIND, "status": "bound", "id": source_binding["id"]},
        "observed_started_at": started.isoformat(),
        "observed_finished_at": finished.isoformat(),
        "snapshot_atomic": False,
        "observation_date": started.date().isoformat(),
        "broker_reported_balances": balance_rows,
        "cash": _producer_money_rows(cash, _CASH_FIELDS),
    }
    if financing is not None:
        record["financing"] = _producer_financing_rows(
            financing,
            {row["currency"] for row in balance_rows},
        )
    if positions is not None:
        record.update(
            broker_reported_positions=_independent_position_rows(positions),
            broker_reported_positions_scope="stocks_only",
            broker_reported_positions_complete=True,
        )
    body = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    uri = (
        f"{config.prefix}/{config.target_id}/{source_binding['id']}/"
        f"{record['observation_date']}/{finished.strftime('%H%M%S%fZ.json')}"
    )
    return body, uri


def _producer_financing_rows(value: Any, balance_currencies: set[str]) -> list[dict[str, str]]:
    try:
        return validate_financing_payload(value, balance_currencies=balance_currencies)
    except FinancingPayloadError as exc:
        raise _Rejected("projection_invalid") from exc


def _producer_money_rows(value: Any, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise _Rejected("projection_invalid")
    rows, seen = [], set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("projection_invalid")
        row: dict[str, str] = {}
        for field in fields:
            cell = item.get(field)
            if field == "currency":
                if not isinstance(cell, str) or not _CURRENCY.fullmatch(cell) or cell in seen:
                    raise _Rejected("projection_invalid")
                seen.add(cell)
            elif (
                isinstance(cell, bool)
                or not isinstance(cell, str)
                or not _PRODUCER_DECIMAL.fullmatch(cell)
                or not Decimal(cell).is_finite()
            ):
                raise _Rejected("projection_invalid")
            row[field] = cell
        rows.append(row)
    return rows


def _producer_create_bounded(store: Any, uri: str, body: str) -> bool:
    from google.api_core.exceptions import Conflict, PreconditionFailed

    client, parse_uri = getattr(store, "client", None), getattr(store, "_parse_uri", None)
    if client is None or not callable(parse_uri):
        raise _Rejected("store_unbounded")
    try:
        bucket, name = parse_uri(uri)
        blob = client.bucket(bucket).blob(name)
        upload = blob.upload_from_string
    except Exception:
        raise _Rejected("store_unbounded") from None
    try:
        params = inspect.signature(upload).parameters
    except (TypeError, ValueError):
        raise _Rejected("store_unbounded") from None
    if not all(
        name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        for name in ("if_generation_match", "timeout", "retry")
    ):
        raise _Rejected("store_unbounded")
    try:
        upload(
            body,
            content_type="application/json",
            if_generation_match=0,
            timeout=_PRODUCER_WRITE_TIMEOUT_SECONDS,
            retry=None,
        )
    except (Conflict, PreconditionFailed):
        return False
    return True
