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

from scripts.reconcile_cloud_runtime import ReconcileError, observe_ready_revision, serving_traffic_rows
from scripts.record_daily_account_snapshot import _PROJECT_ID, _Rejected, _TARGET_ID, _gcs_prefix
from strategy_registry import LONGBRIDGE_PLATFORM, resolve_strategy_definition


class AdmissionError(ValueError):
    """A deployed runtime target is not safe to receive a new image."""


_SHA = re.compile(r"^[0-9a-f]{40}$")
# Reviewed PAPER history image. Workflow control stays on main; only fixed reviewed candidates may be archived.
APPROVED_PAPER_HISTORY_CANDIDATE = "0b939723c1db3ef59175535998b470cbcd4b8824"
# Reviewed PAPER HTTP snapshot image. Distinct from the natural-cycle history archive above.
APPROVED_PAPER_HTTP_SNAPSHOT_CANDIDATE = "d8314a61df697cae1dd03a78ddc5c2fc4179ec67"
# Reviewed PAPER internal-probe snapshot producer. It carries no history or snapshot env update.
APPROVED_PAPER_PROBE_SNAPSHOT_CANDIDATE = "318bf0419ec91002fd0f0dfd1bc80a0664a85e79"
# Reviewed read-only SG/HK account-snapshot producer. It is not a PAPER candidate.
APPROVED_SGHK_ACCOUNT_SNAPSHOT_CANDIDATE = "df3d29d13daeffc7b4af9fec5db0e9ec1f07b60f"
# Fixed 922-based HK candidate carrying only bounded /probe failure diagnostics.
APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE = "779d8e1c39d161144bc34ae8503ce34d87971b16"
# One already-staged PAPER revision that may hold history env while serving still runs an older image.
_PAPER_HTTP_STAGED_SOURCE_REVISION = "longbridge-quant-paper-service-r36423178119"
_PAPER_HTTP_STAGED_SOURCE_COMMIT = APPROVED_PAPER_HISTORY_CANDIDATE
_PAPER_HTTP_STAGED_SOURCE_IMAGE_DIGEST = (
    "sha256:9a3260ca8255873309b1c27cd2fd7403e6006a4e9088241e6f89a4e5e65f7a10"
)
_HISTORY_KEYS = (
    "ACCOUNT_HISTORY_RECORDING_ENABLED",
    "ACCOUNT_HISTORY_GCS_PREFIX",
    "ACCOUNT_HISTORY_TARGET_ID",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE",
)
_ACCOUNT_SNAPSHOT_INPUT = "ACCOUNT_SNAPSHOT_ENABLED_INPUT"
_ACCOUNT_SNAPSHOT_ENV = "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED"
_UNSAFE_HISTORY = re.compile(r"[,=\s'\"`$\\|&<>]|^\-")
_INGRESS = "run.googleapis.com/ingress"
_SOURCE_DECLARATIONS = ("uv.lock", "pyproject.toml", "qsl.toml")


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


def ues_revision_from_declarations(*, lock_text: str, project_text: str, qsl_text: str) -> str:
    """Agree uv.lock, pyproject.toml, and qsl.toml. These texts are data, not code."""

    candidate = _locked_ues_revision(lock_text)
    try:
        project = tomllib.loads(project_text)
        dependencies = project.get("project", {}).get("dependencies", [])
    except tomllib.TOMLDecodeError as exc:
        raise AdmissionError("UES pyproject and uv.lock revisions differ") from exc
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
    try:
        qsl = tomllib.loads(qsl_text)
    except tomllib.TOMLDecodeError as exc:
        raise AdmissionError("UES QSL contract and uv.lock revisions differ") from exc
    if qsl.get("qsl", {}).get("requires", {}).get("us_equity_strategies") != candidate:
        raise AdmissionError("UES QSL contract and uv.lock revisions differ")
    return candidate


def ues_revision_at(commit: str, run: Callable[[Sequence[str]], str]) -> str:
    """Read one commit's lock declarations with git show. Do not import or run its scripts."""

    if not _SHA.fullmatch(commit):
        raise AdmissionError("image source is not an exact commit")
    texts: dict[str, str] = {}
    for name in _SOURCE_DECLARATIONS:
        texts[name] = run(["git", "show", f"{commit}:{name}"])
    return ues_revision_from_declarations(
        lock_text=texts["uv.lock"],
        project_text=texts["pyproject.toml"],
        qsl_text=texts["qsl.toml"],
    )


