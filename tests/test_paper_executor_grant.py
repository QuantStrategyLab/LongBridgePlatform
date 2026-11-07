"""Fake-only contract and adverse tests for the bootstrap grant definition."""
import copy
import json
import subprocess

import textwrap
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


def test_remediation_artifact_separates_intent_install_and_proposals():
    root = Path(__file__).parents[1]
    remediation = (root / "docs/iam/paper-executor-grant-remediation.md").read_text()
    design = (root / "docs/iam/paper-scheduler-get.md").read_text()
    for token in [
        "OWNER-PROVIDED", "PROPOSED_NOT_OBSERVED", "Card A", "Card B",
        "382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22",
        "5fd9761bc0f5e7a1b75fc007b946b554af81773faee45d940bbf52c04c52152e",
        "Variable absence is **not** evidence",
        "Rejected as bootstrap",
        "paper-iam-bootstrap@",
        "GRANT_PAPER_EXECUTOR_FOUR_PERMISSIONS",
        "no grant",
    ]:
        assert token in remediation
    assert "paper-executor-grant-remediation.md" in design
    assert "382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22" in design
    # Recipient expected hash must track the installed executor mapping, not a new identity.
    assert remediation.index("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT") < remediation.index(
        "382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22")


@pytest.mark.parametrize("mode", ["dry-run", "apply"])
def test_role_response_order_is_irrelevant(mode):
    cloud = Cloud()
    m.execute(ENV, cloud)
    cloud.calls.clear()
    count = 0

    def request(method, url, body=None, missing=False):
        nonlocal count
        result = cloud(method, url, body, missing)
        if method == "GET" and result is not None:
            count += 1
            result["includedPermissions"] = sorted(m.PERMISSIONS, reverse=count % 2 == 0)
        return result

    m.execute({**ENV, "IAM_MODE": mode}, request)
    assert not writes(cloud)


def test_role_creation_accepts_reordered_readback():
    cloud = Cloud()

    def request(method, url, body=None, missing=False):
        result = cloud(method, url, body, missing)
        if method == "GET" and result is not None:
            result["includedPermissions"] = sorted(m.PERMISSIONS)
        return result

    m.execute(ENV, request)
    assert len(writes(cloud)) == 2


@pytest.mark.parametrize("permissions", [
    None, "not-a-list", [], list(m.PERMISSIONS) + [m.PERMISSIONS[0]],
    list(m.PERMISSIONS[:-1]) + [m.PERMISSIONS[0]],
    list(m.PERMISSIONS[:-1]) + [None],
    list(m.PERMISSIONS[:-1]) + [{}],
])
def test_invalid_role_permissions_block_without_writes(permissions):
    cloud = Cloud()
    cloud.role = {**m.DEFINITION, "name": f"projects/{PROJECT}/roles/{m.ROLE_ID}",
                  "etag": "fake-role-etag", "includedPermissions": permissions}
    with pytest.raises(Blocked):
        m.execute(ENV, cloud)
    assert not writes(cloud)


def test_mapping_channel_uses_vars_context_not_rest_api():
    workflow = (Path(__file__).parents[1] /
        ".github/workflows/grant-paper-executor-permissions.yml").read_text()
    mappings = ["GCP_PROJECT_ID", "PAPER_SCHEDULER_GET_PROJECT_ID",
                "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "PAPER_IAM_GRANT_RECIPIENT",
                "PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT", "PAPER_IAM_BOOTSTRAP_WIF_PROVIDER"]
    job_header, steps = workflow.split("    steps:\n", 1)
    assert all(key + ":" not in job_header for key in mappings)
    first = steps.split("      - name:")[1]
    assert "uses: actions/github-script@v8" in first
    assert "environments/{environment_name}/variables/{name}" not in first
    assert "actions/variables/{name}" not in first
    assert "github.request" not in first
    for name in mappings:
        assert f"MAPPING_{name}: ${{{{ vars.{name} }}}}" in first
    assert "process.env['MAPPING_' + name]" in first


