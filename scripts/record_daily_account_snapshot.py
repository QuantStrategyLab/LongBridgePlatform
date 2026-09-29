"""Create one bounded GCS object from a captured HK or SG account observation."""

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


HISTORY_SCHEMA = "longbridge_account_snapshot_history.v1"
SNAPSHOT_SCHEMA = "longbridge_account_snapshot.v1"
SOURCE_KIND = "deployment_scope_token_version"
WRITE_TIMEOUT_SECONDS = 20.0
OBSERVATION_WINDOW = timedelta(minutes=15)
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_TARGET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BINDING_ID = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_FORBIDDEN_PREFIX_PARTS = ("execution-report", "execution_report", "runtime-report")
_BALANCE_FIELDS = ("currency", "net_assets", "total_cash")
_CASH_FIELDS = ("currency", "available_cash", "frozen_cash", "settling_cash")


@dataclass(frozen=True)
class DailyAccountRecordResult:
    status: str
    category: str = ""


class _Rejected(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


@dataclass(frozen=True)
class _Config:
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
) -> DailyAccountRecordResult:
    """Validate one HK/SG observation and confirm its create-only write."""

    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return DailyAccountRecordResult("skipped", "disabled")
    if str(account_scope or "").strip() not in {"HK", "SG"}:
        return DailyAccountRecordResult("skipped", "scope")
    try:
        config = _config(env)
        if str(account_scope or "").strip() != config.account_scope:
            raise _Rejected("scope")
        body, uri = _history_object(
            config=config,
            source_binding=source_binding,
            balances=balances,
            cash=cash,
            started=started,
            finished=finished,
        )
    except _Rejected as rejected:
        return DailyAccountRecordResult("skipped", rejected.category)
    try:
        created = _create_bounded(open_store(config.project_id), uri, body)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        return DailyAccountRecordResult("error", "store_unknown")
    if created is True:
        return DailyAccountRecordResult("recorded")
    if created is False:
        return DailyAccountRecordResult("error", "object_conflict")
    return DailyAccountRecordResult("error", "store_unknown")


def _config(env: Mapping[str, str]) -> _Config:
    prefix = _gcs_prefix(str(env.get("ACCOUNT_HISTORY_GCS_PREFIX") or ""))
    target_id = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    account_scope = str(env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") or "").strip()
    project_id = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if (
        account_scope not in {"HK", "SG"}
        or target_id != account_scope.lower()
        or _TARGET_ID.fullmatch(target_id) is None
        or _PROJECT_ID.fullmatch(project_id) is None
    ):
        raise _Rejected("config_invalid")
    return _Config(
        prefix=prefix,
        target_id=target_id,
        account_scope=account_scope,
        project_id=project_id,
    )


def _gcs_prefix(value: str) -> str:
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
        or any(
            denied in segment.lower()
            for segment in segments
            for denied in _FORBIDDEN_PREFIX_PARTS
        )
    ):
        raise _Rejected("config_invalid")
    return f"gs://{parsed.netloc}/{'/'.join(segments)}"


def _history_object(
    *,
    config: _Config,
    source_binding: Mapping[str, Any],
    balances: Any,
    cash: Any,
    started: datetime,
    finished: datetime,
) -> tuple[str, str]:
    binding_id = _binding_id(source_binding)
    if not isinstance(started, datetime) or started.tzinfo is None or started.utcoffset() is None:
        raise _Rejected("observation_invalid")
    if not isinstance(finished, datetime) or finished.tzinfo is None or finished.utcoffset() is None:
        raise _Rejected("observation_invalid")
    started_utc = started.astimezone(timezone.utc)
    finished_utc = finished.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if (
        finished_utc < started_utc
        or finished_utc > now
        or started_utc < now - OBSERVATION_WINDOW
    ):
        raise _Rejected("observation_invalid")
    record = {
        "schema_version": HISTORY_SCHEMA,
        "snapshot_schema_version": SNAPSHOT_SCHEMA,
        "account_scope": config.account_scope,
        "target_id": config.target_id,
        "source_binding": {
            "kind": SOURCE_KIND,
            "status": "bound",
            "id": binding_id,
        },
        "observed_started_at": started_utc.isoformat(),
        "observed_finished_at": finished_utc.isoformat(),
        "snapshot_atomic": False,
        "observation_date": started_utc.date().isoformat(),
        "broker_reported_balances": _money_rows(balances, _BALANCE_FIELDS),
        "cash": _money_rows(cash, _CASH_FIELDS),
    }
    body = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    filename = finished_utc.strftime("%H%M%S%fZ.json")
    uri = (
        f"{config.prefix}/{config.target_id}/{binding_id}/"
        f"{record['observation_date']}/{filename}"
    )
    return body, uri


def _binding_id(value: object) -> str:
    if not isinstance(value, Mapping):
        raise _Rejected("source_unbound")
    binding_id = value.get("id")
    if (
        value.get("kind") != SOURCE_KIND
        or value.get("status") != "bound"
        or not isinstance(binding_id, str)
        or _BINDING_ID.fullmatch(binding_id) is None
    ):
        raise _Rejected("source_unbound")
    return binding_id


def _money_rows(value: object, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise _Rejected("projection_invalid")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("projection_invalid")
        row: dict[str, str] = {}
        for field in fields:
            cell = item.get(field)
            if field == "currency":
                if not isinstance(cell, str) or _CURRENCY.fullmatch(cell) is None or cell in seen:
                    raise _Rejected("projection_invalid")
                seen.add(cell)
            elif (
                isinstance(cell, bool)
                or not isinstance(cell, str)
                or _DECIMAL.fullmatch(cell) is None
                or not Decimal(cell).is_finite()
            ):
                raise _Rejected("projection_invalid")
            row[field] = cell
        rows.append(row)
    return rows


def _create_bounded(store: Any, uri: str, body: str) -> bool:
    """Upload once with an explicit timeout, no retry, and no-overwrite guard."""

    from google.api_core.exceptions import Conflict, PreconditionFailed

    client = getattr(store, "client", None)
    parse_uri = getattr(store, "_parse_uri", None)
    if client is None or not callable(parse_uri):
        raise _Rejected("store_unbounded")
    try:
        bucket_name, blob_name = parse_uri(uri)
        blob = client.bucket(bucket_name).blob(blob_name)
    except Exception:
        raise _Rejected("store_unbounded") from None
    upload = getattr(blob, "upload_from_string", None)
    if not callable(upload) or not _accepts_bounds(upload):
        raise _Rejected("store_unbounded")
    try:
        upload(
            body,
            content_type="application/json",
            if_generation_match=0,
            timeout=WRITE_TIMEOUT_SECONDS,
            retry=None,
        )
    except (Conflict, PreconditionFailed):
        return False
    return True


def _accepts_bounds(upload: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(upload).parameters
    except (TypeError, ValueError):
        return False
    return all(
        name in parameters
        or any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        for name in ("if_generation_match", "timeout", "retry")
    )
