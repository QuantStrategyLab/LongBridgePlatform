#!/usr/bin/env python3
"""Build the redacted disabled-process binding from QRT and protected env."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from application.v7_paper_application import V7PaperApplicationError, build_v7_runtime_binding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-file", required=True)
    args = parser.parse_args()
    try:
        with Path(args.record_file).open(encoding="utf-8") as handle:
            record = json.load(handle)
        if not isinstance(record, dict):
            raise V7PaperApplicationError("application record is not an object")
        protected_env = {
            key: os.environ.get(key, "")
            for key in (
                "CLOUD_RUN_SERVICE",
                "CLOUD_RUN_REGION",
                "RUNTIME_TARGET_JSON",
                "QSL_RUNTIME_TARGET_JSON",
                "LONGBRIDGE_DRY_RUN_ONLY",
                "LONGPORT_SECRET_NAME",
                "LONGPORT_APP_KEY_SECRET_NAME",
                "LONGPORT_APP_SECRET_SECRET_NAME",
                "LONGBRIDGE_PHYSICAL_ACCOUNT_ID",
            )
        }
        binding = build_v7_runtime_binding(record, protected_env=protected_env)
        json.dump(binding, sys.stdout, ensure_ascii=True, separators=(",", ":"))
        sys.stdout.write("\n")
    except (OSError, json.JSONDecodeError, V7PaperApplicationError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
