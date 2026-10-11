#!/usr/bin/env python3
"""Read-only, advisory shared-dependency delta report. Never imports runtime code."""
from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import tomllib
from pathlib import Path

REPOSITORIES = {
    "quant-platform-kit": "QuantPlatformKit",
    "us-equity-strategies": "UsEquityStrategies",
    "hk-equity-strategies": "HkEquityStrategies",
}
SHA = re.compile(r"[0-9a-f]{40}")
MAX_OUTPUT = 2_000_000
# Deliberately conservative path hints, not a call graph or a safety verdict.
FLAGS = {
    "auth / token refresh": ("auth", "token", "credential"),
    "cloud / possible secret writes": ("secret", "cloud", "auth", "token"),
    "execution": ("execution", "order", "broker"),
    "risk": ("risk", "guard", "stop_loss", "stoploss"),
    "scheduler / automation": ("scheduler", "schedule", "watch/", "automation"),
}
CALL_NOTES = (
    "Local review note (not automatically proven): main.build_composer injects "
    "refresh_token_if_needed into application/runtime_bootstrap_adapters.py "
    "LongBridgeRuntimeBootstrap.build_contexts. That normal startup path can reach upstream "
    "longbridge/auth.py and token/Secret Manager writes. The separate read-only "
    "account-snapshot context path bypasses refresh; that does not make normal startup read-only."
)


class Incomplete(Exception):
    """Evidence is missing, invalid, failed, or exceeds the bounded report limit."""


def exact_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise Incomplete("Missing or invalid full commit SHA")
    return value


def run_git(repo: Path, *args: str, timeout: int = 60) -> bytes:
    # No shell, no diff helpers, no credential contents in output/errors.
    with tempfile.TemporaryFile() as output:
        try:
            subprocess.run(
                ["git", "-C", str(repo), *args], stdout=output,
                stderr=subprocess.DEVNULL, check=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise Incomplete("Git comparison/read failed or timed out; access may be unavailable") from exc
        if output.tell() > MAX_OUTPUT:
            raise Incomplete("Git output exceeds report limit; comparison incomplete (not truncated silently)")
        output.seek(0)
        return output.read()


def resolve_pins(project_text: str, lock_text: str) -> dict[str, str]:
    try:
        project, lock = tomllib.loads(project_text), tomllib.loads(lock_text)
        dependencies = project["project"]["dependencies"]
        overrides = project.get("tool", {}).get("uv", {}).get("override-dependencies", [])
        result = {}
        for name, repository in REPOSITORIES.items():
            prefix = f"{name} @ git+https://github.com/QuantStrategyLab/{repository}.git@"
            def matches_name(dependency: str) -> bool:
                match = re.match(r"^\s*([A-Za-z0-9_.-]+)", dependency)
                return bool(match and re.sub(r"[-_.]+", "-", match[1]).lower() == name)

            declarations = [d for d in dependencies if matches_name(d)]
            refs = [d[len(prefix):] for d in declarations if d.startswith(prefix)]
            if len(declarations) != 1 or len(refs) != 1:
                raise Incomplete(f"{name}: missing or ambiguous direct pin")
            pin = exact_sha(refs[0])
            for dependency in overrides:
                if matches_name(dependency) and dependency != prefix + pin:
                    raise Incomplete(f"{name}: override mismatch")
            packages = [p for p in lock["package"] if p["name"] == name]
            expected = f"https://github.com/QuantStrategyLab/{repository}.git?rev={pin}#{pin}"
            if len(packages) != 1 or packages[0].get("source", {}).get("git") != expected:
                raise Incomplete(f"{name}: pyproject/lock SHA mismatch or missing resolved SHA")
            result[name] = pin
        return result
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise Incomplete("Missing or malformed dependency manifests") from exc


def pins_at(repo: Path, ref: str) -> dict[str, str]:
    exact_sha(ref)
    return resolve_pins(
        run_git(repo, "show", f"{ref}:pyproject.toml").decode(),
        run_git(repo, "show", f"{ref}:uv.lock").decode(),
    )


def changed_paths(repo: Path, base: str, head: str) -> list[str]:
    exact_sha(base)
    exact_sha(head)
    raw = run_git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                  "--name-only", "-z", base, head, "--")
    if raw and not raw.endswith(b"\0"):
        raise Incomplete("Incomplete/truncated path list")
    try:
        return sorted(set(p.decode("utf-8") for p in raw.split(b"\0") if p))
    except UnicodeError as exc:
        raise Incomplete("Non-UTF-8 path cannot be represented losslessly") from exc


