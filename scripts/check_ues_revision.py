"""Fail the image build unless installed UES matches the frozen source pin."""
from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path
import sys

from scripts.review_shared_dependency_delta import Incomplete, resolve_pins

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path) -> None:
    try:
        pins = resolve_pins(
            (root / "pyproject.toml").read_text(encoding="utf-8"),
            (root / "uv.lock").read_text(encoding="utf-8"),
        )
        raw = metadata.distribution("us-equity-strategies").read_text("direct_url.json")
        installed = json.loads(raw or "{}")
        vcs = installed.get("vcs_info", {})
        if vcs.get("vcs") != "git" or vcs.get("commit_id") != pins["us-equity-strategies"]:
            raise Incomplete("Installed UES does not match the frozen source pin")
    except (OSError, ValueError, TypeError, AttributeError, metadata.PackageNotFoundError) as exc:
        raise Incomplete("UES revision evidence unavailable or invalid") from exc


def main() -> int:
    try:
        verify(ROOT)
    except Incomplete:
        # Metadata, file paths and exception diagnostics may contain private data.
        print("UES installed revision verification failed.", file=sys.stderr)
        return 1
    print("UES installed revision verified against pyproject.toml and uv.lock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
