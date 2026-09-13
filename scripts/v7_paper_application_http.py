#!/usr/bin/env python3
"""Bounded QRT application-record transport for the paper candidate workflow."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path

_EXPECTED_HOST = "qsl-strategy-switch-console.pigbibi.workers.dev"
_PATH_PREFIX = "/api/internal/research-promotion-application/"


class ApplicationTransportError(ValueError):
    """A bounded transport call cannot be safely completed."""


def _application_id(value: str) -> str:
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise ApplicationTransportError("invalid application id") from exc


def _endpoint(base_url: str, application_id: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != _EXPECTED_HOST
        or parsed.port is not None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ApplicationTransportError("invalid control-plane origin")
    return urllib.parse.urljoin(
        f"https://{_EXPECTED_HOST}/",
        f"{_PATH_PREFIX}{urllib.parse.quote(_application_id(application_id), safe='')}",
    )


def _request(*, method: str, url: str, token: str, payload: Mapping[str, object] | None = None) -> dict[str, object]:
    if not token.strip():
        raise ApplicationTransportError("sync token is missing")
    body = None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(256 * 1024)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise ApplicationTransportError("control-plane request failed") from exc
    try:
        result = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ApplicationTransportError("control-plane response is invalid") from exc
    if not isinstance(result, dict):
        raise ApplicationTransportError("control-plane response is not an object")
    return result


def fetch(*, base_url: str, token: str, application_id: str) -> dict[str, object]:
    result = _request(method="GET", url=_endpoint(base_url, application_id), token=token)
    if result.get("ok") is not True or not isinstance(result.get("application"), Mapping):
        raise ApplicationTransportError("application record unavailable")
    application = dict(result["application"])
    if str(application.get("application_id") or "").strip() != _application_id(application_id):
        raise ApplicationTransportError("application record identity mismatch")
    return application


def post_claim(*, base_url: str, token: str, record: Mapping[str, object], run_id: str, attempt: str) -> dict[str, object]:
    application_id = _application_id(str(record.get("application_id") or ""))
    claim = record.get("claim") if isinstance(record.get("claim"), Mapping) else record
    claim_token = str(claim.get("token") or claim.get("claim_token") or "").strip()
    result = _request(
        method="POST",
        url=_endpoint(base_url, application_id),
        token=token,
        payload={
            "application_id": application_id,
            "status": "claimed",
            "claim": {
                "token": claim_token,
                "workflow_run_id": str(run_id),
                "workflow_run_attempt": str(attempt),
            },
        },
    )
    if result.get("ok") is not True or result.get("claimed") is not True:
        raise ApplicationTransportError("application claim was not accepted")
    return result


def post_result(
    *,
    base_url: str,
    token: str,
    record: Mapping[str, object],
    status: str,
    readback: Mapping[str, object] | None,
    run_id: str,
    attempt: str,
) -> dict[str, object]:
    if status not in {"applied_paused", "rejected", "uncertain"}:
        raise ApplicationTransportError("invalid application result status")
    application_id = _application_id(str(record.get("application_id") or ""))
    claim = record.get("claim") if isinstance(record.get("claim"), Mapping) else record
    claim_token = str(claim.get("token") or claim.get("claim_token") or "").strip()
    result = _request(
        method="POST",
        url=_endpoint(base_url, application_id),
        token=token,
        payload={
            "application_id": application_id,
            "status": status,
            "readback": dict(readback) if readback is not None else None,
            "claim": {
                "token": claim_token,
                "workflow_run_id": str(run_id),
                "workflow_run_attempt": str(attempt),
            },
        },
    )
    if result.get("ok") is not True or result.get("status") != status:
        raise ApplicationTransportError("application result was not accepted")
    return result


def _load(path: str) -> dict[str, object]:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationTransportError("application record file is invalid") from exc
    if not isinstance(payload, dict):
        raise ApplicationTransportError("application record file is not an object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("fetch", "claim", "result"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--application-id")
    parser.add_argument("--record-file")
    parser.add_argument("--status")
    parser.add_argument("--readback-file")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    try:
        token = __import__("os").environ.get("EXECUTION_EVIDENCE_SYNC_TOKEN", "")
        if args.operation == "fetch":
            result = fetch(base_url=args.base_url, token=token, application_id=args.application_id or "")
        else:
            if not args.record_file:
                raise ApplicationTransportError("record file is required")
            record = _load(args.record_file)
            if args.operation == "claim":
                result = post_claim(
                    base_url=args.base_url,
                    token=token,
                    record=record,
                    run_id=args.run_id,
                    attempt=args.attempt,
                )
            else:
                readback = _load(args.readback_file) if args.readback_file else None
                result = post_result(
                    base_url=args.base_url,
                    token=token,
                    record=record,
                    status=args.status or "uncertain",
                    readback=readback,
                    run_id=args.run_id,
                    attempt=args.attempt,
                )
        json.dump(result, sys.stdout, ensure_ascii=True, separators=(",", ":"))
        sys.stdout.write("\n")
    except ApplicationTransportError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
