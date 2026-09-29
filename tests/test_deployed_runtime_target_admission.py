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


CANDIDATE = admission.APPROVED_PAPER_HISTORY_CANDIDATE
SERVING = "1" * 40
UES = "e" * 40
OTHER_UES = "f" * 40
HISTORY = {
    "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
    "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/account_snapshots",
    "ACCOUNT_HISTORY_TARGET_ID": "paper",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
    "WORKFLOW_TARGET": "PAPER",
}
MAIN_SHA = "a" * 40


def _main_env(*, snapshot_setting: str = "") -> dict[str, str]:
    return {
        "WORKFLOW_TARGET": "PAPER",
        "SOURCE_COMMIT": MAIN_SHA,
        "APPROVED_REF": "main",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GITHUB_REF_NAME": "main",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": MAIN_SHA,
        "GITHUB_WORKFLOW_SHA": MAIN_SHA,
        "GITHUB_WORKFLOW_REF": "QuantStrategyLab/LongBridgePlatform/.github/workflows/sync-cloud-run-env.yml@refs/heads/main",
        "ACCOUNT_SNAPSHOT_ENABLED_INPUT": snapshot_setting,
    }


def _declaration(name: str, pin: str) -> str:
    if name == "uv.lock":
        return (
            '[[package]]\nname = "us-equity-strategies"\n'
            f'source = {{ git = "https://github.com/QuantStrategyLab/UsEquityStrategies.git?rev={pin}#{pin}" }}\n'
        )
    if name == "pyproject.toml":
        return (
            "[project]\ndependencies = ["
            f'"us-equity-strategies @ git+https://github.com/QuantStrategyLab/UsEquityStrategies.git@{pin}"'
            "]\n"
        )
    return f'[qsl.requires]\nus_equity_strategies = "{pin}"\n'


def _paper_target() -> dict:
    return {
        "platform_id": "longbridge",
        "service_name": "paper-service",
        "account_scope": "PAPER",
        "account_selector": ["PAPER"],
        "strategy_profile": "russell_top50_leader_rotation",
        "execution_mode": "live",
        "dry_run_only": False,
    }


def _env() -> list[dict[str, str]]:
    return [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(_paper_target())},
        {"name": "STRATEGY_PROFILE", "value": "russell_top50_leader_rotation"},
        {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
        {"name": "RUNTIME_TARGET_ENABLED", "value": "true"},
    ]


def _service() -> dict:
    return {
        "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
        "spec": {"template": {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": _env()}]}}},
        "status": {"latestReadyRevisionName": "guessed-latest", "traffic": [{"revisionName": "serving-rev", "percent": 100}]},
    }


def _revision(name: str, commit: str, image: str, *, ready: bool = True, extra_env: list | None = None) -> dict:
    env = _env() + (extra_env or [])
    return {
        "metadata": {"name": name, "labels": {"commit-sha": commit}},
        "spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env, "image": image}]},
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def test_staging_cli_rejects_main_sha_with_history(monkeypatch, tmp_path):
    assert not hasattr(admission, "approve_image_source")
    monkeypatch.setenv("WORKFLOW_TARGET", "PAPER")
    monkeypatch.setenv("SOURCE_COMMIT", "a" * 40)
    for key, value in HISTORY.items():
        if key != "WORKFLOW_TARGET":
            monkeypatch.setenv(key, value)
    plan = tmp_path / "plan.json"
    code = admission.main(
        [
            "--project",
            "synthetic-project",
            "--region",
            "synthetic-region",
            "--service",
            "paper-service",
            "--image-only-staging",
            "--plan-out",
            str(plan),
        ]
    )
    assert code == 1
    assert not plan.exists()


def test_prepare_rejects_main_commit_as_history_image():
    def run(command):
        raise AssertionError(command)

    with pytest.raises(admission.AdmissionError, match="not approved"):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="synthetic-project",
            region="synthetic-region",
            service_json={},
            env=HISTORY,
            image_commit="a" * 40,
            run=run,
        )


def _service_with(target: dict) -> tuple[dict, list[dict[str, str]]]:
    env = [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "STRATEGY_PROFILE", "value": target["strategy_profile"]},
        {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
        {"name": "RUNTIME_TARGET_ENABLED", "value": "true"},
    ]
    service = {
        "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
        "spec": {"template": {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env}]}}},
        "status": {"traffic": [{"revisionName": "serving-rev", "percent": 100}]},
    }
    return service, env