def _serving_ues_revisions(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    run: Callable[[Sequence[str]], str] | None = None,
) -> set[str]:
    traffic = service_json.get("status", {}).get("traffic", [])
    if not isinstance(traffic, list):
        raise AdmissionError(f"{service}: serving traffic is unavailable")
    serving = [entry.get("revisionName") for entry in traffic if isinstance(entry, Mapping) and entry.get("percent", 0) > 0]
    if not serving or any(not isinstance(revision, str) or not revision for revision in serving):
        raise AdmissionError(f"{service}: serving revisions are unavailable")
    invoke = run or _run
    revisions = set()
    for revision in serving:
        try:
            revision_json = json.loads(invoke([
                "gcloud", "run", "revisions", "describe", revision,
                f"--project={project}", f"--region={region}", "--format=json",
            ]))
            source_sha = revision_json["metadata"]["labels"]["commit-sha"]
            if not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha):
                raise ValueError("invalid source SHA")
            lock_text = invoke(["git", "show", f"{source_sha}:uv.lock"])
            revisions.add(_locked_ues_revision(lock_text))
        except (KeyError, TypeError, ValueError, AdmissionError) as exc:
            raise AdmissionError(f"{service}: serving UES revision cannot be independently verified") from exc
    return revisions


def verify_ues_image_pin(
    *, service: str, project: str, region: str, service_json: Mapping[str, Any], admission: Mapping[str, object]
) -> None:
    """Keep a configured account on its serving UES pin until the target approves a change."""
    if not admission["enabled"]:
        return
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


def _load_object(payload: str) -> Mapping[str, Any]:
    loaded = json.loads(payload)
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
    env: Mapping[str, str], *, workflow_target: str, project_id: str, allow_sghk: bool = False
) -> dict[str, str] | None:
    """Return one exact target history update, or None when the four inputs were omitted."""

    values = {key: str(env.get(key) or "") for key in _HISTORY_KEYS}
    if all(value == "" for value in values.values()):
        return None
    if any(not value or _UNSAFE_HISTORY.search(value) for value in values.values()):
        raise AdmissionError("history settings are invalid")
    if values["ACCOUNT_HISTORY_RECORDING_ENABLED"] != "true":
        raise AdmissionError("history settings are invalid")
    target_pairs = {"PAPER": ("paper", "PAPER")}
    if allow_sghk:
        target_pairs.update({"HK": ("hk", "HK"), "SG": ("sg", "SG")})
    expected = target_pairs.get(workflow_target)
    if expected is None:
        raise AdmissionError("history settings are only admitted for approved targets")
    if (values["ACCOUNT_HISTORY_TARGET_ID"], values["ACCOUNT_HISTORY_EXPECTED_SCOPE"]) != expected:
        raise AdmissionError("history settings do not match the selected target")
    if _PROJECT_ID.fullmatch(project_id) is None or _TARGET_ID.fullmatch(values["ACCOUNT_HISTORY_TARGET_ID"]) is None:
        raise AdmissionError("history settings are invalid")
    try:
        prefix, _, _ = _gcs_prefix(values["ACCOUNT_HISTORY_GCS_PREFIX"])
    except _Rejected:
        raise AdmissionError("history settings are invalid") from None
    if prefix != values["ACCOUNT_HISTORY_GCS_PREFIX"]:
        raise AdmissionError("history settings are invalid")
    if allow_sghk and prefix != "gs://qsl-runtime-logs-shared/longbridge/account_snapshots":
        raise AdmissionError("history settings are not admitted for this candidate")
    return values


