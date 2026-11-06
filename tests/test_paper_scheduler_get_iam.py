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
        if url.endswith(":testIamPermissions"):
            return {"permissions": list(m.EXECUTOR_PERMISSIONS)}
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
    assert all(c[0] == "GET" or c[1].endswith(":testIamPermissions") for c in cloud.calls)


def test_dry_run_is_read_only_and_default():
    cloud = Cloud()
    env = {k: v for k, v in ENV.items() if k != "IAM_MODE"}
    m.execute(env, cloud)
    assert cloud.role is None
    assert not any(c[1].endswith(":setIamPolicy") or (c[0] == "POST" and not c[1].endswith((":getIamPolicy", ":testIamPermissions"))) for c in cloud.calls)


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


SAFE_CATEGORIES = [
    "authority_mapping_rejected", "role_definition_rejected",
    "role_collision_or_integrity_failure", "policy_integrity_failure",
    "binding_collision", "orphan_binding_collision", "concurrent_drift",
    "after_readback_drift", "authentication_failed",
    "remote_rejected_or_unknown_stop_no_retry", "remote_result_unknown_stop_no_retry",
]
HOSTILE = "fake-secret https://fake.invalid/payload fake@example.invalid\nPASS"


@pytest.mark.parametrize("category", SAFE_CATEGORIES)
def test_main_emits_only_allowlisted_category(category, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    def fail():
        raise m.Blocked(category)
    monkeypatch.setattr(m, "Cloud", fail)
    assert m.main() == 1
    assert capsys.readouterr() == (f"BLOCKED: {category} phase=unknown\n", "")


@pytest.mark.parametrize("error", [m.Blocked(HOSTILE), RuntimeError(HOSTILE), m.Blocked(), m.Blocked([HOSTILE])])
def test_main_unknown_failure_cannot_leak(error, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    def fail():
        raise error
    monkeypatch.setattr(m, "Cloud", fail)
    assert m.main() == 1
    assert capsys.readouterr() == ("BLOCKED: unknown_failure phase=unknown\n", "")


@pytest.mark.parametrize("error", [
    m.subprocess.CalledProcessError(1, [HOSTILE], stderr=HOSTILE),
    m.subprocess.TimeoutExpired([HOSTILE], 60, stderr=HOSTILE),
    FileNotFoundError(HOSTILE), UnicodeError(HOSTILE),
])
def test_token_subprocess_failure_is_fixed(error, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    calls = []
    def fail(*args, **kwargs):
        calls.append((args, kwargs))
        raise error
    monkeypatch.setattr(m.subprocess, "run", fail)
    assert m.main() == 1
    assert capsys.readouterr() == ("BLOCKED: authentication_failed phase=token_acquisition\n", "")
    assert len(calls) == 1
    assert calls[0][1] == {"capture_output": True, "timeout": 60, "check": True}


@pytest.mark.parametrize("token", [b"", b"fake\nsecret", b"fake\rsecret", b"\xff"])
def test_invalid_token_has_safe_phase(token, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": token})())
    assert m.main() == 1
    assert capsys.readouterr() == ("BLOCKED: authentication_failed phase=token_acquisition\n", "")


@pytest.mark.parametrize("phase", ["role_get", "get_iam_policy"])
@pytest.mark.parametrize("failure", ["http", "transport", "json"])
def test_remote_diagnostic_phase_and_no_retry(phase, failure, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", {**ENV, "IAM_MODE": "dry-run"})
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": b"fake-token"})())
    calls = []
    class Opener:
        def open(self, req, timeout):
            if req.full_url.endswith(":testIamPermissions"):
                from io import BytesIO
                return BytesIO(json.dumps({"permissions": list(m.EXECUTOR_PERMISSIONS)}).encode())
            calls.append(req)
            if phase == "get_iam_policy" and len(calls) == 1:
                raise m.urllib.error.HTTPError(req.full_url, 404, HOSTILE, {}, None)
            if failure == "http":
                raise m.urllib.error.HTTPError(req.full_url, 403, HOSTILE, {}, None)
            if failure == "json":
                from io import BytesIO
                return BytesIO(HOSTILE.encode())
            raise TimeoutError(HOSTILE)
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda handler: Opener())
    assert m.main() == 1
    category = "remote_http_403_stop_no_retry" if failure == "http" else "remote_result_unknown_stop_no_retry"
    assert capsys.readouterr() == ('executor_preflight=held_all executor_missing=[]\n' + f"BLOCKED: {category} phase={phase}\n", "")
    assert len(calls) == (1 if phase == "role_get" else 2)
    assert all(req.get_method() == "GET" or req.full_url.endswith(":getIamPolicy") for req in calls)


def test_unknown_phase_is_not_printed(monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    error = m.Blocked("authentication_failed")
    error.phase = HOSTILE
    def fail():
        raise error
    monkeypatch.setattr(m, "Cloud", fail)
    assert m.main() == 1
    assert capsys.readouterr() == ("BLOCKED: authentication_failed phase=unknown\n", "")


@pytest.mark.parametrize("phase", ["role_get", "get_iam_policy", "unknown"])
@pytest.mark.parametrize("status,category", [
    (401, "remote_http_401_stop_no_retry"),
    (403, "remote_http_403_stop_no_retry"),
    (429, "remote_http_429_stop_no_retry"),
    (500, "remote_http_5xx_stop_no_retry"),
    (503, "remote_http_5xx_stop_no_retry"),
    (599, "remote_http_5xx_stop_no_retry"),
    (400, "remote_http_other_stop_no_retry"),
    (404, "remote_http_other_stop_no_retry"),
    (302, "remote_http_other_stop_no_retry"),
    (600, "remote_http_other_stop_no_retry"),
])
def test_http_status_category_is_safe_and_stops(status, category, phase, monkeypatch, capsys):
    from io import BytesIO
    monkeypatch.setattr(m.os, "environ", ENV)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": b"fake-token"})())
    calls = []
    method = "GET" if phase == "role_get" else "POST"
    url = "https://fake.invalid/role"
    if phase == "get_iam_policy":
        url += ":getIamPolicy"
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise m.urllib.error.HTTPError(url, status, HOSTILE, {"X-Fake": HOSTILE}, BytesIO(HOSTILE.encode()))
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda handler: Opener())
    def execute(env, request):
        request(method, url)
        pytest.fail("HTTP failure must stop execution")
    monkeypatch.setattr(m, "execute", execute)
    assert m.main() == 1
    assert capsys.readouterr() == (f"BLOCKED: {category} phase={phase}\n", "")
    assert len(calls) == 1


def test_role_get_404_remains_accepted_without_output(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": b"fake-token"})())
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            raise m.urllib.error.HTTPError(req.full_url, 404, HOSTILE, {}, None)
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda handler: Opener())
    assert m.Cloud()("GET", "https://fake.invalid/role", missing=True) is None
    assert len(calls) == 1
    assert capsys.readouterr() == ("", "")


EXECUTOR_PERMISSIONS = (
    "iam.roles.get", "resourcemanager.projects.getIamPolicy",
    "iam.roles.create", "resourcemanager.projects.setIamPolicy",
)


@pytest.mark.parametrize("held", [[], list(EXECUTOR_PERMISSIONS), [EXECUTOR_PERMISSIONS[2]]])
def test_executor_preflight_fixed_allowlist_and_diagnostic_only(held, capsys):
    cloud = Cloud()
    original = cloud.__call__
    def request(method, url, body=None, missing=False):
        if url.endswith(":testIamPermissions"):
            cloud.calls.append((method, url, body))
            assert method == "POST" and body == {"permissions": list(EXECUTOR_PERMISSIONS)}
            return {"permissions": held}
        return original(method, url, body, missing)
    m.execute({**ENV, "IAM_MODE": "dry-run"}, request)
    missing = [p for p in EXECUTOR_PERMISSIONS if p not in held]
    category = "missing" if missing else "held_all"
    output = capsys.readouterr().out
    assert output.startswith(f"executor_preflight={category} executor_missing=" + json.dumps(missing) + "\n")
    assert cloud.calls[0][1].endswith(":testIamPermissions")
    assert cloud.calls[1][0] == "GET"  # diagnostic never replaces role validation
    assert cloud.role is None


@pytest.mark.parametrize("result", [None, [], {"permissions": None},
    {"permissions": [HOSTILE]}, {"permissions": [1]},
    {"permissions": [EXECUTOR_PERMISSIONS[0]] * 2}, {"extra": HOSTILE},
    {"permissions": [], "extra": HOSTILE}])
def test_executor_preflight_malformed_stops_without_leak(result, monkeypatch, capsys):
    monkeypatch.setattr(m.os, "environ", ENV)
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return result
    monkeypatch.setattr(m, "Cloud", lambda: request)
    assert m.main() == 1
    assert capsys.readouterr() == ("BLOCKED: executor_preflight_unknown_stop_no_retry phase=test_iam_permissions\n", "")
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["http", "transport", "json", "oversized"])
def test_executor_preflight_transport_bounded_and_single_attempt(failure, monkeypatch, capsys):
    from io import BytesIO
    monkeypatch.setattr(m.os, "environ", ENV)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": b"fake-token"})())
    calls = []
    class Response(BytesIO):
        def read(self, size=-1):
            assert size == 4097
            return super().read(size)
    class Opener:
        def open(self, req, timeout):
            calls.append(req)
            assert req.full_url.endswith(":testIamPermissions")
            if failure == "http":
                raise m.urllib.error.HTTPError(req.full_url, 403, HOSTILE, {}, BytesIO(HOSTILE.encode()))
            if failure == "transport":
                raise TimeoutError(HOSTILE)
            return Response((HOSTILE if failure == "json" else " " * 4097).encode())
    monkeypatch.setattr(m.urllib.request, "build_opener", lambda handler: Opener())
    assert m.main() == 1
    category = "remote_http_403_stop_no_retry" if failure == "http" else "remote_result_unknown_stop_no_retry"
    assert capsys.readouterr() == (f"BLOCKED: {category} phase=test_iam_permissions\n", "")
    assert len(calls) == 1


def test_executor_preflight_empty_object_means_no_permissions(capsys):
    calls = []
    def request(method, url, body=None, missing=False):
        calls.append(url)
        if url.endswith(":testIamPermissions"):
            return {}  # API omits an empty repeated field
        raise m.Blocked("remote_http_403_stop_no_retry", phase="role_get")
    with pytest.raises(m.Blocked, match="remote_http_403"):
        m.execute(ENV, request)
    assert len(calls) == 2
    assert capsys.readouterr().out == 'executor_preflight=missing executor_missing=' + json.dumps(list(EXECUTOR_PERMISSIONS)) + '\n'
