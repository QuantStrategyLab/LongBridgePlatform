import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import inspect_paper_serving_metadata as inspect


def fixtures():
    target = {"platform_id": "longbridge", "service_name": "synthetic-service", "account_scope": "PAPER",
              "account_selector": ["PAPER"], "deployment_selector": "PAPER",
              "strategy_profile": "russell_top50_leader_rotation", "execution_mode": "paper",
              "dry_run_only": False, "physical_account_id": "synthetic-private-binding",
              "strategy_release": {"strategy_revision": "a" * 40},
              "runtime_risk_limits": {"binding": {"ues_revision": "a" * 40}}}
    env_rows = [{"name": "RUNTIME_TARGET_JSON", "value": json.dumps(target)},
                {"name": "STRATEGY_PROFILE", "value": target["strategy_profile"]},
                {"name": "LONGBRIDGE_DRY_RUN_ONLY", "value": "false"},
                {"name": "RUNTIME_TARGET_ENABLED", "value": "false"}]
    service = {"metadata": {"name": "synthetic-service", "generation": 1},
               "spec": {"template": {"spec": {"containers": [{"env": env_rows}]}}},
               "status": {"traffic": [{"revisionName": "synthetic-service-first", "percent": 40},
                                       {"revisionName": "synthetic-service-second", "percent": 60}]}}
    image = "us-central1-docker.pkg.dev/example-project/images/private-name@sha256:" + "1" * 64
    revision = {"metadata": {"name": "synthetic-service-first", "labels": {"commit-sha": "b" * 40}},
                "spec": {"containers": [{"env": env_rows, "image": image}]},
                "status": {"imageDigest": image}}
    payload = {"packages": {name: {"present": True, "version": "0.7.60", "commit": "a" * 40}
                            for name in inspect.PACKAGES},
               "lock_sha256": hashlib.sha256(b"synthetic-lock").hexdigest(),
               "lock_commits": {name: "a" * 40 for name in inspect.PACKAGES}}
    environment = {"WORKFLOW_TARGET": "PAPER", "GCP_PROJECT_ID": "example-project",
                   "CLOUD_RUN_SERVICE": "synthetic-service", "CLOUD_RUN_REGION": "us-central1",
                   "EXPECTED_RUNTIME_TARGET_JSON": json.dumps(target), "GITHUB_SHA": "c" * 40}
    return environment, service, revision, image, payload


def fake_io(service, revision, image, payload, *, mutation=None):
    calls = []
    service_reads = 0
    revision_reads = {}

    def load(args, **kwargs):
        nonlocal service_reads
        calls.append(args)
        if args[:4] == ["gcloud", "run", "services", "describe"]:
            service_reads += 1
            result = copy.deepcopy(service)
            if service_reads == 2 and mutation == "service":
                result["metadata"]["generation"] = 2
            return result
        if args[:4] == ["gcloud", "run", "revisions", "describe"]:
            result = copy.deepcopy(revision)
            result["metadata"]["name"] = args[4]
            revision_reads[args[4]] = revision_reads.get(args[4], 0) + 1
            if revision_reads[args[4]] == 2 and mutation == "revision":
                result["metadata"]["labels"]["commit-sha"] = "f" * 40
            return result
        if args[:3] == ["docker", "image", "inspect"]:
            return [{"RepoDigests": [image], "Config": {"Labels": {"org.opencontainers.image.revision": "b" * 40}}}]
        if args[:2] == ["docker", "run"]:
            return copy.deepcopy(payload)
        raise AssertionError(args)

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "show"]:
            return b"synthetic-lock"
        assert args[:2] in (["gcloud", "auth"], ["docker", "pull"])
        return b""

    return run, load, calls


def test_observes_all_serving_revisions_and_never_discloses_resource_identity():
    env, service, revision, image, payload = fixtures()
    run, load, calls = fake_io(service, revision, image, payload)
    result = inspect.observe(env=env, run=run, load=load)
    assert result["status"] == "observed"
    assert [r["traffic_percent"] for r in result["revisions"]] == [40, 60]
    assert all(r["installed_ues_equals_release"] is True for r in result["revisions"])
    assert all(r["image_lock_equals_source_lock"] is True for r in result["revisions"])
    assert all(r["target"]["runtime_target_enabled"] is False for r in result["revisions"])
    assert result["template"]["expected_target_comparisons"]["physical_account_id_matches_expected"] is True
    raw = json.dumps(result)
    assert all(secret not in raw for secret in (image, "1" * 64, "synthetic-service", "synthetic-private-binding", "example-project"))
    probes = [c for c in calls if c[:2] == ["docker", "run"]]
    assert len(probes) == 2
    for command in probes:
        assert all(flag in command for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "-I", "-S"))
        assert command[command.index("--entrypoint=/usr/local/bin/python") + 1] == image
    assert all("update" not in c and "curl" not in c and "secrets" not in c for c in calls)


