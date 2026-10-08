#!/usr/bin/env python3
"""Publish one PAPER daily runtime projection to the existing private bucket.

This step does not call the heartbeat command, send a notification, or query a
broker. It reads reports the heartbeat already knows how to list and writes the
unchanged projection JSON once.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from google.api_core.exceptions import Forbidden, GoogleAPICallError, PreconditionFailed

from scripts import execution_report_heartbeat as heartbeat
from scripts.daily_runtime_projection import project_listed_reports
from scripts.runtime_heartbeat_policy import (
    canonical_serving_route,
    load_runtime_targets,
    resolve_bound_scheduler,
)

_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_MAX_REPORTS = 20
_LOOKBACK_HOURS = 36.0
_LIST_TIMEOUT_SECONDS = 20.0
_READ_TIMEOUT_SECONDS = 20.0
_MAX_REPORT_BYTES = 1_048_576
_MAX_LIST_BYTES = 262_144
_MAX_LIST_SCAN = 256
_RUNTIME_DAILY_SYNC_PATH = "/api/runtime-daily/sync"
_RUNTIME_DAILY_SYNC_TIMEOUT_SECONDS = 20
_RUNTIME_DAILY_SYNC_MAX_BODY_BYTES = 64 * 1024
_RUNTIME_DAILY_SYNC_MAX_RESPONSE_BYTES = 64 * 1024
_HTTPS_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RUNTIME_DAILY_SYNC_TARGET_KEY = "longbridge-quant-paper-service|russell_top50_leader_rotation|paper"
_ONE_SHOT_SYNC_URL = "https://qsl-strategy-switch-console.pigbibi.workers.dev/api/runtime-daily/sync"


class _Rejected(Exception):
    """The projection was not published. Nothing was written."""


class _KnownFailure(RuntimeError):
    """A safe, fixed diagnostic category for a known failure phase."""

    _CODES = frozenset(
        {"schedule_unavailable", "schedule_unevaluable",
         "storage_write_permission_denied", "write_unknown", "private_result_write_failed"}
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("invalid diagnostic code")
        self.code = code
        super().__init__(code)


def _prefix(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise _Rejected("config_invalid") from exc
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
        or segments[-1] != "runtime_daily"
        or any(segment in {".", ".."} for segment in segments)
    ):
        raise _Rejected("config_invalid")
    return f"gs://{parsed.netloc}/{'/'.join(segments)}"


def _enabled(env: Mapping[str, str]) -> bool:
    return env.get("RUNTIME_DAILY_PROJECTION_ENABLED") == "true"


def _require_config(env: Mapping[str, str]) -> str:
    if env.get("RUNTIME_DAILY_PROJECTION_TARGET_ID") != "paper":
        raise _Rejected("config_invalid")
    if env.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") != "PAPER":
        raise _Rejected("config_invalid")
    return _prefix(str(env.get("RUNTIME_DAILY_PROJECTION_GCS_PREFIX") or ""))


def _positive_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise _Rejected("config_invalid") from exc
    if parsed < 1:
        raise _Rejected("config_invalid")
    return parsed


def _field(payload: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _container_env_value(payload: Mapping[str, Any], name: str) -> str | None:
    spec = payload.get("spec")
    template = spec.get("template") if isinstance(spec, dict) else {}
    containers = template.get("spec", {}).get("containers") if isinstance(template, dict) else None
    if not isinstance(containers, list) and isinstance(template, dict):
        containers = template.get("containers")
    for container in containers if isinstance(containers, list) else []:
        if not isinstance(container, dict):
            continue
        for entry in container.get("env") or []:
            if isinstance(entry, dict) and entry.get("name") == name and entry.get("value") is not None:
                return str(entry.get("value"))
    return None


def _explicitly_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    return text not in {"0", "false", "no", "n", "off"}


def _deployment_enabled(payload: Mapping[str, Any], deployed: Mapping[str, Any]) -> bool:
    seen = False
    enabled = True
    if "runtime_target_enabled" in deployed:
        seen = True
        enabled = enabled and _explicitly_enabled(deployed.get("runtime_target_enabled"))
    env_enabled = _container_env_value(payload, "RUNTIME_TARGET_ENABLED")
    if env_enabled is not None:
        seen = True
        enabled = enabled and _explicitly_enabled(env_enabled)
    return seen and enabled


def _one_shot_report_root(value: str) -> str:
    bases = heartbeat._split_values(value)
    if len(bases) != 1:
        raise _Rejected("one_shot_report_prefix_unavailable")
    base = bases[0].rstrip("/")
    parsed = urlsplit(base)
    segments = parsed.path.lstrip("/").split("/") if parsed.path else []
    if (
        parsed.scheme != "gs" or _BUCKET.fullmatch(parsed.netloc or "") is None
        or parsed.username or parsed.password or parsed.query or parsed.fragment
        or parsed.port is not None or any(c in parsed.path for c in "*?[]%")
        or any(segment in {".", "..", ""} for segment in segments)
    ):
        raise _Rejected("one_shot_report_prefix_unavailable")
    return base


def _one_shot_environment(
    env: Mapping[str, str], *, report_root_uri: str | None = None, defer_report_root: bool = False,
) -> dict[str, str]:
    """Enable only this projection process without changing runtime control."""
    if (
        env.get("RUNTIME_TARGET_ENABLED") not in {"true", "false"}
        or env.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") != "PAPER"
        or not env.get("CLOUD_RUN_REGION")
        or not env.get("GOOGLE_APPLICATION_CREDENTIALS")
        or not env.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE")
        or env["GOOGLE_APPLICATION_CREDENTIALS"] != env["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"]
    ):
        raise _Rejected("one_shot_config_invalid")
    base = report_root_uri
    if base is None and not defer_report_root:
        # Retain direct helper compatibility; the production one-shot path defers
        # this choice until it has verified the active serving revision.
        configured = env.get("RUNTIME_HEARTBEAT_GCS_URIS") or env.get("EXECUTION_REPORT_GCS_URI") or ""
        base = _one_shot_report_root(configured)
    if base is not None:
        base = _one_shot_report_root(base)
        for name in ("RUNTIME_HEARTBEAT_GCS_URIS", "EXECUTION_REPORT_GCS_URI"):
            configured = str(env.get(name) or "").strip()
            if configured and _one_shot_report_root(configured) != base:
                raise _Rejected("one_shot_report_prefix_conflict")
        prefix = _prefix(base + "/runtime_daily")
        if prefix != base + "/runtime_daily":
            raise _Rejected("one_shot_report_prefix_unavailable")
    else:
        prefix = ""
    scoped = {
        **env, "RUNTIME_DAILY_PROJECTION_ENABLED": "true", "RUNTIME_DAILY_PROJECTION_TARGET_ID": "paper",
        "RUNTIME_DAILY_PROJECTION_GCS_PREFIX": prefix, "RUNTIME_DAILY_SYNC_ENABLED": "true",
        "RUNTIME_DAILY_SYNC_URL": _ONE_SHOT_SYNC_URL,
    }
    target = _select_target(scoped)
    if not env.get("CLOUD_RUN_SERVICE") or target.get("service") != env["CLOUD_RUN_SERVICE"]:
        raise _Rejected("one_shot_config_invalid")
    return scoped


def _one_shot_serving_deployment(
    service_payload: Mapping[str, Any], *, service: str, project: str | None, region: str,
) -> dict[str, Any]:
    """A staged template is not the effective configuration of the served target."""
    try:
        rows = [row for row in service_payload["status"]["traffic"] if row.get("percent", 0) > 0]
        if len(rows) != 1 or rows[0].get("percent") != 100:
            raise _Rejected("one_shot_serving_unavailable")
        revision_name = rows[0]["revisionName"]
        if not isinstance(revision_name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", revision_name):
            raise _Rejected("one_shot_serving_unavailable")
        command = ["gcloud", "run", "revisions", "describe", revision_name,
                   "--region", region, "--format=json"]
        if not project or not region:
            raise _Rejected("one_shot_serving_unavailable")
        command.extend(["--project", project])
        result = heartbeat._run_gcloud(command)
        if result.returncode != 0 or len(result.stdout.encode()) > _MAX_REPORT_BYTES:
            raise _Rejected("one_shot_serving_unavailable")
        revision = json.loads(result.stdout)
        metadata = revision["metadata"]
        ready = [row for row in revision["status"]["conditions"] if row.get("type") == "Ready"]
        containers = revision["spec"]["containers"]
        if (
            metadata.get("name") != revision_name
            or (metadata.get("labels") or {}).get("serving.knative.dev/service") != service
            or len(ready) != 1 or ready[0].get("status") != "True"
            or len(containers) != 1
        ):
            raise _Rejected("one_shot_serving_unavailable")
        report_rows = [
            row for row in containers[0].get("env", [])
            if isinstance(row, dict) and row.get("name") == "EXECUTION_REPORT_GCS_URI"
        ]
        if (
            len(report_rows) != 1 or "valueSource" in report_rows[0]
            or not isinstance(report_rows[0].get("value"), str)
            or not report_rows[0]["value"].strip()
        ):
            raise _Rejected("one_shot_report_prefix_unavailable")
        report_root = _one_shot_report_root(report_rows[0]["value"])
        return {
            "spec": {"template": {"metadata": metadata, "spec": revision["spec"]}},
            "one_shot_report_root_uri": report_root,
        }
    except _Rejected:
        raise
    except Exception:
        raise _Rejected("one_shot_serving_unavailable") from None


def _one_shot_disabled_reason(
    payload: Mapping[str, Any],
    deployed: Mapping[str, Any],
    *,
    declared_runtime_enabled: str,
) -> str:
    values = []
    if "runtime_target_enabled" in deployed:
        values.append(deployed["runtime_target_enabled"])
    env_value = _container_env_value(payload, "RUNTIME_TARGET_ENABLED")
    if env_value is not None:
        values.append(env_value)
    resolved: list[bool] = []
    for value in values:
        if value is True or value == "true":
            resolved.append(True)
        elif value is False or value == "false":
            resolved.append(False)
        else:
            return "runtime_target_enablement_unknown"
    if not resolved:
        return "runtime_target_enablement_unknown"
    if len(set(resolved)) != 1:
        return "runtime_target_enablement_conflict"
    if declared_runtime_enabled not in {"true", "false"}:
        return "runtime_target_enablement_unknown"
    effective = resolved[0]
    if effective != (declared_runtime_enabled == "true"):
        return "runtime_target_enablement_conflict"
    return "runtime_target_enabled_schedule_unverified" if effective else "runtime_target_disabled"


def _deployment_matches_paper(candidate: Mapping[str, Any], deployed: Mapping[str, Any]) -> bool:
    service = str(candidate.get("service") or "").strip()
    return (
        str(candidate.get("account_scope") or "").strip() == "PAPER"
        and _field(deployed, ("account_scope", "account_group", "account_region")) == "PAPER"
        and bool(service)
        and _field(deployed, ("service_name", "service", "cloud_run_service")) == service
    )


def _profiles_from_same_readback(
    target: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    project: str | None,
) -> list[dict[str, Any]]:
    service = str(target.get("service") or "").strip()
    original = heartbeat._describe_cloud_run_service

    def _same_readback(requested: str, *, project: str | None) -> dict[str, Any]:
        if requested == service:
            return dict(payload)
        return original(requested, project=project)

    heartbeat._describe_cloud_run_service = _same_readback
    try:
        return heartbeat._hydrate_runtime_target_profiles([dict(target)], project=project)
    finally:
        heartbeat._describe_cloud_run_service = original


def _select_target(env: Mapping[str, str]) -> dict[str, Any]:
    try:
        targets = load_runtime_targets(env, include_disabled=True)
    except ValueError as exc:
        raise _Rejected("config_invalid") from exc
    matched = [
        target
        for target in targets
        if str(target.get("account_scope") or "").strip() == "PAPER"
    ]
    if len(matched) != 1:
        raise _Rejected("target_not_unique")
    return matched[0]


# Compatibility-only matching for the unadopted natural daily publisher.
# It conveys no verified-route or cycle-health authority.
def _legacy_scheduler_identity(job: Mapping[str, Any], service: str) -> bool:
    """Reuse the heartbeat helper's service and URI rule, including paused jobs."""

    probe = dict(job)
    probe["state"] = "ENABLED"
    return heartbeat._scheduler_job_targets_strategy_run(probe, service)

