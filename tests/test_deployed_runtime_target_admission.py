import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_deployed_runtime_target_admission.py"
SPEC = importlib.util.spec_from_file_location("deployed_target_admission", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
admission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(admission)


def service_payload(*, runtime_target: dict, profile: str, dry_run_only: str = "true") -> dict:
    return {
        "spec": {"template": {"spec": {"containers": [{"env": [
            {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(runtime_target)},
            {"name": "STRATEGY_PROFILE", "value": profile},
            {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": dry_run_only},
            {"name": "RUNTIME_TARGET_ENABLED", "value": "true"},
        ]}]}}}
    }


def admitted_target(*, profile: str = "russell_top50_leader_rotation", dry_run_only: bool = True) -> dict:
    return {
        "platform_id": "longbridge",
        "service_name": "paper-service",
        "strategy_profile": profile,
        "execution_mode": "paper" if dry_run_only else "live",
        "dry_run_only": dry_run_only,
    }


def test_verify_service_accepts_admitted_shadow_target():
    result = admission.verify_service(
        service="paper-service",
        service_json=service_payload(runtime_target=admitted_target(), profile="russell_top50_leader_rotation"),
    )
    assert result["profile"] == "russell_top50_leader_rotation"
    assert result["dry_run_only"] is True


def test_verify_service_accepts_paper_broker_submission_target():
    target = admitted_target(dry_run_only=False)
    target["execution_mode"] = "paper"
    result = admission.verify_service(
        service="paper-service",
        service_json=service_payload(
            runtime_target=target, profile="russell_top50_leader_rotation", dry_run_only="false"
        ),
    )
    assert result["execution_mode"] == "paper"
    assert result["dry_run_only"] is False


@pytest.mark.parametrize(
    ("target", "profile", "message"),
    [
        (admitted_target(), "different_profile", "STRATEGY_PROFILE does not match"),
        ({**admitted_target(), "execution_mode": "live"}, "russell_top50_leader_rotation", "dry-run/shadow target"),
        ({**admitted_target(), "strategy_profile": "retired_profile"}, "retired_profile", "not admitted"),
    ],
)
def test_verify_service_rejects_target_drift(target, profile, message):
    with pytest.raises(admission.AdmissionError, match=message):
        admission.verify_service(
            service="paper-service", service_json=service_payload(runtime_target=target, profile=profile)
        )
