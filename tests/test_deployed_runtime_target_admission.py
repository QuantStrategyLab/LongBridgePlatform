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
        "account_scope": "PAPER",
        "account_selector": ["PAPER"],
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


CANDIDATE = "4a3943883cd6b5bbfe32a559e56a91b40a81b7ce"
SERVING = "1" * 40
HISTORY = {
    "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
    "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots",
    "ACCOUNT_HISTORY_TARGET_ID": "paper",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
}


def lock_text(sha: str) -> str:
    return (
        "[[package]]\n"
        'name = "us-equity-strategies"\n'
        "source = { git = "
        f'"https://github.com/QuantStrategyLab/UsEquityStrategies.git?rev={sha}#{sha}" }}\n'
    )


def revision_payload(env: list[dict], *, name: str, commit: str, image: str = "old") -> dict:
    return {
        "metadata": {
            "name": name,
            "labels": {"commit-sha": commit},
        },
        "spec": {
            "serviceAccountName": "runtime@example.invalid",
            "containers": [{"image": image, "env": env}],
        },
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def staging_service(*extra_env: dict, runtime_target: dict | None = None, dry_run_only: str = "true") -> dict:
    target = admitted_target() if runtime_target is None else runtime_target
    payload = service_payload(
        runtime_target=target, profile=str(target["strategy_profile"]), dry_run_only=dry_run_only
    )
    payload["metadata"] = {"annotations": {"run.googleapis.com/ingress": "internal"}}
    template = payload["spec"]["template"]
    template["metadata"] = {}
    template["spec"]["serviceAccountName"] = "runtime@example.invalid"
    template["spec"]["containers"][0]["env"].extend(extra_env)
    payload["status"] = {"traffic": [{"revisionName": "paper-service-serving", "percent": 100}]}
    return payload


def test_candidate_ues_revision_matches_declared_pin():
    assert admission._candidate_ues_revision() == CANDIDATE


def test_history_update_omits_blank_inputs_and_rejects_partial_or_non_paper():
    assert admission.history_update({}, workflow_target="HK", project_id="longbridgequant") is None
    with pytest.raises(admission.AdmissionError, match="only admitted for PAPER"):
        admission.history_update(HISTORY, workflow_target="HK", project_id="longbridgequant")
    with pytest.raises(admission.AdmissionError, match="^history settings are invalid$"):
        admission.history_update(
            {"ACCOUNT_HISTORY_RECORDING_ENABLED": "true"},
            workflow_target="PAPER",
            project_id="longbridgequant",
        )
    injected = {**HISTORY, "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots,extra"}
    with pytest.raises(admission.AdmissionError, match="history settings are invalid") as invalid:
        admission.history_update(injected, workflow_target="PAPER", project_id="longbridgequant")
    assert "account_snapshots" not in str(invalid.value)
    with pytest.raises(admission.AdmissionError, match="history settings are invalid"):
        admission.history_update(
            {**HISTORY, "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/other"},
            workflow_target="PAPER",
            project_id="longbridgequant",
        )


def _identity_run(service_json: dict):
    serving = revision_payload(
        service_json["spec"]["template"]["spec"]["containers"][0]["env"],
        name="paper-service-serving",
        commit=SERVING,
    )

    def run(command: list[str]) -> str:
        if command[:4] == ["gcloud", "run", "revisions", "describe"]:
            return json.dumps(serving)
        if command[:2] == ["git", "show"]:
            return (Path(__file__).resolve().parents[1] / "uv.lock").read_text()
        raise AssertionError(command)

    return run


def test_prepare_rejects_sg_scope_for_paper_workflow_and_accepts_live_paper_scope():
    sg_target = admitted_target(dry_run_only=False)
    sg_target["execution_mode"] = "live"
    sg_target["account_scope"] = "SG"
    sg_target["deployment_selector"] = "SG"
    sg_target["account_selectors"] = ["SG"]
    del sg_target["account_selector"]
    sg_service = staging_service(runtime_target=sg_target, dry_run_only="false")
    with pytest.raises(admission.AdmissionError, match="does not match PAPER") as rejected:
        admission.prepare_image_only_staging(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            service_json=sg_service,
            env={"WORKFLOW_TARGET": "PAPER", **HISTORY},
            run=_identity_run(sg_service),
        )
    assert "SG" not in str(rejected.value)
    assert "execution_mode" not in str(rejected.value)

    conflicted = admitted_target(dry_run_only=False)
    conflicted["execution_mode"] = "live"
    conflicted["deployment_selector"] = "SG"
    conflicted_service = staging_service(runtime_target=conflicted, dry_run_only="false")
    with pytest.raises(admission.AdmissionError, match="does not match PAPER"):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            service_json=conflicted_service,
            env={"WORKFLOW_TARGET": "PAPER", **HISTORY},
            run=_identity_run(conflicted_service),
        )

    live_paper = admitted_target(dry_run_only=False)
    live_paper["execution_mode"] = "live"
    live_paper["account_selector"] = "PAPER"
    live_service = staging_service(runtime_target=live_paper, dry_run_only="false")
    plan = admission.prepare_image_only_staging(
        service="paper-service",
        project="longbridgequant",
        region="asia-east1",
        service_json=live_service,
        env={"WORKFLOW_TARGET": "PAPER", **HISTORY},
        run=_identity_run(live_service),
    )
    assert plan["history_arg"].startswith("ACCOUNT_HISTORY_RECORDING_ENABLED=true,")
    assert "deployment_selector" not in live_paper
    assert "account_selectors" not in live_paper