def _legacy_confirmed_scheduler(
    target: Mapping[str, Any],
    *,
    project: str | None,
) -> tuple[dict[str, Any], str | None]:
    """Use the service's Cloud Scheduler job instead of the template cron."""

    service = str(target.get("service") or "").strip()
    jobs = heartbeat._list_scheduler_jobs(project=project)
    if not isinstance(jobs, list):
        raise RuntimeError("schedule_unavailable")
    enabled: list[Mapping[str, Any]] = []
    paused: list[Mapping[str, Any]] = []
    other: list[Mapping[str, Any]] = []
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        state = str(job.get("state") or "").strip().upper()
        same_service = _legacy_scheduler_identity(job, service)
        if state == "ENABLED" and same_service:
            enabled.append(job)
        elif state == "PAUSED" and same_service:
            paused.append(job)
        elif same_service:
            other.append(job)
    if len(enabled) == 1 and not other:
        job = enabled[0]
        schedule = str(job.get("schedule") or "").strip()
        timezone_name = str(job.get("timeZone") or "").strip()
        if len(schedule.split()) != 5 or not timezone_name:
            return dict(target), "scheduler_conflict"
        scheduler = dict(target.get("scheduler") or {})
        scheduler["main_time"] = schedule
        scheduler["timezone"] = timezone_name
        return {**target, "scheduler": scheduler}, None
    if enabled or other:
        return dict(target), "scheduler_conflict"
    if paused:
        return dict(target), "scheduler_paused"
    return dict(target), "scheduler_missing"


