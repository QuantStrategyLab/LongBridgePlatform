import copy
import json

import pytest

from scripts import inspect_paper_serving_metadata as inspect
from test_inspect_paper_serving_metadata import fixtures


def setup_observation():
    env, service, revision, _, _ = fixtures()
    env["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"] = "inspector@example-project.iam.gserviceaccount.com"
    for resource in (service, {"spec": {"template": {"spec": revision["spec"]}}}):
        rows = resource["spec"]["template"]["spec"]["containers"][0]["env"]
        target = json.loads(rows[0]["value"])
        target["execution_environment"] = "paper"
        rows[0]["value"] = json.dumps(target)
    return env, service, revision


def reader(service, revision, *, change=False):
    calls = []

    def load(args):
        calls.append(args)
        assert args[:4] in (["gcloud", "run", "services", "describe"],
                            ["gcloud", "run", "revisions", "describe"])
        result = copy.deepcopy(service if args[2] == "services" else revision)
        result["metadata"]["name"] = args[4]
        if change and len(calls) > 1:
            result["metadata"]["generation"] = len(calls)
        return result

    return load, calls


def test_config_only_uses_explicit_existing_account_and_projects_no_identifiers():
    env, service, revision = setup_observation()
    load, calls = reader(service, revision)
    result = inspect.observe_target_config(env=env, load=load)
    assert result["status"] == "observed"
    assert result["broker_environment"] == "paper"
    assert result["strategy_profile"] == "russell_top50_leader_rotation"
    assert result["service_stable"] is True
    assert result["positive_traffic_revision_count"] == 2
    assert result["image_execution_performed"] is False
    assert all("--account=" + env["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"] in c for c in calls)
    raw = json.dumps(result)
    assert all(v not in raw for v in (env["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"],
                                      "synthetic-service", "synthetic-private-binding", "example-project"))


@pytest.mark.parametrize("value", ["", "user@example.invalid", "bad\naccount"])
def test_missing_or_invalid_identity_blocks_before_any_read(value):
    env, service, revision = setup_observation()
    env["GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT"] = value
    load, calls = reader(service, revision)
    with pytest.raises(inspect.ObservationError):
        inspect.observe_target_config(env=env, load=load)
    assert calls == []


@pytest.mark.parametrize("field,value", [("execution_environment", None),
                                        ("execution_environment", "live"),
                                        ("strategy_profile", "soxl_soxx_vol_ret_dd")])
@pytest.mark.parametrize("location", ["service", "revision"])
def test_unknown_live_or_other_strategy_blocks(field, value, location):
    env, service, revision = setup_observation()
    spec = service["spec"]["template"]["spec"] if location == "service" else revision["spec"]
    rows = spec["containers"][0]["env"]
    target = json.loads(rows[0]["value"])
    target[field] = value
    rows[0]["value"] = json.dumps(target)
    load, _ = reader(service, revision)
    with pytest.raises(inspect.ObservationError):
        inspect.observe_target_config(env=env, load=load)


def test_concurrent_service_or_revision_change_is_not_an_attestation():
    env, service, revision = setup_observation()
    load, _ = reader(service, revision, change=True)
    with pytest.raises(inspect.ObservationError):
        inspect.observe_target_config(env=env, load=load)


def test_transport_failure_is_nonzero_and_diagnostic_is_not_disclosed(monkeypatch, capsys):
    def fail():
        raise RuntimeError("synthetic-private-binding")

    monkeypatch.setattr(inspect, "observe_target_config", fail)
    assert inspect.main(["--target-config"]) == 1
    output = capsys.readouterr().out
    assert "synthetic-private-binding" not in output
    assert json.loads(output)["status"] == "unknown"