def test_image_only_staging_reads_serving_lock_and_rejects_pin_or_template_drift():
    service_json = staging_service()
    serving = revision_payload(service_json["spec"]["template"]["spec"]["containers"][0]["env"], name="paper-service-serving", commit=SERVING)
    calls: list[list[str]] = []

    def run(command: list[str]) -> str:
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(serving)
        if command[:2] == ["git", "show"]:
            assert command[2] == f"{SERVING}:uv.lock"
            return (Path(__file__).resolve().parents[1] / "uv.lock").read_text()
        raise AssertionError(command)

    plan = admission.prepare_image_only_staging(
        service="paper-service",
        project="longbridgequant",
        region="asia-east1",
        service_json=service_json,
        env={"WORKFLOW_TARGET": "PAPER"},
        run=run,
    )
    assert plan["history_arg"] == ""
    assert plan["service_ingress"] == "internal"
    assert "RUNTIME_TARGET_JSON" not in json.dumps(plan)
    assert ["git", "show", f"{SERVING}:uv.lock"] in calls

    def mismatched(command: list[str]) -> str:
        if command[:2] == ["git", "show"]:
            return lock_text("c" * 40)
        return json.dumps(serving)

    with pytest.raises(admission.AdmissionError, match="differs from serving image"):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            service_json=service_json,
            env={"WORKFLOW_TARGET": "PAPER"},
            run=mismatched,
        )
    drifted = json.loads(json.dumps(serving))
    for item in drifted["spec"]["containers"][0]["env"]:
        if item["name"] == "LONGBRIDGE_DRY_RUN_ONLY":
            item["value"] = "false"
    with pytest.raises(admission.AdmissionError, match="template does not match"):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            service_json=service_json,
            env={"WORKFLOW_TARGET": "PAPER"},
            run=lambda command: json.dumps(drifted),
        )


