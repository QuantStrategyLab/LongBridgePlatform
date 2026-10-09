#!/usr/bin/env python3
"""Trigger one internal LongBridge probe and sync its bounded GCS observation."""

from __future__ import annotations

import json
import inspect
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HISTORY_SCHEMA = "longbridge_account_snapshot_history.v1"
SNAPSHOT_SCHEMA = "longbridge_account_snapshot.v1"
SOURCE_KIND = "deployment_scope_token_version"
ACCOUNT_FACTS_SYNC_PATH = "/api/account-facts/sync"
_EXPECTED_TARGETS = {"paper": ("PAPER", "paper"), "hk": ("HK", "live"), "sg": ("SG", "live")}
_EXPECTED_GCS_PREFIX = "gs://qsl-runtime-logs-shared/longbridge/account_snapshots"
SCHEDULER_SERVICE_ACCOUNT = "longbridge-platform-scheduler@longbridgequant.iam.gserviceaccount.com"
OBSERVATION_WINDOW = timedelta(minutes=15)
WAIT_SECONDS = 180.0
POLL_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 20.0
GCS_TIMEOUT_SECONDS = 15.0
MAX_OBJECTS_PER_DAY = 64
MAX_OBJECT_BYTES = 64 * 1024
ARCHIVE_INSPECTION_MAX_DURATION = timedelta(minutes=5)
MAX_QRS_BODY_BYTES = 64 * 1024
MAX_QRS_RESPONSE_BYTES = 64 * 1024
_RUN_APP_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.run\.app$")
_HTTPS_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_TARGET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_BINDING_ID = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DECIMAL_TEXT = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")
_FILENAME = re.compile(r"^\d{12}Z\.json$")
_FORBIDDEN_PREFIX_PARTS = ("execution-report", "execution_report", "runtime-report")
_BALANCE_FIELDS = ("currency", "net_assets", "total_cash")
_CASH_FIELDS = ("currency", "available_cash", "frozen_cash", "settling_cash")
# Restricted resume→run→pause is allowed only for these live targets when
# RUNTIME_TARGET_ENABLED is exactly false. Cron matches platform hk_daily / us_daily.
_PAUSED_PROBE_CONTRACTS = {
    "sg": ("35 9,15 * * 1-5", "America/New_York"),
    "hk": ("35 9,15 * * 1-5", "Asia/Hong_Kong"),
}
_PAUSED_PROBE_HOURS = (9, 15)
_PAUSED_PROBE_MINUTE = 35
_PAUSED_PROBE_SCHEDULE_GUARD = timedelta(minutes=10)
_SG_PROBE_REQUIRED_PERMISSIONS = (
    "cloudscheduler.jobs.get",
    "cloudscheduler.jobs.run",
    "cloudscheduler.jobs.enable",
    "cloudscheduler.jobs.pause",
)
_SCHEDULER_OUTPUT_FIELDS = frozenset({
    "state", "status", "lastAttemptTime", "scheduleTime", "userUpdateTime", "updateTime", "etag",
})
_PRODUCER_WRITE_TIMEOUT_SECONDS = 20.0
_PRODUCER_DECIMAL = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


@dataclass(frozen=True)
class DailyAccountRecordResult:
    status: str
    category: str = ""
    publish_status: str = "disabled"
    publish_category: str = ""


