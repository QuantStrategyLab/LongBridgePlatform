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
_SCHEDULER_KEYS = {"name", "state", "lastAttemptTime", "status", "userUpdateTime"}
SCHEDULER_SCHEMA_CHECKS = (
    "profile_available",
    "object_type_valid",
    "required_keys_present",
    "allowed_keys_only",
    "name_present",
    "name_type_valid",
    "state_present",
    "state_is_null",
    "state_type_valid",
    "state_enum_valid",
    "status_present",
    "status_is_null",
    "status_object_valid",
    "status_keys_valid",
    "status_code_present",
    "status_code_is_null",
    "status_code_exact_int",
    "status_code_canonical_range",
    "status_code_current_contract_valid",
    "last_attempt_present",
    "last_attempt_is_null",
    "last_attempt_type_valid",
    "last_attempt_parse_valid",
    "user_update_present",
    "user_update_is_null",
    "user_update_type_valid",
    "user_update_parse_valid",
)


class MetadataRejected(ValueError):
    def __init__(self, category: str, *, http_status: int | None = None):
        super().__init__(category)
        self.category = category
        self.http_status = http_status
        self.stage: str | None = None
        self.resource_slot: str | None = None
        self.resource_shape: dict[str, bool] | None = None
        self.schema_reason: str | None = None
        self.scheduler_schema: dict[str, bool] | None = None


def _reject(category: str) -> None:
    raise MetadataRejected(category)


def _resource_invalid(value: Any, expected: str, slot: str) -> None:
    """Project shape only; never accept or disclose an unknown resource alias."""
    shape = {
        "configured_project_id_matches": False,
        "project_segment_is_numeric": False,
        "fixed_location_and_resource_suffix_matches": False,
        "short_expected_service_parent_matches": False,
    }
    if isinstance(value, str) and len(value) <= 512:
        match = re.fullmatch(r"projects/([a-z][a-z0-9-]{0,62}|[0-9]{1,20})/(.+)", value)
        if match:
            project, suffix = match.groups()
            shape["configured_project_id_matches"] = project == "longbridgequant"
            shape["project_segment_is_numeric"] = (
                re.fullmatch(r"[0-9]+", project) is not None
            )
            shape["fixed_location_and_resource_suffix_matches"] = (
                suffix == expected.split("/", 2)[2]
            )
        shape["short_expected_service_parent_matches"] = (
            slot == "revision_parent" and value == "longbridge-quant-hk-service"
        )
    error = MetadataRejected("metadata_resource_invalid")
    error.resource_slot = slot
    error.resource_shape = shape
    raise error


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


def _revision_resource(value: Any, *, slot: str = "traffic_revision") -> str:
    value = _text(value, 512)
    prefix = SERVICE_RESOURCE + "/revisions/"
    if value.startswith(prefix):
        name = value[len(prefix) :]
    elif "/" not in value:
        name = value
    else:
        terminal = value.rsplit("/", 1)[-1]
        expected = prefix
        if (
            terminal.startswith("longbridge-quant-hk-service-")
            and re.fullmatch(r"[a-z][a-z0-9-]{0,99}", terminal) is not None
        ):
            expected += terminal
        _resource_invalid(value, expected, slot)
    if (
        not name.startswith("longbridge-quant-hk-service-")
        or re.fullmatch(r"[a-z][a-z0-9-]{0,99}", name) is None
    ):
        _resource_invalid(value, prefix, slot)
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
        elif resource == _revision_resource(resource, slot="request_resource"):
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
        _resource_invalid(value["name"], SERVICE_RESOURCE, "service_name")
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
    if value["name"] != resource:
        _resource_invalid(value["name"], resource, "revision_name")
    # The exact full revision name above anchors project/location/service;
    # accept only the observed short spelling of that same configured parent.
    if value["service"] not in (SERVICE_RESOURCE, "longbridge-quant-hk-service"):
        _resource_invalid(value["service"], SERVICE_RESOURCE, "revision_parent")
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


def _scheduler_state_valid(value: Any) -> bool:
    return isinstance(value, str) and value in _STATES


def _scheduler_code_valid(value: Any) -> bool:
    return value is None or (type(value) is int and 0 <= value <= 16)


def _validation_ok(validate: Callable[[], Any]) -> bool:
    try:
        validate()
        return True
    except Exception:
        return False


