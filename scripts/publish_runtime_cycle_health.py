#!/usr/bin/env python3
"""Publish bounded LongBridge PAPER cycle observations to the admitted QRS source."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from scripts import execution_report_heartbeat as heartbeat
from scripts.collect_runtime_cycle_health import (
    CollectionUnavailable,
    _source_material,
    bounded_cycle_window_end,
    collect_runtime_cycle_health,
    cycle_configuration_sha256,
    runtime_report_prefix_ranges,
)
from scripts.runtime_cycle_health import CycleContext
from scripts.runtime_cycle_health_source import (
    SourceContractError,
    validate_source_ack,
    validate_source_checkpoint,
)
from scripts.runtime_heartbeat_policy import _market_session_dates, load_runtime_targets


_SOURCE_PATH = "/api/internal/runtime-cycle-health-source"
_SOURCE_ID = "longbridge.paper"
_TARGET_ID = "longbridge.paper"
_MAX_BODY_BYTES = 256 * 1024
_MAX_REPORT_BYTES = 1_048_576


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _endpoint(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.path != _SOURCE_PATH
    ):
        raise CollectionUnavailable("source_endpoint_unavailable")
    return f"https://{parsed.netloc}{_SOURCE_PATH}"


def _required(env: dict[str, str], name: str) -> str:
    value = str(env.get(name) or "").strip()
    if not value or len(value) > 4096:
        raise CollectionUnavailable("source_configuration_unavailable")
    return value


def _normalize_report_root(value: str) -> str:
    candidates = heartbeat._split_values(value)
    if len(candidates) != 1:
        raise CollectionUnavailable("report_prefix_unavailable")
    try:
        parsed = urlsplit(candidates[0])
        port = parsed.port
    except ValueError:
        raise CollectionUnavailable("report_prefix_unavailable") from None
    path = parsed.path.strip("/")
    if (
        parsed.scheme != "gs"
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or port is not None
        or any(char in parsed.path for char in "*?[]%")
        or (path and any(part in {"", ".", ".."} for part in path.split("/")))
    ):
        raise CollectionUnavailable("report_prefix_unavailable")
    return f"gs://{parsed.netloc}/{path}" if path else f"gs://{parsed.netloc}"


def _read_revision(service_payload: dict[str, Any], *, service: str, project: str, region: str) -> tuple[dict[str, Any], dict[str, str], str | None]:
    traffic = service_payload.get("status", {}).get("traffic")
    active = [row for row in traffic if isinstance(row, dict) and row.get("percent", 0) > 0] if isinstance(traffic, list) else []
    if len(active) != 1 or active[0].get("percent") != 100:
        raise CollectionUnavailable("serving_revision_unavailable")
    name = active[0].get("revisionName")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", name):
        raise CollectionUnavailable("serving_revision_unavailable")
    result = heartbeat._run_gcloud([
        "gcloud", "run", "revisions", "describe", name,
        "--project", project, "--region", region, "--format=json",
    ])
    if result.returncode != 0 or len(result.stdout.encode()) > _MAX_BODY_BYTES:
        raise CollectionUnavailable("serving_revision_unavailable")
    try:
        revision = json.loads(result.stdout)
        metadata = revision["metadata"]
        containers = revision["spec"]["containers"]
        conditions = revision["status"]["conditions"]
        rows = containers[0]["env"]
        if (
            metadata.get("name") != name
            or (metadata.get("labels") or {}).get("serving.knative.dev/service") != service
            or len(containers) != 1
            or len([item for item in conditions if item.get("type") == "Ready" and item.get("status") == "True"]) != 1
        ):
            raise ValueError()
        # Keep only non-secret source-contract fields; secret references and all
        # unrelated environment values are intentionally ignored.
        allowed = {
            "RUNTIME_TARGET_JSON", "RUNTIME_TARGET_ENABLED", "LONGBRIDGE_MARKET_CALENDAR",
            "LONGBRIDGE_MARKET_TIMEZONE", "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES",
            "EXECUTION_REPORT_GCS_URI", "RUNTIME_HEARTBEAT_GCS_URIS",
        }
        report_rows = [
            row for row in rows
            if isinstance(row, dict)
            and row.get("name") in {"EXECUTION_REPORT_GCS_URI", "RUNTIME_HEARTBEAT_GCS_URIS"}
        ]
        report_root = None
        if report_rows:
            if len(report_rows) != 1:
                raise CollectionUnavailable("report_prefix_unavailable")
            row = report_rows[0]
            # Never resolve a secret reference to discover a report location.
            if "valueSource" in row or not isinstance(row.get("value"), str) or not row["value"].strip():
                raise CollectionUnavailable("report_prefix_unavailable")
            report_roots = heartbeat._split_values(row["value"])
            if len(report_roots) != 1:
                raise CollectionUnavailable("report_prefix_unavailable")
            report_root = _normalize_report_root(report_roots[0])
        values = {
            row["name"]: str(row.get("value") or "")
            for row in rows
            if isinstance(row, dict) and row.get("name") in allowed
        }
        if len(values) != sum(1 for row in rows if isinstance(row, dict) and row.get("name") in allowed):
            raise ValueError()
        return revision, values, report_root
    except CollectionUnavailable:
        raise
    except (ValueError, TypeError, KeyError, IndexError):
        raise CollectionUnavailable("serving_revision_unavailable") from None


def _serving_source(env: dict[str, str], *, binding_id: str) -> dict[str, Any]:
    service = _required(env, "CLOUD_RUN_SERVICE")
    project = _required(env, "GCP_PROJECT_ID")
    region = _required(env, "CLOUD_RUN_REGION")
    policy_grace = _required(env, "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES")
    service_payload = heartbeat._describe_cloud_run_service(service, project=project)
    revision, values, report_root_uri = _read_revision(
        service_payload, service=service, project=project, region=region
    )
    raw_target = values.get("RUNTIME_TARGET_JSON")
    try:
        deployed = json.loads(raw_target or "")
        release = deployed["strategy_release"]
        strategy_revision = release["strategy_revision"]
        revision_commit = revision["metadata"]["labels"]["commit-sha"]
        if not isinstance(deployed, dict) or not isinstance(release, dict):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise CollectionUnavailable("serving_target_unavailable") from None
    values["RUNTIME_HEARTBEAT_ACCOUNT_SCOPE"] = "PAPER"
    values["RUNTIME_TARGET_ENABLED"] = values.get("RUNTIME_TARGET_ENABLED", "true")
    targets = load_runtime_targets(values, include_disabled=True)
    if len(targets) != 1:
        raise CollectionUnavailable("serving_target_unavailable")
    jobs = heartbeat._describe_scheduler_jobs_for_services([service], project=project)
    if len(jobs) != 1:
        raise CollectionUnavailable("scheduler_unavailable")
    service_info = {
        "metadata": service_payload.get("metadata", {}),
        "status": service_payload.get("status", {}),
    }
    serving_context = {
        "service": service_info,
        "revision": revision,
        "route_contract": {
            "service": service,
            "source_commit": revision_commit,
            "path": "/run",
            "http_method": "POST",
        },
    }
    return {
        "target": targets[0],
        "serving_context": serving_context,
        "jobs": jobs,
        "source_binding": {
            "id": binding_id,
            "service": service,
            "revision": revision["metadata"]["name"],
        },
        "release_contract": {
            "source_commit": revision_commit,
            "strategy_revision": strategy_revision,
        },
        "monitor_policy": {
            "RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES": policy_grace,
        },
        "report_root_uri": report_root_uri,
    }


def _qrs_request(method: str, url: str, *, token: str, binding_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    import requests

    headers = {
        "Authorization": f"Bearer {token}",
        "X-QSL-Source-Binding-ID": binding_id,
        "Content-Type": "application/json",
    }
    response = None
    try:
        response = requests.request(
            method,
            url,
            params={"source_id": _SOURCE_ID, "target_id": _TARGET_ID} if method == "GET" else None,
            json=payload,
            headers=headers,
            timeout=(3, 15),
            allow_redirects=False,
            stream=True,
        )
        if response.status_code != 200:
            raise CollectionUnavailable("source_transport_unconfirmed")
        body = bytearray()
        for chunk in response.iter_content(chunk_size=16 * 1024):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > _MAX_BODY_BYTES:
                raise CollectionUnavailable("source_transport_unconfirmed")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except CollectionUnavailable:
        raise
    except Exception:
        raise CollectionUnavailable("source_transport_unconfirmed") from None
    finally:
        if response is not None:
            response.close()


def _report_root(source: dict[str, Any], env: dict[str, str]) -> str:
    value = source.get("report_root_uri")
    if not isinstance(value, str) or not value:
        raise CollectionUnavailable("report_prefix_unavailable")
    normalized = _normalize_report_root(value)
    if normalized != value:
        raise CollectionUnavailable("report_prefix_unavailable")
    for name in ("RUNTIME_HEARTBEAT_GCS_URIS", "EXECUTION_REPORT_GCS_URI"):
        configured = str(env.get(name) or "").strip()
        if configured and _normalize_report_root(configured) != normalized:
            raise CollectionUnavailable("report_prefix_conflict")
    return normalized


def _gcs_callbacks(project: str, bucket_name: str):
    from google.cloud import storage

    client = storage.Client(project=project)

    def list_page_range(prefix: str, token: str | None, start_offset: str, end_offset: str):
        iterator = client.list_blobs(
            bucket_name,
            prefix=prefix,
            start_offset=start_offset,
            end_offset=end_offset,
            page_token=token,
            page_size=21,
            max_results=21,
            timeout=15,
            retry=None,
            fields="items(name),nextPageToken",
        )
        pages = iterator.pages
        try:
            page = next(pages)
        except StopIteration:
            page = ()
        return {
            "items": [{"name": blob.name} for blob in page],
            "nextPageToken": iterator.next_page_token,
        }

    def read_report(uri: str):
        blob = client.bucket(bucket_name).blob(uri)
        if isinstance(blob.size, int) and blob.size > _MAX_REPORT_BYTES:
            return None
        raw = blob.download_as_bytes(start=0, end=_MAX_REPORT_BYTES - 1, timeout=15, retry=None)
        if len(raw) >= _MAX_REPORT_BYTES:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None

    return list_page_range, read_report


def _target_envelope(env: dict[str, str], *, cycle_health: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    configured_state = str(env.get("RUNTIME_TARGET_CONFIGURED_STATE") or "").strip()
    execution_mode = str(env.get("RUNTIME_TARGET_EXECUTION_MODE") or "").strip()
    runtime_guard = str(env.get("RUNTIME_GUARD_STATUS") or "").strip()
    execution_heartbeat = str(env.get("EXECUTION_HEARTBEAT_STATUS") or "").strip()
    check_statuses = {"pass", "attention", "not_due", "not_applicable", "unavailable"}
    if (
        configured_state not in {"enabled", "disabled"}
        or execution_mode not in {"dry_run", "paper", "live"}
        or runtime_guard not in check_statuses
        or execution_heartbeat not in check_statuses
    ):
        raise CollectionUnavailable("lifecycle_status_unavailable")
    if runtime_guard == "attention":
        disposition, reason = "parked", "runtime_guard_attention"
    elif execution_heartbeat == "attention":
        disposition, reason = "parked", "execution_heartbeat_attention"
    elif "unavailable" in {runtime_guard, execution_heartbeat}:
        disposition, reason = "parked", "monitoring_unavailable"
    elif configured_state == "disabled":
        if execution_heartbeat != "not_applicable":
            raise CollectionUnavailable("lifecycle_status_unavailable")
        disposition, reason = "continue_disabled_validation", "target_intentionally_disabled"
    else:
        disposition, reason = "continue_enabled_monitoring", "none"
    stamp = now.isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "qsl_runtime_target_lifecycle_source_snapshot.v1",
        "source_id": _SOURCE_ID,
        "generated_at": stamp,
        "computed_at": stamp,
        "data_status": "ready",
        "errors": [],
        "targets": [{
            "target_id": _TARGET_ID,
            "target": {
                "platform": "longbridge",
                "configured_state": configured_state,
                "execution_mode": execution_mode,
            },
            "monitoring": {
                "runtime_guard": runtime_guard,
                "execution_heartbeat": execution_heartbeat,
            },
            "disposition": {"code": disposition, "reason_code": reason},
            "no_order": True,
            "cycle_health": cycle_health,
        }],
    }


def publish(env: dict[str, str] | None = None, *, now: dt.datetime | None = None) -> str:
    env = dict(os.environ if env is None else env)
    if str(env.get("RUNTIME_CYCLE_HEALTH_ENABLED") or "").strip().lower() != "true":
        return "disabled"
    now = (now or _utc_now()).astimezone(dt.timezone.utc).replace(microsecond=0)
    endpoint = _endpoint(_required(env, "RUNTIME_CYCLE_HEALTH_SYNC_URL"))
    token = _required(env, "ACCOUNT_FACTS_SYNC_TOKEN")
    binding_id = _required(env, "RUNTIME_CYCLE_HEALTH_SOURCE_BINDING_ID")
    source = _serving_source(env, binding_id=binding_id)
    report_root = _report_root(source, env)
    config_sha = cycle_configuration_sha256(source, report_root_uri=report_root)
    resolved, material, binding = _source_material(source)
    context = CycleContext(
        target_id=_TARGET_ID,
        service=resolved["service"],
        source_binding_id=binding["id"],
        configuration_sha256=config_sha,
        strategy_profile=resolved["strategy_profile"],
        strategy_revision=material["release"]["strategy_revision"],
        scheduler_job_name=resolved["scheduler"]["job_name"],
    )
    admitted = validate_source_checkpoint(
        _qrs_request("GET", endpoint, token=token, binding_id=binding_id),
        source_id=_SOURCE_ID,
        target_id=_TARGET_ID,
        source_binding_id=binding_id,
        expected_configuration_sha256=config_sha,
    )
    if admitted["required_from"] is None:
        raise CollectionUnavailable("source_baseline_unavailable")
    grace = dt.timedelta(minutes=float(material["monitor_policy"]["publication_grace_minutes"]))
    cursor = admitted["covered_through"] or admitted["required_from"]
    since = max(
        dt.datetime.fromisoformat(admitted["required_from"].replace("Z", "+00:00")),
        dt.datetime.fromisoformat(cursor.replace("Z", "+00:00")) - grace,
    )
    through, reason = bounded_cycle_window_end(
        resolved,
        since=since,
        through=now,
        publication_grace=grace,
        session_dates_loader=_market_session_dates,
    )
    if through is None:
        raise CollectionUnavailable(reason or "collection_window_unavailable")
    report_root_parts = urlsplit(report_root)
    if report_root_parts.scheme != "gs" or not report_root_parts.netloc:
        raise CollectionUnavailable("report_prefix_unavailable")
    prefixes = runtime_report_prefix_ranges(
        report_root,
        strategy_profile=resolved["strategy_profile"],
        account_scope="PAPER",
        since=since,
        through=through,
    )
    list_page_range, read_report = _gcs_callbacks(
        _required(env, "GCP_PROJECT_ID"), report_root_parts.netloc
    )
    result = collect_runtime_cycle_health(
        context=context,
        read_source=lambda: _serving_source(env, binding_id=binding_id),
        list_page=lambda _prefix, _token: {},
        list_page_range=list_page_range,
        list_ranges=prefixes,
        read_report=read_report,
        required_prefixes=list(prefixes),
        since=since,
        now=now,
        coverage_through=through,
        session_dates_loader=_market_session_dates,
    )
    cycle_health = result.get("cycle_health")
    if not isinstance(cycle_health, dict):
        raise CollectionUnavailable(str(result.get("reason") or "cycle_collection_unavailable"))
    cycle_health["resolutions"] = []
    body = _target_envelope(env, cycle_health=cycle_health, now=now)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > _MAX_BODY_BYTES:
        raise CollectionUnavailable("source_payload_too_large")
    digest = hashlib.sha256(encoded).hexdigest()
    ack = validate_source_ack(
        _qrs_request("POST", endpoint, token=token, binding_id=binding_id, payload=body),
        source_id=_SOURCE_ID,
        target_id=_TARGET_ID,
        source_binding_id=binding_id,
        configuration_sha256=config_sha,
        required_from=admitted["required_from"],
        expected_observation_sha256=digest,
        expected_observed_at=now.isoformat().replace("+00:00", "Z"),
        expected_coverage_through=cycle_health["coverage"]["through"],
        expected_previous_covered_through=admitted["covered_through"],
    )
    return "stored_complete" if ack["coverage_complete"] else "stored_incomplete"


def main() -> int:
    try:
        status = publish()
    except (CollectionUnavailable, SourceContractError):
        print("LongBridge cycle-health source: unavailable")
        return 2
    except Exception:
        print("LongBridge cycle-health source: unavailable")
        return 2
    print(f"LongBridge cycle-health source: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