class _Rejected(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


@dataclass(frozen=True)
class _Config:
    project_id: str
    region: str
    service: str
    service_url: str
    prefix: str
    bucket: str
    prefix_path: str
    target_id: str
    expected_scope: str
    source_binding_id: str
    scheduler_job: str
    scheduler_resource: str


@dataclass(frozen=True)
class _ProducerConfig:
    prefix: str
    target_id: str
    account_scope: str
    project_id: str


def record_projected_daily_account(
    env: Mapping[str, str], *, account_scope: str, source_binding: Mapping[str, Any],
    balances: Any, cash: Any, started: datetime, finished: datetime,
    open_store: Callable[[str], Any],
    financing: Any = None,
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
            config, source_binding, balances, cash, started, finished, financing=financing,
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
    return DailyAccountRecordResult("error", "object_conflict" if created is False else "store_unknown")


def _producer_config(env: Mapping[str, str]) -> _ProducerConfig:
    prefix = _producer_gcs_prefix(str(env.get("ACCOUNT_HISTORY_GCS_PREFIX") or ""))
    scope = str(env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") or "").strip()
    target = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    project = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if scope not in {"HK", "SG"} or target != scope.lower() or not _TARGET_ID.fullmatch(target) or not _PROJECT_ID.fullmatch(project):
        raise _Rejected("config_invalid")
    return _ProducerConfig(prefix, target, scope, project)


def _producer_gcs_prefix(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("config_invalid") from None
    segments = [part for part in parsed.path.split("/") if part]
    if (parsed.scheme != "gs" or parsed.username or parsed.password or parsed.query or parsed.fragment or port is not None
            or _BUCKET.fullmatch(parsed.netloc or "") is None or not segments or segments[-1] != "account_snapshots"
            or any(part in {".", ".."} for part in segments)
            or any(denied in part.lower() for part in segments for denied in _FORBIDDEN_PREFIX_PARTS)):
        raise _Rejected("config_invalid")
    return f"gs://{parsed.netloc}/{'/'.join(segments)}"


def _producer_history_object(config, source_binding, balances, cash, started, finished, financing=None):
    if (not isinstance(source_binding, Mapping) or source_binding.get("kind") != SOURCE_KIND
            or source_binding.get("status") != "bound" or not isinstance(source_binding.get("id"), str)
            or not _BINDING_ID.fullmatch(source_binding["id"])):
        raise _Rejected("source_unbound")
    if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None for value in (started, finished)):
        raise _Rejected("observation_invalid")
    started, finished = started.astimezone(timezone.utc), finished.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if finished < started or finished > now or started < now - OBSERVATION_WINDOW:
        raise _Rejected("observation_invalid")
    balance_rows = _producer_money_rows(balances, _BALANCE_FIELDS)
    record = {
        "schema_version": HISTORY_SCHEMA, "snapshot_schema_version": SNAPSHOT_SCHEMA,
        "account_scope": config.account_scope, "target_id": config.target_id,
        "source_binding": {"kind": SOURCE_KIND, "status": "bound", "id": source_binding["id"]},
        "observed_started_at": started.isoformat(), "observed_finished_at": finished.isoformat(),
        "snapshot_atomic": False, "observation_date": started.date().isoformat(),
        "broker_reported_balances": balance_rows,
        "cash": _producer_money_rows(cash, _CASH_FIELDS),
    }
    if financing is not None:
        record["financing"] = _producer_financing_rows(
            financing, {row["currency"] for row in balance_rows},
        )
    body = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    uri = f"{config.prefix}/{config.target_id}/{source_binding['id']}/{record['observation_date']}/{finished.strftime('%H%M%S%fZ.json')}"
    return body, uri


def _producer_financing_rows(value, balance_currencies):
    from application.account_financing import FinancingPayloadError, validate_financing_payload

    try:
        return validate_financing_payload(value, balance_currencies=balance_currencies)
    except FinancingPayloadError as exc:
        raise _Rejected("projection_invalid") from exc


def _producer_money_rows(value, fields):
    if not isinstance(value, list) or not value:
        raise _Rejected("projection_invalid")
    rows, seen = [], set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("projection_invalid")
        row = {}
        for field in fields:
            cell = item.get(field)
            if field == "currency":
                if not isinstance(cell, str) or not _CURRENCY.fullmatch(cell) or cell in seen:
                    raise _Rejected("projection_invalid")
                seen.add(cell)
            elif (isinstance(cell, bool) or not isinstance(cell, str) or not _PRODUCER_DECIMAL.fullmatch(cell)
                  or not Decimal(cell).is_finite()):
                raise _Rejected("projection_invalid")
            row[field] = cell
        rows.append(row)
    return rows


def _producer_create_bounded(store, uri, body):
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
    if not all(name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
               for name in ("if_generation_match", "timeout", "retry")):
        raise _Rejected("store_unbounded")
    try:
        upload(body, content_type="application/json", if_generation_match=0,
               timeout=_PRODUCER_WRITE_TIMEOUT_SECONDS, retry=None)
    except (Conflict, PreconditionFailed):
        return False
    return True



@dataclass(frozen=True)
class _IndependentConfig:
    project_id: str
    region: str
    service: str
    service_url: str
    source_binding_id: str


def _independent_hk_config(env: Mapping[str, str], *, require_id_token: bool = True) -> _IndependentConfig:
    """This first opt-in is manual HK observation with the existing identity only."""
    if (
        env.get("ACCOUNT_HISTORY_OBSERVATION_MODE") != "independent_get"
        or env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") != "true"
        or env.get("ACCOUNT_HISTORY_TARGET_ID") != "hk"
        or env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") != "HK"
        or env.get("GOOGLE_CLOUD_PROJECT") != "longbridgequant"
        or env.get("RUNTIME_TARGET_ENABLED") != "false"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("ACCOUNT_HISTORY_REQUESTED_TARGET") != "hk"
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_WORKFLOW_REF") != (
            "QuantStrategyLab/LongBridgePlatform/.github/workflows/"
            "execution-report-heartbeat.yml@refs/heads/main"
        )
    ):
        raise _Rejected("independent_config_invalid")
    binding = str(env.get("ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID") or "")
    token = str(env.get("ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN") or "")
    if not _BINDING_ID.fullmatch(binding) or (
        require_id_token and (not token or len(token) > 16384 or any(c.isspace() for c in token))
    ):
        raise _Rejected("independent_config_invalid")
    from application.runtime_target_manifest import load_runtime_target_manifest

    targets = [target for target in load_runtime_target_manifest().targets if target.id == "hk"]
    if len(targets) != 1 or targets[0].mode != "live" or targets[0].account_scope != "HK":
        raise _Rejected("independent_config_invalid")
    target = targets[0]
    if env.get("SNAPSHOT_SERVICE") != target.service or env.get("SNAPSHOT_REGION") != target.region:
        raise _Rejected("independent_config_invalid")
    raw_url = env.get("ACCOUNT_HISTORY_SERVICE_URL")
    service_url = _service_url(str(raw_url or ""), service=target.service, region=target.region)
    # Unlike the legacy Scheduler route, this exact string is also the token audience.
    if raw_url != service_url:
        raise _Rejected("independent_config_invalid")
    return _IndependentConfig(
        "longbridgequant", target.region, target.service,
        service_url, binding,
    )


def _validate_independent_service(value: object, config: _IndependentConfig) -> None:
    """Validate in-memory gcloud service readback before sending its audience token."""
    try:
        if not isinstance(value, Mapping):
            raise TypeError
        metadata, status = value["metadata"], value["status"]
        annotations = metadata["annotations"]
        template = value["spec"]["template"]
        containers = template["spec"]["containers"]
        generation = metadata["generation"]
        observed_generation = status["observedGeneration"]
        ready = [row for row in status["conditions"] if row.get("type") == "Ready"]
        traffic = status["traffic"]
        revision = status["latestReadyRevisionName"]
        if (
            metadata["name"] != config.service
            or str(metadata["namespace"]) not in {config.project_id, "252919773759"}
            or metadata["labels"]["cloud.googleapis.com/location"] != config.region
            or annotations.get("run.googleapis.com/ingress") != "all"
            or annotations.get("run.googleapis.com/ingress-status", "all") != "all"
            or status["url"] != config.service_url
            or len(ready) != 1 or ready[0].get("status") != "True"
            or not isinstance(revision, str) or not revision.startswith(config.service + "-")
            or type(generation) is not int or generation < 1
            or type(observed_generation) is not int or observed_generation != generation
            or status["latestCreatedRevisionName"] != revision
            or template["metadata"]["name"] != revision
            or not isinstance(traffic, list) or len(traffic) != 1
            or type(traffic[0].get("percent")) is not int or traffic[0]["percent"] != 100
            or traffic[0].get("revisionName") != revision or traffic[0].get("tag")
            or not isinstance(containers, list) or len(containers) != 1
        ):
            raise ValueError
        snapshot_flags = [
            row for row in containers[0].get("env", [])
            if row.get("name") == "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED"
        ]
        if len(snapshot_flags) != 1 or snapshot_flags[0].get("value") != "true" or "valueFrom" in snapshot_flags[0]:
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise _Rejected("independent_service_unqualified") from None


def _independent_money_rows(value: object, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise _Rejected("snapshot_invalid")
    if any(not isinstance(row, Mapping) or set(row) != set(fields) for row in value):
        raise _Rejected("snapshot_invalid")
    rows = _money_rows(value, fields)
    # Match the existing QRS primary-money contract without rounding native facts.
    for row in rows:
        for field in fields:
            if field == "currency":
                continue
            whole, _, fraction = row[field].lstrip("-").partition(".")
            if len(whole) > 15 or len(fraction) > 8:
                raise _Rejected("snapshot_invalid")
    return rows


def _project_independent_history(
    payload: object, config: _IndependentConfig, started_at: datetime, now: datetime,
) -> dict[str, Any]:
    """Discard private positions/orders; retain only the existing history contract."""
    if not isinstance(payload, Mapping):
        raise _Rejected("snapshot_invalid")
    binding = payload.get("source_binding")
    if (
        payload.get("schema_version") != SNAPSHOT_SCHEMA
        or payload.get("status") != "partial"
        or payload.get("account_scope") != "HK"
        or payload.get("no_order") is not True
        or payload.get("live_authority_granted") is not False
        or payload.get("snapshot_atomic") is not False
        or payload.get("cash_complete") is not True
        or payload.get("positions_complete") is not True
        or not isinstance(binding, Mapping)
        or set(binding) != {"kind", "status", "id"}
        or binding.get("kind") != SOURCE_KIND or binding.get("status") != "bound"
        or binding.get("id") != config.source_binding_id
    ):
        raise _Rejected("snapshot_invalid")
    try:
        started = _aware(payload.get("observed_started_at"))
        finished = _aware(payload.get("observed_finished_at"))
        # Preserve the recorder's existing strict no-future and 15-minute policy.
        if not started_at <= started <= finished <= now or not _is_fresh(payload, now):
            raise _Rejected("snapshot_invalid")
        balances = _independent_money_rows(payload.get("broker_reported_balances"), _BALANCE_FIELDS)
        cash = _independent_money_rows(payload.get("cash"), _CASH_FIELDS)
        record = {
            "schema_version": HISTORY_SCHEMA, "snapshot_schema_version": SNAPSHOT_SCHEMA,
            "account_scope": "HK", "target_id": "hk",
            "source_binding": {"kind": SOURCE_KIND, "status": "bound", "id": config.source_binding_id},
            "observed_started_at": payload["observed_started_at"],
            "observed_finished_at": payload["observed_finished_at"],
            "snapshot_atomic": False, "observation_date": started.date().isoformat(),
            "broker_reported_balances": balances, "cash": cash,
        }
        if "financing" in payload:
            record["financing"] = _producer_financing_rows(
                payload["financing"], {row["currency"] for row in balances},
            )
        return record
    except _Rejected:
        raise _Rejected("snapshot_invalid") from None


def observe_independent_hk_account_snapshot(
    env: Mapping[str, str], *, service_metadata: object, http_get: Callable[..., Any],
    http_post: Callable[..., Any], now_reader: Callable[[], datetime],
) -> DailyAccountRecordResult:
    """One qualified GET and optional QRS POST; never Scheduler, GCS or strategy."""
    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return DailyAccountRecordResult("disabled")
    try:
        config = _independent_hk_config(env)
        sync_config = _account_facts_sync_config(env)
        _validate_independent_service(service_metadata, config)
        started_at = _utc_now(now_reader)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:  # noqa: BLE001 - fail closed without private provider details
        return DailyAccountRecordResult("error", "independent_config_invalid")
    response = None
    try:
        response = http_get(
            config.service_url + "/account-snapshot",
            headers={"Authorization": f"Bearer {env['ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN']}", "Accept": "application/json"},
            timeout=HTTP_TIMEOUT_SECONDS, allow_redirects=False, stream=True,
        )
        if getattr(response, "status_code", None) != 200 or getattr(response, "is_redirect", False):
            raise _Rejected("snapshot_http_rejected")
        payload = _bounded_response_json(response)
        record = _project_independent_history(payload, config, started_at, _utc_now(now_reader))
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:  # noqa: BLE001 - an uncertain read is never retried or disclosed
        return DailyAccountRecordResult("error", "snapshot_request_unknown")
    finally:
        _close(response)
    if sync_config is None:
        return DailyAccountRecordResult("observed")
    if not _is_fresh(record, _utc_now(now_reader)):
        return DailyAccountRecordResult("observed", publish_status="rejected", publish_category="observation_stale")
    body = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    publish_status, publish_category = _publish_account_facts(sync_config, body, record, http_post=http_post)
    return DailyAccountRecordResult("observed", publish_status=publish_status, publish_category=publish_category)


def _default_http_get(url: str, **kwargs: Any):
    import requests
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=0))
    try:
        response = session.get(url, **kwargs)
    except Exception:
        session.close()
        raise
    return _OwnedResponse(response, session)


def _read_independent_service_metadata() -> object:
    # gcloud stdout is piped directly here; never save or print its private env.
    body = sys.stdin.buffer.read(256 * 1024 + 1)
    if len(body) > 256 * 1024:
        raise _Rejected("independent_service_unqualified")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Rejected("independent_service_unqualified") from None


def record_daily_account_snapshot(
    env: Mapping[str, str],
    *,
    open_store: Callable[[str], Any],
    session_factory: Callable[[], Any],
    http_post: Callable[..., Any],
    now_reader: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DailyAccountRecordResult:
    """Validate, trigger once, select a fresh same-source object, and POST once."""

    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return DailyAccountRecordResult("disabled", publish_status="disabled")
    mode = env.get("ACCOUNT_HISTORY_OBSERVATION_MODE") or "scheduler_archive"
    if mode != "scheduler_archive":
        return DailyAccountRecordResult("error", "independent_entrypoint_required" if mode == "independent_get" else "observation_mode_invalid")
    try:
        config = _config(env)
        sync_config = _account_facts_sync_config(env)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category, "rejected", rejected.category)

    session = None
    try:
        session = session_factory()
        job = _scheduler_get(session, config)
    except _Rejected as rejected:
        _close(session)
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        _close(session)
        return DailyAccountRecordResult("error", "scheduler_read_failed")

    paused_resumable = (
        config.target_id in _PAUSED_PROBE_CONTRACTS and job.get("state") == "PAUSED"
    )
    try:
        if paused_resumable:
            if env.get("RUNTIME_TARGET_ENABLED") != "false":
                raise _Rejected("runtime_target_not_disabled")
            _validate_scheduler_job(job, config, allow_paused=True)
            _validate_paused_probe_schedule(job, config.target_id, now_reader)
            selected = _run_paused_probe(
                session,
                config,
                job,
                now_reader,
                monotonic=monotonic,
                observe=lambda started_at, deadline: _observe_archived_snapshot(
                    open_store,
                    config,
                    started_at=started_at,
                    deadline=deadline,
                    now_reader=now_reader,
                    monotonic=monotonic,
                    sleep=sleep,
                ),
            )
        else:
            _validate_scheduler_job(job, config)
            started_at = _utc_now(now_reader)
            _scheduler_run(session, config)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        category = "scheduler_run_unknown" if not paused_resumable else "scheduler_transition_failed"
        return DailyAccountRecordResult("error", category)
    finally:
        _close(session)

    if not paused_resumable:
        try:
            selected = _observe_archived_snapshot(
                open_store,
                config,
                started_at=started_at,
                deadline=None,
                now_reader=now_reader,
                monotonic=monotonic,
                sleep=sleep,
            )
        except _Rejected as rejected:
            return DailyAccountRecordResult("error", rejected.category)
        except Exception:
            return DailyAccountRecordResult("error", "gcs_read_failed")

    if selected is None:
        return DailyAccountRecordResult("error", "observation_timeout")
    body, record = selected
    if sync_config is None:
        return DailyAccountRecordResult("recorded", publish_status="disabled")
    if not _is_fresh(record, _utc_now(now_reader)):
        return DailyAccountRecordResult("recorded", publish_status="rejected", publish_category="observation_stale")
    publish_status, publish_category = _publish_account_facts(
        sync_config, body, record, http_post=http_post
    )
    return DailyAccountRecordResult("recorded", "", publish_status, publish_category)


def _config(env: Mapping[str, str]) -> _Config:
    prefix, bucket, prefix_path = _gcs_prefix(str(env.get("ACCOUNT_HISTORY_GCS_PREFIX") or ""))
    project_id = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    target_id = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    expected_scope = str(env.get("ACCOUNT_HISTORY_EXPECTED_SCOPE") or "").strip()
    source_binding_id = str(env.get("ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID") or "").strip()
    target_contract = _EXPECTED_TARGETS.get(target_id)
    if (
        _PROJECT_ID.fullmatch(project_id) is None
        or project_id != "longbridgequant"
        or target_contract is None
        or _TARGET_ID.fullmatch(target_id) is None
        or expected_scope != target_contract[0]
        or prefix != _EXPECTED_GCS_PREFIX
        or _BINDING_ID.fullmatch(source_binding_id) is None
    ):
        raise _Rejected("config_invalid")
    try:
        from application.runtime_target_manifest import load_runtime_target_manifest

        matches = [target for target in load_runtime_target_manifest().targets if target.id == target_id]
    except Exception:
        raise _Rejected("config_invalid") from None
    if (
        len(matches) != 1
        or matches[0].mode != target_contract[1]
        or matches[0].account_scope != expected_scope
    ):
        raise _Rejected("config_invalid")
    service = matches[0].service
    region = matches[0].region
    service_url = _service_url(
        str(env.get("ACCOUNT_HISTORY_SERVICE_URL") or ""), service=service, region=region
    )
    scheduler_job = f"{service}-probe-scheduler"
    scheduler_resource = f"projects/{project_id}/locations/{region}/jobs/{scheduler_job}"
    return _Config(
        project_id=project_id,
        region=region,
        service=service,
        service_url=service_url,
        prefix=prefix,
        bucket=bucket,
        prefix_path=prefix_path,
        target_id=target_id,
        expected_scope=expected_scope,
        source_binding_id=source_binding_id,
        scheduler_job=scheduler_job,
        scheduler_resource=scheduler_resource,
    )


def _service_url(value: str, *, service: str, region: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("config_invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or _RUN_APP_HOST.fullmatch(host) is None
    ):
        raise _Rejected("config_invalid")
    return f"https://{host}"


def _gcs_prefix(value: str) -> tuple[str, str, str]:
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
        or any(denied in segment.lower() for segment in segments for denied in _FORBIDDEN_PREFIX_PARTS)
    ):
        raise _Rejected("config_invalid")
    path = "/".join(segments)
    return f"gs://{parsed.netloc}/{path}", parsed.netloc, path


def _authorized_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from requests.adapters import HTTPAdapter

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    session = AuthorizedSession(credentials, max_refresh_attempts=0)
    adapter = HTTPAdapter(max_retries=0)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _scheduler_get(session: Any, config: _Config) -> Mapping[str, Any]:
    response = session.get(
        f"https://cloudscheduler.googleapis.com/v1/{config.scheduler_resource}",
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        if getattr(response, "status_code", None) != 200 or getattr(response, "is_redirect", False):
            raise _Rejected("scheduler_read_failed")
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise _Rejected("scheduler_read_invalid")
        return payload
    except _Rejected:
        raise
    except Exception:
        raise _Rejected("scheduler_read_invalid") from None
    finally:
        _close(response)


def inspect_sg_probe_permissions(
    env: Mapping[str, str],
    *,
    session_factory: Callable[[], Any],
) -> dict[str, bool | str]:
    """Read fixed project-level IAM permissions for the SG probe path only."""
    target_id = str(env.get("ACCOUNT_HISTORY_TARGET_ID") or "").strip()
    project_id = str(env.get("GOOGLE_CLOUD_PROJECT") or "").strip()
    if target_id != "sg" or project_id != "longbridgequant":
        raise _Rejected("permission_inspection_target_invalid")
    session = None
    response = None
    try:
        session = session_factory()
        response = session.post(
            f"https://cloudresourcemanager.googleapis.com/v1/projects/{project_id}:testIamPermissions",
            json={"permissions": list(_SG_PROBE_REQUIRED_PERMISSIONS)},
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        status = getattr(response, "status_code", None)
        if status != 200 or getattr(response, "is_redirect", False):
            raise _Rejected("permission_inspection_unknown")
        try:
            payload = response.json()
        except Exception:
            raise _Rejected("permission_inspection_response_invalid") from None
        if not isinstance(payload, Mapping):
            raise _Rejected("permission_inspection_response_invalid")
        granted_value = payload.get("permissions", [])
        if (
            not isinstance(granted_value, list)
            or any(not isinstance(item, str) for item in granted_value)
            or len(set(granted_value)) != len(granted_value)
            or not set(granted_value).issubset(_SG_PROBE_REQUIRED_PERMISSIONS)
        ):
            raise _Rejected("permission_inspection_response_invalid")
        granted = set(granted_value)
        return {
            "context": "project",
            **{permission: permission in granted for permission in _SG_PROBE_REQUIRED_PERMISSIONS},
        }
    except _Rejected:
        raise
    except Exception:
        raise _Rejected("permission_inspection_unknown") from None
    finally:
        _close(response)
        _close(session)


def _validate_scheduler_job(
    job: Mapping[str, Any], config: _Config, *, allow_paused: bool = False
) -> None:
    target = job.get("httpTarget")
    target = target if isinstance(target, Mapping) else {}
    auth = target.get("oidcToken")
    auth = auth if isinstance(auth, Mapping) else {}
    retry = job.get("retryConfig")
    retry = retry if isinstance(retry, Mapping) else {}
    body = target.get("body")
    if isinstance(body, str):
        body_empty = not body
    elif isinstance(body, (bytes, bytearray)):
        body_empty = not body
    else:
        body_empty = body is None
    allowed_states = {"ENABLED", "PAUSED"} if allow_paused else {"ENABLED"}
    if (
        job.get("name") != config.scheduler_resource
        or job.get("state") not in allowed_states
        or target.get("httpMethod") != "POST"
        or target.get("uri") != f"{config.service_url}/probe"
        or not body_empty
        or auth.get("serviceAccountEmail") != SCHEDULER_SERVICE_ACCOUNT
        or auth.get("audience") != config.service_url
        or retry.get("retryCount", 0) != 0
        or isinstance(retry.get("retryCount", 0), bool)
        or not _zero_duration(retry.get("maxRetryDuration"))
    ):
        raise _Rejected("scheduler_job_mismatch")


def _validate_paused_probe_schedule(
    job: Mapping[str, Any], target_id: str, now_reader: Callable[[], datetime]
) -> None:
    contract = _PAUSED_PROBE_CONTRACTS.get(target_id)
    if contract is None:
        raise _Rejected("scheduler_schedule_mismatch")
    expected_schedule, expected_timezone = contract
    if job.get("schedule") != expected_schedule or job.get("timeZone") != expected_timezone:
        raise _Rejected("scheduler_schedule_mismatch")
    try:
        zone = ZoneInfo(expected_timezone)
    except ZoneInfoNotFoundError:
        raise _Rejected("scheduler_schedule_mismatch") from None
    now = _utc_now(now_reader)
    local_day = now.astimezone(zone).date()
    occurrences = []
    for offset in range(-7, 8):
        day = local_day + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for hour in _PAUSED_PROBE_HOURS:
            occurrence = datetime.combine(
                day, datetime_time(hour, _PAUSED_PROBE_MINUTE), tzinfo=zone
            )
            occurrences.append(occurrence.astimezone(timezone.utc))
    if (
        not occurrences
        or min(abs(now - item) for item in occurrences) < _PAUSED_PROBE_SCHEDULE_GUARD
    ):
        raise _Rejected("scheduler_natural_window")


def _scheduler_action(session: Any, config: _Config, action: str) -> None:
    if action not in {"pause", "resume"}:
        raise ValueError("unsupported scheduler action")
    category_prefix = f"scheduler_{action}"
    try:
        response = session.post(
            f"https://cloudscheduler.googleapis.com/v1/{config.scheduler_resource}:{action}",
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except Exception:
        raise _Rejected(f"{category_prefix}_unknown") from None
    try:
        status = getattr(response, "status_code", None)
        if getattr(response, "is_redirect", False) or not isinstance(status, int):
            raise _Rejected(f"{category_prefix}_unknown")
        if 200 <= status < 300:
            return
        if status >= 500:
            raise _Rejected(f"{category_prefix}_unknown")
        raise _Rejected(f"{category_prefix}_rejected")
    finally:
        _close(response)


def _scheduler_control_view(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in job.items()
        if key not in _SCHEDULER_OUTPUT_FIELDS
    }


def _read_scheduler_job(session: Any, config: _Config) -> Mapping[str, Any]:
    job = _scheduler_get(session, config)
    if job.get("name") != config.scheduler_resource:
        raise _Rejected("scheduler_readback_mismatch")
    return job


def _pause_and_verify(
    session: Any,
    config: _Config,
    original: Mapping[str, Any],
) -> None:
    pause_error = ""
    try:
        _scheduler_action(session, config, "pause")
    except _Rejected as rejected:
        pause_error = rejected.category
    except Exception:
        pause_error = "scheduler_pause_unknown"

    try:
        restored = _read_scheduler_job(session, config)
    except _Rejected:
        raise _Rejected(pause_error or "scheduler_restore_readback_unknown") from None
    except Exception:
        raise _Rejected(pause_error or "scheduler_restore_readback_unknown") from None
    if (
        restored.get("state") != "PAUSED"
        or _scheduler_control_view(restored) != _scheduler_control_view(original)
    ):
        raise _Rejected(pause_error or "scheduler_restore_mismatch")
    if pause_error:
        raise _Rejected(pause_error)


def _run_paused_probe(
    session: Any,
    config: _Config,
    original_job: Mapping[str, Any],
    now_reader: Callable[[], datetime],
    *,
    monotonic: Callable[[], float],
    observe: Callable[[datetime, float], tuple[bytes, dict[str, Any]] | None],
) -> tuple[bytes, dict[str, Any]] | None:
    original = deepcopy(dict(original_job))
    original_last_attempt = original.get("lastAttemptTime")
    deadline = monotonic() + WAIT_SECONDS
    try:
        _scheduler_action(session, config, "resume")
    except _Rejected as rejected:
        if rejected.category != "scheduler_resume_unknown":
            raise
        try:
            observed = _read_scheduler_job(session, config)
        except Exception:
            raise _Rejected("scheduler_resume_state_unknown") from None
        if observed.get("state") == "ENABLED":
            _pause_and_verify(session, config, original)
        elif observed.get("state") != "PAUSED":
            raise _Rejected("scheduler_resume_state_unknown")
        raise rejected

    try:
        resumed = _read_scheduler_job(session, config)
    except Exception:
        _pause_and_verify(session, config, original)
        raise _Rejected("scheduler_resume_readback_unknown") from None
    last_attempt_changed = resumed.get("lastAttemptTime") != original_last_attempt
    resumed_is_valid = (
        resumed.get("state") == "ENABLED"
        and _scheduler_control_view(resumed) == _scheduler_control_view(original)
        and not last_attempt_changed
    )
    if not resumed_is_valid:
        if resumed.get("state") == "ENABLED":
            _pause_and_verify(session, config, original)
        if last_attempt_changed:
            raise _Rejected("scheduler_unexpected_attempt")
        raise _Rejected("scheduler_resume_readback_mismatch")

    try:
        if monotonic() >= deadline:
            raise _Rejected("observation_timeout")
        started_at = _utc_now(now_reader)
        try:
            _scheduler_run(session, config)
        except _Rejected:
            raise
        except Exception:
            raise _Rejected("scheduler_run_unknown") from None
        if monotonic() >= deadline:
            raise _Rejected("observation_timeout")
        return observe(started_at, deadline)
    finally:
        _pause_and_verify(session, config, original)


def _zero_duration(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value in {"0s", "0.0s"}
    if isinstance(value, Mapping):
        return value.get("seconds", 0) == 0 and value.get("nanos", 0) == 0
    return False


def _scheduler_run(session: Any, config: _Config) -> None:
    response = session.post(
        f"https://cloudscheduler.googleapis.com/v1/{config.scheduler_resource}:run",
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        status = getattr(response, "status_code", None)
        if getattr(response, "is_redirect", False) or not isinstance(status, int):
            raise _Rejected("scheduler_run_rejected")
        if 200 <= status < 300:
            return
        if status < 500:
            raise _Rejected("scheduler_run_rejected")
        raise _Rejected("scheduler_run_unknown")
    finally:
        _close(response)


def _observe_archived_snapshot(
    open_store: Callable[[str], Any],
    config: _Config,
    *,
    started_at: datetime,
    deadline: float | None,
    now_reader: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[bytes, dict[str, Any]] | None:
    if deadline is not None and monotonic() >= deadline:
        return None
    try:
        store = open_store(config.project_id)
        if deadline is None:
            deadline = monotonic() + WAIT_SECONDS
        if monotonic() >= deadline:
            return None
        client = _storage_client(store)
        selected = _wait_for_observation(
            client,
            config,
            started_at=started_at,
            deadline=deadline,
            now_reader=now_reader,
            monotonic=monotonic,
            sleep=sleep,
        )
        if monotonic() >= deadline:
            return None
        return selected
    except _Rejected:
        raise
    except Exception:
        raise _Rejected("gcs_read_failed") from None


def _storage_client(store: Any) -> Any:
    client = getattr(store, "client", None)
    if client is None:
        raise _Rejected("gcs_config_invalid")
    return client


def _wait_for_observation(
    client: Any,
    config: _Config,
    *,
    started_at: datetime,
    deadline: float,
    now_reader: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[bytes, dict[str, Any]] | None:
    while True:
        if monotonic() >= deadline:
            return None
        now = _utc_now(now_reader)
        dates = {started_at.date(), now.date()}
        candidates = _list_candidates(
            client, config, dates, deadline=deadline, monotonic=monotonic
        )
        if monotonic() >= deadline:
            return None
        valid: list[tuple[datetime, bytes, dict[str, Any]]] = []
        for candidate in candidates:
            try:
                payload, raw = _read_candidate(
                    client, config, candidate, deadline=deadline, monotonic=monotonic
                )
                record = _validate_history_object(payload, config, candidate, started_at, now)
            except _Rejected as rejected:
                if rejected.category == "generation_changed":
                    raise
                continue
            if monotonic() >= deadline:
                return None
            valid.append((datetime.fromisoformat(record["observed_finished_at"]), raw, record))
        if valid:
            _finished, raw, record = max(valid, key=lambda entry: entry[0])
            if monotonic() < deadline and _is_fresh(record, _utc_now(now_reader)):
                return raw, record
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        sleep(min(POLL_SECONDS, remaining))


def _list_candidates(
    client: Any,
    config: _Config,
    dates: set[Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for day in sorted(dates):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise _Rejected("observation_timeout")
        prefix = f"{config.prefix_path}/{config.target_id}/{config.source_binding_id}/{day.isoformat()}/"
        try:
            blobs = client.list_blobs(
                config.bucket,
                prefix=prefix,
                max_results=MAX_OBJECTS_PER_DAY + 1,
                page_size=MAX_OBJECTS_PER_DAY + 1,
                timeout=min(GCS_TIMEOUT_SECONDS, remaining),
                retry=None,
                fields="items(name,generation,size),nextPageToken",
            )
            pages = getattr(blobs, "pages", None)
            if pages is None:
                page = list(blobs)
                has_next_page = False
            else:
                first_page = next(iter(pages), ())
                page = list(first_page)
                has_next_page = bool(getattr(blobs, "next_page_token", None))
        except Exception:
            raise _Rejected("gcs_list_failed") from None
        if monotonic() >= deadline:
            raise _Rejected("observation_timeout")
        if has_next_page or len(page) > MAX_OBJECTS_PER_DAY:
            raise _Rejected("gcs_listing_truncated")
        for blob in page:
            name = getattr(blob, "name", None)
            generation = getattr(blob, "generation", None)
            size = getattr(blob, "size", None)
            if (
                not isinstance(name, str)
                or not name.startswith(prefix)
                or not _FILENAME.fullmatch(name[len(prefix):])
                or isinstance(generation, bool)
                or not str(generation or "").isdigit()
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
            ):
                continue
            if size > MAX_OBJECT_BYTES:
                raise _Rejected("gcs_object_too_large")
            candidates.append({"name": name, "generation": int(generation), "size": size})
    if len(candidates) > MAX_OBJECTS_PER_DAY * 2:
        raise _Rejected("gcs_listing_truncated")
    return candidates


def _list_archive_inspection_candidates(
    client: Any,
    config: _Config,
    dates: set[Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> list[dict[str, Any]]:
    """List only bounded SG/date objects, including prior binding folders."""
    target_prefix = f"{config.prefix_path}/{config.target_id}/"
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _Rejected("inspection_timeout")
    try:
        iterator = client.list_blobs(
            config.bucket,
            prefix=target_prefix,
            delimiter="/",
            max_results=MAX_OBJECTS_PER_DAY + 1,
            page_size=MAX_OBJECTS_PER_DAY + 1,
            timeout=min(GCS_TIMEOUT_SECONDS, remaining),
            retry=None,
            fields="items(name,generation,size),prefixes,nextPageToken",
        )
        page = next(iter(iterator.pages), ())
        binding_prefixes = set(getattr(page, "prefixes", ()) or ())
        binding_prefixes.update(getattr(iterator, "prefixes", ()) or ())
        has_next_page = bool(getattr(iterator, "next_page_token", None))
    except Exception:
        raise _Rejected("gcs_list_failed") from None
    if monotonic() >= deadline:
        raise _Rejected("inspection_timeout")
    if has_next_page or len(binding_prefixes) > MAX_OBJECTS_PER_DAY:
        raise _Rejected("gcs_listing_truncated")

    bindings = []
    for binding_prefix in binding_prefixes:
        if not isinstance(binding_prefix, str) or not binding_prefix.startswith(target_prefix):
            continue
        binding_id = binding_prefix[len(target_prefix) :].rstrip("/")
        if _BINDING_ID.fullmatch(binding_id) and binding_prefix == f"{target_prefix}{binding_id}/":
            bindings.append(binding_id)

    candidates: list[dict[str, Any]] = []
    for day in sorted(dates):
        day_candidates: list[dict[str, Any]] = []
        for binding_id in bindings:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _Rejected("inspection_timeout")
            prefix = f"{target_prefix}{binding_id}/{day.isoformat()}/"
            try:
                iterator = client.list_blobs(
                    config.bucket,
                    prefix=prefix,
                    max_results=MAX_OBJECTS_PER_DAY + 1,
                    page_size=MAX_OBJECTS_PER_DAY + 1,
                    timeout=min(GCS_TIMEOUT_SECONDS, remaining),
                    retry=None,
                    fields="items(name,generation,size),nextPageToken",
                )
                page = list(next(iter(iterator.pages), ()))
                has_next_page = bool(getattr(iterator, "next_page_token", None))
            except Exception:
                raise _Rejected("gcs_list_failed") from None
            if monotonic() >= deadline:
                raise _Rejected("inspection_timeout")
            if has_next_page or len(page) > MAX_OBJECTS_PER_DAY:
                raise _Rejected("gcs_listing_truncated")
            for blob in page:
                name = getattr(blob, "name", None)
                generation = getattr(blob, "generation", None)
                size = getattr(blob, "size", None)
                if (
                    not isinstance(name, str)
                    or not name.startswith(prefix)
                    or not _FILENAME.fullmatch(name[len(prefix) :])
                    or isinstance(generation, bool)
                    or not str(generation or "").isdigit()
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size < 0
                ):
                    continue
                if size > MAX_OBJECT_BYTES:
                    raise _Rejected("gcs_object_too_large")
                day_candidates.append(
                    {"name": name, "generation": int(generation), "size": size}
                )
                if len(day_candidates) > MAX_OBJECTS_PER_DAY:
                    raise _Rejected("gcs_listing_truncated")
        candidates.extend(day_candidates)
    return candidates


def inspect_archived_account_snapshot(
    env: Mapping[str, str],
    *,
    target: str,
    triggered_at: str,
    completed_at: str,
    open_store: Callable[[str], Any],
    now_reader: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Inspect one existing paper or SG object set; never writes or samples."""
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise _Rejected("inspection_not_manual")
    config = _config(env)
    if (
        target not in {"paper", "sg"}
        or config.target_id != target
        or config.expected_scope != _EXPECTED_TARGETS[target][0]
    ):
        raise _Rejected("inspection_target_invalid")
    try:
        requested_after = _aware(triggered_at)
        inspection_completed = _aware(completed_at)
    except _Rejected:
        raise _Rejected("inspection_time_invalid") from None
    now = _utc_now(now_reader)
    if (
        requested_after > inspection_completed
        or inspection_completed > now
        or inspection_completed - requested_after > ARCHIVE_INSPECTION_MAX_DURATION
        or now - requested_after > timedelta(hours=24)
    ):
        raise _Rejected("inspection_time_invalid")

    store = open_store(config.project_id)
    client = _storage_client(store)
    deadline = monotonic() + WAIT_SECONDS
    candidates = _list_archive_inspection_candidates(
        client,
        config,
        {requested_after.date(), now.date()},
        deadline=deadline,
        monotonic=monotonic,
    )
    from application.account_financing import financing_shape_ok

    observations = []
    required_fields = {
        "schema_version", "snapshot_schema_version", "account_scope", "target_id",
        "source_binding", "observed_started_at", "observed_finished_at", "snapshot_atomic",
        "observation_date", "broker_reported_balances", "cash",
    }
    for candidate in candidates:
        try:
            payload, _raw = _read_candidate(
                client,
                config,
                candidate,
                deadline=deadline,
                monotonic=monotonic,
            )
        except _Rejected as rejected:
            observations.append(
                {
                    "historical_window_match": False,
                    "current_stale": False,
                    "reason": rejected.category,
                    "source_binding_matches": False,
                    "scope_matches": False,
                    "target_matches": False,
                    "schema_matches": False,
                    "trigger_time_matches": False,
                    "object_path_matches": False,
                    "observed_started_at": None,
                    "observed_finished_at": None,
                    "balance_currency_rows": 0,
                    "cash_currency_rows": 0,
                }
            )
            continue

        binding = payload.get("source_binding")
        started = _try_aware(payload.get("observed_started_at"))
        finished = _try_aware(payload.get("observed_finished_at"))
        name_parts = candidate["name"][len(config.prefix_path) + 1 :].split("/")
        source_matches = (
            len(name_parts) == 4
            and name_parts[0] == config.target_id
            and name_parts[1] == config.source_binding_id
            and isinstance(binding, Mapping)
            and binding.get("kind") == SOURCE_KIND
            and binding.get("status") == "bound"
            and binding.get("id") == config.source_binding_id
        )
        target_matches = payload.get("target_id") == config.target_id and len(name_parts) == 4 and name_parts[0] == config.target_id
        scope_matches = payload.get("account_scope") == config.expected_scope
        payload_fields = set(payload)
        schema_matches = (
            required_fields <= payload_fields <= required_fields | {"financing"}
            and payload.get("schema_version") == HISTORY_SCHEMA
            and payload.get("snapshot_schema_version") == SNAPSHOT_SCHEMA
            and payload.get("snapshot_atomic") is False
            and ("financing" not in payload or financing_shape_ok(payload.get("financing")))
        )
        trigger_time_matches = (
            started is not None and finished is not None
            and requested_after <= started <= finished <= inspection_completed
        )
        object_path_matches = False
        if started is not None and finished is not None:
            expected_name = (
                f"{config.prefix_path}/{config.target_id}/{config.source_binding_id}/"
                f"{started.date().isoformat()}/{_filename_for(finished)}"
            )
            object_path_matches = candidate.get("name") == expected_name
        started_at = _inspection_time(payload.get("observed_started_at"))
        finished_at = _inspection_time(payload.get("observed_finished_at"))
        balances = payload.get("broker_reported_balances")
        cash = payload.get("cash")
        historical_window_match = False
        historical_reason = "gcs_object_invalid"
        try:
            _validate_history_object(payload, config, candidate, requested_after, inspection_completed)
        except _Rejected as rejected:
            historical_reason = rejected.category
        else:
            historical_window_match = True
        current_stale = started is not None and started < now - OBSERVATION_WINDOW
        result = {
            "historical_window_match": historical_window_match,
            "current_stale": current_stale,
            "reason": "source_binding_mismatch" if not source_matches else historical_reason,
            "source_binding_matches": source_matches,
            "scope_matches": scope_matches,
            "target_matches": target_matches,
            "schema_matches": schema_matches,
            "trigger_time_matches": trigger_time_matches,
            "object_path_matches": object_path_matches,
            "observed_started_at": started_at,
            "observed_finished_at": finished_at,
            "balance_currency_rows": len(balances) if isinstance(balances, list) else 0,
            "cash_currency_rows": len(cash) if isinstance(cash, list) else 0,
        }
        if historical_window_match and current_stale:
            result["reason"] = "historical_match_currently_stale"
        elif historical_window_match:
            result["reason"] = "historical_match_currently_fresh"
        observations.append(result)

    return {
        "inspection": "complete",
        "target": target,
        "requested_after": requested_after.isoformat().replace("+00:00", "Z"),
        "historical_inspection_completed_at": inspection_completed.isoformat().replace("+00:00", "Z"),
        "inspected_at": now.isoformat().replace("+00:00", "Z"),
        "candidate_count": len(observations),
        "historical_window_match_count": sum(item["historical_window_match"] for item in observations),
        "currently_stale_count": sum(item["current_stale"] for item in observations),
        "source_binding_matches": any(item["source_binding_matches"] for item in observations),
        "observations": observations,
    }


def _inspection_time(value: object) -> str | None:
    try:
        return _aware(value).isoformat().replace("+00:00", "Z")
    except _Rejected:
        return None


def _try_aware(value: object) -> datetime | None:
    try:
        return _aware(value)
    except _Rejected:
        return None


def _read_candidate(
    client: Any,
    config: _Config,
    candidate: Mapping[str, Any],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[dict[str, Any], bytes]:
    blob = client.bucket(config.bucket).blob(candidate["name"], generation=candidate["generation"])
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _Rejected("observation_timeout")
    try:
        raw = blob.download_as_bytes(
            start=0,
            end=MAX_OBJECT_BYTES - 1,
            if_generation_match=candidate["generation"],
            timeout=min(GCS_TIMEOUT_SECONDS, remaining),
            retry=None,
        )
    except Exception as exc:
        text = str(exc).lower()
        if "generation" in text or "precondition" in text or "conditionnotmet" in text:
            raise _Rejected("generation_changed") from None
        raise _Rejected("gcs_read_failed") from None
    if monotonic() >= deadline:
        raise _Rejected("observation_timeout")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_OBJECT_BYTES or len(raw) != candidate["size"]:
        raise _Rejected("gcs_object_truncated")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise _Rejected("gcs_object_invalid") from None
    if not isinstance(payload, dict):
        raise _Rejected("gcs_object_invalid")
    return payload, bytes(raw)


def _validate_history_object(
    payload: Mapping[str, Any],
    config: _Config,
    candidate: Mapping[str, Any],
    triggered_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    expected_fields = {
        "schema_version", "snapshot_schema_version", "account_scope", "target_id",
        "source_binding", "observed_started_at", "observed_finished_at", "snapshot_atomic",
        "observation_date", "broker_reported_balances", "cash",
    }
    binding = payload.get("source_binding")
    payload_fields = set(payload)
    if (
        not expected_fields <= payload_fields <= expected_fields | {"financing"}
        or payload.get("schema_version") != HISTORY_SCHEMA
        or payload.get("snapshot_schema_version") != SNAPSHOT_SCHEMA
        or payload.get("account_scope") != config.expected_scope
        or payload.get("target_id") != config.target_id
        or payload.get("snapshot_atomic") is not False
        or not isinstance(binding, Mapping)
        or binding.get("kind") != SOURCE_KIND
        or binding.get("status") != "bound"
        or binding.get("id") != config.source_binding_id
    ):
        raise _Rejected("gcs_object_invalid")
    started = _aware(payload.get("observed_started_at"))
    finished = _aware(payload.get("observed_finished_at"))
    expected_name = (
        f"{config.prefix_path}/{config.target_id}/{config.source_binding_id}/"
        f"{started.date().isoformat()}/{_filename_for(finished)}"
    )
    if (
        started < triggered_at
        or finished < started
        or finished > now
        or payload.get("observation_date") != started.date().isoformat()
        or candidate["name"] != expected_name
        or not _is_fresh(payload, now)
    ):
        raise _Rejected("gcs_object_invalid")
    balances = _money_rows(payload.get("broker_reported_balances"), _BALANCE_FIELDS)
    cash = _money_rows(payload.get("cash"), _CASH_FIELDS)
    if "financing" in payload:
        from application.account_financing import FinancingPayloadError, validate_financing_payload

        try:
            validate_financing_payload(
                payload.get("financing"),
                balance_currencies={row["currency"] for row in balances},
            )
        except FinancingPayloadError as exc:
            raise _Rejected("gcs_object_invalid") from exc
    return dict(payload)


def _filename_for(finished: datetime) -> str:
    return finished.astimezone(timezone.utc).strftime("%H%M%S%fZ.json")


def _is_fresh(record: Mapping[str, Any], now: datetime) -> bool:
    try:
        started = _aware(record.get("observed_started_at"))
    except _Rejected:
        return False
    current = now.astimezone(timezone.utc)
    return started <= current and started >= current - OBSERVATION_WINDOW


def _aware(value: object) -> datetime:
    if not isinstance(value, str):
        raise _Rejected("gcs_object_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Rejected("gcs_object_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Rejected("gcs_object_invalid")
    return parsed.astimezone(timezone.utc)


def _money_rows(value: object, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise _Rejected("gcs_object_invalid")
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("gcs_object_invalid")
        row: dict[str, str] = {}
        for field in fields:
            cell = item.get(field)
            if field == "currency":
                if not isinstance(cell, str) or _CURRENCY.fullmatch(cell) is None or cell in seen:
                    raise _Rejected("gcs_object_invalid")
                seen.add(cell)
            elif (
                isinstance(cell, bool)
                or not isinstance(cell, str)
                or _DECIMAL_TEXT.fullmatch(cell) is None
                or not Decimal(cell).is_finite()
            ):
                raise _Rejected("gcs_object_invalid")
            row[field] = cell
        result.append(row)
    return result


def _account_facts_sync_config(env: Mapping[str, str]) -> tuple[str, str] | None:
    if str(env.get("ACCOUNT_FACTS_SYNC_ENABLED") or "").strip() != "true":
        return None
    url = _account_facts_sync_url(str(env.get("ACCOUNT_FACTS_SYNC_URL") or ""))
    token = str(env.get("ACCOUNT_FACTS_SYNC_TOKEN") or "")
    if not token or token != token.strip():
        raise _Rejected("qrs_config_invalid")
    return url, token


def _account_facts_sync_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("qrs_config_invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != ACCOUNT_FACTS_SYNC_PATH
        or _HTTPS_HOST.fullmatch(host) is None
    ):
        raise _Rejected("qrs_config_invalid")
    return f"https://{host}{ACCOUNT_FACTS_SYNC_PATH}"


def _publish_account_facts(
    sync_config: tuple[str, str],
    body: bytes,
    record: Mapping[str, Any],
    *,
    http_post: Callable[..., Any],
) -> tuple[str, str]:
    if len(body) > MAX_QRS_BODY_BYTES:
        return "rejected", "qrs_payload_too_large"
    url, token = sync_config
    try:
        response = http_post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=body,
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        return "unknown", "qrs_request_unknown"
    try:
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            return "unknown", "qrs_response_invalid"
        if getattr(response, "is_redirect", False) or 300 <= status < 400:
            return "rejected", "qrs_redirect_rejected"
        if 400 <= status < 500:
            return "rejected", "qrs_http_rejected"
        if status >= 500 or not 200 <= status < 300:
            return "unknown", "qrs_response_unknown"
        try:
            payload = _bounded_response_json(response)
        except _Rejected as rejected:
            return "unknown", rejected.category
        if isinstance(payload, dict) and payload.get("ok") is False:
            return "rejected", "qrs_application_rejected"
        if (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("stored") is True
            and payload.get("target_id") == record.get("target_id")
            and payload.get("observation_date") == record.get("observation_date")
            and payload.get("observed_finished_at") == record.get("observed_finished_at")
        ):
            return "published", ""
        return "unknown", "qrs_response_unconfirmed"
    finally:
        _close(response)


def _bounded_response_json(response: Any) -> Any:
    chunks: list[bytes] = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        try:
            for chunk in iterator(chunk_size=8192):
                if not chunk:
                    continue
                if not isinstance(chunk, bytes):
                    raise _Rejected("qrs_response_invalid")
                total += len(chunk)
                if total > MAX_QRS_RESPONSE_BYTES:
                    raise _Rejected("qrs_response_too_large")
                chunks.append(chunk)
        except _Rejected:
            raise
        except Exception:
            raise _Rejected("qrs_response_invalid") from None
        content = b"".join(chunks)
    else:
        content = getattr(response, "content", b"")
        if not isinstance(content, bytes) or len(content) > MAX_QRS_RESPONSE_BYTES:
            raise _Rejected("qrs_response_too_large")
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Rejected("qrs_response_invalid") from None


def _default_http_post(url: str, **kwargs: Any):
    import requests
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=0))
    try:
        response = session.post(url, **kwargs)
    except Exception:
        session.close()
        raise
    return _OwnedResponse(response, session)


class _OwnedResponse:
    def __init__(self, response: Any, session: Any) -> None:
        self._response = response
        self._session = session

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._session.close()


def _open_store(project_id: str):
    from quant_platform_kit.cloud import get_object_store

    return get_object_store(project_id=project_id)


def _utc_now(reader: Callable[[], datetime]) -> datetime:
    value = reader()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise _Rejected("clock_invalid")
    return value.astimezone(timezone.utc)


def _close(value: Any) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    open_store: Callable[[str], Any] | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_post: Callable[..., Any] | None = None,
    now_reader: Callable[[], datetime] | None = None,
    http_get: Callable[..., Any] | None = None,
    service_metadata: object = None,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--validate-independent-hk-audience"]:
        try:
            config = _independent_hk_config(os.environ if environ is None else environ, require_id_token=False)
        except Exception:  # noqa: BLE001 - never disclose invalid operational input
            print("independent_audience=error:independent_config_invalid")
            return 1
        # The workflow redirects this one verified origin to GITHUB_OUTPUT.
        print(f"service_url={config.service_url}")
        return 0
    if args in (["--validate-independent-hk-account-snapshot"], ["--independent-hk-account-snapshot"]):
        env = os.environ if environ is None else environ
        try:
            _independent_hk_config(env)
            _account_facts_sync_config(env)
            if args == ["--validate-independent-hk-account-snapshot"]:
                print("independent_config=ok")
                return 0
            metadata = _read_independent_service_metadata() if service_metadata is None else service_metadata
            result = observe_independent_hk_account_snapshot(
                env, service_metadata=metadata, http_get=http_get or _default_http_get,
                http_post=http_post or _default_http_post,
                now_reader=now_reader or (lambda: datetime.now(UTC)),
            )
        except _Rejected as rejected:
            result = DailyAccountRecordResult("error", rejected.category)
        except Exception:  # noqa: BLE001 - do not print private service metadata errors
            result = DailyAccountRecordResult("error", "independent_config_invalid")
        observation = f"error:{result.category}" if result.status == "error" else result.status
        print(f"observation={observation} account_facts_publish={result.publish_status}")
        return int(result.status == "error" or result.publish_status in {"rejected", "unknown"})
    if args == ["--inspect-sg-probe-permissions"]:
        try:
            result = inspect_sg_probe_permissions(
                os.environ if environ is None else environ,
                session_factory=session_factory or _authorized_session,
            )
        except _Rejected as rejected:
            print(f"permission_inspection=error:{rejected.category}")
            return 1
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    if (len(args) == 7 and args[0] == "--inspect-archived-account-snapshot"
            and args[1] == "--target" and args[3] == "--triggered-at"
            and args[5] == "--completed-at" and args[2] in {"paper", "sg"}):
        try:
            result = inspect_archived_account_snapshot(
                os.environ if environ is None else environ,
                target=args[2],
                triggered_at=args[4],
                completed_at=args[6],
                open_store=open_store or _open_store,
                now_reader=now_reader or (lambda: datetime.now(timezone.utc)),
            )
        except _Rejected as rejected:
            print(f"archive_inspection=error:{rejected.category}")
            return 1
        except Exception:
            print("archive_inspection=error:gcs_read_failed")
            return 1
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    if args:
        print("error: config_invalid")
        return 1
    result = record_daily_account_snapshot(
        os.environ if environ is None else environ,
        open_store=open_store or _open_store,
        session_factory=session_factory or _authorized_session,
        http_post=http_post or _default_http_post,
        now_reader=now_reader or (lambda: datetime.now(timezone.utc)),
    )
    record_part = f"record=error:{result.category}" if result.status == "error" else f"record={result.status}"
    publish_part = f"account_facts_publish={result.publish_status}"
    print(f"{record_part} {publish_part}")
    if result.status == "error" or result.publish_status in {"rejected", "unknown"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
