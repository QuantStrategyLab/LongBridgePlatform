"""Fake-only contract and adverse tests for the bootstrap grant definition."""
import copy
from pathlib import Path

import pytest

from scripts import paper_executor_grant as m
from scripts.paper_scheduler_get_iam import Blocked

PROJECT = "fake-paper-project"
EXECUTOR = f"executor@{PROJECT}.iam.gserviceaccount.com"
ENV = {
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
    "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
    "IAM_CONFIRM": "GRANT_PAPER_EXECUTOR_FOUR_PERMISSIONS",
    "IAM_MODE": "apply", "GCP_PROJECT_ID": PROJECT,
    "PAPER_SCHEDULER_GET_PROJECT_ID": PROJECT,
    "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT": EXECUTOR,
    "PAPER_IAM_GRANT_RECIPIENT": EXECUTOR,
    "PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT": f"bootstrap@{PROJECT}.iam.gserviceaccount.com",
    "IAM_APPROVED_REVISION": "a" * 40, "GITHUB_SHA": "a" * 40,
    "IAM_OWNER_RUN_CARD": "fake-owner-run-card",
}


class Cloud:
    def __init__(self):
        self.role = None
        self.policy = {"version": 3, "etag": "fake-etag", "bindings": [], "auditConfigs": []}
        self.calls = []
        self.reads = 0
        self.drift = False
        self.ambiguous = False

    def __call__(self, method, url, body=None, missing=False):
        self.calls.append((method, url, copy.deepcopy(body)))
        if url.endswith(":testIamPermissions"):
            return {"permissions": list(m.PERMISSIONS)}
        if url.endswith(":getIamPolicy"):
            self.reads += 1
            if self.drift and self.reads > 1:
                self.policy["etag"] = "other-writer"
            return copy.deepcopy(self.policy)
        if url.endswith(":setIamPolicy"):
            if self.ambiguous:
                raise Blocked("unknown")
            assert body["policy"]["etag"] == self.policy["etag"]
            assert body["updateMask"] == "bindings,etag"
            self.policy["bindings"] = body["policy"]["bindings"]
            self.policy["etag"] = "next-etag"
            return copy.deepcopy(self.policy)
        if method == "GET":
            return copy.deepcopy(self.role)
        self.role = {**body["role"], "name": f"projects/{PROJECT}/roles/{m.ROLE_ID}", "etag": "role-etag"}
        return copy.deepcopy(self.role)


def writes(cloud):
    return [c for c in cloud.calls if c[0] == "POST" and not c[1].endswith((":getIamPolicy", ":testIamPermissions"))]


@pytest.mark.parametrize("key,value", [
    ("IAM_MODE", "unknown"), ("IAM_MODE", ""), ("IAM_OWNER_RUN_CARD", ""),
    ("IAM_APPROVED_REVISION", "b" * 40), ("GITHUB_RUN_ATTEMPT", "2"),
    ("PAPER_IAM_GRANT_RECIPIENT", "other@fake-project.iam.gserviceaccount.com"),
    ("PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT", EXECUTOR),
    ("PAPER_SCHEDULER_GET_PROJECT_ID", "wrong-project"),
])
def test_scope_rejected_before_network(key, value):
    cloud = Cloud()
    with pytest.raises(Blocked):
        m.execute({**ENV, key: value}, cloud)
    assert not cloud.calls


def test_dry_run_zero_writes():
    cloud = Cloud()
    m.execute({**ENV, "IAM_MODE": "dry-run"}, cloud)
    assert not writes(cloud)


def test_exact_delta_and_restart_no_duplicate_write():
    cloud = Cloud()
    before = copy.deepcopy(cloud.policy)
    m.execute(ENV, cloud)
    assert cloud.role["includedPermissions"] == list(m.PERMISSIONS)
    assert cloud.policy["auditConfigs"] == before["auditConfigs"]
    assert cloud.policy["bindings"] == [{"role": cloud.role["name"], "members": [f"serviceAccount:{EXECUTOR}"]}]
    assert len(writes(cloud)) == 2
    m.execute(ENV, cloud)
    assert len(writes(cloud)) == 2


def test_cross_process_drift_blocks_before_write():
    cloud = Cloud()
    cloud.drift = True
    with pytest.raises(Blocked):
        m.execute(ENV, cloud)
    assert not writes(cloud)


def test_ambiguous_write_never_retried():
    cloud = Cloud()
    cloud.ambiguous = True
    with pytest.raises(Blocked):
        m.execute(ENV, cloud)
    assert len([c for c in writes(cloud) if c[1].endswith(":setIamPolicy")]) == 1


def test_permission_expansion_collision_blocks():
    cloud = Cloud()
    m.execute(ENV, cloud)
    cloud.role["includedPermissions"].append("iam.roles.update")
    cloud.calls.clear()
    with pytest.raises(Blocked):
        m.execute(ENV, cloud)
    assert not writes(cloud)


def test_missing_bootstrap_permission_blocks():
    cloud = Cloud()
    def request(method, url, body=None, missing=False):
        if url.endswith(":testIamPermissions"):
            return {"permissions": []}
        return cloud(method, url, body, missing)
    with pytest.raises(Blocked):
        m.execute(ENV, request)
    assert not writes(cloud)


def test_error_output_is_fixed(monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    def fail():
        raise RuntimeError("fake-private-value@example.invalid")
    monkeypatch.setattr(m, "Cloud", fail)
    assert m.main() == 1
    assert capsys.readouterr().out == "BLOCKED: executor_grant_failed_stop_no_retry\n"


def test_workflow_protection_contract():
    text = (Path(__file__).parents[1] / ".github/workflows/grant-paper-executor-permissions.yml").read_text()
    for gate in ["environment: longbridge-paper", "required_reviewers", "reviewers.length",
                 "default: dry-run", "cancel-in-progress: false", "paper-scheduler-jobs-get-iam",
                 "github.run_attempt == 1", "github.ref == 'refs/heads/main'",
                 "PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT", "PAPER_IAM_BOOTSTRAP_WIF_PROVIDER",
                 "persist-credentials: false"]:
        assert gate in text
    assert "secrets." not in text
