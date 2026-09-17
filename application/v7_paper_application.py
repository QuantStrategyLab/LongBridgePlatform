"""Fail-closed binding for the paused V7 LongBridge paper application.

This module is deliberately narrower than the normal strategy registry.  The
V7 profile is still research-only for ordinary runtime loading; this binding
is the one explicit, single-account exception used while a candidate is
deployed disabled for process-level verification.
"""

from __future__ import annotations

import json
import importlib.metadata
import re
import uuid
from collections.abc import Mapping
from typing import Any

from quant_platform_kit.common.runtime_target import build_runtime_target
from quant_platform_kit.common.strategy_release import build_runtime_loaded_receipt
from quant_platform_kit.common.strategy_release import build_strategy_release_identity
from us_equity_strategies.v7_soxl_profile import (
    SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
    V7_CONFIG_SHA256,
    V7_UES_REVISION,
)


V7_PAPER_PROFILE = SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE
V7_PAPER_APPLICATION_ENV = "LONGBRIDGE_V7_PAPER_APPLICATION_JSON"
V7_PAPER_SERVICE = "longbridge-quant-paper-service"
V7_PAPER_SCOPE = "PAPER"
V7_PAPER_PLATFORM = "longbridge"
# The research contract's frozen source is 07b164..., while the package that
# is actually approved for the disabled account process tracks the platform UES pin.
# Keep research source_commit separate from approved_ues_revision.
V7_APPROVED_UES_REVISION = "e2258223310913f6db9f40b810756db0ee2cfd68"

_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
_TICKET_PATTERN = re.compile(r"^rpt_[0-9a-fA-F]{64}$")
_FORBIDDEN_ACCOUNT_IDS = frozenset({"PAPER", "LIVE", "HK", "SG", "US", "DEFAULT"})
_REQUIRED_RECORD_FIELDS = frozenset(
    {
        "application_id",
        "ticket_id",
        "status",
        "platform_id",
        "account_key",
        "account_scope",
        "account_selector",
        "service_name",
        "strategy_profile",
        "candidate_id",
        "config_sha256",
        "source_commit",
        "approved_ues_revision",
        "expected_revision",
        "expected_strategy_profile",
        "desired_state",
        "claim",
    }
)


class V7PaperApplicationError(ValueError):
    """The trusted application record or protected target is not admissible."""


def _application_contract(
    *,
    label: str,
    platform_id: str,
    account_scope: str,
    service_name: str,
    strategy_profile: str,
    candidate_id: str,
    config_sha256: str,
    approved_ues_revision: str,
) -> dict[str, str]:
    """Describe a reviewed application binding without selecting an adapter."""

    return {
        "label": label,
        "platform_id": platform_id,
        "account_scope": account_scope,
        "service_name": service_name,
        "strategy_profile": strategy_profile,
        "candidate_id": candidate_id,
        "config_sha256": config_sha256,
        "approved_ues_revision": approved_ues_revision,
    }


def _v7_application_contract() -> dict[str, str]:
    return _application_contract(
        label="V7",
        platform_id=V7_PAPER_PLATFORM,
        account_scope=V7_PAPER_SCOPE,
        service_name=V7_PAPER_SERVICE,
        strategy_profile=V7_PAPER_PROFILE,
        candidate_id=V7_PAPER_PROFILE,
        config_sha256=V7_CONFIG_SHA256,
        approved_ues_revision=V7_APPROVED_UES_REVISION,
    )


