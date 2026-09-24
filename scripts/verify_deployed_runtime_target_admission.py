#!/usr/bin/env python3
"""Fail closed before a Cloud Run rollout reaches an unadmitted target.

Only non-sensitive target identity fields are read from Cloud Run.  This
checker never reads Secret Manager values and never mutates a service.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from strategy_registry import LONGBRIDGE_PLATFORM, resolve_strategy_definition


class AdmissionError(ValueError):
    """A deployed runtime target is not safe to receive a new image."""


_SHA = re.compile(r"^[0-9a-f]{40}$")


def _locked_ues_revision(lock_text: str) -> str:
    try:
        packages = tomllib.loads(lock_text)["package"]
        sources = [
            package["source"]["git"]
            for package in packages
            if package.get("name") == "us-equity-strategies"
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise AdmissionError("UES lock entry is missing or invalid") from exc
    if len(sources) != 1:
        raise AdmissionError("UES lock must contain exactly one package")
    match = re.search(r"[?&]rev=([0-9a-f]{40})#([0-9a-f]{40})$", sources[0])
    if not match or match.group(1) != match.group(2):
        raise AdmissionError("UES lock must resolve to one full commit SHA")
    return match.group(1)


def _candidate_ues_revision() -> str:
    root = Path(__file__).resolve().parents[1]
    candidate = _locked_ues_revision((root / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project.get("project", {}).get("dependencies", [])
    refs = [
        match.group(1)
        for dependency in dependencies
        if (match := re.match(
            r"^us-equity-strategies\s*@\s*git\+https://github\.com/QuantStrategyLab/UsEquityStrategies\.git@([0-9a-f]{40})$",
            dependency,
        ))
    ]
    if refs != [candidate]:
        raise AdmissionError("UES pyproject and uv.lock revisions differ")
    qsl = tomllib.loads((root / "qsl.toml").read_text(encoding="utf-8"))
    if qsl.get("qsl", {}).get("requires", {}).get("us_equity_strategies") != candidate:
        raise AdmissionError("UES QSL contract and uv.lock revisions differ")
    return candidate


def _serving_ues_revisions(*, service: str, project: str, region: str, service_json: Mapping[str, Any]) -> set[str]:
    traffic = service_json.get("status", {}).get("traffic", [])
    if not isinstance(traffic, list):
        raise AdmissionError(f"{service}: serving traffic is unavailable")
    serving = [entry.get("revisionName") for entry in traffic if isinstance(entry, Mapping) and entry.get("percent", 0) > 0]
    if not serving or any(not isinstance(revision, str) or not revision for revision in serving):
        raise AdmissionError(f"{service}: serving revisions are unavailable")
    revisions = set()
    for revision in serving:
        try:
            revision_json = json.loads(_run([
                "gcloud", "run", "revisions", "describe", revision,
                f"--project={project}", f"--region={region}", "--format=json",
            ]))
            source_sha = revision_json["metadata"]["labels"]["commit-sha"]
            if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
                raise ValueError("invalid source SHA")
            lock_text = _run(["git", "show", f"{source_sha}:uv.lock"])
            revisions.add(_locked_ues_revision(lock_text))
        except (KeyError, TypeError, ValueError, AdmissionError) as exc:
            raise AdmissionError(f"{service}: serving UES revision cannot be independently verified") from exc
    return revisions


def verify_ues_image_pin(
    *, service: str, project: str, region: str, service_json: Mapping[str, Any], admission: Mapping[str, object]
) -> None:
    """Keep a configured account on its serving UES pin until the target approves a change."""
    if admission["profile"] not in {
        "soxl_soxx_trend_income", "russell_top50_leader_rotation"
    }:
        return
    candidate = _candidate_ues_revision()
    env = _container_env(service_json)
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    release = target.get("strategy_release") or {}
    risk_binding = (target.get("runtime_risk_limits") or {}).get("binding") or {}
    approved = release.get("strategy_revision")
    risk_pin = risk_binding.get("ues_revision")
    if approved is not None or risk_pin is not None:
        if approved != candidate or (admission["profile"] == "soxl_soxx_trend_income" and risk_pin != candidate):
            raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding")
        return
    serving = _serving_ues_revisions(service=service, project=project, region=region, service_json=service_json)
    if serving != {candidate}:
        raise AdmissionError(f"{service}: candidate UES revision differs from serving image without approved target binding")


def _run(command: Sequence[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise AdmissionError(detail or f"Command failed: {' '.join(command)}")
    return result.stdout


def _describe_service(*, service: str, project: str, region: str) -> Mapping[str, Any]:
    payload = _run(
        [
            "gcloud",
            "run",
            "services",
            "describe",
            service,
            f"--project={project}",
            f"--region={region}",
            "--format=json",
        ]
    )
    loaded = json.loads(payload)
    if not isinstance(loaded, Mapping):
        raise AdmissionError(f"{service}: Cloud Run describe returned a non-object payload")
    return loaded


def _container_env(service_json: Mapping[str, Any]) -> dict[str, str]:
    containers = service_json.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    if not isinstance(containers, list) or not containers:
        raise AdmissionError("Cloud Run service has no container configuration")
    entries = containers[0].get("env", [])
    if not isinstance(entries, list):
        raise AdmissionError("Cloud Run container environment is malformed")
    return {
        str(entry.get("name") or "").strip(): str(entry.get("value") or "").strip()
        for entry in entries
        if isinstance(entry, Mapping) and str(entry.get("name") or "").strip() and "value" in entry
    }


def _parse_bool(value: object, *, field: str, service: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise AdmissionError(f"{service}: {field} must be a boolean")


def verify_service(*, service: str, service_json: Mapping[str, Any]) -> dict[str, object]:
    """Validate one deployed service without printing account or secret data."""

    env = _container_env(service_json)
    raw_target = env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON")
    if not raw_target:
        raise AdmissionError(f"{service}: RUNTIME_TARGET_JSON is required for image admission")
    try:
        target = json.loads(raw_target)
    except json.JSONDecodeError as exc:
        raise AdmissionError(f"{service}: RUNTIME_TARGET_JSON is invalid JSON") from exc
    if not isinstance(target, Mapping):
        raise AdmissionError(f"{service}: RUNTIME_TARGET_JSON must be an object")
    target_service = str(target.get("service_name") or "").strip()
    if target_service and target_service != service:
        raise AdmissionError(f"{service}: runtime target service_name does not match the deployed service")

    raw_profile = str(target.get("strategy_profile") or "").strip()
    if not raw_profile:
        raise AdmissionError(f"{service}: runtime target strategy_profile is required")
    try:
        definition = resolve_strategy_definition(raw_profile, platform_id=LONGBRIDGE_PLATFORM)
    except (TypeError, ValueError) as exc:
        raise AdmissionError(f"{service}: strategy profile is not admitted") from exc
    canonical_profile = definition.profile
    if str(env.get("STRATEGY_PROFILE") or "").strip() != canonical_profile:
        raise AdmissionError(f"{service}: STRATEGY_PROFILE does not match the admitted runtime target profile")

    execution_mode = str(target.get("execution_mode") or "").strip().lower()
    if execution_mode not in {"paper", "live"}:
        raise AdmissionError(f"{service}: execution_mode must be paper or live")
    if "dry_run_only" not in target:
        raise AdmissionError(f"{service}: runtime target dry_run_only is required")
    target_dry_run = _parse_bool(target["dry_run_only"], field="runtime target dry_run_only", service=service)
    configured_dry_run = env.get("LONGBRIDGE_DRY_RUN_ONLY")
    if configured_dry_run is not None and _parse_bool(
        configured_dry_run, field="LONGBRIDGE_DRY_RUN_ONLY", service=service
    ) != target_dry_run:
        raise AdmissionError(f"{service}: LONGBRIDGE_DRY_RUN_ONLY does not match runtime target dry_run_only")
    if target_dry_run and execution_mode != "paper":
        raise AdmissionError(f"{service}: a dry-run/shadow target must declare execution_mode=paper")

    enabled = _parse_bool(env.get("RUNTIME_TARGET_ENABLED", "true"), field="RUNTIME_TARGET_ENABLED", service=service)
    return {
        "service": service,
        "profile": canonical_profile,
        "execution_mode": execution_mode,
        "dry_run_only": target_dry_run,
        "enabled": enabled,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    args = parser.parse_args()
    try:
        service_json = _describe_service(service=args.service, project=args.project, region=args.region)
        result = verify_service(
            service=args.service,
            service_json=service_json,
        )
        verify_ues_image_pin(
            service=args.service,
            project=args.project,
            region=args.region,
            service_json=service_json,
            admission=result,
        )
    except AdmissionError as exc:
        print(f"Deployed runtime target admission failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Verified deployed runtime target admission: "
        f"service={result['service']}, profile={result['profile']}, "
        f"mode={result['execution_mode']}, dry_run_only={result['dry_run_only']}, "
        f"enabled={result['enabled']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
