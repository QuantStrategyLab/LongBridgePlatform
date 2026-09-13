from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from application.v7_paper_application import (
    V7_CONFIG_SHA256,
    V7_PAPER_PROFILE,
    V7_UES_REVISION,
    V7PaperApplicationError,
    build_v7_loaded_version_readback,
    build_v7_runtime_binding,
    load_v7_paper_application_binding,
)


def _record(**overrides):
    record = {
        "application_id": str(uuid.uuid4()),
        "ticket_id": "rpt_" + "b" * 64,
        "status": "approved",
        "platform_id": "longbridge",
        "account_key": "longbridge-paper",
        "account_scope": "PAPER",
        "account_selector": "PAPER",
        "service_name": "longbridge-quant-paper-service",
        "strategy_profile": V7_PAPER_PROFILE,
        "candidate_id": V7_PAPER_PROFILE,
        "config_sha256": V7_CONFIG_SHA256,
        "source_commit": "a" * 40,
        "approved_ues_revision": "d1ca798d880cd83965f3da5081850ca48a616d19",
        "expected_revision": 42,
        "expected_strategy_profile": "soxl_soxx_trend_income",
        "desired_state": "paused",
        "claim": {
            "token": "claim-token",
            "workflow_run_id": "123",
            "workflow_run_attempt": "1",
        },
    }
    record.update(overrides)
    return record


def _protected_env():
    return {
        "CLOUD_RUN_SERVICE": "longbridge-quant-paper-service",
        "CLOUD_RUN_REGION": "asia-east1",
        "LONGPORT_SECRET_NAME": "longport_token_paper",
        "LONGPORT_APP_KEY_SECRET_NAME": "longport-app-key-paper",
        "LONGPORT_APP_SECRET_SECRET_NAME": "longport-app-secret-paper",
        "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "paper-account-1",
        "LONGBRIDGE_DRY_RUN_ONLY": "false",
        "RUNTIME_TARGET_ENABLED": "true",
        "RUNTIME_TARGET_JSON": json.dumps(
            {
                "platform_id": "longbridge",
                "strategy_profile": "soxl_soxx_trend_income",
                "dry_run_only": False,
                "execution_mode": "live",
                "account_scope": "PAPER",
                "account_selector": ["PAPER"],
                "service_name": "longbridge-quant-paper-service",
                "strategy_release": {
                    "release_id": "old-release",
                    "manifest_sha256": "1" * 64,
                    "strategy_revision": "2" * 40,
                    "config_sha256": "3" * 64,
                    "risk_policy_sha256": "4" * 64,
                    "evidence_sha256": "5" * 64,
                    "plugin_bundle_sha256": "6" * 64,
                    "effective_session": "2026-09-13T00:00:00Z",
                },
            }
        ),
    }


def test_build_v7_runtime_binding_is_paused_and_broker_paper() -> None:
    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())

    assert binding["application_id"]
    assert binding["strategy_profile"] == V7_PAPER_PROFILE
    assert binding["config_sha256"] == V7_CONFIG_SHA256
    assert binding["source_commit"] == "a" * 40
    assert binding["runtime_target_enabled"] is False
    assert binding["runtime_target"]["execution_environment"] == "paper"
    assert binding["runtime_target"]["dry_run_only"] is False
    assert binding["runtime_target"]["execution_mode"] == "live"
    assert binding["account_target"]["physical_account_id"] == "paper-account-1"
    assert binding["expected_revision"] == 42
    assert binding["approved_ues_revision"] == "d1ca798d880cd83965f3da5081850ca48a616d19"
    assert binding["runtime_target"].get("strategy_release") is None
    assert binding["previous_strategy_release"]["release_id"] == "old-release"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("platform_id", "other"),
        ("account_scope", "HK"),
        ("account_selector", "other"),
        ("strategy_profile", "soxl_soxx_trend_income"),
        ("candidate_id", "other"),
        ("config_sha256", "0" * 64),
        ("approved_ues_revision", "b" * 40),
        ("desired_state", "enabled"),
    ],
)
def test_build_v7_runtime_binding_rejects_scope_or_candidate_drift(field, value) -> None:
    with pytest.raises(V7PaperApplicationError):
        build_v7_runtime_binding(_record(**{field: value}), protected_env=_protected_env())


def test_build_v7_runtime_binding_rejects_stale_old_target_or_dry_run() -> None:
    protected = _protected_env()
    protected["RUNTIME_TARGET_JSON"] = json.dumps(
        {
            "platform_id": "longbridge",
            "strategy_profile": "other-old-profile",
            "dry_run_only": False,
            "account_scope": "PAPER",
            "account_selector": ["PAPER"],
            "service_name": "longbridge-quant-paper-service",
        }
    )
    with pytest.raises(V7PaperApplicationError, match="expected strategy"):
        build_v7_runtime_binding(_record(), protected_env=protected)

    protected = _protected_env()
    protected["LONGBRIDGE_DRY_RUN_ONLY"] = "true"
    with pytest.raises(V7PaperApplicationError, match="dry_run_only"):
        build_v7_runtime_binding(_record(), protected_env=protected)


