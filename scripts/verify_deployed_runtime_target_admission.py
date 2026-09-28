#!/usr/bin/env python3
"""Fail closed before a Cloud Run rollout reaches an unadmitted target.

Only non-sensitive target identity fields are read from Cloud Run.  This
checker never reads Secret Manager values and never mutates a service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.reconcile_cloud_runtime import ReconcileError, observe_ready_revision, read_traffic
from strategy_registry import LONGBRIDGE_PLATFORM, resolve_strategy_definition


class AdmissionError(ValueError):
    """A deployed runtime target is not safe to receive a new image."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
_HISTORY_KEYS = (
    "ACCOUNT_HISTORY_RECORDING_ENABLED",
    "ACCOUNT_HISTORY_GCS_PREFIX",
    "ACCOUNT_HISTORY_TARGET_ID",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE",
)
_UNSAFE_HISTORY = re.compile(r"[,=\s'\"`$\\|&<>]|^\-")
_PINNED_PROFILES = {"soxl_soxx_trend_income", "russell_top50_leader_rotation"}
_INGRESS = "run.googleapis.com/ingress"


def _locked_ues_revision(lock_text: str) -> str:
    try:
        packages = tomllib.loads(lock_text)["package"]
        sources = [
            package["source"]["git"]
            for package in packages
            if package.get("name") == "us-equity-strategies"
        ]
    except (KeyError, TypeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise AdmissionError("UES lock entry is missing or invalid") from exc
    if len(sources) != 1:
        raise AdmissionError("UES lock must contain exactly one package")
    match = re.search(r"[?&]rev=([0-9a-f]{40})#([0-9a-f]{40})$", sources[0])
    if not match or match.group(1) != match.group(2):
        raise AdmissionError("UES lock must resolve to one full commit SHA")
    return match.group(1)


def _candidate_ues_revision() -> str:
    candidate = _locked_ues_revision((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project.get("project", {}).get("dependencies", [])
    refs = [
        match.group(1)
        for dependency in dependencies
        if (
            match := re.match(
                r"^us-equity-strategies\s*@\s*git\+https://github\.com/QuantStrategyLab/"
                r"UsEquityStrategies\.git@([0-9a-f]{40})$",
                dependency,
            )
        )
    ]
    if refs != [candidate]:
        raise AdmissionError("UES pyproject and uv.lock revisions differ")
    qsl = tomllib.loads((_ROOT / "qsl.toml").read_text(encoding="utf-8"))
    if qsl.get("qsl", {}).get("requires", {}).get("us_equity_strategies") != candidate:
        raise AdmissionError("UES QSL contract and uv.lock revisions differ")
    return candidate


def _serving_ues_revisions(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    run: Callable[[Sequence[str]], str],
) -> set[str]:
    try:
        serving = [row["revisionName"] for row in read_traffic(service_json) if int(row["percent"]) > 0]
    except ReconcileError as exc:
        raise AdmissionError(f"{service}: serving revisions are unavailable") from exc
    if not serving:
        raise AdmissionError(f"{service}: serving revisions are unavailable")
    revisions: set[str] = set()
    for revision in serving:
        try:
            revision_json = _load_object(
                run(
                    [
                        "gcloud",
                        "run",
                        "revisions",
                        "describe",
                        revision,
                        f"--project={project}",
                        f"--region={region}",
                        "--format=json",
                    ]
                )
            )
            source_sha = revision_json["metadata"]["labels"]["commit-sha"]
            if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
                raise ValueError("invalid source SHA")
            lock_text = run(["git", "show", f"{source_sha}:uv.lock"])
            revisions.add(_locked_ues_revision(lock_text))
        except (KeyError, TypeError, ValueError, AdmissionError) as exc:
            raise AdmissionError(f"{service}: serving UES revision cannot be independently verified") from exc
    return revisions


def verify_ues_image_pin(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    admission: Mapping[str, object],
    run: Callable[[Sequence[str]], str] | None = None,
    require_serving: bool = False,
) -> None:
    """Keep a configured account on its serving UES pin until the target approves a change."""

    if not require_serving:
        if not admission["enabled"]:
            return
        if admission["profile"] not in _PINNED_PROFILES:
            return
    candidate = _candidate_ues_revision()
    env = _container_env(service_json)
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    release = target.get("strategy_release") or {}
    risk_binding = (target.get("runtime_risk_limits") or {}).get("binding") or {}
    approved = release.get("strategy_revision")
    risk_pin = risk_binding.get("ues_revision")
    if approved is not None or risk_pin is not None:
        if approved != candidate or (
            admission["profile"] == "soxl_soxx_trend_income" and risk_pin != candidate
        ):
            raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding")
        if not require_serving:
            return
    serving = _serving_ues_revisions(
        service=service,
        project=project,
        region=region,
        service_json=service_json,
        run=run or _run,
    )
    if serving != {candidate}:
        raise AdmissionError(
            f"{service}: candidate UES revision differs from serving image without approved target binding"
        )


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


def _load_object(payload: str) -> Mapping[str, Any]:
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AdmissionError("cloud read failed") from exc
    if not isinstance(loaded, Mapping):
        raise AdmissionError("cloud read failed")
    return loaded


def _run_quiet(command: Sequence[str]) -> str:
    try:
        return _run(command)
    except AdmissionError:
        raise AdmissionError("cloud read failed") from None


def _pod_spec(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    spec = payload.get("spec")
    if not isinstance(spec, Mapping):
        raise AdmissionError("container environment is malformed")
    containers = spec.get("containers")
    if isinstance(containers, list):
        return spec
    template = spec.get("template")
    if isinstance(template, Mapping) and isinstance(template.get("spec"), Mapping):
        return template["spec"]
    raise AdmissionError("container environment is malformed")


def _env_material(spec: Mapping[str, Any]) -> list[dict[str, str]]:
    containers = spec.get("containers")
    if not isinstance(containers, list) or not containers or not isinstance(containers[0], Mapping):
        raise AdmissionError("container environment is malformed")
    entries = containers[0].get("env", [])
    if not isinstance(entries, list):
        raise AdmissionError("container environment is malformed")
    material: list[dict[str, str]] = []
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise AdmissionError("container environment is malformed")
        name = entry.get("name")
        if not isinstance(name, str) or not name or name != name.strip() or name in names:
            raise AdmissionError("container environment is malformed")
        names.add(name)
        if "value" in entry and "valueFrom" not in entry:
            value = entry.get("value")
            if not isinstance(value, str):
                raise AdmissionError("container environment is malformed")
            material.append({"name": name, "value": value})
            continue
        secret = ((entry.get("valueFrom") or {}) if isinstance(entry.get("valueFrom"), Mapping) else {}).get(
            "secretKeyRef"
        )
        if not isinstance(secret, Mapping) or "value" in entry:
            raise AdmissionError("container environment is malformed")
        secret_name = secret.get("name")
        secret_key = secret.get("key")
        if not isinstance(secret_name, str) or not secret_name or not isinstance(secret_key, str) or not secret_key:
            raise AdmissionError("container environment is malformed")
        material.append({"key": secret_key, "name": name.strip(), "secret": secret_name})
    material.sort(key=lambda item: item["name"])
    return material


def _ingress(metadata: object) -> str:
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise AdmissionError("container environment is malformed")
    annotations = metadata.get("annotations") or {}
    if not isinstance(annotations, Mapping):
        raise AdmissionError("container environment is malformed")
    return str(annotations.get(_INGRESS) or "")


def _configuration(payload: Mapping[str, Any], *, template: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = _pod_spec(payload if template is None else template)
    return {
        "env": _env_material(spec),
        "serviceAccountName": str(spec.get("serviceAccountName") or ""),
    }


def _config_digest(configuration: Mapping[str, Any], *, skip: set[str] | None = None) -> str:
    omitted = skip or set()
    body = {
        "env": [item for item in configuration["env"] if item["name"] not in omitted],
        "serviceAccountName": configuration["serviceAccountName"],
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _literal_values(configuration: Mapping[str, Any]) -> dict[str, str]:
    return {item["name"]: item["value"] for item in configuration["env"] if "value" in item}


def history_update(
    env: Mapping[str, str], *, workflow_target: str, project_id: str
) -> dict[str, str] | None:
    """Return an explicit PAPER enable, or None when history inputs were omitted."""

    values = {key: str(env.get(key) or "") for key in _HISTORY_KEYS}
    if all(value == "" for value in values.values()):
        return None
    if workflow_target != "PAPER":
        raise AdmissionError("history settings are only admitted for PAPER")
    if any(not value or _UNSAFE_HISTORY.search(value) for value in values.values()):
        raise AdmissionError("history settings are invalid")
    if values["ACCOUNT_HISTORY_RECORDING_ENABLED"] != "true":
        raise AdmissionError("history settings are invalid")
    if values["ACCOUNT_HISTORY_EXPECTED_SCOPE"] != "PAPER" or values["ACCOUNT_HISTORY_TARGET_ID"] != "paper":
        raise AdmissionError("history settings are only admitted for PAPER")
    from scripts.record_daily_account_snapshot import _Rejected, _config

    try:
        config = _config({**values, "GOOGLE_CLOUD_PROJECT": project_id})
    except _Rejected:
        raise AdmissionError("history settings are invalid") from None
    if config.prefix != values["ACCOUNT_HISTORY_GCS_PREFIX"] or config.target_id != values["ACCOUNT_HISTORY_TARGET_ID"]:
        raise AdmissionError("history settings are invalid")
    return values


def _history_arg(values: Mapping[str, str] | None) -> str:
    if not values:
        return ""
    return ",".join(f"{key}={values[key]}" for key in _HISTORY_KEYS)


def verify_template_matches_serving(
    *, service: str, service_json: Mapping[str, Any], serving_revisions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Reject an unpromoted template that does not match the revisions receiving traffic."""

    template = service_json.get("spec", {}).get("template")
    if not isinstance(template, Mapping) or not serving_revisions:
        raise AdmissionError(f"{service}: template does not match the serving revision")
    template_configuration = _configuration(service_json, template=template)
    for revision in serving_revisions:
        if _configuration(revision) != template_configuration:
            raise AdmissionError(f"{service}: template does not match the serving revision")
    return template_configuration


def _paper_selector(value: object) -> tuple[str, ...] | None:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return tuple(str(item).strip().upper() for item in value)


def _require_paper_target_identity(*, service: str, service_json: Mapping[str, Any]) -> None:
    """Match the protected PAPER target fields. Optional selectors are checked only when present."""

    env = _container_env(service_json)
    target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    if not isinstance(target, Mapping):
        raise AdmissionError(f"{service}: runtime target identity does not match PAPER")
    if (
        str(target.get("platform_id") or "").strip().lower() != "longbridge"
        or str(target.get("service_name") or "").strip() != service
        or str(target.get("account_scope") or "").strip().upper() != "PAPER"
        or _paper_selector(target.get("account_selector")) != ("PAPER",)
    ):
        raise AdmissionError(f"{service}: runtime target identity does not match PAPER")
    if "deployment_selector" in target and str(target.get("deployment_selector") or "").strip().upper() != "PAPER":
        raise AdmissionError(f"{service}: runtime target identity does not match PAPER")
    if "account_selectors" in target and _paper_selector(target.get("account_selectors")) != ("PAPER",):
        raise AdmissionError(f"{service}: runtime target identity does not match PAPER")


def prepare_image_only_staging(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    env: Mapping[str, str],
    run: Callable[[Sequence[str]], str],
) -> dict[str, Any]:
    """Admit one no-traffic image update. The returned plan contains no service env."""

    admission = verify_service(service=service, service_json=service_json)
    _require_paper_target_identity(service=service, service_json=service_json)
    try:
        traffic = read_traffic(service_json)
    except ReconcileError:
        raise AdmissionError(f"{service}: serving revisions are unavailable") from None
    revisions: list[Mapping[str, Any]] = []
    for row in traffic:
        if int(row["percent"]) <= 0:
            continue
        revisions.append(
            _load_object(
                run(
                    [
                        "gcloud",
                        "run",
                        "revisions",
                        "describe",
                        str(row["revisionName"]),
                        f"--project={project}",
                        f"--region={region}",
                        "--format=json",
                    ]
                )
            )
        )
    configuration = verify_template_matches_serving(
        service=service, service_json=service_json, serving_revisions=revisions
    )
    verify_ues_image_pin(
        service=service,
        project=project,
        region=region,
        service_json=service_json,
        admission=admission,
        run=run,
        require_serving=True,
    )
    history = history_update(env, workflow_target=str(env.get("WORKFLOW_TARGET") or ""), project_id=project)
    return {
        "history_arg": _history_arg(history),
        "history_values": history or {},
        "service_ingress": _ingress(service_json.get("metadata")),
        "serving_traffic": traffic,
        "template_digest": _config_digest(configuration, skip=set(history or ())),
    }


def confirm_image_only_readback(
    *,
    service: str,
    project: str,
    region: str,
    plan: Mapping[str, Any],
    expected_image: str,
    expected_commit: str,
    expected_revision: str,
    run: Callable[[Sequence[str]], str],
) -> None:
    """Read the named revision once. A failed or unknown read is not a retry."""

    if (
        not _SHA.fullmatch(expected_commit)
        or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", expected_image)
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,61}[a-z0-9]", expected_revision)
        or not isinstance(plan.get("service_ingress"), str)
    ):
        raise AdmissionError("staged readback is incomplete")
    service_json = _load_object(
        run(
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
    )
    try:
        traffic = read_traffic(service_json)
    except ReconcileError:
        raise AdmissionError("staged readback is incomplete") from None
    if traffic != plan.get("serving_traffic"):
        raise AdmissionError("serving traffic changed")
    if _ingress(service_json.get("metadata")) != plan.get("service_ingress"):
        raise AdmissionError("service ingress changed")
    if any(row["revisionName"] == expected_revision and int(row["percent"]) > 0 for row in traffic):
        raise AdmissionError("staged revision is serving traffic")
    revision = _load_object(
        run(
            [
                "gcloud",
                "run",
                "revisions",
                "describe",
                expected_revision,
                f"--project={project}",
                f"--region={region}",
                "--format=json",
            ]
        )
    )
    try:
        observed = observe_ready_revision(revision)
    except ReconcileError:
        raise AdmissionError("staged revision was not observed") from None
    if (
        observed["revision"] != expected_revision
        or observed["commit"] != expected_commit
        or observed["image"] != expected_image
    ):
        raise AdmissionError("staged revision does not match the approved image")
    history = plan.get("history_values") or {}
    if not isinstance(history, Mapping):
        raise AdmissionError("staged readback is incomplete")
    configuration = _configuration(revision)
    skip = set(history)
    if _config_digest(configuration, skip=skip) != plan.get("template_digest"):
        raise AdmissionError("staged revision configuration changed")
    literals = _literal_values(configuration)
    for key, value in history.items():
        if literals.get(str(key)) != value:
            raise AdmissionError("staged history settings do not match")


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


def _write_plan(path: str, plan: Mapping[str, Any]) -> None:
    Path(path).write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--image-only-staging", action="store_true")
    parser.add_argument("--plan-out", default="")
    parser.add_argument("--image-only-readback", action="store_true")
    parser.add_argument("--plan", default="")
    parser.add_argument("--expected-image", default="")
    parser.add_argument("--expected-commit", default="")
    parser.add_argument("--expected-revision", default="")
    args = parser.parse_args(argv)
    if args.image_only_staging and args.image_only_readback:
        print("Image-only staging admission failed.", file=sys.stderr)
        return 1
    if args.image_only_staging:
        if not args.plan_out:
            print("Image-only staging admission failed.", file=sys.stderr)
            return 1
        try:
            service_json = _load_object(
                _run_quiet(
                    [
                        "gcloud",
                        "run",
                        "services",
                        "describe",
                        args.service,
                        f"--project={args.project}",
                        f"--region={args.region}",
                        "--format=json",
                    ]
                )
            )
            plan = prepare_image_only_staging(
                service=args.service,
                project=args.project,
                region=args.region,
                service_json=service_json,
                env=os.environ,
                run=_run_quiet,
            )
            _write_plan(args.plan_out, plan)
        except (AdmissionError, OSError):
            print("Image-only staging admission failed.", file=sys.stderr)
            return 1
        return 0
    if args.image_only_readback:
        try:
            plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            if not isinstance(plan, Mapping):
                raise AdmissionError("staged readback is incomplete")
            confirm_image_only_readback(
                service=args.service,
                project=args.project,
                region=args.region,
                plan=plan,
                expected_image=args.expected_image,
                expected_commit=args.expected_commit,
                expected_revision=args.expected_revision,
                run=_run_quiet,
            )
        except (AdmissionError, OSError, json.JSONDecodeError):
            print("Image-only staging readback failed.", file=sys.stderr)
            return 1
        return 0
    try:
        result = verify_service(
            service=args.service,
            service_json=_describe_service(service=args.service, project=args.project, region=args.region),
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