@pytest.mark.parametrize("mutation", ["service", "revision"])
def test_changed_metadata_is_unknown_not_a_consistent_snapshot(mutation):
    env, service, revision, image, payload = fixtures()
    run, load, _ = fake_io(service, revision, image, payload, mutation=mutation)
    with pytest.raises(inspect.ObservationError, match="changed_during_observation"):
        inspect.observe(env=env, run=run, load=load)


@pytest.mark.parametrize("rows", [[], [{"revisionName": "synthetic", "percent": 99}],
                                  [{"revisionName": "synthetic", "percent": True}],
                                  [{"revisionName": "synthetic", "percent": 50}] * 2])
def test_missing_incomplete_or_ambiguous_traffic_never_means_all_revisions(rows):
    with pytest.raises(inspect.ObservationError, match="traffic_unavailable"):
        inspect.traffic_rows({"status": {"traffic": rows}})


def test_missing_install_or_binding_remains_unknown():
    env, service, revision, image, payload = fixtures()
    payload["packages"]["us-equity-strategies"]["commit"] = None
    payload["lock_commits"] = {}
    payload["lock_sha256"] = None
    env["EXPECTED_RUNTIME_TARGET_JSON"] = ""
    run, load, _ = fake_io(service, revision, image, payload)
    result = inspect.observe(env=env, run=run, load=load)
    row = result["revisions"][0]
    assert row["installed_ues_equals_release"] is None
    assert row["image_lock_equals_source_lock"] is None
    assert row["target"]["independent_approval_verified"] is False
    assert all(v is None for v in row["target"]["expected_target_comparisons"].values())


def test_mutable_image_is_rejected_before_docker():
    env, service, revision, image, payload = fixtures()
    revision["status"]["imageDigest"] = "mutable:latest"
    run, load, calls = fake_io(service, revision, image, payload)
    with pytest.raises(inspect.ObservationError, match="immutable_image_unavailable"):
        inspect.observe(env=env, run=run, load=load)
    assert not any(c[0] == "docker" for c in calls)


def test_stdout_contains_only_fixed_reason_on_external_exception(monkeypatch, capsys):
    def fail():
        raise RuntimeError("synthetic-private-account-token")
    monkeypatch.setattr(inspect, "observe", fail)
    assert inspect.main([]) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "observation_unavailable"


def test_probe_runs_with_isolated_stdlib_python():
    result = subprocess.run([sys.executable, "-I", "-S", "-c", inspect.IMAGE_PROBE],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert set(inspect.project_install(json.loads(result.stdout))["packages"]) == set(inspect.PACKAGES)


def test_cloud_denial_remains_distinct_without_rendering_stderr(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a, 1, b"", b"PERMISSION_DENIED: synthetic-private-account"))
    with pytest.raises(inspect.ObservationError, match="^external_read_denied$"):
        inspect.read_json(["gcloud", "run", "services", "describe"])


def test_workflow_reuses_paper_environment_and_identity_without_write_actions():
    workflow = Path(".github/workflows/inspect-paper-serving-metadata.yml").read_text()
    deployment = Path(".github/workflows/sync-cloud-run-env.yml").read_text()
    assert "workflow_dispatch:" in workflow and "schedule:" not in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "environment: longbridge-paper" in workflow
    assert "longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com" in workflow
    assert "longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com" in deployment
    assert "Deploy Cloud Run-${{ github.ref_name }}" in workflow
    assert workflow.index("Validate existing PAPER selection") < workflow.index("google-github-actions/auth")
    assert all(token not in workflow for token in ("upload-artifact", "update-traffic", "scheduler", "curl", "secret-access"))


def source_chain_io(*, registry_error=None, audit_error=None, changed=False, audit_rows=None):
    env, service, revision, image, _ = fixtures()
    revision["metadata"].update(creationTimestamp="2026-10-30T12:00:00Z")
    revision["metadata"]["labels"].update({"github-run-id": "123456", "qsl-overlay-commit": "d" * 40,
                                           "qsl-image-source-kind": "notification-overlay"})
    calls = []
    reads = {}

    def load(args, **kwargs):
        calls.append(args)
        assert "docker" != args[0], "source-chain must never pull or execute an image"
        if args[:4] == ["gcloud", "run", "services", "describe"]:
            return copy.deepcopy(service)
        if args[:4] == ["gcloud", "run", "revisions", "describe"]:
            result = copy.deepcopy(revision)
            result["metadata"]["name"] = args[4]
            reads[args[4]] = reads.get(args[4], 0) + 1
            if changed and reads[args[4]] == 2:
                result["metadata"]["labels"]["commit-sha"] = "f" * 40
            return result
        if args[:5] == ["gcloud", "artifacts", "docker", "images", "describe"]:
            if registry_error:
                raise inspect.ObservationError(registry_error)
            return {"provenance_summary": {"provenance": [{"synthetic-private-subject": image}]},
                    "build_details_summary": {"build_details": []}}
        if args[:3] == ["gcloud", "logging", "read"]:
            if audit_error:
                raise inspect.ObservationError(audit_error)
            assert "--limit=51" in args and "--order=asc" in args
            query = args[3]
            assert "synthetic-service" in query and 'timestamp>="2026-10-30T11:45:00+00:00"' in query
            return [] if audit_rows is None else copy.deepcopy(audit_rows)
        raise AssertionError(args)
    return env, load, calls


