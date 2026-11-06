"""One bounded PAPER IAM delta. Remote data and diagnostics never leave memory.

Only fixed status messages are emitted, as in stop_hk_runtime.py. There is no
arbitrary command, project, permission or member argument and no write retry.
"""
import copy
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

EXECUTOR_PERMISSIONS = (
    "iam.roles.get", "resourcemanager.projects.getIamPolicy",
    "iam.roles.create", "resourcemanager.projects.setIamPolicy",
)
PREFLIGHT_MAX_BYTES = 4096

ROLE_ID = "paperSchedulerJobsGet"
PERMISSION = "cloudscheduler.jobs.get"
ROLE_FILE = Path(__file__).resolve().parents[1] / "docs/iam/paper-scheduler-get-role.json"


class Blocked(ValueError):
    """Fixed failure category; never attach remote payloads or identifiers."""

    def __init__(self, *args, phase="unknown"):
        super().__init__(*args)
        self.phase = phase


def role_definition(path=ROLE_FILE):
    value = json.loads(path.read_text())
    if (set(value) != {"title", "description", "stage", "includedPermissions"}
            or value["includedPermissions"] != [PERMISSION] or value["stage"] != "GA"):
        raise Blocked("role_definition_rejected")
    return value


def identity(env):
    required = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
        "IAM_ENVIRONMENT": "longbridge-paper", "IAM_CONFIRM": "APPLY_PAPER_SCHEDULER_JOBS_GET",
    }
    project = env.get("GCP_PROJECT_ID", "")
    reader = env.get("PAPER_SCHEDULER_GET_READER_EMAIL", "")
    if (any(env.get(k) != v for k, v in required.items())
            or env.get("IAM_MODE", "dry-run") not in {"dry-run", "apply"}
            or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
            or project != env.get("PAPER_SCHEDULER_GET_PROJECT_ID")
            or not re.fullmatch(r"grok-gcp-ro@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com", reader)):
        raise Blocked("authority_mapping_rejected")
    return project, f"serviceAccount:{reader}"


def validate_role(role, name):
    if (not isinstance(role, dict) or role.get("name") != name
            or role.get("includedPermissions") != [PERMISSION]
            or role.get("deleted", False) is not False or role.get("stage") != "GA"
            or not isinstance(role.get("etag"), str) or not role["etag"]):
        raise Blocked("role_collision_or_integrity_failure")


def validate_policy(policy, name, member):
    if (not isinstance(policy, dict) or not isinstance(policy.get("etag"), str)
            or not policy["etag"] or policy.get("version") not in {1, 3}
            or not isinstance(policy.get("bindings"), list)):
        raise Blocked("policy_integrity_failure")
    matches = []
    for binding in policy["bindings"]:
        if (not isinstance(binding, dict) or not isinstance(binding.get("role"), str)
                or not isinstance(binding.get("members"), list)
                or not binding["members"] or any(not isinstance(m, str) for m in binding["members"])):
            raise Blocked("policy_integrity_failure")
        if binding["role"] == name:
            matches.append(binding)
    if matches and (len(matches) != 1 or matches[0] != {"role": name, "members": [member]}):
        raise Blocked("binding_collision")
    return bool(matches)


def execute(env, request):
    project, member = identity(env)
    definition = role_definition()
    name = f"projects/{project}/roles/{ROLE_ID}"
    role_url = f"https://iam.googleapis.com/v1/{name}"
    project_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}"

    def read_policy():
        policy = request("POST", project_url + ":getIamPolicy", {"options": {"requestedPolicyVersion": 3}})
        validate_policy(policy, name, member)
        return policy

    # Observations only: even held_all cannot establish API availability or
    # authorize writes. Missing permissions never replace the existing reads.
    result = request("POST", project_url + ":testIamPermissions",
                     {"permissions": list(EXECUTOR_PERMISSIONS)})
    held = result.get("permissions", []) if isinstance(result, dict) else None
    if (not isinstance(result, dict) or set(result) - {"permissions"}
            or not isinstance(held, list)
            or any(type(p) is not str or p not in EXECUTOR_PERMISSIONS for p in held)
            or len(held) != len(set(held))):
        raise Blocked("executor_preflight_unknown_stop_no_retry", phase="test_iam_permissions")
    missing_permissions = [p for p in EXECUTOR_PERMISSIONS if p not in held]
    status = "missing" if missing_permissions else "held_all"
    print("executor_preflight=" + status + " executor_missing=" + json.dumps(missing_permissions))

    before_role = request("GET", role_url, missing=True)
    if before_role is not None:
        validate_role(before_role, name)
    before_policy = read_policy()
    already_bound = validate_policy(before_policy, name, member)
    if before_role is None and already_bound:
        raise Blocked("orphan_binding_collision")
    print("before: project=longbridge-paper reader=existing-box-reader role=" + ROLE_ID)
    print("plan: permissions=[cloudscheduler.jobs.get]; additive binding only")
    if env.get("IAM_MODE", "dry-run") == "dry-run":
        print("dry-run: no writes; PAPER remains stopped")
        return

    # Re-read both objects immediately before any write. Never adopt a role
    # created by another writer after the initial absent snapshot.
    current_role = request("GET", role_url, missing=True)
    current_policy = read_policy()
    if current_role != before_role or current_policy != before_policy:
        raise Blocked("concurrent_drift")
    if before_role is None:
        request("POST", f"https://iam.googleapis.com/v1/projects/{project}/roles",
                {"roleId": ROLE_ID, "role": definition})
        created_role = request("GET", role_url)
        validate_role(created_role, name)
    else:
        created_role = before_role
    # Role creation and policy mutation are separate APIs, not a transaction.
    # A failure can leave the role created with no binding. Stop; never rollback
    # or retry. A later owner card must inspect the partial result.
    fresh_policy = read_policy()
    fresh_role = request("GET", role_url)
    validate_role(fresh_role, name)
    if fresh_policy != before_policy or fresh_role != created_role:
        raise Blocked("concurrent_drift")
    if not already_bound:
        bindings = copy.deepcopy(fresh_policy["bindings"])
        bindings.append({"role": name, "members": [member]})
        # Google exposes additive project bindings through setIamPolicy. Use
        # ONLY the binding field and etag CAS: no whole-policy replacement,
        # auditConfig update, stale replay, CLI retry or unconditional write.
        request("POST", project_url + ":setIamPolicy", {
            "policy": {"version": fresh_policy["version"], "etag": fresh_policy["etag"], "bindings": bindings},
            "updateMask": "bindings,etag",
        })
    after_role = request("GET", role_url)
    validate_role(after_role, name)
    after_policy = read_policy()
    expected = copy.deepcopy(before_policy)
    if not already_bound:
        expected["bindings"].append({"role": name, "members": [member]})
    # Etag changes are expected. All unrelated policy fields must be identical.
    if ({k: v for k, v in after_policy.items() if k != "etag"}
            != {k: v for k, v in expected.items() if k != "etag"}
            or after_role != created_role
            or not validate_policy(after_policy, name, member)):
        raise Blocked("after_readback_drift")
    print("after: permissions=[cloudscheduler.jobs.get]; existing-box-reader binding verified")
    print("complete: PAPER remains stopped; no further operation authorized")