def _text(value: object, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise V7PaperApplicationError(f"{field} is required")
    return normalized


def _uuid_text(value: object, *, field: str) -> str:
    normalized = _text(value, field=field)
    try:
        return str(uuid.UUID(normalized))
    except (ValueError, AttributeError) as exc:
        raise V7PaperApplicationError(f"{field} must be a UUID") from exc


def _ticket_text(value: object) -> str:
    normalized = _text(value, field="ticket_id")
    if not _TICKET_PATTERN.fullmatch(normalized):
        raise V7PaperApplicationError("ticket_id must be an rpt_ ticket id")
    return normalized.lower()


def _revision_number(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise V7PaperApplicationError(f"{field} must be an integer revision")
    try:
        revision = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise V7PaperApplicationError(f"{field} must be an integer revision") from exc
    if revision < 0:
        raise V7PaperApplicationError(f"{field} must be an integer revision")
    return revision


def _parse_bool(value: object, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise V7PaperApplicationError(f"{field} must be boolean")


def _parse_json_object(raw: object, *, field: str) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(str(raw or ""))
        except json.JSONDecodeError as exc:
            raise V7PaperApplicationError(f"{field} must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise V7PaperApplicationError(f"{field} must be an object")
    return payload


def _validate_application_record(
    record: Mapping[str, Any],
    *,
    contract: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    contract = contract or _v7_application_contract()
    payload = dict(record)
    # QRT keeps the claim fields flat on the wire.  Normalize them only in
    # memory so the token can never be copied into the process binding.
    if "claim" not in payload and {
        "claim_token",
        "workflow_run_id",
        "workflow_run_attempt",
    }.issubset(payload):
        payload["claim"] = {
            "token": payload.pop("claim_token"),
            "workflow_run_id": payload["workflow_run_id"],
            "workflow_run_attempt": payload["workflow_run_attempt"],
        }
    missing = sorted(_REQUIRED_RECORD_FIELDS - payload.keys())
    if missing:
        raise V7PaperApplicationError(f"application record missing fields: {', '.join(missing)}")
    application_id = _uuid_text(payload["application_id"], field="application_id")
    payload["ticket_id"] = _ticket_text(payload["ticket_id"])
    if str(payload["status"]).strip().lower() not in {"approved", "claimed"}:
        raise V7PaperApplicationError("application status is not approved or claimed")
    if str(payload["platform_id"]).strip().lower() != contract["platform_id"]:
        raise V7PaperApplicationError(f"application platform does not match {contract['label']}")
    if str(payload["account_scope"]).strip().upper() != contract["account_scope"]:
        raise V7PaperApplicationError(f"application account_scope does not match {contract['label']}")
    if str(payload["account_selector"]).strip().upper() != contract["account_scope"]:
        raise V7PaperApplicationError(f"application account_selector does not match {contract['label']}")
    if str(payload["service_name"]).strip() != contract["service_name"]:
        raise V7PaperApplicationError(f"application service does not match {contract['label']}")
    if str(payload["strategy_profile"]).strip() != contract["strategy_profile"]:
        raise V7PaperApplicationError(f"application strategy profile does not match {contract['label']}")
    if str(payload["candidate_id"]).strip() != contract["candidate_id"]:
        raise V7PaperApplicationError(f"application candidate identity does not match {contract['label']}")
    if str(payload["config_sha256"]).strip().lower() != contract["config_sha256"]:
        raise V7PaperApplicationError(f"application config identity does not match {contract['label']}")
    source_commit = _text(payload["source_commit"], field="source_commit")
    if not _COMMIT_PATTERN.fullmatch(source_commit):
        raise V7PaperApplicationError("source_commit must be a full commit SHA")
    if str(payload["approved_ues_revision"]).strip() != contract["approved_ues_revision"]:
        raise V7PaperApplicationError("approved UES revision does not match the controlled source")
    _text(payload["account_key"], field="account_key")
    payload["expected_revision"] = _revision_number(payload["expected_revision"], field="expected_revision")
    _text(payload["expected_strategy_profile"], field="expected_strategy_profile")
    if str(payload["desired_state"]).strip().lower() != "paused":
        raise V7PaperApplicationError("application desired_state must be paused")
    claim = payload["claim"]
    if not isinstance(claim, Mapping):
        raise V7PaperApplicationError("application claim is required")
    _text(claim.get("token"), field="claim.token")
    _text(claim.get("workflow_run_id"), field="claim.workflow_run_id")
    _text(claim.get("workflow_run_attempt"), field="claim.workflow_run_attempt")
    payload["application_id"] = application_id
    payload["source_commit"] = source_commit.lower()
    return payload


def _protected_target(
    protected_env: Mapping[str, str],
    *,
    contract: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    contract = contract or _v7_application_contract()
    service = _text(protected_env.get("CLOUD_RUN_SERVICE"), field="CLOUD_RUN_SERVICE")
    if service != contract["service_name"]:
        raise V7PaperApplicationError(f"protected Cloud Run service does not match {contract['label']}")
    _text(protected_env.get("CLOUD_RUN_REGION"), field="CLOUD_RUN_REGION")
    raw_target = protected_env.get("RUNTIME_TARGET_JSON") or protected_env.get("QSL_RUNTIME_TARGET_JSON")
    target = _parse_json_object(raw_target, field="RUNTIME_TARGET_JSON")
    if str(target.get("platform_id") or "").strip().lower() != contract["platform_id"]:
        raise V7PaperApplicationError("protected runtime target platform does not match")
    if str(target.get("service_name") or "").strip() != contract["service_name"]:
        raise V7PaperApplicationError("protected runtime target service does not match")
    if str(target.get("account_scope") or "").strip().upper() != contract["account_scope"]:
        raise V7PaperApplicationError("protected runtime target account_scope does not match")
    selectors = target.get("account_selector")
    if isinstance(selectors, str):
        selectors = [selectors]
    if not isinstance(selectors, (list, tuple)) or tuple(str(item).strip().upper() for item in selectors) != (contract["account_scope"],):
        raise V7PaperApplicationError("protected runtime target account_selector does not match")
    return target


def _physical_account_id(protected_env: Mapping[str, str]) -> str:
    account_id = _text(
        protected_env.get("LONGBRIDGE_PHYSICAL_ACCOUNT_ID"),
        field="LONGBRIDGE_PHYSICAL_ACCOUNT_ID",
    )
    if account_id.upper() in _FORBIDDEN_ACCOUNT_IDS:
        raise V7PaperApplicationError("physical account id cannot be a scope label")
    return account_id


def build_v7_runtime_binding(
    record: Mapping[str, Any],
    *,
    protected_env: Mapping[str, str],
    current_service: Mapping[str, Any] | None = None,
    execution_materials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine QRT's bounded approval with protected PAPER deployment facts.

    Secret values never enter this object.  Only secret names and the explicit
    physical-account binding are retained for the paused process readback.
    """

    contract = _v7_application_contract()
    application = _validate_application_record(record, contract=contract)
    if execution_materials is not None:
        _validate_v7_execution_materials(execution_materials)
    current_target = _protected_target(protected_env, contract=contract)
    if str(current_target.get("strategy_profile") or "").strip() != str(
        application["expected_strategy_profile"]
    ).strip():
        raise V7PaperApplicationError("protected target expected strategy does not match")
    if current_service is not None and "application_revision" in current_service:
        if _revision_number(current_service["application_revision"], field="application_revision") != application["expected_revision"]:
            raise V7PaperApplicationError("protected application revision is stale")
    if "LONGBRIDGE_DRY_RUN_ONLY" in protected_env and _parse_bool(
        protected_env["LONGBRIDGE_DRY_RUN_ONLY"], field="LONGBRIDGE_DRY_RUN_ONLY"
    ):
        raise V7PaperApplicationError("dry_run_only=true cannot represent a broker PAPER target")
    credential_refs = {
        field: _text(protected_env.get(field), field=field)
        for field in (
            "LONGPORT_SECRET_NAME",
            "LONGPORT_APP_KEY_SECRET_NAME",
            "LONGPORT_APP_SECRET_SECRET_NAME",
        )
    }
    physical_account_id = _physical_account_id(protected_env)
    # Start from the protected target so risk, cash, release, account identity,
    # scheduling and decision-data fields cannot disappear at this boundary.
    target = dict(current_target)
    previous_strategy_release = target.pop("strategy_release", None)
    target.pop("live_continuity", None)
    target.update(
        {
            "platform_id": V7_PAPER_PLATFORM,
            "strategy_profile": V7_PAPER_PROFILE,
            "dry_run_only": False,
            "execution_mode": "live",
            "execution_environment": "paper",
            "deployment_selector": V7_PAPER_SCOPE,
            "account_selector": [V7_PAPER_SCOPE],
            "account_scope": V7_PAPER_SCOPE,
            "service_name": V7_PAPER_SERVICE,
        }
    )
    # Validate the copied target with the shared parser before serializing it.
    build_runtime_target(
        platform_id=str(target["platform_id"]),
        strategy_profile=str(target["strategy_profile"]),
        dry_run_only=False,
        deployment_selector=target.get("deployment_selector"),
        account_selector=target.get("account_selector"),
        account_scope=target.get("account_scope"),
        service_name=target.get("service_name"),
        market=target.get("market"),
        market_calendar=target.get("market_calendar"),
        market_timezone=target.get("market_timezone"),
        scheduler=target.get("scheduler"),
        execution_windows=target.get("execution_windows"),
        strategy_release=target.get("strategy_release"),
        account_identity=target.get("account_identity"),
        execution_environment=target.get("execution_environment"),
        live_continuity=target.get("live_continuity"),
        decision_data=target.get("decision_data"),
        continuity_fingerprint_payload=target,
    )
    return {
        "application_id": application["application_id"],
        "ticket_id": application["ticket_id"],
        "account_key": application["account_key"],
        "account_scope": V7_PAPER_SCOPE,
        "account_selector": V7_PAPER_SCOPE,
        "service_name": V7_PAPER_SERVICE,
        "cloud_run_region": str(protected_env["CLOUD_RUN_REGION"]).strip(),
        "strategy_profile": V7_PAPER_PROFILE,
        "candidate_id": V7_PAPER_PROFILE,
        "config_sha256": V7_CONFIG_SHA256,
        "source_commit": application["source_commit"],
        "approved_ues_revision": V7_APPROVED_UES_REVISION,
        "expected_revision": application["expected_revision"],
        "expected_strategy_profile": application["expected_strategy_profile"],
        "desired_state": "paused",
        "runtime_target_enabled": False,
        "runtime_target": target,
        "previous_strategy_release": previous_strategy_release,
        "account_target": {
            "platform_id": V7_PAPER_PLATFORM,
            "account_scope": V7_PAPER_SCOPE,
            "account_selector": V7_PAPER_SCOPE,
            "service_name": V7_PAPER_SERVICE,
            "physical_account_id": physical_account_id,
            "credential_refs": credential_refs,
        },
        "claim": {
            "workflow_run_id": str(application["claim"]["workflow_run_id"]),
            "workflow_run_attempt": str(application["claim"]["workflow_run_attempt"]),
        },
        **(
            {"execution_materials": _json_safe_v7_execution_materials(execution_materials)}
            if execution_materials is not None
            else {}
        ),
    }


def _json_safe_v7_execution_materials(materials: Mapping[str, Any]) -> dict[str, Any]:
    candidate = materials["candidate_risk_identity"]
    candidate_fields = (
        "strategy_profile",
        "account_mode",
        "strategy_revision",
        "runner_revision",
        "config_sha256",
        "input_manifest_sha256",
        "authority_receipt_sha256",
    )
    if isinstance(candidate, Mapping):
        candidate_payload = {field: candidate.get(field) for field in candidate_fields}
        candidate_payload["candidate_sha256"] = candidate.get("candidate_sha256")
    else:
        candidate_payload = {
            field: getattr(candidate, field, None) for field in (*candidate_fields, "candidate_sha256")
        }
    capital_base = materials.get("capital_base")
    if isinstance(capital_base, Mapping):
        capital_payload = dict(capital_base)
    else:
        capital_payload = {
            field: getattr(capital_base, field, None)
            for field in (
                "reported_equity",
                "reported_currency",
                "target_currency",
                "fx_rate_to_target",
                "as_of",
                "account_scope",
                "runtime_scope",
                "strategy_scope",
                "source_digest_sha256",
                "capital_scope",
                "valuation_basis",
                "allocation_scope",
                "component_coverage_digest_sha256",
                "fx_source_digest_sha256",
            )
        }
    capital_binding = materials.get("capital_base_binding")
    if isinstance(capital_binding, Mapping):
        binding_payload = dict(capital_binding)
    else:
        binding_payload = {
            field: getattr(capital_binding, field, None)
            for field in (
                "account_scope",
                "runtime_scope",
                "strategy_scope",
                "target_currency",
                "capital_scope",
                "valuation_basis",
                "allocation_scope",
                "max_age_seconds",
            )
        }
    for payload in (capital_payload, binding_payload):
        for key, value in tuple(payload.items()):
            if hasattr(value, "value"):
                payload[key] = value.value
            elif hasattr(value, "isoformat"):
                payload[key] = value.isoformat().replace("+00:00", "Z")
    return {
        "candidate_risk_identity": candidate_payload,
        "mandate_provenance": dict(materials["mandate_provenance"]),
        "capital_base": capital_payload,
        "capital_base_binding": binding_payload,
        "strategy_release": build_strategy_release_identity(materials["strategy_release"]).to_dict(),
        **(
            {"market_data": dict(materials["market_data"])}
            if isinstance(materials.get("market_data"), Mapping)
            else {}
        ),
    }


def _validate_v7_execution_materials(materials: Mapping[str, Any]) -> None:
    """Validate explicit PAPER execution evidence without issuing authority."""
    candidate = materials.get("candidate_risk_identity")
    mandate = materials.get("mandate_provenance")
    release_value = materials.get("strategy_release")
    if candidate is None or not isinstance(mandate, Mapping) or release_value is None:
        raise V7PaperApplicationError("V7 execution materials are incomplete")
    try:
        release = build_strategy_release_identity(release_value)
    except (TypeError, ValueError) as exc:
        raise V7PaperApplicationError("V7 execution release is invalid") from exc
    def candidate_value(name):
        return candidate.get(name) if isinstance(candidate, Mapping) else getattr(candidate, name, None)

    if (
        candidate_value("strategy_profile") != V7_PAPER_PROFILE
        or candidate_value("config_sha256") != V7_CONFIG_SHA256
        or candidate_value("strategy_revision") != release.strategy_revision
        or candidate_value("config_sha256") != release.config_sha256
        or mandate.get("authority_scope") != V7_PAPER_SCOPE
        or mandate.get("candidate_identity_sha256") != candidate_value("candidate_sha256")
    ):
        raise V7PaperApplicationError("V7 execution materials do not match PAPER candidate")


def _validate_bound_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(binding)
    payload["application_id"] = _uuid_text(payload.get("application_id"), field="application_id")
    payload["ticket_id"] = _ticket_text(payload.get("ticket_id"))
    source_commit = _text(payload.get("source_commit"), field="source_commit").lower()
    if not _COMMIT_PATTERN.fullmatch(source_commit):
        raise V7PaperApplicationError("bound source commit is invalid")
    payload["source_commit"] = source_commit
    payload["expected_revision"] = _revision_number(
        payload.get("expected_revision"), field="expected_revision"
    )
    if str(payload.get("desired_state") or "").strip().lower() != "paused":
        raise V7PaperApplicationError("bound V7 application must remain paused")
    if not _text(payload.get("expected_strategy_profile"), field="expected_strategy_profile"):
        raise V7PaperApplicationError("bound expected strategy is required")
    if str(payload.get("strategy_profile") or "").strip() != V7_PAPER_PROFILE:
        raise V7PaperApplicationError("bound V7 strategy profile is invalid")
    if str(payload.get("candidate_id") or "").strip() != V7_PAPER_PROFILE:
        raise V7PaperApplicationError("bound V7 candidate identity is invalid")
    if str(payload.get("config_sha256") or "").strip().lower() != V7_CONFIG_SHA256:
        raise V7PaperApplicationError("bound V7 config identity is invalid")
    if str(payload.get("approved_ues_revision") or "").strip() != V7_APPROVED_UES_REVISION:
        raise V7PaperApplicationError("bound V7 UES revision is invalid")
    if payload.get("runtime_target_enabled") is not False:
        raise V7PaperApplicationError("bound V7 application must remain paused")
    target = _parse_json_object(payload.get("runtime_target"), field="runtime_target")
    if target.get("platform_id") != V7_PAPER_PLATFORM:
        raise V7PaperApplicationError("bound runtime target platform is invalid")
    if target.get("strategy_profile") != V7_PAPER_PROFILE:
        raise V7PaperApplicationError("bound runtime target strategy is invalid")
    if target.get("account_scope") != V7_PAPER_SCOPE or target.get("service_name") != V7_PAPER_SERVICE:
        raise V7PaperApplicationError("bound runtime target account is invalid")
    if target.get("dry_run_only") is not False or target.get("execution_environment") != "paper":
        raise V7PaperApplicationError("bound runtime target must be broker PAPER and paused")
    if target.get("strategy_release") is not None:
        raise V7PaperApplicationError("bound V7 target cannot reuse the previous strategy release")
    account_target = payload.get("account_target")
    if not isinstance(account_target, Mapping):
        raise V7PaperApplicationError("bound account target is required")
    if (
        account_target.get("platform_id") != V7_PAPER_PLATFORM
        or account_target.get("account_scope") != V7_PAPER_SCOPE
        or account_target.get("account_selector") != V7_PAPER_SCOPE
        or account_target.get("service_name") != V7_PAPER_SERVICE
    ):
        raise V7PaperApplicationError("bound account target is invalid")
    _physical_account_id({"LONGBRIDGE_PHYSICAL_ACCOUNT_ID": account_target.get("physical_account_id")})
    claim = payload.get("claim")
    if not isinstance(claim, Mapping):
        raise V7PaperApplicationError("bound claim is required")
    _text(claim.get("workflow_run_id"), field="claim.workflow_run_id")
    _text(claim.get("workflow_run_attempt"), field="claim.workflow_run_attempt")
    if payload.get("execution_materials") is not None:
        materials = payload.get("execution_materials")
        if not isinstance(materials, Mapping):
            raise V7PaperApplicationError("bound V7 execution materials are invalid")
        _validate_v7_execution_materials(materials)
    return payload


def load_v7_paper_application_binding(
    env: Mapping[str, str | None],
) -> dict[str, Any] | None:
    """Load the exact paused binding from the process environment, if present."""

    raw = str(env.get(V7_PAPER_APPLICATION_ENV) or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise V7PaperApplicationError("V7 application binding is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise V7PaperApplicationError("V7 application binding must be an object")
    return _validate_bound_binding(payload)


def _installed_revision(distribution_name: str) -> str | None:
    """Read a VCS revision from the installed wheel metadata when available."""

    try:
        distribution = importlib.metadata.distribution(distribution_name)
        raw = distribution.read_text("direct_url.json")
        if not raw:
            return None
        payload = json.loads(raw)
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    vcs_info = payload.get("vcs_info")
    if not isinstance(vcs_info, Mapping):
        return None
    revision = str(vcs_info.get("commit_id") or "").strip()
    return revision or None


def build_v7_loaded_version_readback(
    binding: Mapping[str, Any],
    *,
    runtime_settings: Any,
    strategy_runtime: Any,
    runtime_env: Mapping[str, str | None],
) -> dict[str, Any]:
    """Return process-observed V7 identity for the disabled candidate revision."""

    bound = _validate_bound_binding(binding)
    target = getattr(runtime_settings, "runtime_target", None)
    if target is None:
        raise V7PaperApplicationError("runtime target is not loaded")
    if getattr(runtime_settings, "runtime_target_enabled", True):
        raise V7PaperApplicationError("runtime target must remain disabled")
    if getattr(runtime_settings, "dry_run_only", True):
        raise V7PaperApplicationError("runtime target must be broker PAPER, not dry-run")
    execution_environment = getattr(target, "execution_environment", None)
    if getattr(execution_environment, "value", execution_environment) != "paper":
        raise V7PaperApplicationError("runtime target execution environment is not paper")
    entrypoint = getattr(strategy_runtime, "entrypoint", None)
    adapter = getattr(strategy_runtime, "runtime_adapter", None)
    manifest = getattr(entrypoint, "manifest", None)
    if getattr(manifest, "profile", None) != V7_PAPER_PROFILE:
        raise V7PaperApplicationError("loaded entrypoint profile does not match V7")
    if getattr(adapter, "available_inputs", frozenset()) != frozenset(
        {"derived_indicators", "portfolio_snapshot"}
    ):
        raise V7PaperApplicationError("loaded V7 adapter inputs do not match the named entrypoint")
    source_commit = str(runtime_env.get("V7_PAPER_SOURCE_COMMIT") or "").strip().lower()
    if not _COMMIT_PATTERN.fullmatch(source_commit):
        raise V7PaperApplicationError("loaded source commit is unavailable")
    if source_commit != str(bound["source_commit"]).lower():
        raise V7PaperApplicationError("loaded source commit does not match application")
    ues_revision = _installed_revision("us-equity-strategies")
    qpk_revision = _installed_revision("quant-platform-kit")
    if ues_revision != V7_APPROVED_UES_REVISION:
        raise V7PaperApplicationError("loaded UES revision does not match the controlled source")
    runtime_target = target.to_dict()
    if runtime_target.get("strategy_profile") != V7_PAPER_PROFILE:
        raise V7PaperApplicationError("loaded runtime target strategy does not match V7")
    if runtime_target.get("account_scope") != V7_PAPER_SCOPE:
        raise V7PaperApplicationError("loaded runtime target scope does not match PAPER")
    return {
        "application_id": bound["application_id"],
        "platform_id": V7_PAPER_PLATFORM,
        "account_scope": V7_PAPER_SCOPE,
        "service_name": V7_PAPER_SERVICE,
        "strategy_profile": V7_PAPER_PROFILE,
        "candidate_id": V7_PAPER_PROFILE,
        "config_sha256": V7_CONFIG_SHA256,
        "source_commit": source_commit,
        "ues_revision": ues_revision,
        "qpk_revision": qpk_revision,
        "revision_name": str(runtime_env.get("K_REVISION") or "").strip() or None,
        "runtime_target_enabled": False,
        "runtime_target": {
            "account_selector": list(target.account_selector),
            "execution_environment": target.execution_environment.value,
            "execution_mode": target.execution_mode,
            "dry_run_only": target.dry_run_only,
        },
        "account_target": {
            "account_selector": bound["account_target"]["account_selector"],
            "physical_account_bound": bool(bound["account_target"].get("physical_account_id")),
        },
        "loaded_entrypoint": {
            "profile": manifest.profile,
            "module": getattr(getattr(entrypoint, "_evaluate", None), "__module__", None),
            "signal_effective_after_trading_days": getattr(
                getattr(adapter, "runtime_policy", None),
                "signal_effective_after_trading_days",
                None,
            ),
        },
        "runtime_loaded_receipt": build_runtime_loaded_receipt(
            strategy_release=None,
            runtime_revision=str(runtime_env.get("K_REVISION") or "").strip() or None,
        ),
        "activation_state": "applied_paused",
        "activation_remaining": [
            "v7_qualification",
            "explicit_account_activation",
            "owner_migration",
            "order_submission_enablement",
        ],
    }


__all__ = [
    "V7_CONFIG_SHA256",
    "V7_PAPER_APPLICATION_ENV",
    "V7_PAPER_PROFILE",
    "V7_PAPER_SCOPE",
    "V7_PAPER_SERVICE",
    "V7_UES_REVISION",
    "V7PaperApplicationError",
    "build_v7_runtime_binding",
    "build_v7_loaded_version_readback",
    "load_v7_paper_application_binding",
]
