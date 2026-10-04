"""Bounded current HK metadata, using an injected already-authorized session.

This library creates no credentials or clients and has no default CLI. Metadata
claims describe configuration only, never archive write permission, an original
request receipt, current account health or physical broker identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

SERVICE_RESOURCE = (
    "projects/longbridgequant/locations/asia-east2/services/longbridge-quant-hk-service"
)
SCHEDULER_RESOURCE = (
    "projects/longbridgequant/locations/asia-east2/jobs/"
    "longbridge-quant-hk-service-probe-scheduler"
)
RUNTIME_WRITER = "longbridge-platform-runtime@longbridgequant.iam.gserviceaccount.com"
SCHEDULER_CALLER = (
    "longbridge-platform-scheduler@longbridgequant.iam.gserviceaccount.com"
)
OBSERVER = "longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com"
SERVICE_FIELDS = (
    "name,uid,etag,generation,observedGeneration,reconciling,terminalCondition/state,"
    "traffic(type,revision,percent,tag),trafficStatuses(type,revision,percent,tag)"
)
REVISION_FIELDS = (
    "name,service,uid,etag,generation,observedGeneration,reconciling,serviceAccount,"
    "containers(image,env(name))"
)
SCHEDULER_FIELDS = "name,state,lastAttemptTime,status/code,userUpdateTime"
ARCHIVE_FLAGS = (
    "ACCOUNT_HISTORY_RECORDING_ENABLED",
    "ACCOUNT_HISTORY_GCS_PREFIX",
    "ACCOUNT_HISTORY_TARGET_ID",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE",
)
MAX_REQUESTS = 5
MAX_RESPONSE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = MAX_REQUESTS * MAX_RESPONSE_BYTES
MAX_OUTPUT_BYTES = 8 * 1024
TOTAL_SECONDS = 180.0
RPC_SECONDS = 15.0
MAX_REVISIONS = 2
MAX_TRAFFIC_ROWS = 8
MAX_CONTAINERS = 2
MAX_ENV_NAMES = 128
_TRAFFIC_TYPES = {
    "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST",
    "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
    "TRAFFIC_TARGET_ALLOCATION_TYPE_UNSPECIFIED",
}
_STATES = {"STATE_UNSPECIFIED", "ENABLED", "PAUSED", "DISABLED", "UPDATE_FAILED"}


class MetadataRejected(ValueError):
    def __init__(self, category: str, *, http_status: int | None = None):
        super().__init__(category)
        self.category = category
        self.http_status = http_status


def _reject(category: str) -> None:
    raise MetadataRejected(category)


def _text(value: Any, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _reject("metadata_schema_invalid")
    return value


def _keys(value: Any, allowed: set[str], required: set[str]) -> None:
    if (
        not isinstance(value, dict)
        or not required <= set(value)
        or set(value) - allowed
    ):
        _reject("metadata_schema_invalid")


def _generation(value: Any) -> int:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[1-9][0-9]{0,18}", value) is None
        or int(value) > 2**63 - 1
    ):
        _reject("metadata_schema_invalid")
    return int(value)


def _duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("metadata_schema_invalid")
        result[key] = value
    return result


def _revision_resource(value: Any) -> str:
    value = _text(value, 512)
    prefix = SERVICE_RESOURCE + "/revisions/"
    if value.startswith(prefix):
        name = value[len(prefix) :]
    elif "/" not in value:
        name = value
    else:
        _reject("metadata_resource_invalid")
    if (
        not name.startswith("longbridge-quant-hk-service-")
        or re.fullmatch(r"[a-z][a-z0-9-]{0,99}", name) is None
    ):
        _reject("metadata_resource_invalid")
    return prefix + name


class _Budget:
    def __init__(self, monotonic: Callable[[], float]):
        self.monotonic = monotonic
        self.started = self._clock()
        self.deadline = self.started + TOTAL_SECONDS
        self.calls = 0
        self.bytes = 0

    def _clock(self) -> float:
        try:
            value = self.monotonic()
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError
            return float(value)
        except Exception:
            raise MetadataRejected("metadata_deadline") from None

    def remaining(self) -> float:
        try:
            current = self._clock()
            if current < self.started:
                raise ValueError
            remaining = self.deadline - current
            if not math.isfinite(remaining) or remaining <= 0:
                raise ValueError
        except Exception:
            _reject("metadata_deadline")
        return remaining

    def get(self, session: Any, resource: str, fields: str) -> dict:
        if resource == SERVICE_RESOURCE:
            base = "https://run.googleapis.com/v2/" + resource
            expected = SERVICE_FIELDS
        elif resource == SCHEDULER_RESOURCE:
            base = "https://cloudscheduler.googleapis.com/v1/" + resource
            expected = SCHEDULER_FIELDS
        elif resource == _revision_resource(resource):
            base = "https://run.googleapis.com/v2/" + resource
            expected = REVISION_FIELDS
        else:
            _reject("metadata_resource_invalid")
        if fields != expected or self.calls >= MAX_REQUESTS:
            _reject("metadata_request_limit")
        timeout = min(RPC_SECONDS, self.remaining())
        self.calls += 1
        url = base + "?" + urlencode({"fields": fields, "prettyPrint": "false"})
        response = None
        try:
            response = session.request(
                "GET", url, timeout=timeout, allow_redirects=False, stream=True
            )
            code = response.status_code
            if (
                not isinstance(code, int)
                or isinstance(code, bool)
                or not 100 <= code <= 599
            ):
                _reject("metadata_http_invalid")
            if (
                response.is_redirect
                or response.history
                or code in {301, 302, 303, 307, 308}
            ):
                _reject("metadata_redirect_rejected")
            if code != 200:
                raise MetadataRejected(
                    "metadata_http_denied"
                    if code in {401, 403}
                    else "metadata_http_failed",
                    http_status=int(code),
                )
            actual, wanted = urlsplit(response.url), urlsplit(url)
            if (
                (actual.scheme, actual.netloc, actual.path)
                != (wanted.scheme, wanted.netloc, wanted.path)
                or parse_qsl(actual.query) != parse_qsl(wanted.query)
                or actual.fragment
            ):
                _reject("metadata_redirect_rejected")
            chunks = []
            size = 0
            # One decoded sentinel byte detects overflow without accepting a
            # full extra transport chunk beyond either decoded-response limit.
            for chunk in response.iter_content(chunk_size=1):
                self.remaining()
                if not isinstance(chunk, bytes):
                    _reject("metadata_body_invalid")
                size += len(chunk)
                self.bytes += len(chunk)
                if size > MAX_RESPONSE_BYTES or self.bytes > MAX_TOTAL_BYTES:
                    _reject("metadata_byte_limit")
                chunks.append(chunk)
            value = json.loads(
                b"".join(chunks),
                object_pairs_hook=_duplicates,
                parse_constant=lambda _: _reject("metadata_schema_invalid"),
            )
            if not isinstance(value, dict):
                _reject("metadata_schema_invalid")
            if "nextPageToken" in value:
                _reject("metadata_pagination_rejected")
            self.remaining()
            return value
        except MetadataRejected:
            raise
        except Exception:
            raise MetadataRejected("metadata_transport_failed") from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass


def _traffic(rows: Any, *, observed: bool) -> tuple[list[tuple], dict[str, int]]:
    if not isinstance(rows, list) or len(rows) > MAX_TRAFFIC_ROWS:
        _reject("metadata_traffic_invalid")
    canonical = []
    positive = {}
    for row in rows:
        _keys(row, {"type", "revision", "percent", "tag"}, set())
        kind = row.get("type", "TRAFFIC_TARGET_ALLOCATION_TYPE_UNSPECIFIED")
        percent = row.get("percent", 0)
        if (
            not isinstance(kind, str)
            or kind not in _TRAFFIC_TYPES
            or type(percent) is not int
            or not 0 <= percent <= 100
        ):
            _reject("metadata_traffic_invalid")
        tag = row.get("tag", "")
        if not isinstance(tag, str) or len(tag) > 100:
            _reject("metadata_traffic_invalid")
        revision = row.get("revision", "")
        if not isinstance(revision, str):
            _reject("metadata_traffic_invalid")
        if revision:
            revision = _revision_resource(revision)
        elif observed and percent:
            _reject("metadata_traffic_invalid")
        elif kind == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION" and percent:
            _reject("metadata_traffic_invalid")
        canonical.append((kind, revision, percent, tag))
        if observed and percent:
            if revision in positive:
                _reject("metadata_traffic_invalid")
            positive[revision] = percent
    if len(set(canonical)) != len(canonical):
        _reject("metadata_traffic_invalid")
    if observed and (
        not positive or len(positive) > MAX_REVISIONS or sum(positive.values()) != 100
    ):
        _reject("metadata_traffic_invalid")
    if not observed and canonical and sum(row[2] for row in canonical) != 100:
        _reject("metadata_traffic_invalid")
    return sorted(canonical), positive


def _service(value: dict) -> tuple[tuple, dict[str, int]]:
    allowed = {
        "name",
        "uid",
        "etag",
        "generation",
        "observedGeneration",
        "reconciling",
        "terminalCondition",
        "traffic",
        "trafficStatuses",
    }
    _keys(value, allowed, allowed - {"traffic", "reconciling"})
    if value["name"] != SERVICE_RESOURCE:
        _reject("metadata_resource_invalid")
    generation = _generation(value["generation"])
    if (
        _generation(value["observedGeneration"]) != generation
        or value.get("reconciling", False) is not False
    ):
        _reject("metadata_service_unstable")
    _keys(value["terminalCondition"], {"state"}, {"state"})
    if value["terminalCondition"]["state"] != "CONDITION_SUCCEEDED":
        _reject("metadata_service_unstable")
    wanted, _ = _traffic(value.get("traffic", []), observed=False)
    observed, revisions = _traffic(value["trafficStatuses"], observed=True)
    explicit = {
        row[1]: row[2]
        for row in wanted
        if row[2] and row[0] == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
    }
    if any(revisions.get(name) != percent for name, percent in explicit.items()):
        _reject("metadata_service_unstable")
    if wanted:
        desired_latest = sum(
            row[2]
            for row in wanted
            if row[0] == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
        )
        actual_latest = sum(
            row[2]
            for row in observed
            if row[0] == "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
        )
        if desired_latest != actual_latest:
            _reject("metadata_service_unstable")
    return (
        _text(value["uid"]),
        _text(value["etag"]),
        generation,
        wanted,
        observed,
    ), revisions


def _revision(value: dict, resource: str, percent: int) -> dict:
    allowed = {
        "name",
        "service",
        "uid",
        "etag",
        "generation",
        "observedGeneration",
        "reconciling",
        "serviceAccount",
        "containers",
    }
    _keys(value, allowed, allowed - {"reconciling"})
    if value["name"] != resource or value["service"] != SERVICE_RESOURCE:
        _reject("metadata_resource_invalid")
    _text(value["uid"])
    _text(value["etag"])
    if value.get("reconciling", False) is not False or _generation(
        value["generation"]
    ) != _generation(value["observedGeneration"]):
        _reject("metadata_revision_unstable")
    writer = _text(value["serviceAccount"])
    containers = value["containers"]
    if not isinstance(containers, list) or not 1 <= len(containers) <= MAX_CONTAINERS:
        _reject("metadata_schema_invalid")
    projected = []
    for container in containers:
        _keys(container, {"image", "env"}, {"image"})
        image = _text(container["image"], 4096)
        names = []
        env = container.get("env", [])
        if not isinstance(env, list) or len(env) > MAX_ENV_NAMES:
            _reject("metadata_schema_invalid")
        for entry in env:
            _keys(entry, {"name"}, {"name"})
            name = _text(entry["name"], 256)
            if name in names:
                _reject("metadata_schema_invalid")
            names.append(name)
        digest = re.search(r"@sha256:([0-9a-f]{64})$", image)
        projected.append(
            {
                "image_reference_sha256": hashlib.sha256(image.encode()).hexdigest(),
                "immutable_image_digest": digest.group(1) if digest else None,
                "source_commit_sha": None,
                "archive_flag_presence": {
                    name: name in names for name in ARCHIVE_FLAGS
                },
                "archive_flag_values_confirmed": False,
            }
        )
    return {
        "revision": resource.rsplit("/", 1)[-1],
        "traffic_percent": percent,
        "runtime_writer_matches_configured": writer == RUNTIME_WRITER,
        "runtime_writer_identity_sha256": hashlib.sha256(writer.encode()).hexdigest(),
        "containers": projected,
    }


def _timestamp(value: Any) -> str:
    try:
        value = _text(value, 64)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError
        return stamp.astimezone(timezone.utc).isoformat()
    except Exception:
        raise MetadataRejected("metadata_schema_invalid") from None


def _scheduler(value: dict) -> dict:
    _keys(
        value,
        {"name", "state", "lastAttemptTime", "status", "userUpdateTime"},
        {"name", "state"},
    )
    if value["name"] != SCHEDULER_RESOURCE:
        _reject("metadata_resource_invalid")
    if not isinstance(value["state"], str) or value["state"] not in _STATES:
        _reject("metadata_schema_invalid")
    status = value.get("status", {})
    _keys(status, {"code"}, set())
    code = status.get("code", 0) if "status" in value else None
    if code is not None and (type(code) is not int or not 0 <= code <= 16):
        _reject("metadata_schema_invalid")
    return {
        "state": value["state"],
        "last_attempt_at": _timestamp(value["lastAttemptTime"])
        if "lastAttemptTime" in value
        else None,
        "user_updated_at": _timestamp(value["userUpdateTime"])
        if "userUpdateTime" in value
        else None,
        "status_code": code,
        "historical_oct2_invocation_confirmed": False,
    }


def inspect_hk_producer_metadata(
    *, session: Any, monotonic: Callable[[], float] = time.monotonic
) -> dict:
    """Read current fixed-resource configuration, never invoke a producer."""
    budget = _Budget(monotonic)
    first, revisions = _service(budget.get(session, SERVICE_RESOURCE, SERVICE_FIELDS))
    projected = [
        _revision(budget.get(session, resource, REVISION_FIELDS), resource, percent)
        for resource, percent in sorted(revisions.items())
    ]
    scheduler = _scheduler(budget.get(session, SCHEDULER_RESOURCE, SCHEDULER_FIELDS))
    last, _ = _service(budget.get(session, SERVICE_RESOURCE, SERVICE_FIELDS))
    if last != first:
        _reject("metadata_service_changed")
    result = {
        "evidence_kind": "current_hk_producer_metadata",
        "target": "hk",
        "serving_revisions": projected,
        "scheduler": scheduler,
        "metadata_get_count": budget.calls,
        "metadata_response_bytes": budget.bytes,
        "serving_configuration_consistent": True,
        "metadata_snapshot_atomic": False,
        "archive_flag_values_confirmed": False,
        "runtime_writer_storage_permission_confirmed": False,
        "current_health_confirmed": False,
        "request_terminal_confirmed": False,
        "receiver_ack_confirmed": False,
        "native_identity_confirmed": False,
    }
    if len(json.dumps(result, separators=(",", ":")).encode()) > MAX_OUTPUT_BYTES:
        _reject("metadata_output_limit")
    return result