class Cloud:
    """Single-attempt REST adapter. No raw stderr, URL or response is printed."""

    def __init__(self):
        try:
            result = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, timeout=60, check=True)
            self.token = result.stdout.decode().strip()
        except (subprocess.SubprocessError, OSError, UnicodeError):
            raise Blocked("authentication_failed", phase="token_acquisition") from None
        if not self.token or "\n" in self.token or "\r" in self.token:
            raise Blocked("authentication_failed", phase="token_acquisition")

    def __call__(self, method, url, body=None, missing=False):
        # Only read operations receive a diagnostic label; never echo a URL.
        phase = "unknown"
        if method == "GET":
            phase = "role_get"
        elif method == "POST" and url.endswith(":getIamPolicy"):
            phase = "get_iam_policy"
        elif method == "POST" and url.endswith(":testIamPermissions"):
            phase = "test_iam_permissions"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
        })
        # Disable redirects so neither the bearer token nor a mutation can be
        # replayed at another endpoint. urllib performs no automatic retries.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
                if phase == "test_iam_permissions":
                    payload = response.read(PREFLIGHT_MAX_BYTES + 1)
                    if len(payload) > PREFLIGHT_MAX_BYTES:
                        raise ValueError("oversized_preflight")
                    return json.loads(payload)
                return json.load(response)
        except urllib.error.HTTPError as error:
            if missing and method == "GET" and error.code == 404:
                return None
            # Fixed categories only: never render any HTTPError payload.
            category = {
                401: "remote_http_401_stop_no_retry",
                403: "remote_http_403_stop_no_retry",
                429: "remote_http_429_stop_no_retry",
            }.get(error.code, "remote_http_other_stop_no_retry")
            if 500 <= error.code <= 599:
                category = "remote_http_5xx_stop_no_retry"
            raise Blocked(category, phase=phase) from None
        except Exception:
            raise Blocked("remote_result_unknown_stop_no_retry", phase=phase) from None


def main():
    try:
        identity(os.environ)  # Reject wrong scope before token access.
        execute(os.environ, Cloud())
        return 0
    except Blocked as error:
        # Never format exception text, remote data, or arbitrary phase labels.
        categories = {
            "authority_mapping_rejected", "role_definition_rejected",
            "role_collision_or_integrity_failure", "policy_integrity_failure",
            "binding_collision", "orphan_binding_collision", "concurrent_drift",
            "after_readback_drift", "authentication_failed",
            "executor_preflight_unknown_stop_no_retry",
            "remote_rejected_or_unknown_stop_no_retry", "remote_result_unknown_stop_no_retry",
            "remote_http_401_stop_no_retry", "remote_http_403_stop_no_retry",
            "remote_http_429_stop_no_retry", "remote_http_5xx_stop_no_retry",
            "remote_http_other_stop_no_retry",
        }
        category = error.args[0] if len(error.args) == 1 else None
        if type(category) is not str or category not in categories:
            print("BLOCKED: unknown_failure phase=unknown")
        else:
            phase = error.phase
            if type(phase) is not str or phase not in {"token_acquisition", "role_get", "get_iam_policy", "test_iam_permissions"}:
                phase = "unknown"
            print(f"BLOCKED: {category} phase={phase}")
        return 1
    except Exception:
        print("BLOCKED: unknown_failure phase=unknown")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
