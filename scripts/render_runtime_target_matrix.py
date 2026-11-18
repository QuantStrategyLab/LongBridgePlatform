#!/usr/bin/env python3
"""Render a validated runtime-target manifest as a GitHub Actions matrix.

Fail-closed: missing or invalid manifests exit non-zero.
Manifest ``enabled`` never filters targets and is never written into the matrix.
Production enablement remains Environment ``RUNTIME_TARGET_ENABLED``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from longbridge_platform.application.runtime_target_manifest import (  # noqa: E402
    ALLOWED_MODES,
    MATRIX_PROFILES,
    RuntimeTargetManifestError,
    build_github_actions_matrix,
    default_manifest_path,
    load_runtime_target_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Manifest path (default: config/runtime_targets.manifest.json)",
    )
    parser.add_argument(
        "--profile",
        required=True,
        choices=sorted(MATRIX_PROFILES),
        help="Workflow matrix shape to render.",
    )
    parser.add_argument(
        "--target",
        default="all",
        help="Render all targets (default) or exactly one manifest target ID.",
    )
    parser.add_argument(
        "--mode",
        choices=("all", *sorted(ALLOWED_MODES)),
        default=None,
        help="Optional mode filter when --target all (defaults to RUNTIME_TARGET_MATRIX_MODE or 'all').",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="Append compact matrix=... to $GITHUB_OUTPUT for workflow jobs.",
    )
    args = parser.parse_args(argv)

    try:
        mode = args.mode
        if mode is None:
            mode = (os.environ.get("RUNTIME_TARGET_MATRIX_MODE") or "").strip().lower() or "all"
        if mode not in {"all", *ALLOWED_MODES}:
            raise RuntimeTargetManifestError(
                f"Unknown RUNTIME_TARGET_MATRIX_MODE {mode!r}; expected 'all' or one of {sorted(ALLOWED_MODES)}"
            )
        manifest = load_runtime_target_manifest(args.path or default_manifest_path())
        matrix = build_github_actions_matrix(manifest, profile=args.profile)
        if args.target != "all":
            selected = [row for row in matrix["target"] if row["id"] == args.target]
            if not selected:
                raise RuntimeTargetManifestError(
                    f"Unknown target {args.target!r}; expected 'all' or a manifest target ID"
                )
            matrix["target"] = selected
        elif mode != "all":
            allowed_ids = {entry.id for entry in manifest.targets if entry.mode == mode}
            selected = [row for row in matrix["target"] if row["id"] in allowed_ids]
            if not selected:
                raise RuntimeTargetManifestError(
                    f"No manifest targets matched mode {mode!r}"
                )
            matrix["target"] = selected
    except RuntimeTargetManifestError as exc:
        print(f"runtime-target matrix render failed: {exc}", file=sys.stderr)
        return 1

    # Reject accidental emission of enablement fields.
    for row in matrix["target"]:
        if "enabled" in row:
            print(
                "runtime-target matrix render failed: matrix rows must not include enabled",
                file=sys.stderr,
            )
            return 1

    payload = json.dumps(matrix, separators=(",", ":"), sort_keys=True)
    if args.github_output:
        github_output = os.environ.get("GITHUB_OUTPUT")
        if not github_output:
            print(
                "runtime-target matrix render failed: GITHUB_OUTPUT is not set",
                file=sys.stderr,
            )
            return 1
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"matrix={payload}\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