def _prepare_bound(target: dict):
    service, env = _service_with(target)

    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(
                {
                    "metadata": {"name": "serving-rev", "labels": {"commit-sha": SERVING}},
                    "spec": {
                        "serviceAccountName": "runtime@example.invalid",
                        "containers": [{"env": env, "image": "serving-image"}],
                    },
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            )
        return _declaration(command[2].split(":", 1)[1], UES)

    return admission.prepare_image_only_staging(
        service="paper-service",
        project="synthetic-project",
        region="synthetic-region",
        service_json=service,
        env=HISTORY,
        image_commit=CANDIDATE,
        run=run,
    )


def test_release_binding_uses_candidate_ues_not_the_main_pin(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(_declaration("uv.lock", OTHER_UES), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    bound = _paper_target()
    bound["strategy_release"] = {"strategy_revision": UES}
    assert _prepare_bound(bound)["image_commit"] == CANDIDATE
    bound["strategy_release"] = {"strategy_revision": OTHER_UES}
    with pytest.raises(admission.AdmissionError, match="approved target binding"):
        _prepare_bound(bound)


def test_soxl_risk_binding_must_match_candidate_ues(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(_declaration("uv.lock", OTHER_UES), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    bound = _paper_target()
    bound["strategy_profile"] = "soxl_soxx_trend_income"
    bound["strategy_release"] = {"strategy_revision": UES}
    bound["runtime_risk_limits"] = {"binding": {"ues_revision": OTHER_UES}}
    with pytest.raises(admission.AdmissionError, match="approved target binding"):
        _prepare_bound(bound)


def test_candidate_lock_is_read_even_when_the_main_lock_differs(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(_declaration("uv.lock", OTHER_UES), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(command):
        calls.append(list(command))
        sha, name = command[2].split(":", 1)
        assert sha == CANDIDATE
        return _declaration(name, UES)

    assert admission.ues_revision_at(CANDIDATE, run) == UES
    assert calls == [["git", "show", f"{CANDIDATE}:{name}"] for name in ("uv.lock", "pyproject.toml", "qsl.toml")]
    assert not any("record_daily" in part or part.endswith(".py") for command in calls for part in command)


def test_prepare_rejects_candidate_ues_that_differs_from_serving(tmp_path, monkeypatch):
    (tmp_path / "uv.lock").write_text(_declaration("uv.lock", OTHER_UES), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = []

    def run(command):
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        sha, name = command[2].split(":", 1)
        pin = OTHER_UES if sha == SERVING else UES
        return _declaration(name, pin)

    with pytest.raises(admission.AdmissionError, match="differs from serving"):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="synthetic-project",
            region="synthetic-region",
            service_json=_service(),
            env=HISTORY,
            image_commit=CANDIDATE,
            run=run,
        )
    assert not any(command[:3] == ["gcloud", "run", "services"] and "update" in command for command in calls)
    assert all(command[0] in {"git", "gcloud"} for command in calls)


def test_prepare_accepts_live_paper_when_candidate_matches_serving():
    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        return _declaration(command[2].split(":", 1)[1], UES)

    plan = admission.prepare_image_only_staging(
        service="paper-service",
        project="synthetic-project",
        region="synthetic-region",
        service_json=_service(),
        env=HISTORY,
        image_commit=CANDIDATE,
        run=run,
    )
    assert plan["image_commit"] == CANDIDATE
    assert plan["history_values"]["ACCOUNT_HISTORY_TARGET_ID"] == "paper"
    assert "secret" not in json.dumps(plan)


@pytest.mark.parametrize("snapshot_setting", ["", "true", "false"])
def test_prepare_main_image_is_exact_workflow_sha_and_plans_only_snapshot_key(snapshot_setting):
    calls = []
    env = _main_env(snapshot_setting=snapshot_setting)

    def run(command):
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        sha, name = command[2].split(":", 1)
        assert sha in {MAIN_SHA, SERVING}
        return _declaration(name, UES)

    plan = admission.prepare_image_only_staging(
        service="paper-service",
        project="synthetic-project",
        region="synthetic-region",
        service_json=_service(),
        env=env,
        image_commit=MAIN_SHA,
        run=run,
    )

    assert plan["image_commit"] == MAIN_SHA
    assert plan["history_values"] == {}
    assert plan["snapshot_value"] == (snapshot_setting or None)
    assert plan["snapshot_arg"] == (
        f"LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED={snapshot_setting}"
        if snapshot_setting
        else ""
    )
    assert plan["serving_traffic"] == [{"revisionName": "serving-rev", "percent": 100}]
    assert sum(command[:3] == ["gcloud", "run", "revisions"] for command in calls) == 2


@pytest.mark.parametrize(
    ("env_overrides", "image_commit"),
    [
        ({"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "yes"}, MAIN_SHA),
        ({"WORKFLOW_TARGET": "HK", "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"}, MAIN_SHA),
        ({"GITHUB_WORKFLOW_SHA": "b" * 40}, MAIN_SHA),
        ({"GITHUB_REF": "refs/heads/other"}, MAIN_SHA),
        ({"SOURCE_COMMIT": "c" * 40}, MAIN_SHA),
        ({**HISTORY, "ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"}, MAIN_SHA),
        ({"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"}, CANDIDATE),
    ],
)
def test_invalid_main_or_snapshot_dispatch_is_rejected_before_cloud_reads(env_overrides, image_commit):
    env = _main_env(snapshot_setting="true")
    env.update(env_overrides)
    calls = []

    with pytest.raises(admission.AdmissionError):
        admission.prepare_image_only_staging(
            service="paper-service",
            project="synthetic-project",
            region="synthetic-region",
            service_json={},
            env=env,
            image_commit=image_commit,
            run=lambda command: calls.append(list(command)) or "",
        )
    assert calls == []


def test_main_image_readback_preserves_traffic_and_all_other_environment():
    image = "repo@sha256:" + "b" * 64
    configuration = admission._configuration(_service())
    plan = {
        "history_values": {},
        "snapshot_value": "true",
        "service_ingress": "internal",
        "serving_traffic": [{"revisionName": "serving-rev", "percent": 100}],
        "template_digest": admission._config_digest(configuration),
    }
    service = _service()
    revision = _revision(
        "paper-service-r123",
        MAIN_SHA,
        image,
        extra_env=[{"name": "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED", "value": "true"}],
    )

    def confirm(service_payload, revision_payload):
        def run(command):
            if command[:3] == ["gcloud", "run", "services"]:
                return json.dumps(service_payload)
            return json.dumps(revision_payload)

        admission.confirm_image_only_readback(
            service="paper-service",
            project="synthetic-project",
            region="synthetic-region",
            plan=plan,
            expected_image=image,
            expected_commit=MAIN_SHA,
            expected_revision="paper-service-r123",
            run=run,
        )

    assert configuration["serviceAccountName"] == "runtime@example.invalid"
    confirm(service, revision)
    changed_traffic = json.loads(json.dumps(service))
    changed_traffic["status"]["traffic"] = [{"revisionName": "paper-service-r123", "percent": 100}]
    with pytest.raises(admission.AdmissionError, match="traffic"):
        confirm(changed_traffic, revision)
    changed_env = _revision(
        "paper-service-r123",
        MAIN_SHA,
        image,
        extra_env=[
            {"name": "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED", "value": "true"},
            {"name": "UNEXPECTED", "value": "1"},
        ],
    )
    with pytest.raises(admission.AdmissionError, match="configuration"):
        confirm(service, changed_env)
    wrong_flag = _revision(
        "paper-service-r123",
        MAIN_SHA,
        image,
        extra_env=[{"name": "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED", "value": "false"}],
    )
    with pytest.raises(admission.AdmissionError, match="setting"):
        confirm(service, wrong_flag)


def test_readback_rejects_ingress_config_history_and_unknown_revision():
    image = "repo@sha256:" + "b" * 64
    plan = {
        "history_values": {key: HISTORY[key] for key in admission._HISTORY_KEYS},
        "service_ingress": "internal",
        "serving_traffic": [{"revisionName": "serving-rev", "percent": 100}],
        "template_digest": admission._config_digest(
            {"env": admission._env_material({"containers": [{"env": _env()}]}), "serviceAccountName": "runtime@example.invalid"},
            skip=set(admission._HISTORY_KEYS),
        ),
    }
    history_env = [{"name": key, "value": HISTORY[key]} for key in admission._HISTORY_KEYS]

    def confirm(service_json, revision_payload, run_error=False):
        def run(command):
            if run_error and command[:3] == ["gcloud", "run", "revisions"]:
                raise admission.AdmissionError("cloud read failed")
            if command[:3] == ["gcloud", "run", "services"]:
                return json.dumps(service_json)
            return json.dumps(revision_payload)
        admission.confirm_image_only_readback(
            service="paper-service",
            project="synthetic-project",
            region="synthetic-region",
            plan=plan,
            expected_image=image,
            expected_commit=CANDIDATE,
            expected_revision="paper-service-r123",
            run=run,
        )

    confirm(_service(), _revision("paper-service-r123", CANDIDATE, image, extra_env=history_env))
    drifted = _service()
    drifted["metadata"]["annotations"]["run.googleapis.com/ingress"] = "all"
    with pytest.raises(admission.AdmissionError, match="ingress"):
        confirm(drifted, _revision("paper-service-r123", CANDIDATE, image, extra_env=history_env))
    with pytest.raises(admission.AdmissionError, match="configuration"):
        confirm(_service(), _revision("paper-service-r123", CANDIDATE, image, extra_env=history_env + [{"name": "OTHER", "value": "1"}]))
    with pytest.raises(admission.AdmissionError, match="history"):
        confirm(_service(), _revision("paper-service-r123", CANDIDATE, image))
    with pytest.raises(admission.AdmissionError, match="not observed"):
        confirm(_service(), _revision("paper-service-r123", CANDIDATE, image, ready=False, extra_env=history_env))
    with pytest.raises(admission.AdmissionError):
        confirm(_service(), _revision("paper-service-r123", CANDIDATE, image, extra_env=history_env), run_error=True)