def test_mapping_masks_precede_all_step_details():
    workflow = (Path(__file__).parents[1] /
        ".github/workflows/grant-paper-executor-permissions.yml").read_text()
    mappings = ["GCP_PROJECT_ID", "PAPER_SCHEDULER_GET_PROJECT_ID",
                "GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT", "PAPER_IAM_GRANT_RECIPIENT",
                "PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT", "PAPER_IAM_BOOTSTRAP_WIF_PROVIDER"]
    first = workflow.split("    steps:\n", 1)[1].split("      - name:")[1]
    script = textwrap.dedent(first.split("          script: |\n", 1)[1])
    harness = r"""
const assert = require('node:assert/strict');
const script = SCRIPT;
const keys = KEYS;
async function check(failure) {
  const masks = new Set(), exported = [];
  const prior = {...process.env};
  for (const name of keys) process.env['MAPPING_' + name] = 'fake-' + name;
  if (failure === 'empty') process.env['MAPPING_' + keys[0]] = '';
  if (failure === 'blank') process.env['MAPPING_' + keys[0]] = '   ';
  try {
    const core = {
      setSecret(value) { masks.add(value); },
      exportVariable(key, value) {
        assert(masks.has(value), 'export before mask');
        assert.equal(masks.size, keys.length, 'export before all masks');
        exported.push(key);
      }
    };
    const run = new Function('github', 'core', 'context', 'return (async () => {' + script + '})()');
    if (failure) {
      await assert.rejects(run({}, core, {}), error =>
        error.message.includes('Protected mapping read failed: name=' + keys[0] +
          ' scope=vars category=blank_value'));
      assert.equal(exported.length, 0);
    } else {
      await run({}, core, {});
      assert.deepEqual(exported, keys);
    }
  } finally {
    for (const key of Object.keys(process.env)) {
      if (!(key in prior)) delete process.env[key];
    }
    Object.assign(process.env, prior);
  }
}
(async () => { await check(null); await check('empty'); await check('blank'); })()
.catch((error) => { console.error(error); process.exit(1); });
""".replace("SCRIPT", json.dumps(script)).replace("KEYS", json.dumps(mappings))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True)


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("failure,category", [
    ("blank", "blank_value"), ("empty", "blank_value"),
])
def test_mapping_failure_diagnostic_is_allowlisted_and_exports_nothing(index, failure, category):
    # Actions vars context yields strings; unset names arrive as "". Exercise that path.
    workflow = (Path(__file__).parents[1] /
        ".github/workflows/grant-paper-executor-permissions.yml").read_text()
    first = workflow.split("      - name:")[1]
    script = textwrap.dedent(first.split("          script: |\n", 1)[1])
    harness = r"""
const assert = require('node:assert/strict');
const keys = ['GCP_PROJECT_ID', 'PAPER_SCHEDULER_GET_PROJECT_ID',
  'GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT', 'PAPER_IAM_GRANT_RECIPIENT',
  'PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT', 'PAPER_IAM_BOOTSTRAP_WIF_PROVIDER'];
const [index, failure, category] = CASE;
const masks = [], exports = [];
const prior = {...process.env};
for (const name of keys) process.env['MAPPING_' + name] = 'fake-' + name;
const target = 'MAPPING_' + keys[index];
process.env[target] = failure === 'blank' ? '   ' : '';
const core = {setSecret: value => masks.push(value),
  exportVariable: (...args) => exports.push(args)};
const run = new Function('github', 'core', 'context', 'return (async () => {' + SCRIPT + '})()');
(async () => {
  try {
    await assert.rejects(run({}, core, {}), error => {
      assert.equal(error.message, 'Protected mapping read failed: name=' + keys[index] +
        ' scope=vars category=' + category + '; stop before authentication');
      assert.equal(error.cause, undefined);
      return true;
    });
    assert.deepEqual(exports, []);
    assert.equal(masks.length, index);
  } finally {
    for (const key of Object.keys(process.env)) {
      if (!(key in prior)) delete process.env[key];
    }
    Object.assign(process.env, prior);
  }
})().catch(error => { console.error(error); process.exit(1); });
""".replace("SCRIPT", json.dumps(script)).replace("CASE", json.dumps([index, failure, category]))
    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
