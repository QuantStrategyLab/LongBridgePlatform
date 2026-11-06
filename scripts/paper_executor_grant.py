"""Fixed bootstrap grant for the existing PAPER scheduler IAM executor.

No remote diagnostics or identifiers are emitted. Uses the scheduler IAM
transport and policy validator; never retries a write or changes runtime state.
"""
import copy
import os
import re

from scripts.paper_scheduler_get_iam import (
    EXECUTOR_PERMISSIONS,
    Blocked,
    Cloud,
    validate_policy,
)

PERMISSIONS = EXECUTOR_PERMISSIONS
ROLE_ID = "paperSchedulerIamExecutor"
DEFINITION = {
    "title": "PAPER scheduler IAM executor",
    "description": "Fixed four-permission PAPER scheduler IAM executor grant",
    "stage": "GA", "includedPermissions": list(PERMISSIONS),
}


def identity(env):
    required = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
        "GITHUB_REF": "refs/heads/main", "GITHUB_RUN_ATTEMPT": "1",
        "IAM_CONFIRM": "GRANT_PAPER_EXECUTOR_FOUR_PERMISSIONS",
    }
    project = env.get("GCP_PROJECT_ID", "")
    recipient = env.get("PAPER_IAM_GRANT_RECIPIENT", "")
    bootstrap = env.get("PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT", "")
    sa_pattern = r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + re.escape(project) + r"\.iam\.gserviceaccount\.com"
    revision = env.get("IAM_APPROVED_REVISION", "")
    if (any(env.get(k) != v for k, v in required.items())
            or env.get("IAM_MODE") not in {"dry-run", "apply"}
            or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
            or project != env.get("PAPER_SCHEDULER_GET_PROJECT_ID")
            or recipient != env.get("GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT")
            or not re.fullmatch(sa_pattern, recipient)
            or not re.fullmatch(sa_pattern, bootstrap) or bootstrap == recipient
            or not re.fullmatch(r"[0-9a-f]{40}", revision)
            or revision != env.get("GITHUB_SHA")
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", env.get("IAM_OWNER_RUN_CARD", ""))):
        raise Blocked("authority_mapping_rejected")
    return project, f"serviceAccount:{recipient}"


def validate_role(role, name):
    if (not isinstance(role, dict) or role.get("name") != name
            or role.get("includedPermissions") != list(PERMISSIONS)
            or role.get("deleted", False) is not False or role.get("stage") != "GA"
            or not isinstance(role.get("etag"), str) or not role["etag"]):
        raise Blocked("role_collision_or_integrity_failure")


def execute(env, request):
    project, member = identity(env)
    root = f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}"
    name = f"projects/{project}/roles/{ROLE_ID}"
    role_url = f"https://iam.googleapis.com/v1/{name}"
    held = request("POST", root + ":testIamPermissions", {"permissions": list(PERMISSIONS)})
    if (not isinstance(held, dict) or set(held) != {"permissions"}
            or not isinstance(held["permissions"], list)
            or len(held["permissions"]) != len(PERMISSIONS)
            or any(type(p) is not str for p in held["permissions"])
            or set(held["permissions"]) != set(PERMISSIONS)):
        raise Blocked("bootstrap_permissions_missing_or_unknown")

    def read_role():
        role = request("GET", role_url, missing=True)
        if role is not None:
            validate_role(role, name)
        return role

    def read_policy():
        policy = request("POST", root + ":getIamPolicy", {"options": {"requestedPolicyVersion": 3}})
        validate_policy(policy, name, member)
        return policy

    role = read_role()
    before = read_policy()
    bound = validate_policy(before, name, member)
    if role is None and bound:
        raise Blocked("orphan_binding_collision")
    print("plan: fixed four-permission role; existing PAPER executor only")
    if env["IAM_MODE"] == "dry-run":
        print("dry-run: zero writes; grant NOT_EXECUTED")
        return
    # Fresh observations detect another process/writer before the first write.
    if read_role() != role or read_policy() != before:
        raise Blocked("concurrent_drift")
    if role is None:
        request("POST", f"https://iam.googleapis.com/v1/projects/{project}/roles",
                {"roleId": ROLE_ID, "role": copy.deepcopy(DEFINITION)})
        role = read_role()
        if role is None:
            raise Blocked("created_role_missing")
    # Creation and binding are not atomic. Stop on partial/unknown outcomes;
    # never roll back or retry. A new run card must inspect any partial result.
    fresh = read_policy()
    if fresh != before or read_role() != role:
        raise Blocked("concurrent_drift")
    expected = copy.deepcopy(before)
    if not bound:
        expected["bindings"].append({"role": name, "members": [member]})
        request("POST", root + ":setIamPolicy", {
            "policy": {k: expected[k] for k in ("version", "etag", "bindings")},
            "updateMask": "bindings,etag",
        })
    after = read_policy()
    if (read_role() != role
            or {k: v for k, v in after.items() if k != "etag"}
            != {k: v for k, v in expected.items() if k != "etag"}
            or not validate_policy(after, name, member)):
        raise Blocked("after_readback_drift")
    print("verified: exact role and additive binding; no runtime action")


def main():
    try:
        identity(os.environ)
        execute(os.environ, Cloud())
        return 0
    except Exception:
        # Shared transport suppresses remote data; never print exception values.
        print("BLOCKED: executor_grant_failed_stop_no_retry")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
