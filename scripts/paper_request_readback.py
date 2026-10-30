"""Bounded, sanitized Cloud Run request-log observation for PAPER dry-run.

``observed`` means matching request logs exist; it does not mean HTTP or
business success, nor authenticate a particular Scheduler attempt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

PROJECT = "longbridgequant"
REGION = "asia-east1"
SERVICE = "longbridge-quant-paper-service"
LIMIT = 20


def validate_window(start: str, end: str) -> tuple[datetime, datetime]:
    if not all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value or "") for value in (start, end)):
        raise ValueError("invalid_window")
    try:
        begin, finish = (datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) for value in (start, end))
    except ValueError:
        raise ValueError("invalid_window") from None
    if not timedelta(0) < finish - begin <= timedelta(minutes=15):
        raise ValueError("invalid_window")
    return begin, finish


def _read(args, run):
    result = run(["gcloud", *args, "--quiet"], capture_output=True, text=True, timeout=45, check=False,
                 env={**os.environ, "CLOUDSDK_CORE_DISABLE_FILE_LOGGING": "1", "CLOUDSDK_CORE_LOG_HTTP": "false"})
    if result.returncode:
        raise ValueError("read_unavailable")
    return json.loads(result.stdout)


def _revision_label(revision, run):
    if not isinstance(revision, str) or not re.fullmatch(re.escape(SERVICE) + r"-[a-z0-9-]{1,30}", revision):
        return None, None
    fingerprint = hashlib.sha256(revision.encode("ascii")).hexdigest()
    try:
        payload = _read(["run", "revisions", "describe", revision, f"--project={PROJECT}", f"--region={REGION}", "--format=json(metadata.name,metadata.labels)"], run)
        metadata = payload["metadata"]
        labels = metadata["labels"]
        value = labels.get("commit-sha")
        if metadata.get("name") != revision or labels.get("serving.knative.dev/service") != SERVICE or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            return fingerprint, None
        return fingerprint, value
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        return fingerprint, None


def _cycle_observation(trace, revision, start, end, run):
    unknown = {"status": "unknown", "trace_correlation": "unverified"}
    if not isinstance(trace, str) or not re.fullmatch(r"projects/longbridgequant/traces/[0-9a-f]{32}", trace):
        return unknown
    if not isinstance(revision, str) or not re.fullmatch(re.escape(SERVICE) + r"-[a-z0-9-]{1,30}", revision):
        return unknown
    query = (f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE}" '
             f'AND resource.labels.location="{REGION}" AND resource.labels.revision_name="{revision}" '
             f'AND trace="{trace}" AND jsonPayload.event="validation_cycle_observation" '
             f'AND timestamp >= "{start}" AND timestamp < "{end}"')
    try:
        rows = _read(["logging", "read", query, f"--project={PROJECT}", "--limit=2", "--order=asc",
                      "--format=json(jsonPayload)"], run)
        if not isinstance(rows, list) or len(rows) != 1:
            return unknown
        row = rows[0]["jsonPayload"]
        if row.get("schema") != "longbridge.validation-cycle-observation.v1":
            return unknown
        fields = ("adapter_instrumented", "cycle_entered", "cycle_returned", "execution_receipt_present")
        if any(type(row.get(key)) is not bool for key in fields):
            return unknown
        attempts = row.get("adapter_submit_attempt_count")
        if ((row["adapter_instrumented"] and (type(attempts) is not int or not 0 <= attempts <= 100000))
                or (not row["adapter_instrumented"] and attempts is not None)
                or (row["cycle_entered"] and not row["adapter_instrumented"])
                or (row["cycle_returned"] and not row["cycle_entered"])
                or row.get("report_status") not in {"ok", "error", "skipped", "unknown"}
                or row.get("broker_submit_count") is not None):
            return unknown
        return {"status": "trace_match_unverified", "trace_correlation": "unverified",
                **{key: row[key] for key in fields}, "adapter_submit_attempt_count": attempts,
                "report_status": row["report_status"], "broker_submit_count": None}
    except (ValueError, TypeError, AttributeError, KeyError, OSError, subprocess.SubprocessError):
        return unknown


def inspect(start: str, end: str, *, run=subprocess.run):
    begin, finish = validate_window(start, end)
    result = {"schema": "longbridge.paper-request-readback.v1", "status": "unavailable", "matching_count": 0,
              "requests": [], "business_cycle": "unknown", "receipt": "unknown", "submit_count": None,
              "source_binding_verified": False, "scheduler_correlation": "unverified"}
    query = (f'resource.type="cloud_run_revision" AND resource.labels.service_name="{SERVICE}" '
             f'AND resource.labels.location="{REGION}" AND logName="projects/{PROJECT}/logs/run.googleapis.com%2Frequests" '
             'AND httpRequest.requestMethod="POST" '
             'AND httpRequest.requestUrl =~ "^https://[^/?#]+/dry-run([?][^#]*)?$" '
             f'AND timestamp >= "{start}" AND timestamp < "{end}"')
    try:
        rows = _read(["logging", "read", query, f"--project={PROJECT}", f"--limit={LIMIT + 1}", "--order=asc",
                      "--format=json(timestamp,trace,resource.labels.revision_name,httpRequest.requestMethod,httpRequest.requestUrl,httpRequest.status)"], run)
        if not isinstance(rows, list):
            raise ValueError("read_unavailable")
        truncated = len(rows) > LIMIT
        cache = {}
        for row in rows[:LIMIT]:
            request = row.get("httpRequest", {})
            url = urlsplit(request.get("requestUrl", ""))
            stamp = datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00"))
            status = request.get("status")
            if (request.get("requestMethod") != "POST" or url.scheme != "https" or url.path != "/dry-run"
                    or stamp.tzinfo is None or not begin <= stamp < finish or type(status) is not int or not 100 <= status <= 599):
                continue
            revision = row.get("resource", {}).get("labels", {}).get("revision_name")
            if revision not in cache:
                cache[revision] = _revision_label(revision, run)
            fingerprint, commit = cache[revision]
            result["requests"].append({"method": "POST", "path": "/dry-run", "timestamp": stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                                       "http_status": status, "revision_fingerprint_sha256": fingerprint, "source_commit_label": commit,
                                       "validation_cycle_observation": _cycle_observation(row.get("trace"), revision, start, end, run)})
        result["matching_count"] = len(result["requests"])
        result["status"] = "truncated" if truncated else ("observed" if result["requests"] else "no_match")
    except (ValueError, TypeError, AttributeError, KeyError, OSError, subprocess.SubprocessError):
        result.update(status="unavailable", matching_count=0, requests=[])
    return result


def main():
    try:
        result = inspect(os.environ.get("READBACK_START_UTC", ""), os.environ.get("READBACK_END_UTC", ""))
    except ValueError:
        result = {"schema": "longbridge.paper-request-readback.v1", "status": "invalid_window", "business_cycle": "unknown", "receipt": "unknown", "submit_count": None, "source_binding_verified": False, "scheduler_correlation": "unverified"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__":
    sys.exit(main())
