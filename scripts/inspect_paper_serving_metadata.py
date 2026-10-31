"""Observe PAPER serving images; never invoke application or broker code.

Resource names, image repository/digest and protected target values remain in
runner memory. Only code identities, proportions and comparison results leave it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

from scripts.verify_deployed_runtime_target_admission import (
    _container_env,
    _require_paper_target_identity,
    verify_service,
)

SCHEMA = "longbridge.paper-serving-metadata.v1"
PACKAGES = ("us-equity-strategies", "quant-platform-kit")
IMAGE_PROBE = r'''
import hashlib, json, pathlib, re, tomllib
from importlib import metadata
names = ("us-equity-strategies", "quant-platform-kit")
paths = [str(p) for root in ("/app/.venv/lib", "/usr/local/lib")
         for p in pathlib.Path(root).glob("python*/site-packages")]
result = {"packages": {}, "lock_sha256": None, "lock_commits": {}}
for name in names:
    rows = [d for d in metadata.distributions(path=paths)
            if re.sub(r"[-_.]+", "-", str(d.metadata.get("Name") or "").lower()) == name]
    item = {"present": len(rows) == 1, "version": None, "commit": None}
    if len(rows) == 1:
        item["version"] = rows[0].version
        try:
            item["commit"] = json.loads(rows[0].read_text("direct_url.json") or "{}").get("vcs_info", {}).get("commit_id")
        except Exception:
            pass
    result["packages"][name] = item
try:
    raw = pathlib.Path("/app/uv.lock").read_bytes()
    result["lock_sha256"] = hashlib.sha256(raw).hexdigest()
    lock = tomllib.loads(raw.decode())
    for name in names:
        rows = [p for p in lock.get("package", []) if p.get("name") == name]
        if len(rows) == 1:
            result["lock_commits"][name] = str(rows[0].get("source", {}).get("git", "")).rsplit("#", 1)[-1]
except Exception:
    pass
print(json.dumps(result, sort_keys=True))
'''


class ObservationError(ValueError):
    pass


def sha(value, length=40):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value) else None


def command(args, *, env=None):
    try:
        result = subprocess.run(args, env=env, capture_output=True, timeout=180, check=False)
        if result.returncode and any(marker in result.stderr for marker in (b"PERMISSION_DENIED", b"Permission denied", b"denied:", b"UNAUTHENTICATED")):
            raise ObservationError("external_read_denied")
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise ObservationError("external_read_unavailable")
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise ObservationError("external_read_unavailable") from None


def read_json(args, **kwargs):
    try:
        return json.loads(command(args, **kwargs))
    except ObservationError:
        raise
    except (ValueError, UnicodeError):
        raise ObservationError("external_read_unavailable") from None


def traffic_rows(service):
    rows = service.get("status", {}).get("traffic")
    if not isinstance(rows, list) or not rows or len(rows) > 100:
        raise ObservationError("traffic_unavailable")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ObservationError("traffic_unavailable")
        percent = row.get("percent", 0)
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ObservationError("traffic_unavailable")
        if percent:
            name = row.get("revisionName")
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
                raise ObservationError("traffic_unavailable")
            result.append((name, percent))
    if sum(p for _, p in result) != 100 or len({n for n, _ in result}) != len(result):
        raise ObservationError("traffic_unavailable")
    return sorted(result)


def project_install(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("packages"), dict):
        raise ObservationError("image_probe_invalid")
    result = {}
    for name in PACKAGES:
        row = payload["packages"].get(name)
        if not isinstance(row, dict) or type(row.get("present")) is not bool:
            raise ObservationError("image_probe_invalid")
        version = row.get("version")
        if version is not None and (not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+!-]{0,63}", version)):
            raise ObservationError("image_probe_invalid")
        commit = sha(row.get("commit"))
        lock_commit = sha(payload.get("lock_commits", {}).get(name))
        result[name] = {"present": row["present"], "version": version, "commit": commit,
                        "lock_commit": lock_commit,
                        "installed_equals_lock": None if not commit or not lock_commit else commit == lock_commit}
    return {"packages": result, "lock_sha256": sha(payload.get("lock_sha256"), 64)}


def nullable_equal(a, b):
    return a == b if a is not None and b is not None else None


def boolean(value):
    if isinstance(value, bool):
        return value
    return {"true": True, "false": False}.get(str(value).lower())


def target_projection(payload, *, service, expected):
    if isinstance(payload.get("spec", {}).get("containers"), list):
        payload = {"spec": {"template": {"spec": payload["spec"]}}}
    policy = verify_service(service=service, service_json=payload)
    _require_paper_target_identity(service=service, service_json=payload)
    env = _container_env(payload)
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON"))
    release = target.get("strategy_release") or {}
    risk = target.get("runtime_risk_limits") or {}
    binding = risk.get("binding") or {}
    comparisons = {}
    for key in ("service_name", "account_scope", "deployment_selector", "strategy_profile", "account_selector", "physical_account_id"):
        a, b = target.get(key), expected.get(key)
        comparisons[key + "_matches_expected"] = None if a is None or b is None else a == b
    return {"paper_identity_admitted": True, "expected_target_comparisons": comparisons,
            "runtime_target_enabled": boolean(env.get("RUNTIME_TARGET_ENABLED")),
            "execution_mode": policy["execution_mode"], "dry_run_only": policy["dry_run_only"],
            "release_present": bool(release), "risk_present": bool(risk),
            "risk_binding_present": bool(binding),
            "declared_strategy_revision": sha(release.get("strategy_revision")),
            "risk_ues_revision": sha(binding.get("ues_revision")),
            "independent_approval_verified": False}


def _run_locator(value):
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value) else None


def _optional_read(args, load):
    try:
        return load(args), None
    except ObservationError as error:
        return None, str(error) if str(error) in {"external_read_denied", "external_read_unavailable"} else "external_read_unavailable"


def registry_projection(raw):
    """Observe provider record shape; do not attest a builder or signature."""
    if not isinstance(raw, dict):
        raise ObservationError("registry_metadata_invalid")
    result = {}
    for alias, wrapper, field in (("provenance", "provenance_summary", "provenance"),
                                   ("build_details", "build_details_summary", "build_details")):
        summary = raw.get(wrapper)
        rows = summary.get(field) if isinstance(summary, dict) else None
        valid = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
        result[alias + "_shape_valid"] = valid
        result[alias + "_present"] = bool(rows) if valid else None
    result.update(signature_verified=False, subject_source_builder_verified=False)
    return result


def audit_projection(raw, *, name, digest, source):
    """Project bounded service events without actor/resource/account values."""
    if not isinstance(raw, list):
        raise ObservationError("audit_metadata_invalid")
    methods = {"google.cloud.run.v1.Services.ReplaceService", "google.cloud.run.v1.Services.UpdateService",
               "google.cloud.run.v1.Services.CreateService", "google.cloud.run.v2.Services.UpdateService",
               "google.cloud.run.v2.Services.CreateService"}
    records = []
    for entry in raw[:50]:
        if not isinstance(entry, dict) or not isinstance(entry.get("protoPayload"), dict):
            raise ObservationError("audit_metadata_invalid")
        body = entry["protoPayload"]
        request = body.get("request") or {}
        if not isinstance(request, dict):
            raise ObservationError("audit_metadata_invalid")
        service = request.get("service", request)
        spec = service.get("spec", {}) if isinstance(service, dict) else {}
        template = spec.get("template", {}) if isinstance(spec, dict) else {}
        metadata = template.get("metadata", {}) if isinstance(template, dict) else {}
        source_label = sha((metadata.get("labels") or {}).get("commit-sha")) if isinstance(metadata, dict) else None
        revision_name = metadata.get("name") if isinstance(metadata, dict) else None
        containers = template.get("spec", {}).get("containers") if isinstance(template, dict) else None
        image = containers[0].get("image") if isinstance(containers, list) and len(containers) == 1 and isinstance(containers[0], dict) else None
        stamp = entry.get("timestamp")
        try:
            stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except (ValueError, AttributeError, TypeError):
            stamp = None
        code = (body.get("status") or {}).get("code")
        records.append({"timestamp": stamp,
            "method": body.get("methodName") if body.get("methodName") in methods else "other_cloud_run_admin_method",
            "status_code": code if type(code) is int else None,
            "request_source_commit": source_label,
            "source_equals_revision_label": nullable_equal(source_label, source),
            "request_revision_equals_observed": None if not isinstance(revision_name, str) else revision_name == name,
            "request_image_equals_observed_digest": None if not isinstance(image, str) or "@sha256:" not in image else image == digest})
    return {"records_returned": len(raw), "record_limit": 50, "truncated": len(raw) > 50,
            "records": records, "all_history_verified": False,
            "original_approval_verified": False}


def observe_source_chain(*, env=os.environ, load=read_json):
    """Read existing service/revision, registry and creation-window audit data only."""
    project, region, service = (env.get(k, "") for k in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    if env.get("WORKFLOW_TARGET") != "PAPER" or not all(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", x) for x in (project, region, service)):
        raise ObservationError("configuration_unavailable")
    service_cmd = ["gcloud", "run", "services", "describe", service, "--project=" + project, "--region=" + region, "--format=json"]
    started = datetime.now(timezone.utc).isoformat()
    before = load(service_cmd)
    if before.get("metadata", {}).get("name") != service:
        raise ObservationError("service_identity_invalid")
    _require_paper_target_identity(service=service, service_json=before)
    verify_service(service=service, service_json=before)
    rows = traffic_rows(before)
    result = {"schema": SCHEMA, "mode": "source-chain", "workflow_sha": sha(env.get("GITHUB_SHA")),
              "observed_started_at": started, "positive_traffic_revision_count": len(rows), "revisions": [],
              "image_execution_performed": False, "broker_invoked": False, "service_invoked": False,
              "business_writes": False, "resource_identifiers_disclosed": False, "image_digest_disclosed": False,
              "independent_approval_verified": False}
    for ordinal, (name, percent) in enumerate(rows, 1):
        revision_cmd = ["gcloud", "run", "revisions", "describe", name, "--project=" + project, "--region=" + region, "--format=json"]
        revision = load(revision_cmd)
        metadata = revision.get("metadata", {})
        if metadata.get("name") != name or not name.startswith(service + "-"):
            raise ObservationError("revision_identity_invalid")
        _require_paper_target_identity(service=service, service_json={"spec": {"template": {"spec": revision.get("spec", {})}}})
        digest = revision.get("status", {}).get("imageDigest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-z0-9-]+-docker\.pkg\.dev/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}", digest):
            raise ObservationError("immutable_image_unavailable")
        labels = metadata.get("labels") or {}
        source = sha(labels.get("commit-sha"))
        row = {"ordinal": ordinal, "traffic_percent": percent, "revision_source_commit": source,
               "reported_github_run_id": _run_locator(labels.get("github-run-id")),
               "overlay_source_commit": sha(labels.get("qsl-overlay-commit")),
               "image_source_kind": "notification-overlay" if labels.get("qsl-image-source-kind") == "notification-overlay" else None}
        registry, error = _optional_read(["gcloud", "artifacts", "docker", "images", "describe", digest,
            "--project=" + project, "--show-provenance", "--show-build-details", "--format=json"], load)
        try:
            row["registry"] = {"read_status": error} if error else {"read_status": "read", **registry_projection(registry)}
        except ObservationError:
            row["registry"] = {"read_status": "registry_metadata_invalid"}
        try:
            created = datetime.fromisoformat(metadata["creationTimestamp"].replace("Z", "+00:00"))
            if created.tzinfo is None:
                raise ValueError
            start, end = created - timedelta(minutes=15), created + timedelta(minutes=15)
            resources = ["namespaces/" + project + "/services/" + service,
                         "projects/" + project + "/locations/" + region + "/services/" + service]
            query = ('protoPayload.serviceName="run.googleapis.com" AND ('
                + ' OR '.join('protoPayload.resourceName=' + json.dumps(r) for r in resources)
                + ') AND timestamp>=' + json.dumps(start.isoformat()) + ' AND timestamp<' + json.dumps(end.isoformat()))
            audit, error = _optional_read(["gcloud", "logging", "read", query, "--project=" + project,
                "--order=asc", "--limit=51", "--format=json"], load)
            row["creation_window_audit"] = {"start_utc": start.isoformat(), "end_utc": end.isoformat(),
                **({"read_status": error} if error else {"read_status": "read", **audit_projection(audit, name=name, digest=digest, source=source)})}
        except (KeyError, ValueError, TypeError):
            row["creation_window_audit"] = {"read_status": "creation_time_or_audit_shape_unavailable"}
        if load(revision_cmd) != revision:
            raise ObservationError("revision_changed_during_observation")
        result["revisions"].append(row)
    if load(service_cmd) != before:
        raise ObservationError("service_changed_during_observation")
    result.update(status="observed", service_stable=True, observed_finished_at=datetime.now(timezone.utc).isoformat())
    return result


def observe(*, env=os.environ, run=command, load=read_json):
    started = datetime.now(timezone.utc).isoformat()
    project, region, service = (env.get(k, "") for k in ("GCP_PROJECT_ID", "CLOUD_RUN_REGION", "CLOUD_RUN_SERVICE"))
    if (env.get("WORKFLOW_TARGET") != "PAPER" or not all(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", x) for x in (project, region, service))):
        raise ObservationError("configuration_unavailable")
    try:
        expected = json.loads(env.get("EXPECTED_RUNTIME_TARGET_JSON") or "{}")
        if not isinstance(expected, dict):
            raise ValueError
    except ValueError:
        raise ObservationError("configuration_unavailable") from None
    service_cmd = ["gcloud", "run", "services", "describe", service, "--project=" + project, "--region=" + region, "--format=json"]
    before = load(service_cmd)
    if before.get("metadata", {}).get("name") != service:
        raise ObservationError("service_identity_invalid")
    rows = traffic_rows(before)
    result = {"schema": SCHEMA, "observed_started_at": started,
              "workflow_sha": sha(env.get("GITHUB_SHA")), "service_identity_matches": True,
              "template": target_projection(before, service=service, expected=expected),
              "positive_traffic_revision_count": len(rows), "revisions": [],
              "resource_identifiers_disclosed": False, "image_digest_disclosed": False,
              "broker_invoked": False, "service_invoked": False, "business_writes": False}
    for ordinal, (name, percent) in enumerate(rows, 1):
        revision_cmd = ["gcloud", "run", "revisions", "describe", name, "--project=" + project, "--region=" + region, "--format=json"]
        revision = load(revision_cmd)
        if revision.get("metadata", {}).get("name") != name or not name.startswith(service + "-"):
            raise ObservationError("revision_identity_invalid")
        policy = target_projection(revision, service=service, expected=expected)
        spec = revision.get("spec", {})
        containers = spec.get("containers")
        if not isinstance(containers, list) or len(containers) != 1:
            raise ObservationError("image_identity_invalid")
        image = containers[0].get("image")
        digest = revision.get("status", {}).get("imageDigest")
        pattern = r"(?:[a-z0-9-]+-docker\.pkg\.dev|(?:[a-z]+\.)?gcr\.io)/[A-Za-z0-9._/-]+@sha256:[0-9a-f]{64}"
        if not isinstance(digest, str) or not re.fullmatch(pattern, digest):
            raise ObservationError("immutable_image_unavailable")
        if not isinstance(image, str) or image.split("@")[0].split(":")[0] != digest.split("@")[0]:
            raise ObservationError("image_identity_invalid")
        with tempfile.TemporaryDirectory(prefix="paper-image-read-") as directory:
            docker_env = {**env, "DOCKER_CONFIG": directory}
            run(["gcloud", "auth", "configure-docker", digest.split("/")[0], "--quiet"], env=docker_env)
            run(["docker", "pull", digest], env=docker_env)
            inspected = load(["docker", "image", "inspect", digest], env=docker_env)
            if not isinstance(inspected, list) or len(inspected) != 1 or digest not in inspected[0].get("RepoDigests", []):
                raise ObservationError("image_readback_mismatch")
            installed = project_install(load(["docker", "run", "--rm", "--network=none", "--read-only",
                "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64",
                "--memory=256m", "--cpus=1", "--entrypoint=/usr/local/bin/python", digest,
                "-I", "-S", "-c", IMAGE_PROBE], env=docker_env))
        source = sha(revision.get("metadata", {}).get("labels", {}).get("commit-sha"))
        oci_source = sha((inspected[0].get("Config", {}).get("Labels") or {}).get("org.opencontainers.image.revision"))
        source_lock = None
        if source:
            try:
                source_lock = hashlib.sha256(run(["git", "show", source + ":uv.lock"])).hexdigest()
            except ObservationError:
                pass
        if load(revision_cmd) != revision:
            raise ObservationError("revision_changed_during_observation")
        ues = installed["packages"]["us-equity-strategies"]["commit"]
        result["revisions"].append({"ordinal": ordinal, "traffic_percent": percent,
            "revision_source_commit": source, "image_oci_source_commit": oci_source,
            "source_equals_image_oci": nullable_equal(source, oci_source),
            "immutable_image_verified": True, "image_digest_disclosed": False,
            "source_lock_sha256": source_lock, "installed": installed,
            "image_lock_equals_source_lock": nullable_equal(installed["lock_sha256"], source_lock),
            "installed_ues_equals_release": nullable_equal(ues, policy["declared_strategy_revision"]),
            "installed_ues_equals_risk_binding": nullable_equal(ues, policy["risk_ues_revision"]),
            "target": policy})
    after = load(service_cmd)
    if before != after:
        raise ObservationError("service_changed_during_observation")
    result.update(service_stable=True, observed_finished_at=datetime.now(timezone.utc).isoformat(), status="observed")
    return result


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args not in ([], ["--source-chain"]):
            raise ObservationError("arguments_invalid")
        result = observe_source_chain() if args else observe()
    except Exception as exc:
        reasons = {"configuration_unavailable", "traffic_unavailable", "service_identity_invalid", "revision_identity_invalid",
                   "image_identity_invalid", "immutable_image_unavailable", "image_readback_mismatch", "image_probe_invalid",
                   "external_read_unavailable", "external_read_denied", "service_changed_during_observation", "revision_changed_during_observation",
                   "arguments_invalid", "registry_metadata_invalid", "audit_metadata_invalid"}
        result = {"schema": SCHEMA, "status": "unknown", "reason": str(exc) if isinstance(exc, ObservationError) and str(exc) in reasons else "observation_unavailable"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
