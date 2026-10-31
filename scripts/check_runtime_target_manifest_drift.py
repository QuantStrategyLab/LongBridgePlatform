#!/usr/bin/env python3
"""Fail when the public manifest disagrees with an Environment runtime target.

``config/runtime_targets.manifest.json`` is a checked-in, hand-maintained
inventory. ``RUNTIME_TARGET_JSON`` / ``STRATEGY_PROFILE`` are the deployment
truth. This script compares the duplicated fields so drift surfaces in the
scheduled Runtime Guard instead of in production behaviour.

Read-only: no Cloud Run, GitHub or broker calls, and no values are written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from application.runtime_target_manifest import (  # noqa: E402
    RuntimeTargetEntry,
    RuntimeTargetManifest,
    RuntimeTargetManifestError,
    default_manifest_path,
    load_runtime_target_manifest,
)


@dataclass(frozen=True)
class DriftFinding:
    code: str
    detail: str


def _text(value: Any) -> str:
    return str(value or "").strip()


def evaluate_target_drift(
    manifest: RuntimeTargetManifest,
    *,
    target_id: str,
    runtime_target_json: str | None,
    strategy_profile_env: str | None = None,
) -> list[DriftFinding]:
    """Compare one manifest target with its Environment runtime target."""
    matches = [target for target in manifest.targets if target.id == target_id]
    if len(matches) != 1:
        return [DriftFinding("manifest_target_missing", f"target id {target_id!r} is not in the manifest")]
    manifest_target: RuntimeTargetEntry = matches[0]

    raw_target = (runtime_target_json or "").strip()
    if not raw_target:
        return [DriftFinding("runtime_target_missing", "RUNTIME_TARGET_JSON is empty")]
    try:
        target = json.loads(raw_target)
    except json.JSONDecodeError:
        return [DriftFinding("runtime_target_invalid", "RUNTIME_TARGET_JSON is not valid JSON")]
    if not isinstance(target, Mapping):
        return [DriftFinding("runtime_target_invalid", "RUNTIME_TARGET_JSON must be a JSON object")]

    findings: list[DriftFinding] = []
    platform_id = _text(target.get("platform_id"))
    if platform_id and platform_id != manifest.platform_id:
        findings.append(
            DriftFinding("platform_id_mismatch", f"runtime target platform_id={platform_id} but manifest platform_id={manifest.platform_id}")
        )

    actual_profile = _text(target.get("strategy_profile"))
    if not actual_profile:
        findings.append(DriftFinding("runtime_target_incomplete", "RUNTIME_TARGET_JSON has no strategy_profile"))
    elif actual_profile != manifest_target.strategy_profile:
        findings.append(
            DriftFinding(
                "strategy_profile_mismatch",
                f"runtime target strategy_profile={actual_profile} but manifest strategy_profile={manifest_target.strategy_profile}",
            )
        )

    env_profile = _text(strategy_profile_env)
    if env_profile and actual_profile and env_profile != actual_profile:
        findings.append(
            DriftFinding(
                "strategy_profile_conflict",
                f"STRATEGY_PROFILE={env_profile} but RUNTIME_TARGET_JSON.strategy_profile={actual_profile}",
            )
        )

    actual_scope = _text(target.get("account_scope"))
    if actual_scope and manifest_target.account_scope and actual_scope != manifest_target.account_scope:
        findings.append(
            DriftFinding(
                "account_scope_mismatch",
                f"runtime target account_scope={actual_scope} but manifest account_scope={manifest_target.account_scope}",
            )
        )

    actual_service = _text(target.get("service_name"))
    if actual_service and actual_service != manifest_target.service:
        findings.append(
            DriftFinding(
                "service_name_mismatch",
                f"runtime target service_name={actual_service} but manifest service={manifest_target.service}",
            )
        )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-id", required=True, help="Manifest target id, e.g. paper / hk / sg")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Manifest path (default: config/runtime_targets.manifest.json)",
    )
    args = parser.parse_args(argv)

    try:
        manifest = load_runtime_target_manifest(args.path or default_manifest_path())
    except RuntimeTargetManifestError as exc:
        print(f"runtime-target manifest drift check failed: {exc}", file=sys.stderr)
        return 1

    findings = evaluate_target_drift(
        manifest,
        target_id=args.target_id,
        runtime_target_json=os.environ.get("RUNTIME_TARGET_JSON"),
        strategy_profile_env=os.environ.get("STRATEGY_PROFILE"),
    )
    if not findings:
        print(f"OK target={args.target_id} manifest matches the Environment runtime target")
        return 0
    for finding in findings:
        print(f"runtime-target manifest drift: {finding.code}: {finding.detail}", file=sys.stderr)
    print(
        "Update config/runtime_targets.manifest.json to match the deployed runtime target; "
        "the manifest is public reference data and never overrides production.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
