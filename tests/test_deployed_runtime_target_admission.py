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
HTTP_SNAPSHOT_CANDIDATE = admission.APPROVED_PAPER_HTTP_SNAPSHOT_CANDIDATE
PROBE_SNAPSHOT_CANDIDATE = admission.APPROVED_PAPER_PROBE_SNAPSHOT_CANDIDATE
SGHK_SNAPSHOT_CANDIDATE = admission.APPROVED_SGHK_ACCOUNT_SNAPSHOT_CANDIDATE
HK_PROBE_DIAGNOSTICS_CANDIDATE = admission.APPROVED_HK_PROBE_DIAGNOSTICS_CANDIDATE
SGHK_PREFIX = "gs://qsl-runtime-logs-shared/longbridge/account_snapshots"
STAGED_SOURCE_REVISION = "longbridge-quant-paper-service-r36423178119"
STAGED_SOURCE_IMAGE = (
    "asia-east1-docker.pkg.dev/synthetic-project/images/longbridgeplatform/paper-service"
    "@sha256:9a3260ca8255873309b1c27cd2fd7403e6006a4e9088241e6f89a4e5e65f7a10"
)


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


def _sghk_target(target_label: str) -> dict:
    target_id = target_label.lower()
    scope = target_label
    service = f"longbridge-quant-{target_id}-service"
    return {
        "platform_id": "longbridge",
        "service_name": service,
        "account_scope": scope,
        "account_selector": scope,
        "deployment_selector": "HK" if target_id == "hk" else "SG",
        "strategy_profile": "tqqq_growth_income" if target_id == "hk" else "soxl_soxx_trend_income",
        "execution_mode": "live",
        "dry_run_only": False,
    }


def _sghk_history(target_label: str) -> dict[str, str]:
    target_id = target_label.lower()
    return {
        "WORKFLOW_TARGET": target_label,
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_GCS_PREFIX": SGHK_PREFIX,
        "ACCOUNT_HISTORY_TARGET_ID": target_id,
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": target_label,
    }


def _prepare_sghk(target_label: str, *, target_overrides=None, history_overrides=None, project="longbridgequant", region=None):
    target = _sghk_target(target_label)
    target.update(target_overrides or {})
    service = target["service_name"]
    target_id = target_label.lower()
    if region is None:
        region = {"hk": "asia-east2", "sg": "asia-southeast1"}[target_id]
    env = [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "STRATEGY_PROFILE", "value": target["strategy_profile"]},
        {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
        {"name": "RUNTIME_TARGET_ENABLED", "value": "false"},
    ]
    service_json = {
        "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
        "spec": {"template": {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env}]}}},
        "status": {"traffic": [{"revisionName": "serving-rev", "percent": 100}]},
    }
    candidate_env = _sghk_history(target_label)
    candidate_env.update(history_overrides or {})

    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps({
                "metadata": {"name": "serving-rev", "labels": {"commit-sha": SERVING}},
                "spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env, "image": "serving-image"}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            })
        sha, name = command[2].split(":", 1)
        return _declaration(name, UES)

    plan = admission.prepare_image_only_staging(
        service=service,
        project=project,
        region=region,
        service_json=service_json,
        env=candidate_env,
        image_commit=SGHK_SNAPSHOT_CANDIDATE,
        run=run,
    )
    return plan, candidate_env


@pytest.mark.parametrize("target_label", ["HK", "SG"])
def test_sghk_snapshot_candidate_requires_exact_target_and_history_quartet(target_label):
    plan, env = _prepare_sghk(target_label)
    assert plan["image_commit"] == SGHK_SNAPSHOT_CANDIDATE
    assert plan["history_values"]["ACCOUNT_HISTORY_TARGET_ID"] == target_label.lower()
    assert plan["history_values"]["ACCOUNT_HISTORY_EXPECTED_SCOPE"] == target_label
    assert plan["snapshot_value"] is None and plan["snapshot_arg"] == ""
    assert plan["history_arg"] == ",".join(
        f"{key}={env[key]}" for key in admission._HISTORY_KEYS
    )
    assert plan["serving_traffic"] == [{"revisionName": "serving-rev", "percent": 100}]


@pytest.mark.parametrize(
    ("target_label", "history_overrides", "target_overrides", "project", "region"),
    [
        ("HK", {"ACCOUNT_HISTORY_TARGET_ID": "sg"}, None, "longbridgequant", "asia-east2"),
        ("HK", {"ACCOUNT_HISTORY_EXPECTED_SCOPE": "SG"}, None, "longbridgequant", "asia-east2"),
        ("HK", {"ACCOUNT_HISTORY_GCS_PREFIX": "gs://other-bucket/longbridge/account_snapshots"}, None, "longbridgequant", "asia-east2"),
        ("SG", {"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false"}, None, "longbridgequant", "asia-southeast1"),
        ("HK", None, {"account_scope": "SG"}, "longbridgequant", "asia-east2"),
        ("HK", None, {"account_selector": "hk"}, "longbridgequant", "asia-east2"),
        ("SG", None, {"deployment_selector": "sg"}, "longbridgequant", "asia-southeast1"),
        ("SG", None, None, "other-project", "asia-southeast1"),
        ("HK", None, None, "longbridgequant", "asia-east1"),
    ],
)
def test_sghk_candidate_rejects_wrong_scope_selector_or_environment(
    target_label, history_overrides, target_overrides, project, region
):
    with pytest.raises(admission.AdmissionError):
        _prepare_sghk(
            target_label,
            history_overrides=history_overrides,
            target_overrides=target_overrides,
            project=project,
            region=region,
        )


def test_sghk_candidate_rejects_arbitrary_sha_and_template_drift():
    env = _sghk_history("HK")
    with pytest.raises(admission.AdmissionError):
        admission._validate_image_only_source(image_commit="b" * 40, env=env, project="longbridgequant")

    target = _sghk_target("HK")
    service_json = {
        "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
        "spec": {"template": {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": [
            {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
            {"name": "STRATEGY_PROFILE", "value": target["strategy_profile"]},
            {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
            {"name": "RUNTIME_TARGET_ENABLED", "value": "false"},
        ]}]}}},
        "status": {"traffic": [{"revisionName": "serving-rev", "percent": 100}]},
    }
    drifted_env = list(service_json["spec"]["template"]["spec"]["containers"][0]["env"])
    drifted_env.append({"name": "UNEXPECTED", "value": "1"})

    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps({
                "metadata": {"name": "serving-rev", "labels": {"commit-sha": SERVING}},
                "spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": drifted_env, "image": "serving-image"}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            })
        return _declaration(command[2].split(":", 1)[1], UES)

    with pytest.raises(admission.AdmissionError, match="template does not match"):
        admission.prepare_image_only_staging(
            service=target["service_name"], project="longbridgequant", region="asia-east2",
            service_json=service_json, env=env, image_commit=SGHK_SNAPSHOT_CANDIDATE, run=run,
        )