def _confirmed_scheduler(
    target: Mapping[str, Any],
    *,
    project: str | None,
    serving_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Resolve only the independently established serving origin and route."""

    try:
        binding = canonical_serving_route(serving_context)
        if binding["service"] != target.get("service"):
            raise ValueError()
    except ValueError:
        return {**target, "scheduler": {}}, "scheduler_context_unevaluable"
    jobs = heartbeat._list_scheduler_jobs(project=project)
    if not isinstance(jobs, list):
        raise RuntimeError("schedule_unavailable")
    return resolve_bound_scheduler(target, jobs, serving_context=serving_context)


def _quiet_read(uri: str, *, project: str | None) -> dict[str, Any] | None:
    with contextlib.redirect_stderr(io.StringIO()):
        payload = heartbeat._cat_gcs_json(uri, project=project)
    return payload if isinstance(payload, dict) else None


def _bounded_objects(
    globs: Sequence[str],
    *,
    project: str | None,
    since: dt.datetime,
    limit: int,
    list_objects: Callable[..., list[dict[str, Any]]],
) -> tuple[list[tuple[str, dt.datetime]], list[str]]:
    found: dict[str, tuple[str, dt.datetime]] = {}
    errors: list[str] = []
    unusable = False
    for pattern in globs:
        try:
            entries = list_objects(pattern, project=project)
        except Exception:
            errors.append(f"{pattern}: list_failed")
            continue
        if not isinstance(entries, list):
            errors.append(f"{pattern}: list_failed")
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                unusable = True
                continue
            uri = heartbeat._object_uri(entry)
            updated = heartbeat._object_updated_at(entry)
            if not uri or updated is None:
                unusable = True
                continue
            if updated < since:
                continue
            found[uri] = (uri, updated)
    if unusable:
        errors.append("listing incomplete")
    ordered = sorted(found.values(), key=lambda item: item[1], reverse=True)
    if len(ordered) > limit:
        errors.append("listing truncated")
        ordered = ordered[:limit]
    return ordered, errors


def _gs_object(pattern: str) -> tuple[str, str] | None:
    parsed = urlsplit(pattern)
    if parsed.scheme != "gs" or not parsed.netloc or parsed.path in {"", "/"}:
        return None
    return parsed.netloc, parsed.path.lstrip("/")


def _list_bounded(
    client: Any,
    globs: Sequence[str],
    *,
    since: dt.datetime,
    limit: int,
) -> tuple[list[tuple[str, dt.datetime]], list[str]]:
    found: dict[str, tuple[str, dt.datetime]] = {}
    errors: list[str] = []
    deadline = time.monotonic() + _LIST_TIMEOUT_SECONDS
    scanned = 0
    scanned_bytes = 0
    truncated = False
    for pattern in globs:
        if truncated or scanned >= _MAX_LIST_SCAN or scanned_bytes > _MAX_LIST_BYTES or time.monotonic() > deadline:
            truncated = True
            break
        located = _gs_object(pattern)
        if located is None:
            errors.append("list_failed")
            continue
        bucket_name, match = located
        try:
            iterator = client.list_blobs(
                bucket_name,
                match_glob=match,
                max_results=_MAX_LIST_SCAN + 1,
                page_size=_MAX_LIST_SCAN + 1,
                timeout=_LIST_TIMEOUT_SECONDS,
                retry=None,
                fields="items(name,updated,size),nextPageToken",
            )
        except Exception:
            errors.append("list_failed")
            continue
        incomplete = False
        try:
            for blob in iterator:
                if (
                    time.monotonic() > deadline
                    or scanned >= _MAX_LIST_SCAN
                    or scanned_bytes > _MAX_LIST_BYTES
                ):
                    truncated = True
                    break
                name = str(getattr(blob, "name", "") or "")
                scanned += 1
                scanned_bytes += len(name.encode("utf-8")) + 64
                updated = getattr(blob, "updated", None)
                if not name or not isinstance(updated, dt.datetime):
                    incomplete = True
                    continue
                if updated.tzinfo is None or updated.utcoffset() is None:
                    incomplete = True
                    continue
                updated = updated.astimezone(dt.timezone.utc)
                if updated < since:
                    continue
                found[f"gs://{bucket_name}/{name}"] = (f"gs://{bucket_name}/{name}", updated)
        except Exception:
            errors.append("list_failed")
            continue
        if incomplete:
            errors.append("listing incomplete")
        if truncated:
            break
    ordered = sorted(found.values(), key=lambda item: item[1], reverse=True)
    if truncated or len(ordered) > limit:
        errors.append("listing truncated")
    return ordered[:limit], errors


def _read_bounded(client: Any, uri: str) -> tuple[dict[str, Any] | None, str | None]:
    located = _gs_object(uri)
    if located is None:
        return None, "read_failed"
    bucket_name, name = located
    blob = client.bucket(bucket_name).blob(name)
    try:
        size = getattr(blob, "size", None)
        if isinstance(size, int) and size > _MAX_REPORT_BYTES:
            return None, "read_truncated"
        payload = blob.download_as_bytes(
            start=0,
            end=_MAX_REPORT_BYTES - 1,
            timeout=_READ_TIMEOUT_SECONDS,
            retry=None,
        )
    except Exception:
        return None, "read_failed"
    if not isinstance(payload, (bytes, bytearray)):
        return None, "read_failed"
    if len(payload) > _MAX_REPORT_BYTES:
        return None, "read_truncated"
    if len(payload) == _MAX_REPORT_BYTES and (not isinstance(size, int) or size > len(payload)):
        return None, "read_truncated"
    if isinstance(size, int) and size > len(payload):
        return None, "read_truncated"
    try:
        parsed = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        return None, "read_failed"
    if not isinstance(parsed, dict):
        return None, "read_failed"
    return parsed, None


def _object_uri(
    prefix: str,
    business_date: str,
    observed_at: dt.datetime,
) -> tuple[str, str, str]:
    root = prefix[len("gs://") :]
    bucket, _, base = root.partition("/")
    observation_version = observed_at.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{base}/longbridge/paper/{business_date}/{observation_version}.json"
    return bucket, name, f"gs://{bucket}/{name}"


def _upload(
    client: Any,
    prefix: str,
    business_date: str,
    observed_at: dt.datetime,
    body: str,
) -> str:
    bucket_name, object_name, uri = _object_uri(prefix, business_date, observed_at)
    blob = client.bucket(bucket_name).blob(object_name)
    try:
        blob.upload_from_string(
            body,
            content_type="application/json",
            if_generation_match=0,
            timeout=20,
            retry=None,
        )
    except PreconditionFailed:
        return "already_recorded"
    except Forbidden:
        raise _KnownFailure("storage_write_permission_denied") from None
    except GoogleAPICallError:
        raise _KnownFailure("write_unknown") from None
    except Exception:
        raise _KnownFailure("write_unknown") from None
    return uri


def _validate_private_result_path(path: str | os.PathLike[str]) -> str:
    """Require a caller-provisioned private directory and an unused filename."""
    destination = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(destination)
    name = os.path.basename(destination)
    if not name or name in {".", ".."}:
        raise _Rejected("private_result_path_invalid")
    try:
        info = os.stat(parent, follow_symlinks=False)
    except OSError:
        raise _Rejected("private_result_path_invalid") from None
    if (
        not os.path.isdir(parent)
        or os.path.islink(parent)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise _Rejected("private_result_path_invalid")
    if os.path.lexists(destination):
        raise _Rejected("private_result_path_exists")
    return destination


def _write_private_result(path: str, result: Mapping[str, Any]) -> None:
    """Create a small private handoff file atomically without overwriting."""
    parent = os.path.dirname(path)
    temporary_path = ""
    try:
        descriptor, temporary_path = tempfile.mkstemp(prefix=".runtime-daily-result-", dir=parent)
        os.fchmod(descriptor, 0o600)
        payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is create-only: an existing result is never replaced.
        os.link(temporary_path, path)
        os.unlink(temporary_path)
        temporary_path = ""
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary_path:
            with contextlib.suppress(OSError):
                os.unlink(temporary_path)
        raise _KnownFailure("private_result_write_failed") from None


def _runtime_daily_sync_url(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        raise _Rejected("qrs_sync_config_invalid") from None
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != _RUNTIME_DAILY_SYNC_PATH
        or _HTTPS_HOST.fullmatch(host) is None
    ):
        raise _Rejected("qrs_sync_config_invalid")
    return f"https://{host}{_RUNTIME_DAILY_SYNC_PATH}"


def _bounded_sync_json(response: Any) -> Any:
    chunks: list[bytes] = []
    total = 0
    iterator = getattr(response, "iter_content", None)
    if callable(iterator):
        for chunk in iterator(chunk_size=8192):
            if not chunk:
                continue
            if not isinstance(chunk, bytes):
                raise _Rejected("qrs_sync_response_invalid")
            total += len(chunk)
            if total > _RUNTIME_DAILY_SYNC_MAX_RESPONSE_BYTES:
                raise _Rejected("qrs_sync_response_too_large")
            chunks.append(chunk)
        raw = b"".join(chunks)
    else:
        raw = getattr(response, "content", None)
        if not isinstance(raw, bytes) or len(raw) > _RUNTIME_DAILY_SYNC_MAX_RESPONSE_BYTES:
            raise _Rejected("qrs_sync_response_invalid")
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        raise _Rejected("qrs_sync_response_invalid") from None


def _sync_runtime_daily(
    env: Mapping[str, str],
    body: str,
    *,
    business_date: str,
    target_key: str,
    http_post: Callable[..., Any] | None = None,
) -> str:
    if env.get("RUNTIME_DAILY_SYNC_ENABLED") != "true":
        return "disabled"
    if target_key != _RUNTIME_DAILY_SYNC_TARGET_KEY:
        return "rejected"
    try:
        url = _runtime_daily_sync_url(str(env.get("RUNTIME_DAILY_SYNC_URL") or ""))
        token = str(env.get("EXECUTION_EVIDENCE_SYNC_TOKEN") or "")
        if not token or token != token.strip():
            raise _Rejected("qrs_sync_config_invalid")
    except _Rejected:
        return "rejected"
    encoded = body.encode("utf-8")
    if len(encoded) > _RUNTIME_DAILY_SYNC_MAX_BODY_BYTES:
        return "rejected"
    try:
        if http_post is None:
            import requests

            http_post = requests.post
        response = http_post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=encoded,
            timeout=_RUNTIME_DAILY_SYNC_TIMEOUT_SECONDS,
            allow_redirects=False,
            stream=True,
        )
    except Exception:
        return "unknown"
    status_code = getattr(response, "status_code", None)
    if not isinstance(status_code, int):
        return "unknown"
    if getattr(response, "is_redirect", False) or 300 <= status_code < 400:
        return "rejected"
    if 400 <= status_code < 500:
        return "rejected"
    if status_code >= 500 or not 200 <= status_code < 300:
        return "unknown"
    try:
        payload = _bounded_sync_json(response)
    except Exception:
        return "unknown"
    if isinstance(payload, dict) and payload.get("ok") is False:
        return "rejected"
    if (
        not isinstance(payload, dict)
        or payload.get("ok") is not True
        or payload.get("stored") is not True
        or payload.get("business_date") != business_date
        or payload.get("target_key") != target_key
        or not isinstance(payload.get("account_key"), str)
        or not payload["account_key"]
    ):
        return "unknown"
    return "recorded"


# Keep the existing daily chain unchanged until an explicit caller adoption.
def publish(
    env: Mapping[str, str],
    *,
    now: dt.datetime,
    session_dates_loader: Any = None,
    client: Any = None,
    list_objects: Callable[..., list[dict[str, Any]]] | None = None,
    read_payload: Callable[[str], Mapping[str, Any] | None] | None = None,
    report_globs: Callable[[dt.datetime, dt.datetime], list[str]] | None = None,
    http_post: Callable[..., Any] | None = None,
    one_shot_only: bool = False,
    private_result_path: str | os.PathLike[str] | None = None,
) -> tuple[str, str, str]:
    """Return record status, date, and independently confirmed QRS sync status."""

    if one_shot_only:
        env = _one_shot_environment(env, defer_report_root=True)
    private_result_destination = (
        _validate_private_result_path(private_result_path) if private_result_path is not None else None
    )
    if not _enabled(env):
        return "disabled", "", "disabled"
    prefix = None if one_shot_only else _require_config(env)
    if now.tzinfo is None or now.utcoffset() is None:
        raise _Rejected("config_invalid")
    observed = now.astimezone(dt.timezone.utc)
    limit = _positive_int(env.get("RUNTIME_HEARTBEAT_MAX_REPORTS_TO_READ"), _MAX_REPORTS)
    if one_shot_only and limit > _MAX_REPORTS:
        raise _Rejected("one_shot_config_invalid")
    try:
        lookback = float(env.get("RUNTIME_HEARTBEAT_LOOKBACK_HOURS") or _LOOKBACK_HOURS)
        grace = float(env.get("RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES") or "30")
    except ValueError as exc:
        raise _Rejected("config_invalid") from exc
    if lookback <= 0 or grace < 0:
        raise _Rejected("config_invalid")
    target = _select_target(env)
    project = (
        env.get("RUNTIME_HEARTBEAT_GCP_PROJECT_ID")
        or env.get("GCP_PROJECT_ID")
        or env.get("GOOGLE_CLOUD_PROJECT")
    )
    service = str(target.get("service") or "").strip()
    try:
        payload = heartbeat._describe_cloud_run_service(service, project=project)
        if not isinstance(payload, dict):
            raise _KnownFailure("schedule_unavailable")
        if one_shot_only:
            payload = _one_shot_serving_deployment(payload, service=service, project=project, region=env["CLOUD_RUN_REGION"])
            env = _one_shot_environment(
                env, report_root_uri=payload.get("one_shot_report_root_uri")
            )
            prefix = _require_config(env)
        deployed = heartbeat._deployed_runtime_target(payload)
        hydrated = _profiles_from_same_readback(target, payload, project=project)
        if not one_shot_only:
            hydrated = heartbeat._hydrate_runtime_target_schedules(hydrated, project=project)
    except RuntimeError as exc:
        raise _KnownFailure("schedule_unavailable") from None
    if not _deployment_matches_paper(target, deployed) or (not one_shot_only and not _deployment_enabled(payload, deployed)):
        raise _Rejected("deployed_target_rejected")
    if len(hydrated) != 1:
        raise _Rejected("target_not_unique")
    if one_shot_only:
        timezone_name = str(deployed.get("market_timezone") or _container_env_value(payload, "LONGBRIDGE_MARKET_TIMEZONE") or "")
        if (
            timezone_name != "America/New_York" or hydrated[0].get("market_timezone") != timezone_name
            or f"{service}|{hydrated[0].get('strategy_profile')}|paper" != _RUNTIME_DAILY_SYNC_TARGET_KEY
        ):
            raise _Rejected("one_shot_target_invalid")
        hydrated[0] = {**hydrated[0], "scheduler": {}}
        scheduler_error = _one_shot_disabled_reason(
            payload, deployed, declared_runtime_enabled=env["RUNTIME_TARGET_ENABLED"]
        )
    else:
        try:
            hydrated[0], scheduler_error = _legacy_confirmed_scheduler(hydrated[0], project=project)
        except RuntimeError as exc:
            raise _KnownFailure("schedule_unavailable") from None
    since = observed - dt.timedelta(hours=lookback)
    globs = (report_globs or heartbeat._report_globs)(since, observed)
    if not globs:
        raise _Rejected("config_invalid")
    store = client if client is not None else (_one_shot_storage_client(env) if one_shot_only else _storage_client())
    if list_objects is None:
        objects, read_errors = _list_bounded(store, globs, since=since, limit=limit)
    else:
        objects, read_errors = _bounded_objects(
            globs,
            project=project,
            since=since,
            limit=limit,
            list_objects=list_objects,
        )
    if scheduler_error:
        read_errors.append(scheduler_error)
    if read_payload is None:
        kept: list[tuple[str, dt.datetime]] = []
        cached: dict[str, dict[str, Any]] = {}
        for uri, updated_at in objects:
            payload_value, error = _read_bounded(store, uri)
            if error or payload_value is None:
                read_errors.append(error or "read_failed")
                continue
            cached[uri] = payload_value
            kept.append((uri, updated_at))
        objects = kept

        def read_payload(
            uri: str,
            cached: dict[str, dict[str, Any]] = cached,
        ) -> dict[str, Any] | None:
            return cached.get(uri)

    kwargs: dict[str, Any] = {
        "observed_at": observed,
        "publication_grace": dt.timedelta(minutes=grace),
        "market_aware": str(env.get("RUNTIME_HEARTBEAT_MARKET_AWARE") or "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "read_errors": read_errors,
    }
    if session_dates_loader is not None:
        kwargs["session_dates_loader"] = session_dates_loader
    if one_shot_only:
        kwargs["business_date"] = observed.astimezone(ZoneInfo(timezone_name)).date()
    projected = project_listed_reports(
        targets=hydrated,
        objects=objects,
        read_payload=read_payload,
        **kwargs,
    )
    records = projected.get("records")
    if not isinstance(records, list) or len(records) != 1:
        raise _Rejected("target_not_unique")
    if one_shot_only:
        projected["completeness"] = records[0]["completeness"] = "incomplete"
        records[0]["schedule"]["reason"] = scheduler_error
        if records[0]["status"] not in {"unknown", "reconciliation_required", "failed", "blocked", "conflict"}:
            records[0]["status"] = "read_incomplete"
            records[0]["kind"] = "incomplete"
    business_date = records[0].get("business_date")
    if not isinstance(business_date, str) or not business_date:
        raise _KnownFailure("schedule_unavailable")
    # QRS stores the fixed PAPER target with its canonical lower-case scope.
    # Keep the actual scope check above case-insensitive and serialize this
    # normalized representation once so GCS and the optional POST share bytes.
    if records[0].get("target_key") == _RUNTIME_DAILY_SYNC_TARGET_KEY:
        records[0]["target"]["account_scope"] = "paper"
    body = json.dumps(projected, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    target_keys = [record.get("target_key") for record in records]
    if private_result_destination is not None and (
        any(not isinstance(key, str) or not key for key in target_keys)
        or len(set(target_keys)) != len(target_keys)
    ):
        raise _Rejected("private_result_target_invalid")
    if prefix is None:
        raise _Rejected("one_shot_report_prefix_unavailable")
    uploaded = _upload(store, prefix, business_date, observed, body)
    if uploaded == "already_recorded":
        return "already_recorded", business_date, "skipped_existing"
    sync_status = _sync_runtime_daily(
        env,
        body,
        business_date=business_date,
        target_key=str(records[0].get("target_key") or ""),
        http_post=http_post,
    )
    if private_result_destination is not None:
        _write_private_result(private_result_destination, {
            "projection_uri": uploaded,
            "business_date": business_date,
            "target_keys": target_keys,
            "projection_completeness": projected.get("completeness"),
        })
    return "recorded", business_date, sync_status


def publish_verified(
    env: Mapping[str, str],
    *,
    now: dt.datetime,
    session_dates_loader: Any = None,
    client: Any = None,
    list_objects: Callable[..., list[dict[str, Any]]] | None = None,
    read_payload: Callable[[str], Mapping[str, Any] | None] | None = None,
    report_globs: Callable[[dt.datetime, dt.datetime], list[str]] | None = None,
    http_post: Callable[..., Any] | None = None,
    serving_context: Mapping[str, Any] | None = None,
) -> tuple[str, str, str]:
    """Return record status, date, and independently confirmed QRS sync status.

    A caller must supply a separately admitted serving revision/route context.
    Without it, this opt-in path remains explicitly unevaluable and writes nothing.
    The natural daily CLI deliberately continues through legacy publish().
    """

    if not _enabled(env):
        return "disabled", "", "disabled"
    prefix = _require_config(env)
    if now.tzinfo is None or now.utcoffset() is None:
        raise _Rejected("config_invalid")
    observed = now.astimezone(dt.timezone.utc)
    limit = _positive_int(env.get("RUNTIME_HEARTBEAT_MAX_REPORTS_TO_READ"), _MAX_REPORTS)
    try:
        lookback = float(env.get("RUNTIME_HEARTBEAT_LOOKBACK_HOURS") or _LOOKBACK_HOURS)
        grace = float(env.get("RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES") or "30")
    except ValueError as exc:
        raise _Rejected("config_invalid") from exc
    if lookback <= 0 or grace < 0:
        raise _Rejected("config_invalid")
    target = _select_target(env)
    project = (
        env.get("RUNTIME_HEARTBEAT_GCP_PROJECT_ID")
        or env.get("GCP_PROJECT_ID")
        or env.get("GOOGLE_CLOUD_PROJECT")
    )
    service = str(target.get("service") or "").strip()
    try:
        # Preserve one supplied observation even if a transport mutates its input.
        serving_context = deepcopy(serving_context)
        binding = canonical_serving_route(serving_context)
        if binding["service"] != service:
            raise ValueError()
        revision = serving_context["revision"]
        revision_spec = revision.get("spec") if isinstance(revision, Mapping) else None
        if (not isinstance(revision_spec, Mapping)
                or not isinstance(revision_spec.get("containers"), list)):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise _KnownFailure("schedule_unevaluable") from None
    try:
        payload = heartbeat._describe_cloud_run_service(service, project=project)
        if not isinstance(payload, dict):
            raise RuntimeError("schedule_unavailable")
    except RuntimeError:
        raise _KnownFailure("schedule_unavailable") from None
    try:
        # The service template may be staged while an older revision serves.
        # Compare serving identity, then use only that bound revision's config.
        readback_binding = canonical_serving_route({**serving_context, "service": payload})
        if readback_binding != binding:
            raise ValueError()
    except ValueError:
        raise _KnownFailure("schedule_unevaluable") from None
    serving_payload = {"spec": {"template": {"spec": dict(revision_spec)}}}
    try:
        deployed = heartbeat._deployed_runtime_target(serving_payload)
        hydrated = _profiles_from_same_readback(target, serving_payload, project=project)
    except RuntimeError:
        raise _KnownFailure("schedule_unavailable") from None
    if not _deployment_matches_paper(target, deployed) or not _deployment_enabled(serving_payload, deployed):
        raise _Rejected("deployed_target_rejected")
    if len(hydrated) != 1:
        raise _Rejected("target_not_unique")
    try:
        hydrated[0], scheduler_error = _confirmed_scheduler(
            hydrated[0], project=project, serving_context=serving_context,
        )
    except RuntimeError:
        raise _KnownFailure("schedule_unavailable") from None
    if scheduler_error:
        # An unproven schedule cannot be archived as a plausible template day.
        raise _KnownFailure("schedule_unevaluable")
    since = observed - dt.timedelta(hours=lookback)
    globs = (report_globs or heartbeat._report_globs)(since, observed)
    if not globs:
        raise _Rejected("config_invalid")
    store = client if client is not None else _storage_client()
    if list_objects is None:
        objects, read_errors = _list_bounded(store, globs, since=since, limit=limit)
    else:
        objects, read_errors = _bounded_objects(
            globs,
            project=project,
            since=since,
            limit=limit,
            list_objects=list_objects,
        )
    if read_payload is None:
        kept: list[tuple[str, dt.datetime]] = []
        cached: dict[str, dict[str, Any]] = {}
        for uri, updated_at in objects:
            payload_value, error = _read_bounded(store, uri)
            if error or payload_value is None:
                read_errors.append(error or "read_failed")
                continue
            cached[uri] = payload_value
            kept.append((uri, updated_at))
        objects = kept

        def read_payload(
            uri: str,
            cached: dict[str, dict[str, Any]] = cached,
        ) -> dict[str, Any] | None:
            return cached.get(uri)

    kwargs: dict[str, Any] = {
        "observed_at": observed,
        "publication_grace": dt.timedelta(minutes=grace),
        "market_aware": str(env.get("RUNTIME_HEARTBEAT_MARKET_AWARE") or "true").strip().lower()
        not in {"0", "false", "no", "off"},
        "read_errors": read_errors,
    }
    if session_dates_loader is not None:
        kwargs["session_dates_loader"] = session_dates_loader
    projected = project_listed_reports(
        targets=hydrated,
        objects=objects,
        read_payload=read_payload,
        **kwargs,
    )
    records = projected.get("records")
    if not isinstance(records, list) or len(records) != 1:
        raise _Rejected("target_not_unique")
    business_date = records[0].get("business_date")
    if not isinstance(business_date, str) or not business_date:
        raise _KnownFailure("schedule_unavailable")
    # QRS stores the fixed PAPER target with its canonical lower-case scope.
    # Keep the actual scope check above case-insensitive and serialize this
    # normalized representation once so GCS and the optional POST share bytes.
    if records[0].get("target_key") == _RUNTIME_DAILY_SYNC_TARGET_KEY:
        records[0]["target"]["account_scope"] = "paper"
    body = json.dumps(projected, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    uploaded = _upload(store, prefix, business_date, observed, body)
    if uploaded == "already_recorded":
        return "already_recorded", business_date, "skipped_existing"
    sync_status = _sync_runtime_daily(
        env,
        body,
        business_date=business_date,
        target_key=str(records[0].get("target_key") or ""),
        http_post=http_post,
    )
    return "recorded", business_date, sync_status


def _storage_client() -> Any:
    from google.cloud import storage

    return storage.Client()


def _one_shot_storage_client(env: Mapping[str, str]) -> Any:
    import google.auth
    from google.cloud import storage

    credentials, _ = google.auth.load_credentials_from_file(env["GOOGLE_APPLICATION_CREDENTIALS"])
    project = env.get("GCP_PROJECT_ID") or env.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise _Rejected("one_shot_config_invalid")
    return storage.Client(project=project, credentials=credentials)


def _sync_exit_code(env: Mapping[str, str], sync_status: str) -> int:
    """An explicitly requested sync needs an ACK; the GCS fact stays separate."""
    if env.get("RUNTIME_DAILY_SYNC_ENABLED") != "true" or sync_status == "recorded":
        return 0
    if sync_status in {"rejected", "disabled"}:
        return 2
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    one_shot_only = False
    private_result_path = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--one-shot-paper" and not one_shot_only:
            one_shot_only = True
            index += 1
        elif argument == "--private-result-file" and private_result_path is None and index + 1 < len(arguments):
            private_result_path = arguments[index + 1]
            index += 2
        else:
            print("daily runtime projection rejected")
            return 2
    if private_result_path is not None and not private_result_path:
        print("daily runtime projection rejected")
        return 2
    try:
        if one_shot_only:
            kwargs = {"one_shot_only": True}
            if private_result_path is not None:
                kwargs["private_result_path"] = private_result_path
            status, business_date, sync_status = publish(os.environ, now=dt.datetime.now(dt.timezone.utc), **kwargs)
        elif private_result_path is not None:
            status, business_date, sync_status = publish(
                os.environ,
                now=dt.datetime.now(dt.timezone.utc),
                private_result_path=private_result_path,
            )
        else:
            status, business_date, sync_status = publish(os.environ, now=dt.datetime.now(dt.timezone.utc))
    except _KnownFailure as exc:
        print(f"daily runtime projection failed; category={exc.code}")
        return 1
    except _Rejected:
        print("daily runtime projection rejected")
        return 2
    except Exception:
        print("daily runtime projection failed")
        return 1
    if one_shot_only:
        print(json.dumps({
            "status": status if status in {"recorded", "already_recorded"} else "failed",
            "projection_created": status == "recorded", "qrs_ack_valid": sync_status == "recorded",
        }, sort_keys=True, separators=(",", ":")))
        return _sync_exit_code({"RUNTIME_DAILY_SYNC_ENABLED": "true"}, sync_status)
    if status == "disabled":
        print("daily runtime projection disabled")
        return 0
    if status == "recorded":
        print(f"daily runtime projection recorded {business_date}; qrs_sync={sync_status}")
        return _sync_exit_code(os.environ, sync_status)
    if status == "already_recorded":
        print(f"daily runtime projection already recorded {business_date}; qrs_sync={sync_status}")
        return _sync_exit_code(os.environ, sync_status)
    print("daily runtime projection failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
