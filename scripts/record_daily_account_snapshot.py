#!/usr/bin/env python3
"""Record one paper account snapshot under an account_snapshots object prefix.

The daily heartbeat may call this script. It stays idle unless recording is
explicitly enabled, and it never follows redirects, retries, or overwrites.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit


HISTORY_SCHEMA = "longbridge_account_snapshot_history.v1"
SNAPSHOT_SCHEMA = "longbridge_account_snapshot.v1"
SOURCE_KIND = "deployment_scope_token_version"
EXPECTED_SCOPE = "PAPER"
OBSERVATION_WINDOW = timedelta(minutes=15)
HTTP_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 256 * 1024
MAX_ACCOUNT_FACTS_SYNC_BODY_BYTES = 64 * 1024
MAX_ACCOUNT_FACTS_SYNC_RESPONSE_BYTES = 64 * 1024
ACCOUNT_FACTS_SYNC_PATH = "/api/account-facts/sync"
ACCOUNT_FACTS_SYNC_TIMEOUT_SECONDS = 20
_RUN_APP_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+run\.app$")
_HTTPS_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
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
    publish_status: str = "disabled"
    publish_category: str = ""


class _Rejected(Exception):
    def __init__(self, category: str) -> None:
        self.category = category


@dataclass(frozen=True)
class _Config:
    audience: str
    snapshot_url: str
    prefix: str
    target_id: str
    project_id: str


def record_daily_account_snapshot(
    env: Mapping[str, str],
    *,
    fetch_id_token: Callable[[str], str],
    http_get: Callable[..., Any],
    open_store: Callable[[str], Any],
    now_reader: Callable[[], datetime],
    http_post: Callable[..., Any] | None = None,
) -> DailyAccountRecordResult:
    """Validate one snapshot response and create the first object for that day."""

    if str(env.get("ACCOUNT_HISTORY_RECORDING_ENABLED") or "").strip() != "true":
        return DailyAccountRecordResult("disabled", publish_status="disabled")
    try:
        sync_config = _account_facts_sync_config(env)
    except _Rejected as rejected:
        return DailyAccountRecordResult(
            "error", rejected.category, "rejected", rejected.category
        )
    try:
        config = _config(env)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    try:
        token = fetch_id_token(config.audience)
    except Exception:
        return DailyAccountRecordResult("error", "token_unavailable")
    if not isinstance(token, str) or not token.strip():
        return DailyAccountRecordResult("error", "token_unavailable")
    try:
        response = http_get(
            config.snapshot_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception:
        return DailyAccountRecordResult("error", "http_failed")
    if getattr(response, "status_code", None) != 200 or getattr(response, "is_redirect", False):
        return DailyAccountRecordResult("error", "http_failed")
    try:
        observed_at = now_reader()
        body, uri = _history_object(response, config=config, now=observed_at)
    except _Rejected as rejected:
        return DailyAccountRecordResult("error", rejected.category)
    try:
        created = open_store(config.project_id).create_text(uri, body, "application/json")
    except Exception:
        return DailyAccountRecordResult(
            "error", "store_unknown", "skipped_store_unknown"
        )
    if created is True:
        publish_status, publish_category = _publish_account_facts(
            sync_config, body, http_post=http_post or _http_post
        )
        return DailyAccountRecordResult(
            "recorded", "", publish_status, publish_category
        )
    if created is False:
        return DailyAccountRecordResult(
            "already_recorded", "", "skipped_already_recorded"
        )
    return DailyAccountRecordResult(
        "error", "store_unknown", "skipped_store_unknown"
    )


def _publish_account_facts(
    sync_config: tuple[str, str] | None,
    body: str,
    *,
    http_post: Callable[..., Any],
) -> tuple[str, str]:
    if sync_config is None:
        return "disabled", ""
    url, token = sync_config

    encoded_body = body.encode("utf-8")
    if len(encoded_body) > MAX_ACCOUNT_FACTS_SYNC_BODY_BYTES:
        return "rejected", "qrs_payload_too_large"
    try:
        response = http_post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=encoded_body,
            timeout=ACCOUNT_FACTS_SYNC_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        return "unknown", "qrs_request_unknown"

    try:
        status_code = getattr(response, "status_code", None)
        if not isinstance(status_code, int):
            return "unknown", "qrs_response_invalid"
        if getattr(response, "is_redirect", False) or 300 <= status_code < 400:
            return "rejected", "qrs_redirect_rejected"
        if 400 <= status_code < 500:
            return "rejected", "qrs_http_rejected"
        if status_code >= 500 or not 200 <= status_code < 300:
            return "unknown", "qrs_response_unknown"
        try:
            payload = _bounded_response_json(
                response, MAX_ACCOUNT_FACTS_SYNC_RESPONSE_BYTES
            )
        except _Rejected as rejected:
            return "unknown", rejected.category
        if isinstance(payload, dict) and payload.get("ok") is False:
            return "rejected", "qrs_application_rejected"
        try:
            sent = json.loads(body)
        except json.JSONDecodeError:
            return "unknown", "qrs_request_body_invalid"
        if (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("stored") is True
            and payload.get("target_id") == sent.get("target_id")
            and payload.get("observation_date") == sent.get("observation_date")
            and payload.get("observed_finished_at")
            == sent.get("observed_finished_at")
        ):
            return "published", ""
        return "unknown", "qrs_response_unconfirmed"
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


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


def _account_facts_sync_config(env: Mapping[str, str]) -> tuple[str, str] | None:
    if str(env.get("ACCOUNT_FACTS_SYNC_ENABLED") or "").strip() != "true":
        return None
    url = _account_facts_sync_url(str(env.get("ACCOUNT_FACTS_SYNC_URL") or ""))
    token = str(env.get("ACCOUNT_FACTS_SYNC_TOKEN") or "")
    if not token or token != token.strip():
        raise _Rejected("qrs_config_invalid")
    return url, token


def _bounded_response_json(response: Any, max_bytes: int) -> Any:
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
                if total > max_bytes:
                    raise _Rejected("qrs_response_too_large")
                chunks.append(chunk)
        except _Rejected:
            raise
        except Exception:
            raise _Rejected("qrs_response_invalid") from None
        content = b"".join(chunks)
    else:
        content = getattr(response, "content", b"")
        if not isinstance(content, bytes) or len(content) > max_bytes:
            raise _Rejected("qrs_response_too_large")
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Rejected("qrs_response_invalid") from None


def _config(env: Mapping[str, str]) -> _Config:
    audience, snapshot_url = _service_url(str(env.get("ACCOUNT_HISTORY_SERVICE_URL") or ""))
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
    return _Config(
        audience=audience,
        snapshot_url=snapshot_url,
        prefix=prefix,
        target_id=target_id,
        project_id=project_id,
    )


def _service_url(value: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise _Rejected("config_invalid") from None
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
    origin = f"https://{host}"
    return origin, f"{origin}/account-snapshot"


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
        or any(
            part in segment.lower()
            for segment in segments
            for part in _FORBIDDEN_PREFIX_PARTS
        )
    ):
        raise _Rejected("config_invalid")
    return f"gs://{parsed.netloc}/{'/'.join(segments)}"


def _history_object(response: Any, *, config: _Config, now: datetime) -> tuple[str, str]:
    content = getattr(response, "content", b"")
    if not isinstance(content, (bytes, bytearray)) or len(content) > MAX_RESPONSE_BYTES:
        raise _Rejected("response_invalid")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _Rejected("response_invalid") from None
    if not isinstance(payload, dict):
        raise _Rejected("response_invalid")
    _require_snapshot_contract(payload)
    binding_id = _binding_id(payload.get("source_binding"))
    started = _aware(payload.get("observed_started_at"))
    finished = _aware(payload.get("observed_finished_at"))
    if now.tzinfo is None or now.utcoffset() is None:
        raise _Rejected("response_invalid")
    current = now.astimezone(timezone.utc)
    if finished < started or finished > current or started < current - OBSERVATION_WINDOW:
        raise _Rejected("response_invalid")
    record = {
        "schema_version": HISTORY_SCHEMA,
        "snapshot_schema_version": SNAPSHOT_SCHEMA,
        "account_scope": EXPECTED_SCOPE,
        "target_id": config.target_id,
        "source_binding": {
            "kind": SOURCE_KIND,
            "status": "bound",
            "id": binding_id,
        },
        "observed_started_at": started.isoformat(),
        "observed_finished_at": finished.isoformat(),
        "snapshot_atomic": False,
        "observation_date": started.date().isoformat(),
        "broker_reported_balances": _money_rows(
            payload.get("broker_reported_balances"), _BALANCE_FIELDS
        ),
        "cash": _money_rows(payload.get("cash"), _CASH_FIELDS),
    }
    body = json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    uri = f"{config.prefix}/{config.target_id}/{binding_id}/{record['observation_date']}.json"
    return body, uri


def _require_snapshot_contract(payload: Mapping[str, Any]) -> None:
    if (
        payload.get("schema_version") != SNAPSHOT_SCHEMA
        or payload.get("status") != "partial"
        or payload.get("account_scope") != EXPECTED_SCOPE
        or payload.get("positions_complete") is not True
        or payload.get("cash_complete") is not True
        or payload.get("no_order") is not True
        or payload.get("live_authority_granted") is not False
        or payload.get("snapshot_atomic") is not False
    ):
        raise _Rejected("response_invalid")


def _binding_id(value: object) -> str:
    if not isinstance(value, Mapping):
        raise _Rejected("response_invalid")
    binding_id = value.get("id")
    if (
        value.get("kind") != SOURCE_KIND
        or value.get("status") != "bound"
        or not isinstance(binding_id, str)
        or _BINDING_ID.fullmatch(binding_id) is None
    ):
        raise _Rejected("response_invalid")
    return binding_id


def _aware(value: object) -> datetime:
    if not isinstance(value, str):
        raise _Rejected("response_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _Rejected("response_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _Rejected("response_invalid")
    return parsed.astimezone(timezone.utc)


def _money_rows(value: object, fields: tuple[str, ...]) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise _Rejected("response_invalid")
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise _Rejected("response_invalid")
        row: dict[str, str] = {}
        for field in fields:
            cell = item.get(field)
            if field == "currency":
                if not isinstance(cell, str) or _CURRENCY.fullmatch(cell) is None or cell in seen:
                    raise _Rejected("response_invalid")
                seen.add(cell)
            elif (
                isinstance(cell, bool)
                or not isinstance(cell, str)
                or _DECIMAL_TEXT.fullmatch(cell) is None
                or not Decimal(cell).is_finite()
            ):
                raise _Rejected("response_invalid")
            row[field] = cell
        rows.append(row)
    return rows


def _fetch_id_token(audience: str) -> str:
    """Read one WIF identity token from gcloud. Stdout stays in memory."""

    import subprocess

    command = [
        "gcloud",
        "auth",
        "print-identity-token",
        f"--audiences={audience}",
        "--quiet",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=HTTP_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _Rejected("token_unavailable") from None
    if completed.returncode != 0:
        raise _Rejected("token_unavailable")
    token = completed.stdout.decode("utf-8", "replace").strip()
    if not token:
        raise _Rejected("token_unavailable")
    return token


def _http_get(url: str, *, headers: Mapping[str, str], timeout: float):
    import requests

    return requests.get(url, headers=dict(headers), timeout=timeout, allow_redirects=False)


def _http_post(
    url: str,
    *,
    headers: Mapping[str, str],
    data: bytes,
    timeout: float,
    allow_redirects: bool,
    stream: bool,
):
    import requests

    return requests.post(
        url,
        headers=dict(headers),
        data=data,
        timeout=timeout,
        allow_redirects=allow_redirects,
        stream=stream,
    )


def _open_store(project_id: str):
    from quant_platform_kit.cloud import get_object_store

    return get_object_store(project_id=project_id)


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    fetch_id_token: Callable[[str], str] | None = None,
    http_get: Callable[..., Any] | None = None,
    http_post: Callable[..., Any] | None = None,
    open_store: Callable[[str], Any] | None = None,
    now_reader: Callable[[], datetime] | None = None,
) -> int:
    """Record from the environment. Extra arguments are rejected without I/O."""

    args = sys.argv[1:] if argv is None else argv
    if args:
        print("error: config_invalid")
        return 1
    result = record_daily_account_snapshot(
        os.environ if environ is None else environ,
        fetch_id_token=fetch_id_token or _fetch_id_token,
        http_get=http_get or _http_get,
        http_post=http_post or _http_post,
        open_store=open_store or _open_store,
        now_reader=now_reader or (lambda: datetime.now(timezone.utc)),
    )
    record_part = (
        f"record=error:{result.category}"
        if result.status == "error"
        else f"record={result.status}"
    )
    publish_part = f"account_facts_publish={result.publish_status}"
    print(f"{record_part} {publish_part}")
    if result.status == "error" or result.publish_status in {"rejected", "unknown"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
