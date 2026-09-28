#!/usr/bin/env python3
"""Create one paper daily account object from an in-process balance projection.

Recording stays idle unless it is explicitly enabled. This module does not
call the HTTP account snapshot, and a failed create is not retried.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from google.api_core.exceptions import Conflict, PreconditionFailed


HISTORY_SCHEMA = "longbridge_account_snapshot_history.v1"
SOURCE_KIND = "deployment_scope_token_version"
EXPECTED_SCOPE = "PAPER"
HISTORY_WRITE_TIMEOUT_SECONDS = 20.0
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_TARGET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_BINDING_ID = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_FORBIDDEN_PREFIX_PARTS = ("execution-report", "execution_report", "runtime-report")
_CASH_FIELDS = ("currency", "available_cash", "frozen_cash", "settling_cash")
_BALANCE_FIELDS = ("currency", "net_assets", "total_cash")


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
    """Create the first object for this day. Existing or unknown results stay put."""

    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return _logged(DailyAccountRecordResult("disabled"))
    if str(account_scope or "").strip().upper() != EXPECTED_SCOPE:
        return _logged(DailyAccountRecordResult("skipped", "scope"))
    try:
        config = _config(env)
        body, uri = _history_object(
            config=config,
            source_binding=source_binding,
            balances=balances,
            cash=cash,
            started=started,
            finished=finished,
        )
    except _Rejected as rejected:
        return _logged(DailyAccountRecordResult("skipped", rejected.category))
    try:
        store = open_store(config.project_id)
        created = _create_bounded(store, uri, body)
    except _Rejected as rejected:
        return _logged(DailyAccountRecordResult("skipped", rejected.category))
    except Exception:
        return _logged(DailyAccountRecordResult("error", "store_unknown"))
    if created is True:
        return _logged(DailyAccountRecordResult("recorded"))
    if created is False:
        return _logged(DailyAccountRecordResult("already_recorded"))
    return _logged(DailyAccountRecordResult("error", "store_unknown"))


def _config(env: Mapping[str, str]) -> _Config:
    prefix = _gcs_prefix(str(env.get("ACCOUNT_HISTORY_GCS_PREFIX") or ""))
    target_id = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    scope = str(env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") or "").strip()
    project_id = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if (
        _TARGET_ID.fullmatch(target_id) is None
        or scope != EXPECTED_SCOPE
        or _PROJECT_ID.fullmatch(project_id) is None
    ):
        raise _Rejected("config_invalid")
    return _Config(prefix=prefix, target_id=target_id, project_id=project_id)


def _gcs_prefix(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("config_invalid") from None
    segments = [segment for segment in parsed.path.split("/") if segment]
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
        or any(segment in {".", ".."} for segment in segments)
        or any(part in segment.lower() for segment in segments for part in _FORBIDDEN_PREFIX_PARTS)
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
    if started.tzinfo is None or started.utcoffset() is None:
        raise _Rejected("observation_invalid")
    if finished.tzinfo is None or finished.utcoffset() is None:
        raise _Rejected("observation_invalid")
    started_utc = started.astimezone(timezone.utc)
    finished_utc = finished.astimezone(timezone.utc)
    if finished_utc < started_utc:
        raise _Rejected("observation_invalid")
    record = {
        "schema_version": HISTORY_SCHEMA,
        "account_scope": EXPECTED_SCOPE,
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
    uri = f"{config.prefix}/{config.target_id}/{binding_id}/{record['observation_date']}.json"
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
                or _DECIMAL_TEXT.fullmatch(cell) is None
                or not Decimal(cell).is_finite()
            ):
                raise _Rejected("projection_invalid")
            row[field] = cell
        rows.append(row)
    return rows


def _logged(result: DailyAccountRecordResult) -> DailyAccountRecordResult:
    category = result.category or "-"
    print(f"account_history status={result.status} category={category}", flush=True)
    return result


def _create_bounded(store: Any, uri: str, body: str) -> bool:
    """Upload once through the store client. Unsupported clients skip."""

    client = getattr(store, "client", None)
    parse_uri = getattr(store, "_parse_uri", None)
    if client is None or not callable(parse_uri):
        raise _Rejected("store_unbounded")
    try:
        bucket_name, blob_name = parse_uri(uri)
        blob = client.bucket(bucket_name).blob(blob_name)
    except Exception as exc:
        raise _Rejected("store_unbounded") from exc
    upload = getattr(blob, "upload_from_string", None)
    if not callable(upload) or not _accepts_bounds(upload):
        raise _Rejected("store_unbounded")
    try:
        upload(
            body,
            content_type="application/json",
            if_generation_match=0,
            timeout=HISTORY_WRITE_TIMEOUT_SECONDS,
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
    return all(_accepts(parameters, name) for name in ("if_generation_match", "timeout", "retry"))


def _accepts(parameters: Mapping[str, inspect.Parameter], name: str) -> bool:
    if name in parameters:
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