def _scheduler_schema_profile(value: Any) -> tuple[str, dict[str, bool]]:
    profile = {key: False for key in SCHEDULER_SCHEMA_CHECKS}
    profile["profile_available"] = True
    profile["object_type_valid"] = isinstance(value, dict)
    if not isinstance(value, dict):
        return "scheduler_fields", profile
    profile["required_keys_present"] = {"name", "state"} <= set(value)
    profile["allowed_keys_only"] = _validation_ok(
        lambda: _keys(value, _SCHEDULER_KEYS, set())
    )
    for field, prefix in (("name", "name"), ("state", "state")):
        profile[prefix + "_present"] = field in value
        profile[prefix + "_type_valid"] = isinstance(value.get(field), str)
    profile["state_is_null"] = "state" in value and value["state"] is None
    profile["state_enum_valid"] = _scheduler_state_valid(value.get("state"))
    status = value.get("status", {})
    profile["status_present"] = "status" in value
    profile["status_is_null"] = "status" in value and status is None
    profile["status_object_valid"] = isinstance(status, dict)
    profile["status_keys_valid"] = _validation_ok(
        lambda: _keys(status, {"code"}, set())
    )
    if isinstance(status, dict):
        profile["status_code_present"] = "code" in status
        raw_code = status.get("code")
        profile["status_code_is_null"] = "code" in status and raw_code is None
        profile["status_code_exact_int"] = type(raw_code) is int
        profile["status_code_canonical_range"] = (
            type(raw_code) is int and 0 <= raw_code <= 16
        )
        code = status.get("code", 0) if "status" in value else None
        profile["status_code_current_contract_valid"] = _scheduler_code_valid(code)
    for field, prefix in (
        ("lastAttemptTime", "last_attempt"),
        ("userUpdateTime", "user_update"),
    ):
        present = field in value
        profile[prefix + "_present"] = present
        profile[prefix + "_is_null"] = present and value[field] is None
        profile[prefix + "_type_valid"] = present and isinstance(value[field], str)
        profile[prefix + "_parse_valid"] = present and _validation_ok(
            lambda: _timestamp(value[field])
        )
    checks = (
        (
            profile["required_keys_present"] and profile["allowed_keys_only"],
            "scheduler_fields",
        ),
        (profile["state_enum_valid"], "scheduler_state"),
        (profile["status_keys_valid"], "scheduler_status_shape"),
        (profile["status_code_current_contract_valid"], "scheduler_status_code"),
        (
            not profile["last_attempt_present"] or profile["last_attempt_parse_valid"],
            "scheduler_last_attempt_time",
        ),
        (
            not profile["user_update_present"] or profile["user_update_parse_valid"],
            "scheduler_user_update_time",
        ),
    )
    return next(
        (reason for valid, reason in checks if not valid), "scheduler_schema_unknown"
    ), profile


def _scheduler_validated(value: dict) -> dict:
    _keys(
        value,
        _SCHEDULER_KEYS,
        {"name", "state"},
    )
    if value["name"] != SCHEDULER_RESOURCE:
        _resource_invalid(value["name"], SCHEDULER_RESOURCE, "scheduler_name")
    if not _scheduler_state_valid(value["state"]):
        _reject("metadata_schema_invalid")
    status = value.get("status", {})
    _keys(status, {"code"}, set())
    code = status.get("code", 0) if "status" in value else None
    if not _scheduler_code_valid(code):
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


def _scheduler(value: dict) -> dict:
    try:
        return _scheduler_validated(value)
    except MetadataRejected as error:
        if error.category == "metadata_schema_invalid":
            error.schema_reason, error.scheduler_schema = _scheduler_schema_profile(
                value
            )
        raise


def inspect_hk_producer_metadata(
    *, session: Any, monotonic: Callable[[], float] = time.monotonic
) -> dict:
    """Read current fixed-resource configuration, never invoke a producer."""
    budget = _Budget(monotonic)
    # This labels the planned operation; native auth can fail before its GET.
    stage = "service_initial"
    try:
        first, revisions = _service(
            budget.get(session, SERVICE_RESOURCE, SERVICE_FIELDS)
        )
        projected = []
        for index, (resource, percent) in enumerate(sorted(revisions.items()), 1):
            stage = "revision_" + str(index)
            projected.append(
                _revision(
                    budget.get(session, resource, REVISION_FIELDS), resource, percent
                )
            )
        stage = "scheduler"
        scheduler = _scheduler(
            budget.get(session, SCHEDULER_RESOURCE, SCHEDULER_FIELDS)
        )
        stage = "service_recheck"
        last, _ = _service(budget.get(session, SERVICE_RESOURCE, SERVICE_FIELDS))
        if last != first:
            _reject("metadata_service_changed")
    except MetadataRejected as error:
        error.stage = stage
        if (
            stage == "scheduler"
            and error.category == "metadata_schema_invalid"
            and error.scheduler_schema is None
        ):
            error.schema_reason = "scheduler_json_schema"
            error.scheduler_schema = {key: False for key in SCHEDULER_SCHEMA_CHECKS}
        raise
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
