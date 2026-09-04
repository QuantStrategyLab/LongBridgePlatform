#!/usr/bin/env python3
"""Classify a local, redacted LongBridge admission-evidence JSON file.

The command has no provider, broker, Secret Manager, deployment, scheduler,
or order side effect.  It emits only fixed gate booleans and a reason code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.local_admission_preflight import evaluate_local_admission  # noqa: E402


def _load_payload(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path, help="Local redacted evidence JSON file")
    args = parser.parse_args(argv)
    result = evaluate_local_admission(_load_payload(args.input))
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["disposition"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
