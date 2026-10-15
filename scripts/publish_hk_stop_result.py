"""Publish one verified HK stop artifact. This does not rerun the stop or retry delivery."""

import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid
import zipfile
from urllib.parse import urlsplit

REPOSITORY = "QuantStrategyLab/LongBridgePlatform"
WORKFLOW_PATH = ".github/workflows/stop-hk-runtime.yml"
API = "https://api.github.com"
RESULT_NAME = "hk-stop-result"
RESULT_PATH = "/api/internal/runtime-stop-result"
MAX_BYTES = 256 * 1024
_SAFE_INTEGER = 2**53 - 1
_RESULT_FIELDS = {
    "schema_version", "request_id", "source_revision", "source_identity_sha256", "target_id",
    "runtime_identity_sha256", "producer", "observed_at", "readback", "no_order",
    "in_flight_state", "retirement_complete",
}
_PRODUCER_FIELDS = {"repository", "workflow_path", "run_id", "run_attempt", "head_sha"}
_READBACK_FIELDS = {
    "project", "region", "service", "revision_name", "runtime_enabled", "scheduler_state",
    "scheduler_count", "scheduler_set_sha256", "complete",
}


class PublishError(ValueError):
    """Only a fixed category may leave this publisher."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _urllib_exchange(url, *, method, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read(MAX_BYTES + 1)
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise TimeoutError from None
        raise PublishError("stop_result_unpublished") from None


def _header(headers, name):
    if headers is None:
        return ""
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return "" if value is None else str(value)


def _exchange(url, *, method, headers, body, exchange, timeout):
    try:
        status, response_headers, payload = exchange(
            url, method=method, headers=headers, body=body, timeout=timeout,
        )
    except TimeoutError:
        raise PublishError("stop_result_unpublished") from None
    except PublishError:
        raise
    except (OSError, ValueError, TypeError):
        raise PublishError("stop_result_unpublished") from None
    if not isinstance(payload, (bytes, bytearray)) or len(payload) > MAX_BYTES:
        raise PublishError("stop_result_rejected")
    return status, response_headers, bytes(payload)


def _api_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "longbridge-hk-stop-result",
    }


def _json_document(url, token, exchange):
    status, headers, payload = _exchange(
        url, method="GET", headers=_api_headers(token), body=None, exchange=exchange, timeout=20,
    )
    if status in {301, 302, 303, 307, 308} or status != 200:
        raise PublishError("stop_result_rejected")
    if urlsplit(url).hostname != "api.github.com":
        raise PublishError("stop_result_rejected")
    try:
        document = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError):
        raise PublishError("stop_result_rejected") from None
    if not isinstance(document, dict):
        raise PublishError("stop_result_rejected")
    if _header(headers, "Location"):
        raise PublishError("stop_result_rejected")
    return document


def _download_artifact(url, token, exchange):
    status, headers, payload = _exchange(
        url, method="GET", headers=_api_headers(token), body=None, exchange=exchange, timeout=20,
    )
    if status in {301, 302, 303, 307, 308}:
        location = _header(headers, "Location")
        parsed = urlsplit(location)
        if (parsed.scheme != "https" or parsed.hostname is None or parsed.username or parsed.password
                or token and token in location):
            raise PublishError("stop_result_rejected")
        redirected_headers = {"Accept": "application/octet-stream", "User-Agent": "longbridge-hk-stop-result"}
        if parsed.hostname == "api.github.com":
            redirected_headers["Authorization"] = f"Bearer {token}"
        status, redirect_headers, payload = _exchange(
            location, method="GET", headers=redirected_headers, body=None, exchange=exchange, timeout=20,
        )
        if status in {301, 302, 303, 307, 308} or _header(redirect_headers, "Location"):
            raise PublishError("stop_result_rejected")
    if status != 200 or urlsplit(url).hostname != "api.github.com":
        raise PublishError("stop_result_rejected")
    return payload


def _result_bytes(blob):
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        infos = archive.infolist()
    except (zipfile.BadZipFile, ValueError):
        raise PublishError("stop_result_rejected") from None
    if len(infos) != 1:
        raise PublishError("stop_result_rejected")
    info = infos[0]
    if (info.filename != "result.json" or info.is_dir() or "\\" in info.filename
            or info.file_size > MAX_BYTES or info.compress_size > MAX_BYTES):
        raise PublishError("stop_result_rejected")
    try:
        data = archive.read(info)
    except (RuntimeError, ValueError, zipfile.BadZipFile):
        raise PublishError("stop_result_rejected") from None
    if len(data) > MAX_BYTES:
        raise PublishError("stop_result_rejected")
    return data


def _canonical_uuid(value):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise PublishError("stop_result_rejected") from None
    if not isinstance(value, str) or str(parsed) != value:
        raise PublishError("stop_result_rejected")
    return value


def _checked_result(data, run):
    try:
        result = json.loads(data)
        producer = result["producer"]
        readback = result["readback"]
        request_id = _canonical_uuid(result["request_id"])
        revision = result["source_revision"]
    except PublishError:
        raise
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        raise PublishError("stop_result_rejected") from None
    expected_producer = {
        "repository": REPOSITORY,
        "workflow_path": WORKFLOW_PATH,
        "run_id": str(run["id"]),
        "run_attempt": 1,
        "head_sha": run["head_sha"],
    }
    if (not isinstance(result, dict) or set(result) != _RESULT_FIELDS
            or not isinstance(producer, dict) or set(producer) != _PRODUCER_FIELDS or producer != expected_producer
            or not isinstance(readback, dict) or set(readback) != _READBACK_FIELDS
            or result["schema_version"] != "qsl_hk_stop_result.v1"
            or result["target_id"] != "longbridge/hk" or request_id is None
            or isinstance(revision, bool) or not isinstance(revision, int)
            or revision < 0 or revision > _SAFE_INTEGER
            or not re.fullmatch(r"[0-9a-f]{64}", str(result["source_identity_sha256"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(result["runtime_identity_sha256"]))
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(result["observed_at"]))
            or result["no_order"] is not True or result["in_flight_state"] != "unknown"
            or result["retirement_complete"] is not False
            or readback["project"] != "longbridgequant" or readback["region"] != "asia-east2"
            or readback["service"] != "longbridge-quant-hk-service"
            or not re.fullmatch(r"[a-z][a-z0-9-]*", str(readback["revision_name"]))
            or readback["runtime_enabled"] is not False or readback["scheduler_state"] != "paused"
            or isinstance(readback["scheduler_count"], bool) or not isinstance(readback["scheduler_count"], int)
            or readback["scheduler_count"] < 1 or readback["scheduler_count"] > _SAFE_INTEGER
            or not re.fullmatch(r"[0-9a-f]{64}", str(readback["scheduler_set_sha256"]))
            or readback["complete"] is not True):
        raise PublishError("stop_result_rejected")
    return data


def _endpoint(base_url):
    parsed = urlsplit(str(base_url or "").strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.port is not None
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise PublishError("stop_result_rejected")
    return f"https://{parsed.hostname}{RESULT_PATH}"


def _unexpired(artifact, now):
    if artifact.get("expired") is not False:
        return False
    expires_at = artifact.get("expires_at")
    if expires_at is None:
        return True
    if not isinstance(expires_at, str):
        return False
    try:
        moment = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return moment.utcoffset() is not None and moment > now


def publish_hk_stop_result(event, environment, *, exchange=_urllib_exchange, now=None):
    """Return true only after one POST. A failed or legacy run posts nothing."""

    moment = now or datetime.now(timezone.utc)
    try:
        repository = event["repository"]["full_name"]
        run_id = event["workflow_run"]["id"]
    except (KeyError, TypeError):
        raise PublishError("stop_result_rejected") from None
    if (repository != REPOSITORY or isinstance(run_id, bool) or not isinstance(run_id, int) or run_id < 1):
        raise PublishError("stop_result_rejected")
    token = str(environment.get("GITHUB_TOKEN") or "")
    if not token.strip():
        raise PublishError("stop_result_rejected")
    run = _json_document(f"{API}/repos/{REPOSITORY}/actions/runs/{run_id}", token, exchange)
    if (run.get("id") != run_id or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
            or run.get("run_attempt") != 1 or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha") or ""))
            or not isinstance(run.get("workflow_id"), int)):
        raise PublishError("stop_result_rejected")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return False
    workflow = _json_document(
        f"{API}/repos/{REPOSITORY}/actions/workflows/{run['workflow_id']}", token, exchange,
    )
    if workflow.get("id") != run["workflow_id"] or workflow.get("path") != WORKFLOW_PATH:
        raise PublishError("stop_result_rejected")
    listed = _json_document(
        f"{API}/repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100", token, exchange,
    )
    artifacts = listed.get("artifacts")
    if listed.get("total_count") == 0 and artifacts == []:
        return False
    if (listed.get("total_count") != 1 or not isinstance(artifacts, list) or len(artifacts) != 1
            or not isinstance(artifacts[0], dict) or artifacts[0].get("name") != RESULT_NAME
            or not _unexpired(artifacts[0], moment)
            or not isinstance(artifacts[0].get("id"), int)
            or not isinstance(artifacts[0].get("size_in_bytes"), int)
            or artifacts[0]["size_in_bytes"] < 1 or artifacts[0]["size_in_bytes"] > MAX_BYTES):
        raise PublishError("stop_result_rejected")
    blob = _download_artifact(
        f"{API}/repos/{REPOSITORY}/actions/artifacts/{artifacts[0]['id']}/zip", token, exchange,
    )
    data = _checked_result(_result_bytes(blob), run)
    sync_token = str(environment.get("EXECUTION_EVIDENCE_SYNC_TOKEN") or "")
    if not sync_token.strip():
        raise PublishError("stop_result_rejected")
    status, response_headers, _payload = _exchange(
        _endpoint(environment.get("EXECUTION_EVIDENCE_SYNC_URL")),
        method="POST",
        headers={
            "Authorization": f"Bearer {sync_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "longbridge-hk-stop-result",
        },
        body=data,
        exchange=exchange,
        timeout=20,
    )
    if status in {301, 302, 303, 307, 308} or _header(response_headers, "Location") or status not in {200, 201, 204}:
        raise PublishError("stop_result_unpublished")
    return True


def main():
    try:
        path = Path(os.environ["GITHUB_EVENT_PATH"])
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise PublishError("stop_result_rejected")
        published = publish_hk_stop_result(json.loads(path.read_text()), os.environ)
        print(json.dumps({"published": published}))
        return 0
    except PublishError as error:
        print(json.dumps({"published": False, "error": str(error)}))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError):
        print(json.dumps({"published": False, "error": "stop_result_rejected"}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
