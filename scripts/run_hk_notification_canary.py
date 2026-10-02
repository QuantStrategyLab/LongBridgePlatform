#!/usr/bin/env python3
"""Run one bounded notification probe through a tagged HK candidate revision.

This is a one-off operator helper. It never changes service traffic percentages,
never enables the runtime target, and never retries an uncertain scheduler run.
The full pre-run service/job configuration is written once to a private local
file on the approved operator host before the first mutation.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from scripts import record_daily_account_snapshot as snapshot
from scripts.verify_deployed_runtime_target_admission import (
    APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE,
    _validate_image_only_source,
)
from application.runtime_target_manifest import load_runtime_target_manifest


BASE_APPLICATION_SHA = "922f338fea7c46391b50bb8316ac88154596916f"
PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
TAG_NAME = re.compile(r"^[a-z][a-z0-9-]{0,48}[a-z0-9]$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_CLOUD_BODY_BYTES = 1_000_000
MAX_LOG_BODY_BYTES = 256_000
MAX_LOG_ROWS = 50
MAX_LOG_PAGES = 10
MAX_LOG_ENTRIES = 500
MAX_WAIT_SECONDS = 60.0
POLL_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 20.0
IN_FLIGHT_QUIET_WINDOW = timedelta(minutes=20)
BACKUP_ROOT = Path("/home/ubuntu/.local/state")
BACKUP_RELATIVE_PATH = Path("qsl-account-facts/hk-canary/20261002-779d-canary.json")
BACKUP_PATH = BACKUP_ROOT / BACKUP_RELATIVE_PATH
PREVIOUS_REQUEST_START = "2026-09-28T00:00:00Z"
PREVIOUS_REQUEST_TIMESTAMP = "2026-09-29T19:37:24.342046Z"
PREVIOUS_REQUEST_LATENCY = "5.385698572s"
_SERVICE_OUTPUT_FIELDS = frozenset(
    {
        "etag",
        "uid",
        "createTime",
        "updateTime",
        "generation",
        "observedGeneration",
        "reconciling",
        "conditions",
        "terminalCondition",
        "latestCreatedRevision",
        "latestReadyRevision",
        "trafficStatuses",
        "uri",
        "urls",
    }
)
_PROBE_STEPS = frozenset(
    {
        "settings",
        "context",
        "balance",
        "archive",
        "indicators",
        "bootstrap",
        "portfolio_snapshot",
    }
)
_REVISION_CONFIG_FIELDS = (
    "serviceAccount",
    "timeout",
    "maxInstanceRequestConcurrency",
    "executionEnvironment",
    "scaling",
    "vpcAccess",
    "volumes",
    "serviceMesh",
    "encryptionKey",
    "encryptionKeyRevocationAction",
    "encryptionKeyShutdownDuration",
    "sessionAffinity",
    "nodeSelector",
    "gpuZonalRedundancyDisabled",
)


class CanaryError(ValueError):
    """Fixed, redacted failure category safe for an operator log."""


def _stop(category: str) -> CanaryError:
    return CanaryError(f"hk_notification_canary_{category}")


def _read_json(
    response: Any, *, max_bytes: int = MAX_CLOUD_BODY_BYTES
) -> Mapping[str, Any]:
    status = getattr(response, "status_code", None)
    if getattr(response, "is_redirect", False) or status != 200:
        raise _stop("cloud_read_failed")
    body = getattr(response, "content", b"")
    if not isinstance(body, (bytes, bytearray)) or len(body) > max_bytes:
        raise _stop("cloud_read_invalid")
    try:
        value = json.loads(body)
    except Exception:
        raise _stop("cloud_read_invalid") from None
    if not isinstance(value, Mapping):
        raise _stop("cloud_read_invalid")
    return value


def _http_session():
    """Reuse the existing zero-retry ADC session used by account snapshot reads."""
    return snapshot._authorized_session()


def _service_resource(project: str, region: str, service: str) -> str:
    return f"projects/{project}/locations/{region}/services/{service}"


def _service_get(session: Any, resource: str) -> Mapping[str, Any]:
    response = session.get(
        f"https://run.googleapis.com/v2/{resource}",
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        return _read_json(response)
    finally:
        snapshot._close(response)


def _service_patch_traffic(
    session: Any,
    resource: str,
    *,
    etag: str,
    traffic: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    response = session.patch(
        f"https://run.googleapis.com/v2/{resource}?updateMask=traffic",
        json={"name": resource, "etag": etag, "traffic": list(traffic)},
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        status = getattr(response, "status_code", None)
        if (
            getattr(response, "is_redirect", False)
            or not isinstance(status, int)
            or not 200 <= status < 300
        ):
            raise _stop(
                "service_tag_mutation_unknown"
                if not isinstance(status, int) or status >= 500
                else "service_tag_mutation_rejected"
            )
        operation = _read_json(response)
    finally:
        snapshot._close(response)
    _await_service_operation(session, operation, resource=resource)
    return operation


def _await_service_operation(
    session: Any, operation: Mapping[str, Any], *, resource: str
) -> None:
    """Wait at most one minute for this exact Cloud Run service mutation."""
    name = operation.get("name")
    service_parent = resource.rsplit("/services/", 1)[0]
    if (
        not isinstance(name, str)
        or not name.startswith(f"{service_parent}/operations/")
        or not re.fullmatch(
            re.escape(service_parent) + r"/operations/[A-Za-z0-9_-]+", name
        )
    ):
        raise _stop("service_operation_unknown")
    deadline = time.monotonic() + MAX_WAIT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _stop("service_operation_unknown")
        response = session.get(
            f"https://run.googleapis.com/v2/{name}",
            timeout=min(HTTP_TIMEOUT_SECONDS, remaining),
            allow_redirects=False,
        )
        try:
            current = _read_json(response)
        finally:
            snapshot._close(response)
        done = current.get("done", False)
        if type(done) is not bool:
            raise _stop("service_operation_unknown")
        if done:
            if current.get("error") is not None:
                raise _stop("service_mutation_failed")
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _stop("service_operation_unknown")
        time.sleep(min(POLL_SECONDS, remaining))


def _scheduler_resource(project: str, region: str, job_name: str) -> str:
    return f"projects/{project}/locations/{region}/jobs/{job_name}"


def _scheduler_list(session: Any, project: str, region: str) -> list[Mapping[str, Any]]:
    parent = f"projects/{project}/locations/{region}"
    response = session.get(
        f"https://cloudscheduler.googleapis.com/v1/{parent}/jobs?pageSize=500",
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        payload = _read_json(response)
    finally:
        snapshot._close(response)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or payload.get("nextPageToken"):
        raise _stop("scheduler_inventory_incomplete")
    if any(not isinstance(item, Mapping) for item in jobs):
        raise _stop("scheduler_inventory_invalid")
    return list(jobs)


def _scheduler_patch_uri(session: Any, resource: str, uri: str) -> None:
    response = session.patch(
        f"https://cloudscheduler.googleapis.com/v1/{resource}?updateMask=httpTarget.uri",
        json={"name": resource, "httpTarget": {"uri": uri}},
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        status = getattr(response, "status_code", None)
        if (
            getattr(response, "is_redirect", False)
            or not isinstance(status, int)
            or not 200 <= status < 300
        ):
            raise _stop(
                "scheduler_uri_mutation_unknown"
                if not isinstance(status, int) or status >= 500
                else "scheduler_uri_mutation_rejected"
            )
    finally:
        snapshot._close(response)


def _revision_get(
    session: Any, project: str, region: str, service: str, revision: str
) -> Mapping[str, Any]:
    resource = (
        f"projects/{project}/locations/{region}/services/{service}/revisions/{revision}"
    )
    response = session.get(
        f"https://run.googleapis.com/v2/{resource}",
        timeout=HTTP_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    try:
        return _read_json(response)
    finally:
        snapshot._close(response)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except Exception:
        raise _stop("backup_invalid") from None


def _open_directory_nofollow(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _create_backup_file(path: Path, body: bytes, *, root: Path) -> None:
    if (
        path != root / BACKUP_RELATIVE_PATH
        or len(body) > MAX_CLOUD_BODY_BYTES
        or not body
    ):
        raise _stop("backup_config_invalid")
    root_descriptor = -1
    directory_descriptor = -1
    file_descriptor = -1
    read_descriptor = -1
    try:
        root_descriptor = _open_directory_nofollow(root)
        directory_descriptor = root_descriptor
        root_descriptor = -1
        directory_flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        for component in BACKUP_RELATIVE_PATH.parts[:-1]:
            created = False
            try:
                os.mkdir(component, mode=0o700, dir_fd=directory_descriptor)
                created = True
            except FileExistsError:
                pass
            if created:
                os.fsync(directory_descriptor)
            next_descriptor = os.open(
                component, directory_flags, dir_fd=directory_descriptor
            )
            if stat.S_IMODE(os.fstat(next_descriptor).st_mode) != 0o700:
                os.close(next_descriptor)
                raise _stop("backup_directory_permissions_invalid")
            os.close(directory_descriptor)
            directory_descriptor = next_descriptor

        file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(
            BACKUP_RELATIVE_PATH.name,
            file_flags,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(file_descriptor, 0o600)
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise _stop("backup_file_type_invalid")
        view = memoryview(body)
        while view:
            written = os.write(file_descriptor, view)
            if written <= 0:
                raise OSError("short backup write")
            view = view[written:]
        os.fsync(file_descriptor)
        os.close(file_descriptor)
        file_descriptor = -1

        read_descriptor = os.open(
            BACKUP_RELATIVE_PATH.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_descriptor,
        )
        read_stat = os.fstat(read_descriptor)
        if (
            not stat.S_ISREG(read_stat.st_mode)
            or stat.S_IMODE(read_stat.st_mode) != 0o600
        ):
            raise _stop("backup_file_permissions_invalid")
        with os.fdopen(os.dup(read_descriptor), "rb") as backup_file:
            if backup_file.read(MAX_CLOUD_BODY_BYTES + 1) != body:
                raise _stop("backup_readback_mismatch")
        os.fsync(directory_descriptor)
    except CanaryError:
        raise
    except Exception:
        raise _stop("backup_create_only_failed") from None
    finally:
        for descriptor in (
            file_descriptor,
            read_descriptor,
            directory_descriptor,
            root_descriptor,
        ):
            if descriptor >= 0:
                os.close(descriptor)


def _container_env(containers: Any) -> dict[str, str]:
    if (
        not isinstance(containers, list)
        or len(containers) != 1
        or not isinstance(containers[0], Mapping)
    ):
        raise _stop("runtime_config_invalid")
    entries = containers[0].get("env", [])
    if not isinstance(entries, list):
        raise _stop("runtime_config_invalid")
    result: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise _stop("runtime_config_invalid")
        name, value = entry.get("name"), entry.get("value")
        if not isinstance(name, str) or not name or name in result:
            raise _stop("runtime_config_invalid")
        if isinstance(value, str) and "valueSource" not in entry:
            result[name] = value
        elif isinstance(entry.get("valueSource"), Mapping) and value is None:
            # Secret-backed values are not read. Their full reference remains
            # covered by the container configuration comparison below.
            continue
        else:
            raise _stop("runtime_config_invalid")
    return result


def _container_configuration(containers: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(containers, list)
        or len(containers) != 1
        or not isinstance(containers[0], Mapping)
    ):
        raise _stop("runtime_config_invalid")
    container = deepcopy(dict(containers[0]))
    container.pop("image", None)
    return [container]


def _revision_configuration_matches(
    template: Mapping[str, Any], revision: Mapping[str, Any]
) -> bool:
    return _container_configuration(
        template.get("containers")
    ) == _container_configuration(revision.get("containers")) and all(
        template.get(field) == revision.get(field)
        for field in _REVISION_CONFIG_FIELDS
        if field in template or field in revision
    )


def _runtime_target(env: Mapping[str, str], *, service: str) -> Mapping[str, Any]:
    try:
        target = json.loads(
            env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or ""
        )
    except Exception:
        raise _stop("runtime_target_invalid") from None
    if not isinstance(target, Mapping):
        raise _stop("runtime_target_invalid")
    selector = target.get("account_selector")
    selectors = (
        (selector,)
        if isinstance(selector, str)
        else tuple(selector)
        if isinstance(selector, list)
        else None
    )
    if (
        target.get("platform_id") != "longbridge"
        or target.get("service_name") != service
        or target.get("account_scope") != "HK"
        or selectors != ("HK",)
        or target.get("deployment_selector") != "HK"
        or target.get("strategy_profile") != "hk_global_etf_tactical_rotation"
        or target.get("execution_mode") != "paper"
        or target.get("dry_run_only") is not True
        or env.get("RUNTIME_TARGET_ENABLED") != "false"
        or env.get("LONGBRIDGE_DRY_RUN_ONLY") != "true"
    ):
        raise _stop("runtime_target_mismatch")
    return target


def _revision_commit(revision: Mapping[str, Any]) -> str:
    labels = revision.get("labels")
    labels = labels if isinstance(labels, Mapping) else {}
    return str(labels.get("commit-sha") or "")


def _revision_ready(revision: Mapping[str, Any]) -> bool:
    conditions = revision.get("conditions")
    if not isinstance(conditions, list):
        return False
    ready_conditions = [
        item
        for item in conditions
        if isinstance(item, Mapping) and item.get("type") == "Ready"
    ]
    return len(ready_conditions) == 1 and (
        ready_conditions[0].get("state") == "CONDITION_SUCCEEDED"
    )


def _positive_int64_string(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        return False
    return int(value) <= 2**63 - 1


def _service_converged(service: Mapping[str, Any]) -> bool:
    if "reconciling" in service and service.get("reconciling") is not False:
        return False
    terminal = service.get("terminalCondition")
    if (
        not isinstance(terminal, Mapping)
        or terminal.get("type") != "Ready"
        or terminal.get("state") != "CONDITION_SUCCEEDED"
    ):
        return False
    generation = service.get("generation")
    observed_generation = service.get("observedGeneration")
    return (
        _positive_int64_string(generation)
        and _positive_int64_string(observed_generation)
        and generation == observed_generation
    )


def _traffic_rows(service: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = service.get("trafficStatuses")
    if not isinstance(raw, list) or not raw:
        raise _stop("service_traffic_unknown")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise _stop("service_traffic_invalid")
        revision = item.get("revision")
        percent = item.get("percent", 0)
        tag, uri = item.get("tag") or "", item.get("uri") or item.get("url") or ""
        if (
            not isinstance(revision, str)
            or not revision
            or type(percent) is not int
            or not 0 <= percent <= 100
            or not isinstance(tag, str)
            or not isinstance(uri, str)
        ):
            raise _stop("service_traffic_invalid")
        rows.append({"revision": revision, "percent": percent, "tag": tag, "uri": uri})
    return rows


def _configured_traffic(service: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = service.get("traffic")
    if not isinstance(raw, list) or not raw:
        raise _stop("service_traffic_unknown")
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise _stop("service_traffic_invalid")
        revision = item.get("revision")
        percent = item.get("percent", 0)
        tag = item.get("tag") or ""
        if (
            not isinstance(revision, str)
            or not revision
            or type(percent) is not int
            or not 0 <= percent <= 100
            or not isinstance(tag, str)
        ):
            raise _stop("service_traffic_invalid")
        # Preserve every field Cloud Run returned so a read/modify/write cycle
        # cannot silently discard an unfamiliar traffic setting.
        row = deepcopy(dict(item))
        row["revision"] = revision
        row["percent"] = percent
        if "tag" in row:
            row["tag"] = tag
        result.append(row)
    return result


def _base_uri(service: Mapping[str, Any]) -> str:
    value = service.get("uri")
    try:
        parsed = urlsplit(str(value or ""))
    except ValueError:
        raise _stop("service_uri_invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(".run.app")
    ):
        raise _stop("service_uri_invalid")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


def _status_traffic_exactly_old100(
    service: Mapping[str, Any], old_revision: str
) -> None:
    rows = _traffic_rows(service)
    serving = [row for row in rows if row["percent"] > 0]
    if (
        len(serving) != 1
        or serving[0]["revision"] != old_revision
        or serving[0]["percent"] != 100
    ):
        raise _stop("serving_traffic_not_old100")


def _resource_jobs(
    jobs: Sequence[Mapping[str, Any]],
    *,
    project: str,
    region: str,
    service: str,
    base_uri: str,
) -> tuple[dict[str, Mapping[str, Any]], str]:
    expected = {
        f"{service}-probe-scheduler",
        f"{service}-precheck-scheduler",
    }
    main_names = {f"{service}-scheduler"}
    selected: dict[str, Mapping[str, Any]] = {}
    base_host = urlsplit(base_uri).hostname
    for job in jobs:
        target = job.get("httpTarget")
        target = target if isinstance(target, Mapping) else {}
        raw_uri = target.get("uri")
        if not isinstance(raw_uri, str):
            continue
        try:
            parsed = urlsplit(raw_uri)
        except ValueError:
            raise _stop("scheduler_inventory_invalid") from None
        if parsed.hostname != base_host:
            continue
        name = job.get("name")
        suffix = str(name).rsplit("/", 1)[-1] if isinstance(name, str) else ""
        if (
            name != _scheduler_resource(project, region, suffix)
            or suffix not in expected | main_names
            or suffix in selected
            or job.get("state") != "PAUSED"
        ):
            raise _stop("scheduler_inventory_invalid")
        selected[suffix] = job
    if (
        not expected.issubset(selected)
        or len(set(selected) & main_names) != 1
        or len(selected) != 3
    ):
        raise _stop("scheduler_inventory_incomplete")
    probe_name = f"{service}-probe-scheduler"
    return selected, _scheduler_resource(project, region, probe_name)


def _aware(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise _stop("scheduler_time_unknown")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _stop("scheduler_time_unknown") from None
    if parsed.tzinfo is None:
        raise _stop("scheduler_time_unknown")
    return parsed.astimezone(timezone.utc)


def _cron_next_times(job: Mapping[str, Any], now: datetime) -> list[datetime]:
    schedule = job.get("schedule")
    timezone_name = job.get("timeZone")
    if not isinstance(schedule, str) or not isinstance(timezone_name, str):
        raise _stop("scheduler_schedule_unknown")
    fields = schedule.split()
    if (
        len(fields) != 5
        or fields[2] != "*"
        or fields[3] != "*"
        or fields[4] not in {"1-5", "*"}
    ):
        raise _stop("scheduler_schedule_unknown")
    try:
        minute = int(fields[0])
        hours = sorted({int(value) for value in fields[1].split(",")})
        zone = ZoneInfo(timezone_name)
    except Exception:
        raise _stop("scheduler_schedule_unknown") from None
    if not 0 <= minute <= 59 or not hours or any(not 0 <= hour <= 23 for hour in hours):
        raise _stop("scheduler_schedule_unknown")
    local_now = now.astimezone(zone)
    result = []
    for offset in range(0, 9):
        day = local_now.date() + timedelta(days=offset)
        if fields[4] == "1-5" and day.weekday() >= 5:
            continue
        for hour in hours:
            candidate = datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=zone
            ).astimezone(timezone.utc)
            if candidate >= now:
                result.append(candidate)
    if not result:
        raise _stop("scheduler_schedule_unknown")
    return result


def _require_quiet_window(jobs: Mapping[str, Mapping[str, Any]], now: datetime) -> None:
    for job in jobs.values():
        scheduled = _cron_next_times(job, now)
        if any(abs(item - now) < IN_FLIGHT_QUIET_WINDOW for item in scheduled):
            raise _stop("scheduler_natural_window")


def _job_control_view(job: Mapping[str, Any]) -> dict[str, Any]:
    return snapshot._scheduler_control_view(job)


def _with_uri(job: Mapping[str, Any], uri: str) -> dict[str, Any]:
    changed = deepcopy(dict(job))
    target = changed.get("httpTarget")
    if not isinstance(target, Mapping):
        raise _stop("probe_job_invalid")
    changed_target = dict(target)
    changed_target["uri"] = uri
    changed["httpTarget"] = changed_target
    return changed


def _validate_original_probe_job(
    job: Mapping[str, Any], *, resource: str, base_uri: str
) -> None:
    config = SimpleNamespace(scheduler_resource=resource, service_url=base_uri)
    try:
        snapshot._validate_scheduler_job(job, config, allow_paused=True)
    except Exception:
        raise _stop("probe_job_invalid")


def _candidate_tag_url(
    service: Mapping[str, Any], *, candidate_revision: str, tag: str
) -> str:
    rows = _traffic_rows(service)
    matches = [
        row
        for row in rows
        if row["revision"] == candidate_revision and row["tag"] == tag
    ]
    if len(matches) != 1 or not matches[0]["uri"]:
        raise _stop("candidate_tag_url_unverified")
    uri = matches[0]["uri"]
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname.endswith(".run.app")
        or parsed.hostname == urlsplit(_base_uri(service)).hostname
    ):
        raise _stop("candidate_tag_url_unverified")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


def _candidate_outcome(request_status: Any, event: Mapping[str, Any]) -> str:
    if type(request_status) is not int or request_status not in {200, 500}:
        raise _stop("probe_terminal_invalid")
    event_name = event.get("event")
    if event.get("execution_window") != "probe":
        raise _stop("probe_event_invalid")
    if request_status == 200 and event_name == "health_probe_completed":
        return "completed"
    if request_status == 500 and event_name == "health_probe_failed":
        step = event.get("probe_step")
        code_status = event.get("error_code_status")
        code = event.get("error_code")
        if (
            not isinstance(step, str)
            or step not in _PROBE_STEPS
            or code_status not in {"known", "unknown"}
        ):
            raise _stop("probe_event_invalid")
        if code_status == "known":
            if type(code) is not int or not -(2**31) <= code <= 2**31 - 1:
                raise _stop("probe_event_invalid")
        elif code is not None:
            raise _stop("probe_event_invalid")
        return "failed"
    raise _stop("probe_event_status_mismatch")


def validate_canary_preconditions(
    *,
    service: Mapping[str, Any],
    candidate_revision: Mapping[str, Any],
    old_revision: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    project: str,
    region: str,
    service_name: str,
    now: datetime,
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Validate both supported admission target selector shapes and all safe gates."""
    if (
        not PROJECT_ID.fullmatch(project)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", region)
        or not SERVICE_NAME.fullmatch(service_name)
    ):
        raise _stop("target_config_invalid")
    if not _service_converged(service):
        raise _stop("service_not_converged")
    template = service.get("template")
    if not isinstance(template, Mapping):
        raise _stop("runtime_config_invalid")
    target_env = _container_env(template.get("containers"))
    target = _runtime_target(target_env, service=service_name)
    try:
        manifest = load_runtime_target_manifest()
        targets = [item for item in manifest.targets if item.id == "hk"]
    except Exception:
        raise _stop("runtime_target_admission_failed") from None
    if (
        len(targets) != 1
        or manifest.platform_id != "longbridge"
        or targets[0].mode != "live"
        or targets[0].account_scope != "HK"
        or targets[0].service != service_name
        or targets[0].region != region
        or targets[0].strategy_profile != target.get("strategy_profile")
        or target_env.get("STRATEGY_PROFILE") != targets[0].strategy_profile
        or target_env.get("RUNTIME_TARGET_ENABLED") != "false"
        or target.get("dry_run_only") is not True
    ):
        raise _stop("runtime_target_not_disabled")
    if _revision_commit(old_revision) != BASE_APPLICATION_SHA or not _revision_ready(
        old_revision
    ):
        raise _stop("serving_source_mismatch")
    if _revision_commit(
        candidate_revision
    ) != APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE or not _revision_ready(
        candidate_revision
    ):
        raise _stop("candidate_revision_not_ready")
    candidate_env = _container_env(candidate_revision.get("containers"))
    if candidate_env != target_env or not _revision_configuration_matches(
        template, candidate_revision
    ):
        raise _stop("candidate_config_mismatch")
    old_revision_name = _revision_name(old_revision)
    candidate_revision_name = _revision_name(candidate_revision)
    if old_revision_name == candidate_revision_name:
        raise _stop("candidate_revision_mismatch")
    _status_traffic_exactly_old100(service, old_revision_name)
    base_uri = _base_uri(service)
    selected, probe_resource = _resource_jobs(
        jobs, project=project, region=region, service=service_name, base_uri=base_uri
    )
    _require_quiet_window(selected, now.astimezone(timezone.utc))
    probe = selected[f"{service_name}-probe-scheduler"]
    _validate_original_probe_job(probe, resource=probe_resource, base_uri=base_uri)
    traffic = _configured_traffic(service)
    active = [row for row in traffic if row["percent"] > 0]
    if (
        len(active) != 1
        or active[0]["revision"] != old_revision_name
        or active[0]["percent"] != 100
    ):
        raise _stop("configured_traffic_not_old100")
    return (
        selected,
        {
            "base_uri": base_uri,
            "probe_resource": probe_resource,
            "target": dict(target),
        },
        traffic,
    )


