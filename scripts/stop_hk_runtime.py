"""Apply only the existing HK target's stop; cloud responses stay in memory.

This disables future execution and its Scheduler jobs. It neither terminates
in-flight requests nor cancels orders, liquidates positions or retires an account.
"""

import copy
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

PROJECT = "longbridgequant"
REGION = "asia-east2"
SERVICE = "longbridge-quant-hk-service"
IDENTITY_FIELDS = {"platform_id", "deployment_selector", "account_selector", "account_scope", "service_name"}


class StopError(ValueError):
    """Only fixed, non-sensitive failure categories may leave this adapter."""


def _identity(request, environment):
    try:
        if (set(request) != {"target_id", "github", "runtime_target"}
                or request["target_id"] != "longbridge/hk"
                or request["github"] != {"repository": "QuantStrategyLab/LongBridgePlatform",
                                          "variable_scope": "environment", "environment": "longbridge-hk"}
                or environment.get("RUNTIME_TARGET_ENABLED") != "false"):
            raise ValueError
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
        return identity
    except (ValueError, TypeError, KeyError):
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


def execute_stop(request, environment, *, run=subprocess.run):
    identity = _identity(request, environment)

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
        print(json.dumps(execute_stop(request, os.environ), sort_keys=True))
        return 0
    except StopError as error:
        print(json.dumps({"platform_applied": None, "error": str(error), "retirement_complete": False}))
    except (ValueError, TypeError, KeyError, OSError):
        print(json.dumps({"platform_applied": None, "error": "stop_request_rejected", "retirement_complete": False}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