def _require_sghk_candidate_identity(
    *, target_label: str, service: str, project: str, region: str, service_json: Mapping[str, Any]
) -> None:
    expected = {"HK": ("hk", "HK"), "SG": ("sg", "SG")}.get(target_label)
    if expected is None or project != "longbridgequant":
        raise AdmissionError("candidate target does not match the approved account")
    target_id, scope = expected
    try:
        from application.runtime_target_manifest import load_runtime_target_manifest

        matches = [item for item in load_runtime_target_manifest().targets if item.id == target_id]
    except Exception:
        raise AdmissionError("candidate target does not match the approved account") from None
    if (
        len(matches) != 1
        or matches[0].mode != "live"
        or matches[0].account_scope != scope
        or matches[0].service != service
        or matches[0].region != region
    ):
        raise AdmissionError("candidate target does not match the approved account")
    env = _container_env(service_json)
    try:
        runtime_target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    except json.JSONDecodeError:
        raise AdmissionError("runtime target identity does not match the approved account") from None
    selector = runtime_target.get("account_selector") if isinstance(runtime_target, Mapping) else None
    selectors = (selector,) if isinstance(selector, str) else tuple(selector) if isinstance(selector, list) else None
    deployment_selector = str(runtime_target.get("deployment_selector") or "") if isinstance(runtime_target, Mapping) else ""
    if (
        not isinstance(runtime_target, Mapping)
        or runtime_target.get("platform_id") != "longbridge"
        or runtime_target.get("service_name") != service
        or runtime_target.get("account_scope") != scope
        or selectors != (scope,)
        or deployment_selector != ("HK" if target_id == "hk" else "SG")
    ):
        raise AdmissionError("runtime target identity does not match the approved account")


def _history_arg(values: Mapping[str, str] | None) -> str:
    if not values:
        return ""
    return ",".join(f"{key}={values[key]}" for key in _HISTORY_KEYS)


def _paper_selector(value: object) -> tuple[str, ...] | None:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)) or not value:
        return None
    return tuple(str(item).strip().upper() for item in value)


def _require_paper_target_identity(*, service: str, service_json: Mapping[str, Any]) -> None:
    env = _container_env(service_json)
    try:
        target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    except json.JSONDecodeError as exc:
        raise AdmissionError(f"{service}: runtime target identity does not match PAPER") from exc
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