def upstream_delta(repository: str, base: str, head: str) -> list[str]:
    if repository not in REPOSITORIES.values():
        raise Incomplete("Unrecognized upstream repository")
    exact_sha(base)
    exact_sha(head)
    with tempfile.TemporaryDirectory(prefix="dependency-review-") as directory:
        repo = Path(directory)
        run_git(repo, "init", "--bare")
        # Fixed GitHub URLs, exact SHAs, depth one. No installation/import or hooks.
        url = f"https://github.com/QuantStrategyLab/{repository}.git"
        for ref in dict.fromkeys((base, head)):
            run_git(repo, "-c", "credential.helper=", "-c", "core.askPass=",
                    "fetch", "--no-tags", "--depth=1", url, ref, timeout=120)
        return changed_paths(repo, base, head)


def category(path: str) -> str:
    lower = path.lower()
    if lower.startswith("docs/") or lower.endswith((".md", ".rst")):
        return "Documentation"
    if lower.startswith(("tests/", "test/")):
        return "Tests"
    if lower.startswith(".github/"):
        return "CI/configuration"
    return "Production / other (review required)"


def quoted(value: str) -> str:
    # Escape paths for GitHub Markdown/HTML and make control characters visible.
    visible = value.encode("unicode_escape").decode("ascii")
    return "<code>" + "".join(c if c.isalnum() else f"&#{ord(c)};" for c in visible) + "</code>"


def report(base: dict[str, str], head: dict[str, str], compare=upstream_delta) -> str:
    lines = ["# Shared dependency change impact (advisory)", "",
             "Report generation success is NOT an upgrade-safety verdict or merge approval.",
             "Dynamic imports, runtime reachability and transitive behavior remain unresolved; "
             "an unflagged path is not proven safe.", "", CALL_NOTES, ""]
    for name, repository in REPOSITORIES.items():
        before, after = base[name], head[name]
        lines += [f"## {name}", f"Base: {quoted(before)}; head: {quoted(after)}"]
        if before == after:
            lines += ["No pin change; upstream comparison not needed.", ""]
            continue
        try:
            paths = compare(repository, before, after)
        except Incomplete as exc:
            lines += [f"INCOMPLETE: {quoted(str(exc))}. No safety conclusion.", ""]
            continue
        flags = [label for label, hints in FLAGS.items()
                 if any(any(hint in path.lower() for hint in hints)
                        for path in paths if category(path).startswith("Production"))]
        lines += ["Comparison: complete path list (full Git tree diff; no REST pagination).",
                  "High-impact review flags: " + (", ".join(flags) or "none matched; not proof of safety")]
        if paths and all(category(p) == "Documentation" for p in paths):
            lines.append("Documentation-only delta (path classification only).")
        for group in ("Production / other (review required)", "Documentation", "Tests", "CI/configuration"):
            selected = [p for p in paths if category(p) == group]
            lines += [f"### {group} ({len(selected)})"]
            lines.extend(f"- {quoted(path)}" for path in selected)
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        content = report(pins_at(args.repo, args.base_ref), pins_at(args.repo, args.head_ref))
    except (Incomplete, UnicodeError) as exc:
        content = "# Shared dependency change impact\n\nINCOMPLETE: " + quoted(str(exc)) + "\n\nNo upgrade-safety conclusion.\n"
    if len(content.encode("utf-8")) > 900_000:
        content = "# Shared dependency change impact\n\nINCOMPLETE: rendered report exceeds summary limit. No safety conclusion.\n"
    args.output.write_text(content, encoding="utf-8")
    return 0  # Advisory only: existing pin/lock and test gates remain authoritative.


if __name__ == "__main__":
    raise SystemExit(main())
