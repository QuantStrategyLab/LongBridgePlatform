"""Read PAPER configuration only; never invoke a service or broker.

This observes configured identity, not credential identity. No authoritative
Kelly/all-stop-loss reader exists here yet: those fields must remain unknown.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime

from application.runtime_target_manifest import load_runtime_target_manifest


class ReadbackError(ValueError):
    """Only fixed diagnostics may leave protected process memory."""


def read_json(args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=60, check=False)
        if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
            raise ValueError
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError):
        raise ReadbackError("protected_read_unavailable") from None


def container_env(spec):
    containers = spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ReadbackError("container_configuration_invalid")
    entries = containers[0].get("env")
    if not isinstance(entries, list):
        raise ReadbackError("container_configuration_invalid")
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ReadbackError("container_configuration_invalid")
        name = entry["name"]
        if name in result:
            raise ReadbackError("container_configuration_invalid")
        # Secret references are deliberately unresolved; never access their values.
        result[name] = entry.get("value") if "valueFrom" not in entry else None
    return result


def require_identity(values, *, service, account):
    try:
        target = json.loads(values["RUNTIME_TARGET_JSON"])
        alias = values.get("QSL_RUNTIME_TARGET_JSON")
        if "QSL_RUNTIME_TARGET_JSON" in values and (alias is None or json.loads(alias) != target):
            raise ValueError
        required = {"platform_id": "longbridge", "service_name": service,
                        "execution_environment": "paper", "execution_mode": "live",
                        "dry_run_only": False, "account_scope": "PAPER",
                        "account_selector": ["PAPER"], "deployment_selector": "PAPER"}
        if (not isinstance(target, dict)
                or any(type(target.get(k)) is not type(v) or target[k] != v for k, v in required.items())
                or values.get("LONGBRIDGE_PHYSICAL_ACCOUNT_ID") != account
                or target.get("account_selectors", ["PAPER"]) != ["PAPER"]
                or target.get("physical_account_id", account) != account
                or values.get("RUNTIME_TARGET_ENABLED") != "false"
                or values.get("LONGBRIDGE_DRY_RUN_ONLY") != "false"
                or any(values.get("QSL_" + key, "false") != "false" for key in (
                    "RUNTIME_TARGET_ENABLED", "LONGBRIDGE_DRY_RUN_ONLY"))):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ReadbackError("paper_disabled_identity_unproven") from None


def configuration(env):
    paper, = [t for t in load_runtime_target_manifest().targets if t.id == "paper"]
    service, region, project, account = (env.get(k, "") for k in (
        "CLOUD_RUN_SERVICE", "CLOUD_RUN_REGION", "GCP_PROJECT_ID", "EXPECTED_PHYSICAL_ACCOUNT_ID"))
    if (env.get("WORKFLOW_ENVIRONMENT") != "longbridge-paper"
            or paper.environment != "longbridge-paper" or paper.mode != "paper"
            or service != paper.service or region != paper.region
            or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project)
            or not account or account != account.strip()
            or account.upper() in {"PAPER", "LIVE", "SG", "HK", "US", "DEFAULT"}):
        raise ReadbackError("protected_configuration_unavailable")
    require_identity({"RUNTIME_TARGET_JSON": env.get("EXPECTED_RUNTIME_TARGET_JSON"),
                      "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": account,
                      "RUNTIME_TARGET_ENABLED": env.get("EXPECTED_RUNTIME_TARGET_ENABLED"),
                      "LONGBRIDGE_DRY_RUN_ONLY": env.get("EXPECTED_DRY_RUN_ONLY")},
                     service=service, account=account)
    return service, region, project, account


def observe(*, env=os.environ, load=read_json):
    service, region, project, account = configuration(env)
    started = datetime.now(UTC)
    flags = ["--project=" + project, "--region=" + region, "--format=json"]
    service_cmd = ["gcloud", "run", "services", "describe", service, *flags]
    before = load(service_cmd)
    if before.get("metadata", {}).get("name") != service:
        raise ReadbackError("service_identity_invalid")
    template = container_env(before.get("spec", {}).get("template", {}).get("spec", {}))
    require_identity(template, service=service, account=account)
    traffic = before.get("status", {}).get("traffic")
    if not isinstance(traffic, list) or not traffic or len(traffic) > 100:
        raise ReadbackError("traffic_unavailable")
    revisions = {}
    for row in traffic:
        percent = row.get("percent", 0)
        if type(percent) is not int or not 0 <= percent <= 100:
            raise ReadbackError("traffic_unavailable")
        if percent:
            name = row.get("revisionName")
            if (not isinstance(name, str) or not re.fullmatch(re.escape(service) + r"-[a-z0-9-]{1,63}", name)
                    or name in revisions):
                raise ReadbackError("revision_identity_invalid")
            revisions[name] = percent
    if sum(revisions.values()) != 100:
        raise ReadbackError("traffic_unavailable")
    values = [template]
    for name in revisions:
        cmd = ["gcloud", "run", "revisions", "describe", name, *flags]
        revision = load(cmd)
        if revision.get("metadata", {}).get("name") != name:
            raise ReadbackError("revision_identity_invalid")
        current = container_env(revision.get("spec", {}))
        require_identity(current, service=service, account=account)
        values.append(current)
        if load(cmd) != revision:
            raise ReadbackError("revision_changed_during_observation")
    if load(service_cmd) != before:
        raise ReadbackError("service_changed_during_observation")
    return {"schema": "longbridge.plain-dca-paper-readback.v1", "status": "partial",
            "observed_started_at": started.isoformat(),
            "observed_finished_at": datetime.now(UTC).isoformat(),
            "configured_paper_identity_verified": True, "runtime_target_enabled": False,
            "physical_account_matches_protected": True,
            "positive_traffic_revision_count": len(revisions), "service_stable": True,
            "live_command_enabled": True if any(v.get("LONGBRIDGE_DURABLE_EXECUTION_COMMAND_LIVE_ENABLED") == "true"
                                                 for v in values) else (
                False if all(v.get("LONGBRIDGE_DURABLE_EXECUTION_COMMAND_LIVE_ENABLED") == "false"
                             for v in values) else None),
            "kelly_enabled": None, "stop_loss_enabled": None,
            "reason": "authoritative_safety_source_unavailable", "paper_start_ready": False,
            "broker_identity_independently_verified": False,
            "service_invoked": False, "broker_invoked": False, "business_writes": False}


def main(argv=None):
    try:
        if argv == ["--validate-config"]:
            configuration(os.environ)
            return 0
        result = observe()
    except Exception as exc:  # noqa: BLE001 - Never render protected cloud errors.
        # No exception text, service identifiers, account values or cloud stderr.
        result = {"schema": "longbridge.plain-dca-paper-readback.v1", "status": "unknown",
                  "reason": "protected_readback_unavailable", "paper_start_ready": False}
        if isinstance(exc, ReadbackError):
            result["reason"] = str(exc)
    print(json.dumps(result, sort_keys=True))
    return 1  # A partial/unknown observation never grants readiness.


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