def _prepare_hk_probe_diagnostics(*, target_label="HK", enabled="false", extra_env=None, input_overrides=None):
    target = _sghk_target(target_label)
    env_rows = [
        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
        {"name": "STRATEGY_PROFILE", "value": target["strategy_profile"]},
        {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
        {"name": "RUNTIME_TARGET_ENABLED", "value": enabled},
    ] + (extra_env or [])
    service = {
        "metadata": {"annotations": {"run.googleapis.com/ingress": "internal"}},
        "spec": {"template": {"spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env_rows}]}}},
        "status": {"traffic": [{"revisionName": "serving-rev", "percent": 100}]},
    }

    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps({
                "metadata": {"name": "serving-rev", "labels": {"commit-sha": SERVING}},
                "spec": {"serviceAccountName": "runtime@example.invalid", "containers": [{"env": env_rows, "image": "serving-image"}]},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            })
        sha, name = command[2].split(":", 1)
        assert sha in {HK_PROBE_DIAGNOSTICS_CANDIDATE, SERVING}
        return _declaration(name, UES)

    return admission.prepare_image_only_staging(
        service=target["service_name"],
        project="longbridgequant",
        region="asia-east2",
        service_json=service,
        env={"WORKFLOW_TARGET": "HK", "SOURCE_COMMIT": HK_PROBE_DIAGNOSTICS_CANDIDATE, **(input_overrides or {})},
        image_commit=HK_PROBE_DIAGNOSTICS_CANDIDATE,
        run=run,
    )


def test_hk_probe_diagnostics_candidate_stages_only_with_exact_target_disabled():
    plan = _prepare_hk_probe_diagnostics()
    assert plan["image_commit"] == HK_PROBE_DIAGNOSTICS_CANDIDATE
    assert plan["history_arg"] == plan["snapshot_arg"] == ""
    assert plan["history_values"] == plan["retained_history_values"] == {}
    assert plan["serving_traffic"] == [{"revisionName": "serving-rev", "percent": 100}]


@pytest.mark.parametrize(
    ("target_label", "enabled", "extra_env"),
    [
        ("SG", "false", None),
        ("HK", "true", None),
    ],
)
def test_hk_probe_diagnostics_candidate_rejects_wrong_target_or_mutable_settings(target_label, enabled, extra_env):
    with pytest.raises(admission.AdmissionError):
        _prepare_hk_probe_diagnostics(target_label=target_label, enabled=enabled, extra_env=extra_env)


@pytest.mark.parametrize(
    "input_overrides",
    [
        {"ACCOUNT_HISTORY_RECORDING_ENABLED": "true"},
        {"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false"},
    ],
)
def test_hk_probe_diagnostics_candidate_rejects_history_or_snapshot_inputs(input_overrides):
    with pytest.raises(admission.AdmissionError):
        _prepare_hk_probe_diagnostics(input_overrides=input_overrides)


def test_hk_probe_diagnostics_candidate_does_not_admit_arbitrary_sha():
    with pytest.raises(admission.AdmissionError, match="not approved"):
        admission._validate_image_only_source(
            image_commit="c" * 40,
            env={"WORKFLOW_TARGET": "HK"},
            project="longbridgequant",
        )


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


def _http_snapshot_service(*, history: dict[str, str] | None = None) -> dict:
    service = _service()
    template = service["spec"]["template"]
    template["metadata"] = {"name": STAGED_SOURCE_REVISION}
    container = template["spec"]["containers"][0]
    container["image"] = STAGED_SOURCE_IMAGE
    if history is not None:
        container["env"].extend({"name": key, "value": value} for key, value in history.items())
    service["status"]["latestCreatedRevisionName"] = STAGED_SOURCE_REVISION
    return service


def _http_snapshot_run(service: dict, *, staged_mutation=None):
    serving = _revision("serving-rev", SERVING, "serving-image")
    staged = _revision(
        STAGED_SOURCE_REVISION,
        CANDIDATE,
        STAGED_SOURCE_IMAGE,
        extra_env=[{"name": key, "value": value} for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"],
    )
    if staged_mutation:
        staged_mutation(staged)

    def run(command):
        if command[:3] == ["gcloud", "run", "revisions"]:
            name = command[4]
            if name == STAGED_SOURCE_REVISION:
                return json.dumps(staged)
            assert name == "serving-rev"
            return json.dumps(serving)
        if command[:2] == ["git", "show"]:
            sha, name = command[2].split(":", 1)
            assert sha in {HTTP_SNAPSHOT_CANDIDATE, SERVING}
            return _declaration(name, UES)
        raise AssertionError(command)

    return run


def _prepare_http_snapshot(*, service=None, env=None, staged_mutation=None):
    service = service or _http_snapshot_service(history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"})
    return admission.prepare_image_only_staging(
        service="paper-service",
        project="synthetic-project",
        region="synthetic-region",
        service_json=service,
        env=env or {**_main_env(), "SOURCE_COMMIT": HTTP_SNAPSHOT_CANDIDATE},
        image_commit=HTTP_SNAPSHOT_CANDIDATE,
        run=_http_snapshot_run(service, staged_mutation=staged_mutation),
    )


def test_http_snapshot_candidate_retains_exact_staged_history_and_digest():
    service = _http_snapshot_service(history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"})
    env = {**_main_env(snapshot_setting="true"), "SOURCE_COMMIT": HTTP_SNAPSHOT_CANDIDATE}
    plan = _prepare_http_snapshot(service=service, env=env)

    assert plan["image_commit"] == HTTP_SNAPSHOT_CANDIDATE
    assert plan["history_arg"] == ""
    assert plan["history_values"] == {}
    assert plan["retained_history_values"] == {
        key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"
    }
    assert plan["snapshot_arg"] == "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true"
    assert plan["template_digest"] == admission._config_digest(
        admission._configuration(service), skip={admission._ACCOUNT_SNAPSHOT_ENV}
    )


def test_http_snapshot_uses_strict_serving_match_when_template_already_matches():
    service = _service()
    calls = []

    def run(command):
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        sha, name = command[2].split(":", 1)
        assert sha in {HTTP_SNAPSHOT_CANDIDATE, SERVING}
        return _declaration(name, UES)

    plan = admission.prepare_image_only_staging(
        service="paper-service", project="synthetic-project", region="synthetic-region",
        service_json=service,
        env={**_main_env(), "SOURCE_COMMIT": HTTP_SNAPSHOT_CANDIDATE},
        image_commit=HTTP_SNAPSHOT_CANDIDATE,
        run=run,
    )
    assert plan["retained_history_values"] == {}
    assert not any(
        command[:4] == ["gcloud", "run", "revisions", "describe"]
        and command[4] == STAGED_SOURCE_REVISION
        for command in calls
    )


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"WORKFLOW_TARGET": "HK"},
        {"WORKFLOW_TARGET": "SG"},
        {"SOURCE_COMMIT": CANDIDATE},
        {"SOURCE_COMMIT": "c" * 40},
        {key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"},
    ],
)
def test_http_snapshot_source_requires_exact_candidate_paper_and_no_history(env_overrides):
    env = {**_main_env(), "SOURCE_COMMIT": HTTP_SNAPSHOT_CANDIDATE}
    env.update(env_overrides)
    calls = []
    with pytest.raises(admission.AdmissionError, match="not approved"):
        admission.prepare_image_only_staging(
            service="paper-service", project="synthetic-project", region="synthetic-region",
            service_json={}, env=env, image_commit=env["SOURCE_COMMIT"],
            run=lambda command: calls.append(command) or "",
        )
    assert calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda revision: revision["metadata"].update(name="wrong-revision"),
        lambda revision: revision["metadata"]["labels"].update({"commit-sha": "f" * 40}),
        lambda revision: revision["spec"]["containers"][0].update(
            image="repo@sha256:" + "f" * 64
        ),
    ],
)
def test_http_snapshot_rejects_wrong_staged_revision_commit_or_digest(mutation):
    with pytest.raises(admission.AdmissionError):
        _prepare_http_snapshot(staged_mutation=mutation)


def test_http_snapshot_accepts_ready_staged_revision_when_not_active():
    def mark_inactive(revision):
        revision["status"]["conditions"].extend(
            [
                {"type": "Active", "status": "False"},
                {"type": "Retired", "status": "True"},
            ]
        )

    assert _prepare_http_snapshot(staged_mutation=mark_inactive)["history_arg"] == ""


def test_http_snapshot_rejects_template_that_does_not_match_staged_source():
    wrong_revision = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    wrong_revision["status"]["latestCreatedRevisionName"] = "other-revision"
    with pytest.raises(admission.AdmissionError, match="template does not match"):
        _prepare_http_snapshot(service=wrong_revision)

    wrong_image = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    wrong_image["spec"]["template"]["spec"]["containers"][0]["image"] = "repo@sha256:" + "f" * 64
    with pytest.raises(admission.AdmissionError, match="template does not match"):
        _prepare_http_snapshot(service=wrong_image)

    wrong_template_name = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    wrong_template_name["spec"]["template"]["metadata"]["name"] = "other-revision"
    with pytest.raises(admission.AdmissionError, match="template does not match"):
        _prepare_http_snapshot(service=wrong_template_name)


def test_http_snapshot_rejects_history_prefix_that_differs_from_immutable_revision():
    service = _http_snapshot_service(
        history={**{key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"},
                 "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-bucket/other"}
    )
    with pytest.raises(admission.AdmissionError, match="template does not match the staged source"):
        _prepare_http_snapshot(service=service)


@pytest.mark.parametrize("case", ["missing", "secret"])
def test_http_snapshot_requires_four_literal_retained_history_values(case):
    service = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    if case == "missing":
        service["spec"]["template"]["spec"]["containers"][0]["env"] = [
            item for item in service["spec"]["template"]["spec"]["containers"][0]["env"]
            if item["name"] != "ACCOUNT_HISTORY_TARGET_ID"
        ]
    else:
        entry = next(
            item for item in service["spec"]["template"]["spec"]["containers"][0]["env"]
            if item["name"] == "ACCOUNT_HISTORY_GCS_PREFIX"
        )
        entry.pop("value")
        entry["valueFrom"] = {"secretKeyRef": {"name": "history-config", "key": "prefix"}}
    with pytest.raises(admission.AdmissionError):
        _prepare_http_snapshot(service=service)


def test_http_snapshot_rejects_nonhistory_environment_and_service_identity_drift():
    service = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    service["spec"]["template"]["spec"]["serviceAccountName"] = "other@example.invalid"
    with pytest.raises(admission.AdmissionError):
        _prepare_http_snapshot(service=service)

    service = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    target = json.loads(service["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"])
    target["service_name"] = "other-service"
    service["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = json.dumps(target)
    with pytest.raises(admission.AdmissionError, match="does not match the deployed service"):
        _prepare_http_snapshot(service=service)


def test_http_snapshot_readback_preserves_retained_history_values():
    service = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    plan = _prepare_http_snapshot(service=service)
    image = "repo@sha256:" + "b" * 64
    history_env = [
        {"name": key, "value": value}
        for key, value in HISTORY.items()
        if key != "WORKFLOW_TARGET"
    ]
    staged = _revision(
        "paper-service-r123", HTTP_SNAPSHOT_CANDIDATE, image,
        extra_env=history_env + [{"name": admission._ACCOUNT_SNAPSHOT_ENV, "value": "false"}],
    )
    plan["snapshot_value"] = "false"

    def confirm(revision):
        def run(command):
            if command[:3] == ["gcloud", "run", "services"]:
                return json.dumps(service)
            return json.dumps(revision)
        admission.confirm_image_only_readback(
            service="paper-service", project="synthetic-project", region="synthetic-region",
            plan=plan, expected_image=image, expected_commit=HTTP_SNAPSHOT_CANDIDATE,
            expected_revision="paper-service-r123", run=run,
        )

    confirm(staged)
    for key in admission._HISTORY_KEYS:
        changed = json.loads(json.dumps(staged))
        entry = next(item for item in changed["spec"]["containers"][0]["env"] if item["name"] == key)
        entry["value"] = "gs://paper-bucket/tampered" if key.endswith("GCS_PREFIX") else "false"
        with pytest.raises(admission.AdmissionError):
            confirm(changed)


def test_probe_snapshot_candidate_uses_strict_serving_configuration():
    service = _service()
    calls = []

    def run(command):
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        sha, name = command[2].split(":", 1)
        assert sha in {PROBE_SNAPSHOT_CANDIDATE, SERVING}
        return _declaration(name, UES)

    plan = admission.prepare_image_only_staging(
        service="paper-service", project="synthetic-project", region="synthetic-region",
        service_json=service,
        env={**_main_env(), "SOURCE_COMMIT": PROBE_SNAPSHOT_CANDIDATE},
        image_commit=PROBE_SNAPSHOT_CANDIDATE,
        run=run,
    )
    assert plan["image_commit"] == PROBE_SNAPSHOT_CANDIDATE
    assert plan["history_arg"] == plan["snapshot_arg"] == ""
    assert plan["history_values"] == plan["retained_history_values"] == {}
    assert plan["template_digest"] == admission._config_digest(admission._configuration(service))
    assert not any(
        command[:4] == ["gcloud", "run", "revisions", "describe"]
        and command[4] == STAGED_SOURCE_REVISION
        for command in calls
    )


@pytest.mark.parametrize(
    "env_overrides",
    [
        {"WORKFLOW_TARGET": "HK"},
        {"WORKFLOW_TARGET": "SG"},
        {"SOURCE_COMMIT": "c" * 40},
        {key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"},
        {"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "true"},
        {"ACCOUNT_SNAPSHOT_ENABLED_INPUT": "false"},
    ],
)
def test_probe_snapshot_source_requires_exact_candidate_paper_and_empty_settings(env_overrides):
    env = {**_main_env(), "SOURCE_COMMIT": PROBE_SNAPSHOT_CANDIDATE}
    env.update(env_overrides)
    calls = []
    with pytest.raises(admission.AdmissionError, match="not approved"):
        admission.prepare_image_only_staging(
            service="paper-service", project="synthetic-project", region="synthetic-region",
            service_json={}, env=env, image_commit=env["SOURCE_COMMIT"],
            run=lambda command: calls.append(command) or "",
        )
    assert calls == []


def test_probe_snapshot_candidate_cannot_use_http_staged_template_exception():
    service = _http_snapshot_service(
        history={key: value for key, value in HISTORY.items() if key != "WORKFLOW_TARGET"}
    )
    calls = []
    def run(command):
        calls.append(list(command))
        if command[:3] == ["gcloud", "run", "revisions"]:
            assert command[4] == "serving-rev"
            return json.dumps(_revision("serving-rev", SERVING, "serving-image"))
        raise AssertionError(command)

    with pytest.raises(admission.AdmissionError, match="template does not match"):
        admission.prepare_image_only_staging(
            service="paper-service", project="synthetic-project", region="synthetic-region",
            service_json=service,
            env={**_main_env(), "SOURCE_COMMIT": PROBE_SNAPSHOT_CANDIDATE},
            image_commit=PROBE_SNAPSHOT_CANDIDATE,
            run=run,
        )
    assert not any(
        command[:4] == ["gcloud", "run", "revisions", "describe"]
        and command[4] == STAGED_SOURCE_REVISION
        for command in calls
    )
