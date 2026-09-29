#!/usr/bin/env python3
"""Trigger one internal PAPER probe and sync its bounded GCS observation."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit


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
    try:
        config = _config(env)
        sync_config = _account_facts_sync_config(env)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category, "rejected", rejected.category)

    session = None
    try:
        session = session_factory()
        job = _scheduler_get(session, config)
        _validate_scheduler_job(job, config)
    except _Rejected as rejected:
        _close(session)
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        _close(session)
        return DailyAccountRecordResult("error", "scheduler_read_failed")
    try:
        started_at = _utc_now(now_reader)
        _scheduler_run(session, config)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    except Exception:
        # The request may have reached Scheduler. Never trigger it a second time.
        return DailyAccountRecordResult("error", "scheduler_run_unknown")
    finally:
        _close(session)

    try:
        store = open_store(config.project_id)
        client = _storage_client(store)
        deadline = monotonic() + WAIT_SECONDS
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
            return DailyAccountRecordResult("error", "observation_timeout")
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


def _validate_scheduler_job(job: Mapping[str, Any], config: _Config) -> None:
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
    if (
        job.get("name") != config.scheduler_resource
        or job.get("state") != "ENABLED"
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
    if (
        set(payload) != expected_fields
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
) -> int:
    args = sys.argv[1:] if argv is None else argv
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