def test_load_binding_requires_exact_application_json_and_never_accepts_enabled() -> None:
    record = _record()
    binding = build_v7_runtime_binding(record, protected_env=_protected_env())
    env = {"LONGBRIDGE_V7_PAPER_APPLICATION_JSON": json.dumps(binding)}

    loaded = load_v7_paper_application_binding(env)
    assert loaded["application_id"] == record["application_id"]
    assert loaded["runtime_target_enabled"] is False

    enabled = dict(binding)
    enabled["runtime_target_enabled"] = True
    with pytest.raises(V7PaperApplicationError, match="paused"):
        load_v7_paper_application_binding(
            {"LONGBRIDGE_V7_PAPER_APPLICATION_JSON": json.dumps(enabled)}
        )


def test_qsl_flat_claim_is_normalized_without_copying_claim_token() -> None:
    record = _record()
    claim = record.pop("claim")
    record.update(
        {
            "claim_token": claim["token"],
            "workflow_run_id": claim["workflow_run_id"],
            "workflow_run_attempt": claim["workflow_run_attempt"],
        }
    )
    binding = build_v7_runtime_binding(record, protected_env=_protected_env())
    assert "claim_token" not in json.dumps(binding)
    assert binding["claim"]["workflow_run_id"] == "123"


def test_qsl_application_record_uses_nested_claim_transport(monkeypatch) -> None:
    from scripts import v7_paper_application_http as transport

    record = _record()
    calls = []

    def fake_request(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "claimed": True}

    monkeypatch.setattr(transport, "_request", fake_request)
    transport.post_claim(
        base_url="https://qsl-strategy-switch-console.pigbibi.workers.dev",
        token="secret-token",
        record=record,
        run_id="200",
        attempt="1",
    )
    assert calls[0]["payload"]["claim"]["token"] == "claim-token"
    assert "claim_token" not in calls[0]["payload"]


def test_qsl_application_origin_is_fixed() -> None:
    from scripts.v7_paper_application_http import ApplicationTransportError, _endpoint

    with pytest.raises(ApplicationTransportError):
        _endpoint("https://attacker.example", str(uuid.uuid4()))


def test_loaded_version_readback_uses_process_identity_and_stays_paused(monkeypatch) -> None:
    from quant_platform_kit.common.runtime_target import build_runtime_target
    from strategy_loader import load_strategy_entrypoint_for_profile, load_strategy_runtime_adapter_for_profile
    from us_equity_strategies import get_strategy_entrypoint

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    target = build_runtime_target(
        platform_id="longbridge",
        strategy_profile=V7_PAPER_PROFILE,
        dry_run_only=False,
        account_selector=["PAPER"],
        account_scope="PAPER",
        service_name="longbridge-quant-paper-service",
        execution_environment="paper",
    )
    runtime = SimpleNamespace(
        entrypoint=get_strategy_entrypoint(V7_PAPER_PROFILE),
        runtime_adapter=load_strategy_runtime_adapter_for_profile(V7_PAPER_PROFILE),
    )
    settings = SimpleNamespace(runtime_target=target, runtime_target_enabled=False, dry_run_only=False)
    readback = build_v7_loaded_version_readback(
        binding,
        runtime_settings=settings,
        strategy_runtime=runtime,
        runtime_env={"V7_PAPER_SOURCE_COMMIT": "a" * 40, "K_REVISION": "paper-v7-00001"},
    )
    assert readback["activation_state"] == "applied_paused"
    assert readback["runtime_target_enabled"] is False
    assert readback["ues_revision"] == "d1ca798d880cd83965f3da5081850ca48a616d19"
    assert readback["revision_name"] == "paper-v7-00001"
    assert readback["runtime_loaded_receipt"]["schema_version"] == "runtime_loaded_receipt.v1"


def test_bound_v7_loader_is_opt_in_and_normal_loader_stays_fail_closed(monkeypatch) -> None:
    from strategy_loader import load_strategy_entrypoint_for_profile

    with pytest.raises(ValueError):
        load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    entrypoint = load_strategy_entrypoint_for_profile(V7_PAPER_PROFILE)

    assert entrypoint.manifest.profile == V7_PAPER_PROFILE
    assert entrypoint.manifest.default_config["managed_symbols"] == ("SOXL", "SOXX", "BOXX")


def test_runtime_settings_accept_only_bound_v7_and_force_disabled_paper_target(monkeypatch) -> None:
    from runtime_config_support import load_platform_runtime_settings

    binding = build_v7_runtime_binding(_record(), protected_env=_protected_env())
    monkeypatch.setenv("LONGBRIDGE_V7_PAPER_APPLICATION_JSON", json.dumps(binding))
    monkeypatch.setenv("RUNTIME_TARGET_JSON", json.dumps(binding["runtime_target"]))
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "false")
    monkeypatch.setenv("LONGBRIDGE_DRY_RUN_ONLY", "false")
    monkeypatch.setenv("ACCOUNT_PREFIX", "PAPER")
    monkeypatch.setenv("ACCOUNT_REGION", "PAPER")
    monkeypatch.setenv("LONGPORT_SECRET_NAME", "longport_token_paper")

    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    assert settings.strategy_profile == V7_PAPER_PROFILE
    assert settings.runtime_target_enabled is False
    assert settings.dry_run_only is False
    assert settings.runtime_target.execution_environment.value == "paper"
