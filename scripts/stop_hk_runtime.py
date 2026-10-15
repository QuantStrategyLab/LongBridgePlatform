"""Apply only the existing HK target's stop; cloud responses stay in memory.

This disables future execution and its Scheduler jobs. It neither terminates
in-flight requests nor cancels orders, liquidates positions or retires an account.
"""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from urllib.parse import urlsplit

PROJECT = "longbridgequant"
REGION = "asia-east2"
SERVICE = "longbridge-quant-hk-service"
IDENTITY_FIELDS = {"platform_id", "deployment_selector", "account_selector", "account_scope", "service_name"}
_SAFE_INTEGER = 2**53 - 1
_REQUEST_FIELDS = {"target_id", "github", "runtime_target"}
_CORRELATION_FIELDS = {"request_id", "source_revision", "source_identity_sha256"}


class StopError(ValueError):
    """Only fixed, non-sensitive failure categories may leave this adapter."""


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _correlation(value):
    if not isinstance(value, dict) or set(value) != _CORRELATION_FIELDS:
        raise ValueError
    request_id = value["request_id"]
    revision = value["source_revision"]
    identity_sha = value["source_identity_sha256"]
    if (not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id
            or isinstance(revision, bool) or not isinstance(revision, int)
            or revision < 0 or revision > _SAFE_INTEGER
            or not isinstance(identity_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", identity_sha)):
        raise ValueError
    return {"request_id": request_id, "source_revision": revision, "source_identity_sha256": identity_sha}


def _producer(environment):
    run_id = environment.get("GITHUB_RUN_ID")
    head_sha = environment.get("GITHUB_SHA")
    if (environment.get("GITHUB_REPOSITORY") != "QuantStrategyLab/LongBridgePlatform"
            or environment.get("GITHUB_RUN_ATTEMPT") != "1"
            or not isinstance(run_id, str) or not re.fullmatch(r"[1-9][0-9]*", run_id)
            or not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha)):
        raise ValueError
    return {"repository": "QuantStrategyLab/LongBridgePlatform",
            "workflow_path": ".github/workflows/stop-hk-runtime.yml",
            "run_id": run_id, "run_attempt": 1, "head_sha": head_sha}


def _identity(request, environment):
    try:
        fields = set(request)
        if (not fields.issuperset(_REQUEST_FIELDS) or fields - _REQUEST_FIELDS not in (set(), {"correlation"})
                or request["target_id"] != "longbridge/hk"
                or request["github"] != {"repository": "QuantStrategyLab/LongBridgePlatform",
                                          "variable_scope": "environment", "environment": "longbridge-hk"}
                or environment.get("RUNTIME_TARGET_ENABLED") != "false"):
            raise ValueError
        correlation = _correlation(request["correlation"]) if "correlation" in request else None
        identity = request["runtime_target"]
        declared = json.loads(environment["RUNTIME_TARGET_JSON"])
        if (not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS
                or not isinstance(declared, dict)
                or any(identity[key] != declared.get(key) for key in IDENTITY_FIELDS)
                or identity["platform_id"] != "longbridge" or identity["service_name"] != SERVICE
                or identity["account_scope"] != "HK"
                or not isinstance(identity["deployment_selector"], str) or not identity["deployment_selector"].strip()
                or not isinstance(identity["account_selector"], list) or not identity["account_selector"]
                or any(not isinstance(item, str) or not item.strip() for item in identity["account_selector"])
                or len(set(identity["account_selector"])) != len(identity["account_selector"])):
            raise ValueError
        return identity, correlation
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StopError("stop_request_rejected") from None


def _container(raw):
    containers = raw.get("containers", [])
    if len(containers) != 1 or not isinstance(containers[0], dict):
        raise StopError("stop_source_unverified")
    container = copy.deepcopy(containers[0])
    values = container.get("env", [])
    if (not isinstance(values, list) or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in values)
            or len({item["name"] for item in values}) != len(values)):
        raise StopError("stop_source_unverified")
    container["env"] = sorted(values, key=lambda item: item["name"])
    return container