def test_readback_compares_traffic_once_and_does_not_move_it():
    service_json = staging_service()
    image = "registry.invalid/synthetic-project/synthetic-images/longbridgeplatform/paper-service@sha256:" + "b" * 64
    serving = revision_payload(
        service_json["spec"]["template"]["spec"]["containers"][0]["env"],
        name="paper-service-serving",
        commit=SERVING,
    )
    staged_env = [*service_json["spec"]["template"]["spec"]["containers"][0]["env"]]
    staged = revision_payload(staged_env, name="paper-service-r1", commit="a" * 40, image=image)
    after = json.loads(json.dumps(service_json))
    after["status"]["latestCreatedRevisionName"] = "paper-service-old-same-sha"
    calls: list[list[str]] = []

    def run(command: list[str]) -> str:
        calls.append(list(command))
        assert command[:4] != ["gcloud", "run", "services", "update"]
        if command[:4] == ["gcloud", "run", "services", "describe"]:
            return json.dumps(after)
        if command[:4] == ["gcloud", "run", "revisions", "describe"]:
            if command[4] == "paper-service-old-same-sha":
                return json.dumps(revision_payload(staged_env, name=command[4], commit="a" * 40, image=image))
            payload = serving if command[4] == "paper-service-serving" else staged
            return json.dumps(payload)
        if command[:2] == ["git", "show"]:
            return (Path(__file__).resolve().parents[1] / "uv.lock").read_text()
        raise AssertionError(command)

    plan = admission.prepare_image_only_staging(
        service="paper-service",
        project="longbridgequant",
        region="asia-east1",
        service_json=service_json,
        env={"WORKFLOW_TARGET": "PAPER", **HISTORY},
        run=run,
    )
    staged["spec"]["containers"][0]["env"] = [
        *staged_env,
        *({"name": key, "value": value} for key, value in HISTORY.items()),
    ]
    admission.confirm_image_only_readback(
        service="paper-service",
        project="longbridgequant",
        region="asia-east1",
        plan=plan,
        expected_image=image,
        expected_commit="a" * 40,
        expected_revision="paper-service-r1",
        run=run,
    )
    described = [call[4] for call in calls if call[:4] == ["gcloud", "run", "revisions", "describe"]]
    assert "paper-service-r1" in described
    assert "paper-service-old-same-sha" not in described
    moved = json.loads(json.dumps(after))
    moved["status"]["traffic"] = [{"revisionName": "paper-service-staged", "percent": 100}]

    def moved_run(command: list[str]) -> str:
        if command[:4] == ["gcloud", "run", "services", "describe"]:
            return json.dumps(moved)
        raise AssertionError(command)

    with pytest.raises(admission.AdmissionError, match="serving traffic changed"):
        admission.confirm_image_only_readback(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            plan=plan,
            expected_image=image,
            expected_commit="a" * 40,
            expected_revision="paper-service-r1",
            run=moved_run,
        )
    changed = json.loads(json.dumps(after))
    changed["metadata"]["annotations"]["run.googleapis.com/ingress"] = "all"

    def changed_ingress(command: list[str]) -> str:
        if command[:4] == ["gcloud", "run", "services", "describe"]:
            return json.dumps(changed)
        raise AssertionError(command)

    with pytest.raises(admission.AdmissionError, match="service ingress changed"):
        admission.confirm_image_only_readback(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            plan=plan,
            expected_image=image,
            expected_commit="a" * 40,
            expected_revision="paper-service-r1",
            run=changed_ingress,
        )

    def old_same_sha(command: list[str]) -> str:
        if command[:4] == ["gcloud", "run", "services", "describe"]:
            return json.dumps(after)
        if command[:4] == ["gcloud", "run", "revisions", "describe"] and command[4] == "paper-service-r1":
            return json.dumps(revision_payload(staged_env, name="paper-service-r1", commit="a" * 40, image="old"))
        raise AssertionError(command)

    with pytest.raises(admission.AdmissionError, match="does not match the approved image"):
        admission.confirm_image_only_readback(
            service="paper-service",
            project="longbridgequant",
            region="asia-east1",
            plan=plan,
            expected_image=image,
            expected_commit="a" * 40,
            expected_revision="paper-service-r1",
            run=old_same_sha,
        )
