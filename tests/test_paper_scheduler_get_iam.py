"""Offline adverse tests: no cloud clients, tokens or real identifiers."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("paper_iam", Path(__file__).parents[1] / "scripts/paper_scheduler_get_iam.py")
# A missing implementation is the expected fail-before result.
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

PROJECT = "fake-paper-project"
READER = "grok-gcp-ro@fake-reader-project.iam.gserviceaccount.com"
ENV = {
    "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
    "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
    "IAM_ENVIRONMENT": "longbridge-paper", "IAM_CONFIRM": "APPLY_PAPER_SCHEDULER_JOBS_GET",
    "IAM_MODE": "apply", "GCP_PROJECT_ID": PROJECT, "PAPER_SCHEDULER_GET_PROJECT_ID": PROJECT,
    "PAPER_SCHEDULER_GET_READER_EMAIL": READER,
}


class Cloud:
    def __init__(self, role=None):
        self.role = role
        self.policy = {"version": 3, "etag": "fake-etag", "bindings": [
            {"role": "roles/viewer", "members": ["serviceAccount:fake@fake.iam.gserviceaccount.com"]}
        ], "auditConfigs": [{"service": "allServices"}]}
        self.calls = []
        self.policy_reads = 0
        self.drift = False
        self.ambiguous = False

    def __call__(self, method, url, body=None, missing=False):
        self.calls.append((method, url, copy.deepcopy(body)))
        if url.endswith(":getIamPolicy"):
            self.policy_reads += 1
            if self.drift and self.policy_reads > 1:
                self.policy["etag"] = "concurrent-writer"
            return copy.deepcopy(self.policy)
        if url.endswith(":setIamPolicy"):
            if self.ambiguous:
                raise m.Blocked("remote_result_unknown")
            assert body["updateMask"] == "bindings,etag"
            assert body["policy"]["etag"] == self.policy["etag"]
            self.policy["bindings"] = copy.deepcopy(body["policy"]["bindings"])
            return copy.deepcopy(self.policy)
        if method == "POST":
            assert body["roleId"] == m.ROLE_ID
            self.role = {**body["role"], "name": f"projects/{PROJECT}/roles/{m.ROLE_ID}", "etag": "fake-role-etag"}
            return copy.deepcopy(self.role)
        return copy.deepcopy(self.role)


def exact_role():
    return {"name": f"projects/{PROJECT}/roles/{m.ROLE_ID}", "stage": "GA", "includedPermissions": [m.PERMISSION], "etag": "fake-role-etag"}


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REF", "refs/heads/other"), ("GITHUB_RUN_ATTEMPT", "2"),
    ("GITHUB_REPOSITORY", "fake/fork"), ("GITHUB_EVENT_NAME", "push"),
    ("IAM_ENVIRONMENT", "longbridge-hk"), ("IAM_CONFIRM", "wrong"),
    ("IAM_MODE", "unknown"), ("GCP_PROJECT_ID", "fake-live-project"),
    ("PAPER_SCHEDULER_GET_PROJECT_ID", ""), ("PAPER_SCHEDULER_GET_READER_EMAIL", "other@fake.iam.gserviceaccount.com"),
])
def test_invalid_authority_blocks_before_network(key, value):
    cloud = Cloud()
    with pytest.raises(m.Blocked):
        m.execute({**ENV, key: value}, cloud)
    assert cloud.calls == []


@pytest.mark.parametrize("permissions", [["cloudscheduler.jobs.run"], [m.PERMISSION, "storage.objects.get"], []])
def test_role_collision_never_adopted(permissions):
    role = {**exact_role(), "includedPermissions": permissions}
    cloud = Cloud(role)
    with pytest.raises(m.Blocked):
        m.execute(ENV, cloud)
    assert all(c[0] == "GET" for c in cloud.calls)


def test_dry_run_is_read_only_and_default():
    cloud = Cloud()
    env = {k: v for k, v in ENV.items() if k != "IAM_MODE"}
    m.execute(env, cloud)
    assert cloud.role is None
    assert not any(c[1].endswith(":setIamPolicy") or (c[0] == "POST" and not c[1].endswith(":getIamPolicy")) for c in cloud.calls)


def test_additive_cas_preserves_unrelated_and_is_idempotent():
    cloud = Cloud()
    before = copy.deepcopy(cloud.policy)
    m.execute(ENV, cloud)
    assert cloud.policy["bindings"][0] == before["bindings"][0]
    assert cloud.policy["auditConfigs"] == before["auditConfigs"]
    assert cloud.policy["bindings"][1] == {"role": exact_role()["name"], "members": [f"serviceAccount:{READER}"]}
    count = len([c for c in cloud.calls if c[1].endswith(":setIamPolicy")])
    m.execute(ENV, cloud)
    assert len([c for c in cloud.calls if c[1].endswith(":setIamPolicy")]) == count


def test_concurrent_policy_change_stops_before_binding():
    cloud = Cloud(exact_role())
    cloud.drift = True
    with pytest.raises(m.Blocked):
        m.execute(ENV, cloud)
    assert not any(c[1].endswith(":setIamPolicy") for c in cloud.calls)


def test_ambiguous_write_is_not_retried():
    cloud = Cloud(exact_role())
    cloud.ambiguous = True
    with pytest.raises(m.Blocked):
        m.execute(ENV, cloud)
    assert len([c for c in cloud.calls if c[1].endswith(":setIamPolicy")]) == 1


@pytest.mark.parametrize("mutation", ["no-etag", "conditional", "duplicate", "extra-member"])
def test_invalid_policy_fails_closed(mutation):
    cloud = Cloud(exact_role())
    target = {"role": exact_role()["name"], "members": [f"serviceAccount:{READER}"]}
    if mutation == "no-etag":
        cloud.policy.pop("etag")
    else:
        cloud.policy["bindings"].append(target)
        if mutation == "conditional":
            target["condition"] = {"expression": "true"}
        if mutation == "duplicate":
            cloud.policy["bindings"].append(copy.deepcopy(target))
        if mutation == "extra-member":
            target["members"].append("serviceAccount:fake-other@fake.iam.gserviceaccount.com")
    with pytest.raises(m.Blocked):
        m.execute(ENV, cloud)
    assert not any(c[1].endswith(":setIamPolicy") for c in cloud.calls)


def test_template_permission_drift_blocks(tmp_path):
    path = tmp_path / "role.json"
    path.write_text(json.dumps({"includedPermissions": ["secretmanager.versions.access"]}))
    with pytest.raises(m.Blocked):
        m.role_definition(path)


def test_remote_failure_never_prints_private_data(monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    def fail():
        raise RuntimeError("fake-private-token fake@fake.invalid")
    monkeypatch.setattr(m, "Cloud", fail)
    assert m.main() == 1
    output = capsys.readouterr().out
    assert "fake-private" not in output and "fake@" not in output
    assert output.startswith("BLOCKED:")


def test_role_drift_after_create_blocks_binding():
    cloud = Cloud()
    original = cloud.__call__
    def request(method, url, body=None, missing=False):
        value = original(method, url, body, missing)
        if url.endswith(":getIamPolicy") and cloud.policy_reads == 3:
            cloud.role["includedPermissions"].append("cloudscheduler.jobs.run")
        return value
    with pytest.raises(m.Blocked):
        m.execute(ENV, request)
    assert not any(c[1].endswith(":setIamPolicy") for c in cloud.calls)


def test_transport_does_not_retry_or_follow_redirects(monkeypatch):
    calls = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": b"fake-token"})())
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise TimeoutError("fake-private-diagnostic")
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda handler: Opener())
    with pytest.raises(m.Blocked, match="remote_result_unknown"):
        m.Cloud()("POST", "https://fake.invalid/test", {})
    assert len(calls) == 1


def test_workflow_boundaries():
    text = (Path(__file__).parents[1] / ".github/workflows/apply-paper-scheduler-jobs-get.yml").read_text()
    for gate in ["workflow_dispatch:", "github.ref == 'refs/heads/main'", "github.run_attempt == 1",
                 "github.repository == 'QuantStrategyLab/LongBridgePlatform'", "environment: longbridge-paper",
                 "cancel-in-progress: false", "default: dry-run", "APPLY_PAPER_SCHEDULER_JOBS_GET"]:
        assert gate in text
    assert "secrets." not in text and "longbridge-hk" not in text
    assert "gcloud " not in text