def test_source_chain_reads_existing_records_without_reprobing_image_or_exporting_subject():
    env, load, calls = source_chain_io()
    result = inspect.observe_source_chain(env=env, load=load)
    assert result["image_execution_performed"] is False
    assert [r["traffic_percent"] for r in result["revisions"]] == [40, 60]
    row = result["revisions"][0]
    assert row["reported_github_run_id"] == "123456"
    assert row["overlay_source_commit"] == "d" * 40
    assert row["registry"]["provenance_present"] is True
    assert row["registry"]["build_details_present"] is False
    assert row["registry"]["subject_source_builder_verified"] is False
    assert row["creation_window_audit"]["records_returned"] == 0
    assert row["creation_window_audit"]["read_status"] == "read"
    assert result["independent_approval_verified"] is False
    text = json.dumps(result)
    assert all(token not in text for token in ("synthetic-private", "synthetic-service", "example-project", "1" * 64))


def test_registry_failure_does_not_hide_independent_audit_result():
    env, load, calls = source_chain_io(registry_error="external_read_denied")
    result = inspect.observe_source_chain(env=env, load=load)
    for row in result["revisions"]:
        assert row["registry"]["read_status"] == "external_read_denied"
        assert row["creation_window_audit"]["read_status"] == "read"
    assert len([c for c in calls if c[:3] == ["gcloud", "logging", "read"]]) == 2


def test_malformed_registry_does_not_hide_independent_audit_result():
    env, original, calls = source_chain_io()
    def load(args, **kwargs):
        if args[:5] == ["gcloud", "artifacts", "docker", "images", "describe"]:
            return None
        return original(args, **kwargs)
    result = inspect.observe_source_chain(env=env, load=load)
    for row in result["revisions"]:
        assert row["registry"]["read_status"] == "registry_metadata_invalid"
        assert row["creation_window_audit"]["read_status"] == "read"


def test_audit_failure_does_not_mean_empty_history():
    env, load, calls = source_chain_io(audit_error="external_read_unavailable")
    result = inspect.observe_source_chain(env=env, load=load)
    for row in result["revisions"]:
        assert row["creation_window_audit"]["read_status"] == "external_read_unavailable"
        assert "records_returned" not in row["creation_window_audit"]


def test_source_chain_rejects_revision_change_before_claiming_stability():
    env, load, calls = source_chain_io(changed=True)
    with pytest.raises(inspect.ObservationError, match="revision_changed_during_observation"):
        inspect.observe_source_chain(env=env, load=load)


def test_audit_projection_keeps_truncation_and_does_not_disclose_actor_or_body():
    _, _, revision, image, _ = fixtures()
    entry = {"timestamp": "2026-10-30T12:00:00Z", "protoPayload": {
        "methodName": "google.cloud.run.v1.Services.ReplaceService", "status": {"code": 0},
        "authenticationInfo": {"principalEmail": "synthetic-private-actor@example.invalid"},
        "resourceName": "synthetic-private-resource", "request": {"service": {
            "spec": {"template": {"metadata": revision["metadata"], "spec": revision["spec"]}}}}}}
    result = inspect.audit_projection([entry] * 51, name=revision["metadata"]["name"], digest=image, source="b" * 40)
    assert result["truncated"] is True and len(result["records"]) == 50
    assert result["records"][0]["request_image_equals_observed_digest"] is True
    assert result["records"][0]["source_equals_revision_label"] is True
    assert result["original_approval_verified"] is False and result["all_history_verified"] is False
    text = json.dumps(result)
    assert all(value not in text for value in ("synthetic-private", image, "synthetic-service"))


@pytest.mark.parametrize("value", [None, {}, {"provenance_summary": {"provenance": "invalid"}}])
def test_missing_registry_shape_is_not_authenticated_provenance(value):
    if value is None:
        with pytest.raises(inspect.ObservationError, match="registry_metadata_invalid"):
            inspect.registry_projection(value)
    else:
        row = inspect.registry_projection(value)
        assert row["provenance_shape_valid"] is False
        assert row["provenance_present"] is None


@pytest.mark.parametrize("value", ["", "0", "-1", "123abc", "9" * 21, None])
def test_invalid_deployment_run_label_stays_unknown(value):
    assert inspect._run_locator(value) is None