def verify_template_matches_serving(
    *, service: str, service_json: Mapping[str, Any], serving_revisions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    template = service_json.get("spec", {}).get("template")
    if not isinstance(template, Mapping) or not serving_revisions:
        raise AdmissionError(f"{service}: template does not match the serving revision")
    template_configuration = _configuration(service_json, template=template)
    for revision in serving_revisions:
        if _configuration(revision) != template_configuration:
            raise AdmissionError(f"{service}: template does not match the serving revision")
    return template_configuration


def _template_container_image(service_json: Mapping[str, Any]) -> str:
    template = service_json.get("spec", {}).get("template")
    if not isinstance(template, Mapping):
        raise AdmissionError("container environment is malformed")
    containers = (template.get("spec") or {}).get("containers") if isinstance(template.get("spec"), Mapping) else None
    if not isinstance(containers, list) or not containers or not isinstance(containers[0], Mapping):
        raise AdmissionError("container environment is malformed")
    image = str(containers[0].get("image") or "").strip()
    if not image:
        raise AdmissionError("container environment is malformed")
    return image


def _revision_container_image(revision: Mapping[str, Any]) -> str:
    containers = (revision.get("spec") or {}).get("containers") if isinstance(revision.get("spec"), Mapping) else None
    if not isinstance(containers, list) or not containers or not isinstance(containers[0], Mapping):
        raise AdmissionError("staged source revision is incomplete")
    image = str(containers[0].get("image") or "").strip()
    if not image:
        raise AdmissionError("staged source revision is incomplete")
    return image


def _image_digest(image: str) -> str:
    _, separator, digest = image.partition("@")
    if separator != "@" or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise AdmissionError("staged source revision image digest is invalid")
    return digest


def _retained_history_values(
    configuration: Mapping[str, Any], *, project_id: str
) -> dict[str, str]:
    """Keep the exact literal PAPER history quartet already on the template."""

    literals = _literal_values(configuration)
    values = {key: str(literals.get(key) or "") for key in _HISTORY_KEYS}
    if any(not values[key] for key in _HISTORY_KEYS):
        raise AdmissionError("retained history settings are incomplete")
    try:
        retained = history_update(values, workflow_target="PAPER", project_id=project_id)
    except AdmissionError as exc:
        raise AdmissionError("retained history settings are invalid") from exc
    if retained is None:
        raise AdmissionError("retained history settings are incomplete")
    return retained


def _optional_retained_history_values(
    configuration: Mapping[str, Any], *, project_id: str
) -> dict[str, str]:
    present = {item["name"] for item in configuration["env"]}
    if not present.intersection(_HISTORY_KEYS):
        return {}
    return _retained_history_values(configuration, project_id=project_id)


def _admit_http_snapshot_template_via_staged_source(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    serving_revisions: Sequence[Mapping[str, Any]],
    run: Callable[[Sequence[str]], str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Allow one known staged history revision to differ from serving only on history keys."""

    if not serving_revisions:
        raise AdmissionError(f"{service}: serving revisions are unavailable")
    staged = _load_object(
        run(
            [
                "gcloud",
                "run",
                "revisions",
                "describe",
                _PAPER_HTTP_STAGED_SOURCE_REVISION,
                f"--project={project}",
                f"--region={region}",
                "--format=json",
            ]
        )
    )
    try:
        observed = observe_ready_revision(staged)
    except ReconcileError as exc:
        raise AdmissionError("staged source revision was not observed") from exc
    staged_image = _revision_container_image(staged)
    if (
        observed["revision"] != _PAPER_HTTP_STAGED_SOURCE_REVISION
        or observed["commit"] != _PAPER_HTTP_STAGED_SOURCE_COMMIT
        or _image_digest(staged_image) != _PAPER_HTTP_STAGED_SOURCE_IMAGE_DIGEST
        or observed["image"] != staged_image
    ):
        raise AdmissionError("staged source revision does not match the approved source")
    status = service_json.get("status")
    if not isinstance(status, Mapping):
        raise AdmissionError(f"{service}: template does not match the staged source revision")
    template = service_json.get("spec", {}).get("template")
    template_metadata = template.get("metadata") if isinstance(template, Mapping) else None
    if (
        not isinstance(template_metadata, Mapping)
        or str(template_metadata.get("name") or "").strip() != _PAPER_HTTP_STAGED_SOURCE_REVISION
    ):
        raise AdmissionError(f"{service}: template does not match the staged source revision")
    if str(status.get("latestCreatedRevisionName") or "").strip() != _PAPER_HTTP_STAGED_SOURCE_REVISION:
        raise AdmissionError(f"{service}: template does not match the staged source revision")
    template_configuration = _configuration(service_json)
    staged_configuration = _configuration(staged)
    if template_configuration != staged_configuration:
        raise AdmissionError(f"{service}: template does not match the staged source revision")
    if _template_container_image(service_json) != staged_image:
        raise AdmissionError(f"{service}: template does not match the staged source revision")
    retained = _retained_history_values(template_configuration, project_id=project)
    for revision in serving_revisions:
        serving_configuration = _configuration(revision)
        if _config_digest(template_configuration, skip=set(_HISTORY_KEYS)) != _config_digest(
            serving_configuration, skip=set(_HISTORY_KEYS)
        ):
            raise AdmissionError(f"{service}: template does not match the serving revision")
    return template_configuration, retained


def _resolve_paper_template_configuration(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    serving_revisions: Sequence[Mapping[str, Any]],
    image_commit: str,
    run: Callable[[Sequence[str]], str],
) -> tuple[dict[str, Any], dict[str, str]]:
    if image_commit != APPROVED_PAPER_HTTP_SNAPSHOT_CANDIDATE:
        return (
            verify_template_matches_serving(
                service=service, service_json=service_json, serving_revisions=serving_revisions
            ),
            {},
        )
    try:
        configuration = verify_template_matches_serving(
            service=service, service_json=service_json, serving_revisions=serving_revisions
        )
    except AdmissionError:
        return _admit_http_snapshot_template_via_staged_source(
            service=service,
            project=project,
            region=region,
            service_json=service_json,
            serving_revisions=serving_revisions,
            run=run,
        )
    return configuration, _optional_retained_history_values(configuration, project_id=project)


def _require_candidate_release_binding(
    *, service: str, service_json: Mapping[str, Any], profile: str, ues_revision: str
) -> None:
    """Match strategy_release and the soxl risk pin to the candidate declaration, not the main lock."""

    env = _container_env(service_json)
    try:
        target = json.loads(env.get("RUNTIME_TARGET_JSON") or env.get("QSL_RUNTIME_TARGET_JSON") or "{}")
    except json.JSONDecodeError as exc:
        raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding") from exc
    if not isinstance(target, Mapping):
        raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding")
    release = target.get("strategy_release") or {}
    risk_binding = (target.get("runtime_risk_limits") or {}).get("binding") or {}
    if not isinstance(release, Mapping) or not isinstance(risk_binding, Mapping):
        raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding")
    approved = release.get("strategy_revision")
    risk_pin = risk_binding.get("ues_revision")
    if approved is None and risk_pin is None:
        return
    if approved != ues_revision or (profile == "soxl_soxx_trend_income" and risk_pin != ues_revision):
        raise AdmissionError(f"{service}: candidate UES revision does not match approved target binding")


def prepare_image_only_staging(
    *,
    service: str,
    project: str,
    region: str,
    service_json: Mapping[str, Any],
    env: Mapping[str, str],
    image_commit: str,
    run: Callable[[Sequence[str]], str],
) -> dict[str, Any]:
    """Admit a fixed PAPER candidate or the signed main image for image-only staging."""

    history, snapshot_value = _validate_image_only_source(
        image_commit=image_commit, env=env, project=project
    )
    admission = verify_service(service=service, service_json=service_json)
    if image_commit in {
        APPROVED_SGHK_ACCOUNT_SNAPSHOT_CANDIDATE,
        APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE,
    }:
        _require_sghk_candidate_identity(
            target_label=str(env.get("WORKFLOW_TARGET") or ""),
            service=service,
            project=project,
            region=region,
            service_json=service_json,
        )
        if image_commit == APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE and admission["enabled"]:
            raise AdmissionError(f"{service}: HK diagnostic staging requires the runtime target to remain disabled")
    else:
        _require_paper_target_identity(service=service, service_json=service_json)
    try:
        traffic = serving_traffic_rows(service_json)
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
    if image_commit == APPROVED_PAPER_HTTP_SNAPSHOT_CANDIDATE:
        configuration, retained_history = _resolve_paper_template_configuration(
            service=service,
            project=project,
            region=region,
            service_json=service_json,
            serving_revisions=revisions,
            image_commit=image_commit,
            run=run,
        )
    else:
        configuration = verify_template_matches_serving(
            service=service, service_json=service_json, serving_revisions=revisions
        )
        retained_history = {}
    source_revision = ues_revision_at(image_commit, run)
    _require_candidate_release_binding(
        service=service,
        service_json=service_json,
        profile=str(admission["profile"]),
        ues_revision=source_revision,
    )
    serving = _serving_ues_revisions(
        service=service, project=project, region=region, service_json=service_json, run=run
    )
    if serving != {source_revision}:
        raise AdmissionError(f"{service}: candidate UES revision differs from serving image")
    history_arg = _history_arg(history)
    snapshot_arg = (
        f"{_ACCOUNT_SNAPSHOT_ENV}={snapshot_value}"
        if snapshot_value is not None
        else ""
    )
    changed_keys = set(history or ())
    if snapshot_value is not None:
        changed_keys.add(_ACCOUNT_SNAPSHOT_ENV)
    return {
        "history_arg": history_arg,
        "history_values": history or {},
        "retained_history_values": retained_history,
        "snapshot_arg": snapshot_arg,
        "snapshot_value": snapshot_value,
        "image_commit": image_commit,
        "service_ingress": _ingress(service_json.get("metadata")),
        "serving_traffic": traffic,
        "template_digest": _config_digest(configuration, skip=changed_keys),
    }


def _validate_image_only_source(
    *, image_commit: str, env: Mapping[str, str], project: str
) -> tuple[dict[str, str] | None, str | None]:
    history = history_update(
        env,
        workflow_target=str(env.get("WORKFLOW_TARGET") or ""),
        project_id=project,
        allow_sghk=image_commit == APPROVED_SGHK_ACCOUNT_SNAPSHOT_CANDIDATE,
    )
    snapshot_value = _account_snapshot_update(env, workflow_target=str(env.get("WORKFLOW_TARGET") or ""))
    if image_commit == APPROVED_PAPER_HISTORY_CANDIDATE:
        if history is None or snapshot_value is not None:
            raise AdmissionError("image source is not approved")
    elif image_commit == APPROVED_PAPER_HTTP_SNAPSHOT_CANDIDATE:
        if str(env.get("WORKFLOW_TARGET") or "") != "PAPER" or history is not None:
            raise AdmissionError("image source is not approved")
    elif image_commit == APPROVED_PAPER_PROBE_SNAPSHOT_CANDIDATE:
        if (
            str(env.get("WORKFLOW_TARGET") or "") != "PAPER"
            or history is not None
            or snapshot_value is not None
        ):
            raise AdmissionError("image source is not approved")
    elif image_commit == APPROVED_SGHK_ACCOUNT_SNAPSHOT_CANDIDATE:
        if (
            str(env.get("WORKFLOW_TARGET") or "") not in {"HK", "SG"}
            or history is None
            or snapshot_value is not None
        ):
            raise AdmissionError("image source is not approved")
    elif image_commit == APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE:
        if (
            str(env.get("WORKFLOW_TARGET") or "") != "HK"
            or history is not None
            or snapshot_value is not None
        ):
            raise AdmissionError("image source is not approved")
    elif _is_exact_main_image(image_commit, env):
        if history is not None:
            raise AdmissionError("image source is not approved")
    else:
        raise AdmissionError("image source is not approved")
    return history, snapshot_value


def _account_snapshot_update(
    env: Mapping[str, str], *, workflow_target: str
) -> str | None:
    value = str(env.get(_ACCOUNT_SNAPSHOT_INPUT) or "")
    if value not in {"", "true", "false"}:
        raise AdmissionError("account snapshot setting is invalid")
    if value and workflow_target != "PAPER":
        raise AdmissionError("account snapshot setting is only admitted for PAPER")
    return value or None


def _is_exact_main_image(image_commit: str, env: Mapping[str, str]) -> bool:
    workflow_sha = str(env.get("GITHUB_SHA") or "")
    return (
        _SHA.fullmatch(image_commit) is not None
        and image_commit == workflow_sha
        and image_commit == str(env.get("GITHUB_WORKFLOW_SHA") or "")
        and str(env.get("GITHUB_REPOSITORY") or "") == "QuantStrategyLab/LongBridgePlatform"
        and str(env.get("APPROVED_REF") or "") == "main"
        and str(env.get("GITHUB_REF_NAME") or "") == "main"
        and str(env.get("GITHUB_REF") or "") == "refs/heads/main"
        and str(env.get("GITHUB_WORKFLOW_REF") or "")
        == "QuantStrategyLab/LongBridgePlatform/.github/workflows/sync-cloud-run-env.yml@refs/heads/main"
    )


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
        traffic = serving_traffic_rows(service_json)
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
    retained_history = plan.get("retained_history_values") or {}
    if not isinstance(history, Mapping) or not isinstance(retained_history, Mapping):
        raise AdmissionError("staged readback is incomplete")
    snapshot_value = plan.get("snapshot_value")
    if snapshot_value not in {None, "true", "false"}:
        raise AdmissionError("staged readback is incomplete")
    skipped_keys = set(history)
    if snapshot_value is not None:
        skipped_keys.add(_ACCOUNT_SNAPSHOT_ENV)
    configuration = _configuration(revision)
    if _config_digest(configuration, skip=skipped_keys) != plan.get("template_digest"):
        raise AdmissionError("staged revision configuration changed")
    literals = _literal_values(configuration)
    for key, value in history.items():
        if literals.get(str(key)) != value:
            raise AdmissionError("staged history settings do not match")
    for key, value in retained_history.items():
        if literals.get(str(key)) != value:
            raise AdmissionError("retained history settings do not match")
    if snapshot_value is not None and literals.get(_ACCOUNT_SNAPSHOT_ENV) != snapshot_value:
        raise AdmissionError("staged account snapshot setting does not match")


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
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.image_only_staging and args.image_only_readback:
        print("Image-only staging admission failed.", file=sys.stderr)
        return 1
    if args.image_only_staging:
        if not args.plan_out:
            print("Image-only staging admission failed.", file=sys.stderr)
            return 1
        try:
            image_commit = str(os.environ.get("SOURCE_COMMIT") or "")
            _validate_image_only_source(
                image_commit=image_commit, env=os.environ, project=args.project
            )
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
                image_commit=image_commit,
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