def _revision_name(revision: Mapping[str, Any]) -> str:
    name = revision.get("name")
    if isinstance(name, str) and name:
        return name.rsplit("/", 1)[-1]
    raise _stop("revision_identity_invalid")


def _service_config_view(service: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in service.items()
        if key not in _SERVICE_OUTPUT_FIELDS | {"traffic"}
    }


def _create_backup(
    *,
    backup_path: str,
    service: Mapping[str, Any],
    candidate: Mapping[str, Any],
    old: Mapping[str, Any],
    jobs: Mapping[str, Mapping[str, Any]],
) -> None:
    payload = {
        "schema_version": "hk_notification_canary_backup.v1",
        "service": service,
        "candidate_revision": candidate,
        "old_revision": old,
        "scheduler_jobs": dict(jobs),
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if backup_path != str(BACKUP_PATH):
        raise _stop("backup_config_invalid")
    _create_backup_file(BACKUP_PATH, _canonical(payload), root=BACKUP_ROOT)


def _job_run_status(job: Mapping[str, Any], *, started_at: datetime) -> int | None:
    if job.get("state") != "PAUSED":
        return None
    try:
        last = _aware(job.get("lastAttemptTime"))
    except CanaryError:
        return None
    status = job.get("status")
    if not isinstance(status, Mapping):
        return None
    code = status.get("code", 0)
    if last < started_at or type(code) is not int or not 0 <= code <= 16:
        return None
    return code


def _health_event(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = entry.get("jsonPayload")
    if not isinstance(payload, Mapping):
        return None
    event = payload.get("event")
    if event not in {"health_probe_completed", "health_probe_failed"}:
        return None
    return payload


def _request_record(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = entry.get("httpRequest")
    if not isinstance(payload, Mapping):
        return None
    request_url = payload.get("requestUrl")
    resource = entry.get("resource")
    labels = resource.get("labels") if isinstance(resource, Mapping) else None
    return {
        "revision": labels.get("revision_name")
        if isinstance(labels, Mapping)
        else None,
        "timestamp": entry.get("timestamp"),
        "method": payload.get("requestMethod"),
        "url": request_url,
        "status": payload.get("status"),
        "latency": payload.get("latency"),
        "service": labels.get("service_name") if isinstance(labels, Mapping) else None,
        "resource_type": resource.get("type")
        if isinstance(resource, Mapping)
        else None,
    }


def _trace(entry: Mapping[str, Any]) -> str:
    value = entry.get("trace") or entry.get("logging.googleapis.com/trace") or ""
    return str(value)


def _parse_terminal_logs(
    entries: Sequence[Mapping[str, Any]],
    *,
    expected_revision: str,
    expected_service: str,
    expected_host: str,
) -> tuple[int, Mapping[str, Any]] | None:
    requests = [
        record for entry in entries if (record := _request_record(entry)) is not None
    ]
    events = [event for entry in entries if (event := _health_event(entry)) is not None]
    if len(requests) > 1 or len(events) > 1:
        raise _stop("probe_terminal_ambiguous")
    if not requests or not events:
        return None
    request = requests[0]
    request_url = request.get("url")
    try:
        parsed_url = urlsplit(request_url) if isinstance(request_url, str) else None
    except ValueError:
        return None
    status = request.get("status")
    if (
        request.get("revision") != expected_revision
        or request.get("service") != expected_service
        or request.get("resource_type") != "cloud_run_revision"
        or request.get("method") != "POST"
        or parsed_url is None
        or parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.hostname != expected_host
        or parsed_url.path != "/probe"
        or parsed_url.query
        or parsed_url.fragment
        or type(status) is not int
    ):
        raise _stop("probe_request_invalid")
    event_entry = next(entry for entry in entries if _health_event(entry) is events[0])
    event_resource = event_entry.get("resource")
    event_labels = (
        event_resource.get("labels") if isinstance(event_resource, Mapping) else None
    )
    if (
        not isinstance(event_labels, Mapping)
        or event_labels.get("revision_name") != expected_revision
        or event_labels.get("service_name") != expected_service
        or event_resource.get("type") != "cloud_run_revision"
    ):
        raise _stop("probe_event_invalid")
    request_entry = next(
        entry for entry in entries if _request_record(entry) is not None
    )
    request_trace, event_trace = _trace(request_entry), _trace(event_entry)
    if request_trace and event_trace and request_trace != event_trace:
        return None
    return status, events[0]


def _validate_previous_request(
    entries: Sequence[Mapping[str, Any]],
    *,
    old_revision: str,
    service: str,
    base_uri: str,
    now: datetime,
) -> None:
    requests = [
        record for entry in entries if (record := _request_record(entry)) is not None
    ]
    if len(requests) != 1:
        raise _stop("previous_request_window_changed")
    record = requests[0]
    try:
        request_url = urlsplit(record["url"])
        observed_at = _aware(record["timestamp"])
    except Exception:
        raise _stop("previous_request_evidence_invalid") from None
    if (
        record.get("revision") != old_revision
        or record.get("service") != service
        or record.get("resource_type") != "cloud_run_revision"
        or record.get("method") != "POST"
        or request_url.scheme != "https"
        or request_url.hostname != urlsplit(base_uri).hostname
        or request_url.path != "/probe"
        or request_url.query
        or request_url.fragment
        or record.get("status") != 500
        or record.get("latency") != PREVIOUS_REQUEST_LATENCY
        or record.get("timestamp") != PREVIOUS_REQUEST_TIMESTAMP
        or now - observed_at < IN_FLIGHT_QUIET_WINDOW
    ):
        raise _stop("previous_request_evidence_mismatch")


def _log_filter(
    *, service: str, started_at: str | datetime, requests_only: bool = False
) -> str:
    timestamp = (
        started_at
        if isinstance(started_at, str)
        else started_at.isoformat().replace("+00:00", "Z")
    )
    request_filter = "httpRequest.requestMethod:*"
    if not requests_only:
        request_filter = f'({request_filter} OR jsonPayload.event="health_probe_completed" OR jsonPayload.event="health_probe_failed")'
    return (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}" '
        f'AND timestamp >= "{timestamp}" '
        f"AND {request_filter}"
    )


def _read_terminal_logs(
    session: Any,
    *,
    project: str,
    filter_text: str,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    for _ in range(MAX_LOG_PAGES):
        timeout = HTTP_TIMEOUT_SECONDS
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _stop("probe_terminal_unknown")
            timeout = min(timeout, remaining)
        body: dict[str, Any] = {
            "resourceNames": [f"projects/{project}"],
            "filter": filter_text,
            "pageSize": MAX_LOG_ROWS,
            "orderBy": "timestamp asc",
        }
        if page_token:
            body["pageToken"] = page_token
        response = session.post(
            "https://logging.googleapis.com/v2/entries:list?alt=json",
            json=body,
            timeout=timeout,
            allow_redirects=False,
        )
        try:
            payload = _read_json(response, max_bytes=MAX_LOG_BODY_BYTES)
        finally:
            snapshot._close(response)
        page = payload.get("entries", [])
        if not isinstance(page, list) or any(
            not isinstance(item, Mapping) for item in page
        ):
            raise _stop("probe_log_invalid")
        entries.extend(page)
        if len(entries) > MAX_LOG_ENTRIES:
            raise _stop("probe_log_window_too_large")
        page_token = payload.get("nextPageToken")
        if page_token is None:
            return entries
        if not isinstance(page_token, str) or not page_token:
            raise _stop("probe_log_pagination_invalid")
        if page_token in seen_tokens:
            raise _stop("probe_log_pagination_invalid")
        seen_tokens.add(page_token)
    raise _stop("probe_log_window_incomplete")


def _wait_terminal(
    *,
    session: Any,
    project: str,
    service: str,
    candidate_revision: str,
    candidate_host: str,
    job_config: Any,
    started_at: datetime,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[str, int, Mapping[str, Any]]:
    deadline = monotonic() + MAX_WAIT_SECONDS
    filter_text = _log_filter(
        service=service,
        started_at=started_at,
    )
    while monotonic() < deadline:
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise _stop("probe_terminal_unknown")
            response = session.get(
                f"https://cloudscheduler.googleapis.com/v1/{job_config.scheduler_resource}",
                timeout=min(HTTP_TIMEOUT_SECONDS, remaining),
                allow_redirects=False,
            )
            try:
                job = _read_json(response)
            finally:
                snapshot._close(response)
            terminal_code = _job_run_status(job, started_at=started_at)
            events = _read_terminal_logs(
                session,
                project=project,
                filter_text=filter_text,
                deadline=deadline,
                monotonic=monotonic,
            )
            parsed = _parse_terminal_logs(
                events,
                expected_revision=candidate_revision,
                expected_service=service,
                expected_host=candidate_host,
            )
            if terminal_code is not None and parsed is not None:
                request_status, event = parsed
                scheduler_success = terminal_code == 0
                if scheduler_success != (request_status == 200):
                    raise _stop("scheduler_http_result_mismatch")
                return _candidate_outcome(request_status, event), request_status, event
        except CanaryError:
            raise
        except Exception:
            raise _stop("probe_terminal_unknown") from None
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(min(POLL_SECONDS, remaining))
    raise _stop("probe_terminal_unknown")


def _tagged_traffic(
    original: Sequence[Mapping[str, Any]], *, revision: str, tag: str
) -> list[dict[str, Any]]:
    if any(item.get("tag") == tag for item in original):
        raise _stop("tag_collision")
    appended = [deepcopy(dict(item)) for item in original]
    appended.append(
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": revision,
            "percent": 0,
            "tag": tag,
        }
    )
    return appended


def _service_tag_is_exact(
    service: Mapping[str, Any],
    *,
    original_rows: Sequence[Mapping[str, Any]],
    revision: str,
    tag: str,
) -> bool:
    rows = _traffic_rows(service)
    before = sorted((r["revision"], r["percent"], r["tag"]) for r in original_rows)
    after = sorted((r["revision"], r["percent"], r["tag"]) for r in rows)
    return after == sorted(before + [(revision, 0, tag)])


def _remove_owned_tag(
    service: Mapping[str, Any], *, revision: str, tag: str
) -> list[dict[str, Any]]:
    traffic = _configured_traffic(service)
    matches = [item for item in traffic if item.get("tag") == tag]
    if not matches:
        return traffic
    if (
        len(matches) != 1
        or matches[0].get("revision") != revision
        or matches[0].get("percent") != 0
    ):
        raise _stop("owned_tag_mismatch")
    return [item for item in traffic if item.get("tag") != tag]


def run_hk_notification_canary(
    *,
    env: Mapping[str, str],
    session_factory: Callable[[], Any] = _http_session,
    now_reader: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run once, then restore config/tag only after one terminal candidate request."""
    if env.get("WORKFLOW_TARGET") != "HK" or env.get("GITHUB_REF") != "refs/heads/main":
        raise _stop("workflow_context_invalid")
    if env.get("GITHUB_REPOSITORY") != "QuantStrategyLab/LongBridgePlatform":
        raise _stop("workflow_context_invalid")
    project = str(env.get("GOOGLE_CLOUD_PROJECT") or "")
    backup_path = str(env.get("HK_NOTIFICATION_CANARY_BACKUP_PATH") or "")
    service_name = str(env.get("CLOUD_RUN_SERVICE") or "")
    region = str(env.get("CLOUD_RUN_REGION") or "")
    candidate_name = str(env.get("HK_CANDIDATE_REVISION") or "")
    if (
        not PROJECT_ID.fullmatch(project)
        or not SERVICE_NAME.fullmatch(service_name)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", region)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", candidate_name)
        or backup_path != str(BACKUP_PATH)
    ):
        raise _stop("target_config_invalid")
    if not GIT_SHA.fullmatch(
        APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
    ) or not GIT_SHA.fullmatch(BASE_APPLICATION_SHA):
        raise _stop("source_pin_invalid")

    session = session_factory()
    service_resource = _service_resource(project, region, service_name)
    probe_resource = ""
    original_job: Mapping[str, Any] | None = None
    original_service: Mapping[str, Any] | None = None
    candidate_revision: Mapping[str, Any] | None = None
    selected_jobs: dict[str, Mapping[str, Any]] = {}
    traffic_before: list[dict[str, Any]] = []
    tag = f"hk-canary-{uuid.uuid4().hex[:12]}"
    probe_resumed = False
    pause_attempted = False
    try:
        original_service = _service_get(session, service_resource)
        candidate_revision = _revision_get(
            session, project, region, service_name, candidate_name
        )
        serving_rows = [
            row for row in _traffic_rows(original_service) if row["percent"] == 100
        ]
        if len(serving_rows) != 1:
            raise _stop("serving_revision_unknown")
        old_revision_name = serving_rows[0]["revision"]
        old_revision = _revision_get(
            session, project, region, service_name, old_revision_name
        )
        service_uri = _base_uri(original_service)
        jobs_list = _scheduler_list(session, project, region)
        selected_jobs, _ = _resource_jobs(
            jobs_list,
            project=project,
            region=region,
            service=service_name,
            base_uri=service_uri,
        )
        current_time = now_reader().astimezone(timezone.utc)
        selected_jobs, contract, traffic_before = validate_canary_preconditions(
            service=original_service,
            candidate_revision=candidate_revision,
            old_revision=old_revision,
            jobs=jobs_list,
            project=project,
            region=region,
            service_name=service_name,
            now=current_time,
        )
        probe_resource = contract["probe_resource"]
        original_job = selected_jobs[f"{service_name}-probe-scheduler"]
        old_revision_name = _revision_name(old_revision)
        history_filter = _log_filter(
            service=service_name,
            started_at=PREVIOUS_REQUEST_START,
            requests_only=True,
        )
        previous_requests = _read_terminal_logs(
            session, project=project, filter_text=history_filter
        )
        _validate_previous_request(
            previous_requests,
            old_revision=old_revision_name,
            service=service_name,
            base_uri=contract["base_uri"],
            now=current_time,
        )
        _validate_image_only_source(
            image_commit=APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE,
            env={"WORKFLOW_TARGET": "HK"},
            project=project,
        )
        if not TAG_NAME.fullmatch(tag):
            raise _stop("tag_invalid")

        # Detect state drift since the preflight read before starting writes.
        service_recheck = _service_get(session, service_resource)
        jobs_recheck = _scheduler_list(session, project, region)
        rechecked, _, recheck_traffic = validate_canary_preconditions(
            service=service_recheck,
            candidate_revision=_revision_get(
                session, project, region, service_name, candidate_name
            ),
            old_revision=_revision_get(
                session, project, region, service_name, old_revision_name
            ),
            jobs=jobs_recheck,
            project=project,
            region=region,
            service_name=service_name,
            now=now_reader().astimezone(timezone.utc),
        )
        if (
            _service_config_view(service_recheck)
            != _service_config_view(original_service)
            or recheck_traffic != traffic_before
            or {key: _job_control_view(value) for key, value in rechecked.items()}
            != {key: _job_control_view(value) for key, value in selected_jobs.items()}
        ):
            raise _stop("preflight_state_changed")
        previous_requests = _read_terminal_logs(
            session, project=project, filter_text=history_filter
        )
        _validate_previous_request(
            previous_requests,
            old_revision=old_revision_name,
            service=service_name,
            base_uri=contract["base_uri"],
            now=now_reader().astimezone(timezone.utc),
        )

        # Persist the private config snapshot immediately before the first write.
        _create_backup(
            backup_path=backup_path,
            service=original_service,
            candidate=candidate_revision,
            old=old_revision,
            jobs=selected_jobs,
        )

        candidate_revision_name = _revision_name(candidate_revision)
        traffic_with_tag = _tagged_traffic(
            traffic_before, revision=candidate_revision_name, tag=tag
        )
        etag = str(original_service.get("etag") or "")
        if not etag:
            raise _stop("service_etag_missing")
        try:
            _service_patch_traffic(
                session, service_resource, etag=etag, traffic=traffic_with_tag
            )
        except Exception:
            try:
                _service_get(session, service_resource)
            except Exception:
                pass
            raise _stop("service_tag_mutation_unknown") from None
        tagged_service = _service_get(session, service_resource)
        if not _service_converged(tagged_service) or not _service_tag_is_exact(
            tagged_service,
            original_rows=_traffic_rows(original_service),
            revision=candidate_revision_name,
            tag=tag,
        ):
            raise _stop("candidate_tag_readback_failed")
        tagged_url = _candidate_tag_url(
            tagged_service, candidate_revision=candidate_revision_name, tag=tag
        )

        base_probe_uri = f"{contract['base_uri']}/probe"
        _validate_original_probe_job(
            original_job, resource=probe_resource, base_uri=contract["base_uri"]
        )
        temporary_probe_uri = f"{tagged_url}/probe"
        try:
            _scheduler_patch_uri(session, probe_resource, temporary_probe_uri)
        except Exception:
            try:
                snapshot._scheduler_get(
                    session, SimpleNamespace(scheduler_resource=probe_resource)
                )
            except Exception:
                pass
            raise _stop("scheduler_uri_mutation_unknown") from None
        job_config = SimpleNamespace(scheduler_resource=probe_resource)

        def pause_once() -> None:
            nonlocal pause_attempted, probe_resumed
            if pause_attempted:
                return
            pause_attempted = True
            try:
                snapshot._scheduler_action(session, job_config, "pause")
            except Exception:
                try:
                    snapshot._scheduler_get(session, job_config)
                except Exception:
                    pass
                raise _stop("probe_pause_unknown") from None
            probe_resumed = False

        temp_job = snapshot._scheduler_get(session, job_config)
        expected_temp = _with_uri(original_job, f"{tagged_url}/probe")
        if temp_job.get("state") != "PAUSED" or _job_control_view(
            temp_job
        ) != _job_control_view(expected_temp):
            raise _stop("probe_job_temporary_readback_failed")

        original_last_attempt = original_job.get("lastAttemptTime")
        try:
            snapshot._scheduler_action(
                session,
                job_config,
                "resume",
            )
        except Exception:
            # Unknown resume is read once; never infer it succeeded.
            try:
                resume_read = snapshot._scheduler_get(
                    session,
                    job_config,
                )
            except Exception:
                raise _stop("probe_resume_state_unknown") from None
            if resume_read.get("state") == "ENABLED":
                probe_resumed = True
                pause_once()
            raise _stop("probe_resume_unknown") from None
        probe_resumed = True
        resumed_job = snapshot._scheduler_get(session, job_config)
        if (
            resumed_job.get("state") != "ENABLED"
            or _job_control_view(resumed_job) != _job_control_view(expected_temp)
            or resumed_job.get("lastAttemptTime") != original_last_attempt
        ):
            if resumed_job.get("state") == "ENABLED":
                pause_once()
            raise _stop("probe_resume_readback_mismatch")

        started_at = now_reader().astimezone(timezone.utc)
        if any(
            abs(item - started_at) < IN_FLIGHT_QUIET_WINDOW
            for job in selected_jobs.values()
            for item in _cron_next_times(job, started_at)
        ):
            pause_once()
            raise _stop("scheduler_natural_window")
        try:
            snapshot._scheduler_run(session, job_config)
        except Exception:
            pass

        # This is the first operation after the one run request: pause immediately.
        pause_once()
        paused_job = snapshot._scheduler_get(session, job_config)
        if paused_job.get("state") != "PAUSED":
            raise _stop("probe_pause_readback_failed")

        outcome, request_status, _event = _wait_terminal(
            session=session,
            project=project,
            service=service_name,
            candidate_revision=candidate_revision_name,
            candidate_host=urlsplit(tagged_url).hostname or "",
            job_config=job_config,
            started_at=started_at,
            monotonic=monotonic,
            sleep=sleep,
        )
        del request_status

        # Restore only our modified URI, then remove only our unique tag.
        try:
            _scheduler_patch_uri(session, probe_resource, base_probe_uri)
        except Exception:
            try:
                snapshot._scheduler_get(session, job_config)
            except Exception:
                pass
            raise _stop("scheduler_uri_restore_unknown") from None
        restored_job = snapshot._scheduler_get(session, job_config)
        if restored_job.get("state") != "PAUSED" or _job_control_view(
            restored_job
        ) != _job_control_view(original_job):
            raise _stop("probe_job_restore_mismatch")
        current_service = _service_get(session, service_resource)
        if not _service_converged(current_service) or not _service_tag_is_exact(
            current_service,
            original_rows=_traffic_rows(original_service),
            revision=candidate_revision_name,
            tag=tag,
        ):
            raise _stop("candidate_tag_changed")
        final_traffic = _remove_owned_tag(
            current_service, revision=candidate_revision_name, tag=tag
        )
        try:
            _service_patch_traffic(
                session,
                service_resource,
                etag=str(current_service.get("etag") or ""),
                traffic=final_traffic,
            )
        except Exception:
            try:
                _service_get(session, service_resource)
            except Exception:
                pass
            raise _stop("service_tag_restore_unknown") from None
        final_service = _service_get(session, service_resource)
        if (
            not _service_converged(final_service)
            or _traffic_rows(final_service) != _traffic_rows(original_service)
            or _configured_traffic(final_service) != traffic_before
            or _service_config_view(final_service)
            != _service_config_view(original_service)
        ):
            raise _stop("service_restore_mismatch")
        final_jobs = _scheduler_list(session, project, region)
        final_selected, _ = _resource_jobs(
            final_jobs,
            project=project,
            region=region,
            service=service_name,
            base_uri=contract["base_uri"],
        )
        if (
            set(final_selected) != set(selected_jobs)
            or any(item.get("state") != "PAUSED" for item in final_selected.values())
            or {key: _job_control_view(value) for key, value in final_selected.items()}
            != {key: _job_control_view(value) for key, value in selected_jobs.items()}
        ):
            raise _stop("scheduler_restore_mismatch")
        return outcome
    except Exception:
        if probe_resumed and not pause_attempted:
            try:
                pause_once()
            except Exception:
                pass
        raise
    finally:
        snapshot._close(session)


def main() -> int:
    try:
        result = run_hk_notification_canary(env=os.environ)
    except CanaryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("hk_notification_canary_failed", file=sys.stderr)
        return 1
    print(f"hk_notification_canary_{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
