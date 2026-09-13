#!/usr/bin/env python3
"""Extract one safe V7 startup receipt from Cloud Logging JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def find_readback(*, logs: object, application_id: str, revision: str) -> dict[str, object] | None:
    if not isinstance(logs, list):
        return None
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        payload = entry.get("jsonPayload")
        if not isinstance(payload, dict):
            raw = entry.get("textPayload")
            try:
                payload = json.loads(raw) if isinstance(raw, str) else None
            except json.JSONDecodeError:
                payload = None
        if not isinstance(payload, dict):
            continue
        if payload.get("event") != "v7_paper_application_loaded" or payload.get("status") != "applied_paused":
            continue
        readback = payload.get("readback")
        if (
            isinstance(readback, dict)
            and readback.get("application_id") == application_id
            and readback.get("revision_name") == revision
            and readback.get("runtime_target_enabled") is False
        ):
            return readback
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs-file", required=True)
    parser.add_argument("--application-id", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--readback-out", required=True)
    args = parser.parse_args()
    try:
        logs = json.loads(Path(args.logs_file).read_text(encoding="utf-8"))
        readback = find_readback(logs=logs, application_id=args.application_id, revision=args.revision)
        if readback is None:
            return 2
        Path(args.readback_out).write_text(json.dumps(readback, separators=(",", ":")), encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