def _observed_at(moment):
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise StopError("stop_request_rejected")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_result(path, payload):
    if not isinstance(path, Path) or path.name != "result.json" or path.is_symlink():
        raise StopError("stop_request_rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".result.json.tmp")
    temporary.write_bytes(_canonical_bytes(payload))
    os.replace(temporary, path)


def execute_stop(request, environment, *, run=subprocess.run, result_path=None, now=None):
    identity, correlation = _identity(request, environment)
    producer = None
    if correlation is not None:
        try:
            producer = _producer(environment)
        except (ValueError, TypeError):
            raise StopError("stop_request_rejected") from None
        if result_path is None or result_path.is_symlink() or (result_path.exists() and not result_path.is_file()):
            raise StopError("stop_request_rejected")
        if result_path.exists():
            result_path.unlink()

    def cloud(args, *, write=False):
        try:
            result = run(["gcloud", *args, f"--project={PROJECT}", "--format=json", "--quiet"],
                         capture_output=True, text=True, timeout=45, check=False,
                         env={**os.environ, "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1", "CLOUDSDK_CORE_LOG_HTTP": "false"})
            if result.returncode:
                raise ValueError
            return json.loads(result.stdout or "{}")
        except (ValueError, OSError, subprocess.SubprocessError):
            raise StopError("stop_write_unverified" if write else "stop_source_unverified") from None

    def snapshot():
        try:
            service = cloud(["run", "services", "describe", SERVICE, f"--region={REGION}"])
            status = service["status"]
            traffic = [item for item in status.get("traffic", []) if item.get("percent", 0) > 0]
            if (service["metadata"]["name"] != SERVICE or len(traffic) != 1 or traffic[0].get("percent") != 100
                    or status.get("latestCreatedRevisionName") != status.get("latestReadyRevisionName")):
                raise ValueError
            revision = traffic[0]["revisionName"]
            if revision != status.get("latestReadyRevisionName") or not re.fullmatch(r"[a-z][a-z0-9-]*", revision):
                raise ValueError
            container = _container(cloud(["run", "revisions", "describe", revision, f"--region={REGION}"])["spec"])
            if _container(service["spec"]["template"]["spec"]) != container:
                raise ValueError
            values = {item["name"]: item.get("value") for item in container["env"]}
            target = json.loads(values["RUNTIME_TARGET_JSON"])
            if any(target.get(key) != value for key, value in identity.items()) or values.get("RUNTIME_TARGET_ENABLED") not in {"true", "false"}:
                raise ValueError
            url = urlsplit(status["url"])
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError
            jobs = cloud(["scheduler", "jobs", "list", f"--location={REGION}"])
            if not isinstance(jobs, list):
                raise ValueError
            selected = []
            for job in jobs:
                uri = urlsplit(job.get("httpTarget", {}).get("uri", ""))
                if uri.scheme == "https" and uri.netloc == url.netloc:
                    if (not re.fullmatch(rf"projects/{PROJECT}/locations/{REGION}/jobs/[A-Za-z0-9_-]+", job.get("name", ""))
                            or job.get("state") not in {"ENABLED", "PAUSED"}):
                        raise ValueError
                    selected.append({"name": job["name"], "state": job["state"], "uri": uri.geturl()})
            if not selected or len({job["name"] for job in selected}) != len(selected):
                raise ValueError
            return {"container": container, "jobs": sorted(selected, key=lambda item: item["name"]), "url": url.geturl(), "revision": revision}
        except StopError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StopError("stop_source_unverified") from None

    before = snapshot()
    # Guard stale source/configuration before writing; this is not a cloud CAS.
    if snapshot() != before:
        raise StopError("stop_source_changed")
    expected = copy.deepcopy(before["container"])
    enabled = next(item for item in expected["env"] if item["name"] == "RUNTIME_TARGET_ENABLED")
    was_enabled = enabled["value"] == "true"
    enabled["value"] = "false"
    if was_enabled:
        cloud(["run", "services", "update", SERVICE, f"--region={REGION}",
               "--update-env-vars=RUNTIME_TARGET_ENABLED=false"], write=True)
        after = snapshot()
        if after["container"] != expected or after["url"] != before["url"] or after["jobs"] != before["jobs"]:
            raise StopError("stop_readback_unverified")
    for job in before["jobs"]:
        if job["state"] == "ENABLED":
            cloud(["scheduler", "jobs", "pause", job["name"], f"--location={REGION}"], write=True)
    after = snapshot()
    expected_jobs = [{**job, "state": "PAUSED"} for job in before["jobs"]]
    if after["container"] != expected or after["url"] != before["url"] or after["jobs"] != expected_jobs:
        raise StopError("stop_readback_unverified")
    if correlation is not None:
        jobs = after["jobs"]
        if not jobs or any(job.get("state") != "PAUSED" for job in jobs):
            raise StopError("stop_readback_unverified")
        try:
            _write_result(result_path, {
                "schema_version": "qsl_hk_stop_result.v1",
                "request_id": correlation["request_id"],
                "source_revision": correlation["source_revision"],
                "source_identity_sha256": correlation["source_identity_sha256"],
                "target_id": "longbridge/hk",
                "runtime_identity_sha256": _sha256({key: identity[key] for key in IDENTITY_FIELDS}),
                "producer": producer,
                "observed_at": _observed_at(now or datetime.now(timezone.utc)),
                "readback": {
                    "project": PROJECT,
                    "region": REGION,
                    "service": SERVICE,
                    "revision_name": after["revision"],
                    "runtime_enabled": False,
                    "scheduler_state": "paused",
                    "scheduler_count": len(jobs),
                    "scheduler_set_sha256": _sha256(jobs),
                    "complete": True,
                },
                "no_order": True,
                "in_flight_state": "unknown",
                "retirement_complete": False,
            })
        except OSError:
            if result_path.is_file() and not result_path.is_symlink():
                result_path.unlink()
            raise StopError("stop_request_rejected") from None
    return {"platform_applied": True, "runtime_enabled": False, "scheduler_state": "paused",
            "in_flight_state": "unknown", "retirement_complete": False, "no_order": True}


def main():
    try:
        if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_REPOSITORY") != "QuantStrategyLab/LongBridgePlatform"
                or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"):
            raise StopError("stop_workflow_rejected")
        path = Path(os.environ["GITHUB_EVENT_PATH"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise StopError("stop_request_rejected")
        event = json.loads(path.read_text())
        if event["inputs"].get("confirm") != "STOP_ONLY":
            raise StopError("stop_request_rejected")
        request = json.loads(event["inputs"]["stop_request"])
        result_path = os.environ.get("HK_STOP_RESULT_PATH")
        print(json.dumps(execute_stop(
            request, os.environ, result_path=Path(result_path) if result_path else None,
        ), sort_keys=True))
        return 0
    except StopError as error:
        print(json.dumps({"platform_applied": None, "error": str(error), "retirement_complete": False}))
    except (ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"platform_applied": None, "error": "stop_request_rejected", "retirement_complete": False}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
