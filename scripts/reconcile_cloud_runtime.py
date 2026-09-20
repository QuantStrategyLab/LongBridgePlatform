#!/usr/bin/env python3
"""Reconcile Cloud Run and Cloud Scheduler runtime drift.

Traffic routing and Scheduler pause/resume are explicit operator actions.
Ordinary image/env sync must preserve serving traffic and existing Scheduler
enabled state. This script also deletes known legacy Scheduler jobs.
Runtime code errors, secret gaps, and live/paper policy conflicts must still
fail fast in the owning workflow.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class RuntimeTarget:
    service_name: str
    region: str = ""
    account_scope: str = ""


class ReconcileError(RuntimeError):
    pass


def _load_json_object(raw: str, *, field_name: str) -> Mapping[str, Any] | list[Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReconcileError(f"{field_name} must be valid JSON: {exc}") from exc
    if not isinstance(value, (Mapping, list)):
        raise ReconcileError(f"{field_name} must decode to an object or list")
    return value


def _runtime_target(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = entry.get("runtime_target") or entry.get("runtime_target_json")
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str) and raw.strip():
        loaded = _load_json_object(raw, field_name="runtime_target_json")
        if isinstance(loaded, Mapping):
            return loaded
    return {}


def _target_from_entry(entry: Mapping[str, Any]) -> RuntimeTarget | None:
    runtime_target = _runtime_target(entry)
    service = (
        entry.get("service_name")
        or entry.get("service")
        or entry.get("cloud_run_service")
        or runtime_target.get("service_name")
    )
    service_name = str(service or "").strip()
    if not service_name:
        return None
    account_scope = str(
        entry.get("ACCOUNT_GROUP")
        or entry.get("account_scope")
        or runtime_target.get("account_scope")
        or ""
    ).strip()
    region = str(entry.get("region") or entry.get("cloud_run_region") or "").strip()
    return RuntimeTarget(service_name=service_name, region=region, account_scope=account_scope)


def _dedupe_targets(targets: Iterable[RuntimeTarget]) -> list[RuntimeTarget]:
    by_service: dict[str, RuntimeTarget] = {}
    for target in targets:
        current = by_service.get(target.service_name)
        if current is None:
            by_service[target.service_name] = target
            continue
        by_service[target.service_name] = RuntimeTarget(
            service_name=target.service_name,
            region=current.region or target.region,
            account_scope=current.account_scope or target.account_scope,
        )
    return list(by_service.values())


def load_targets(*, env: Mapping[str, str]) -> list[RuntimeTarget]:
    targets: list[RuntimeTarget] = []

    raw_plan = str(env.get("SYNC_PLAN_JSON") or "").strip()
    if raw_plan:
        plan = _load_json_object(raw_plan, field_name="SYNC_PLAN_JSON")
        entries = plan.get("targets") if isinstance(plan, Mapping) else plan
        if not isinstance(entries, list):
            raise ReconcileError("SYNC_PLAN_JSON.targets must be a list")
        for entry in entries:
            if isinstance(entry, Mapping):
                target = _target_from_entry(entry)
                if target:
                    targets.append(target)

    raw_targets = str(env.get("CLOUD_RUN_SERVICE_TARGETS_JSON") or "").strip()
    if raw_targets:
        payload = _load_json_object(raw_targets, field_name="CLOUD_RUN_SERVICE_TARGETS_JSON")
        entries = payload.get("targets") if isinstance(payload, Mapping) else payload
        if not isinstance(entries, list):
            raise ReconcileError("CLOUD_RUN_SERVICE_TARGETS_JSON.targets must be a list")
        for entry in entries:
            if isinstance(entry, Mapping):
                target = _target_from_entry(entry)
                if target:
                    targets.append(target)

    raw_services = str(env.get("CLOUD_RUN_SERVICES") or env.get("CLOUD_RUN_SERVICE") or "").strip()
    for chunk in raw_services.replace(";", ",").replace("\n", ",").split(","):
        service_name = chunk.strip()
        if service_name:
            targets.append(RuntimeTarget(service_name=service_name))

    return _dedupe_targets(targets)


def select_targets(targets: Sequence[RuntimeTarget], *, service_name: str = "") -> list[RuntimeTarget]:
    """Optionally restrict reconciliation to one deployment service."""
    selected_service = str(service_name or "").strip()
    if not selected_service:
        return list(targets)
    selected = [target for target in targets if target.service_name == selected_service]
    if not selected:
        raise ReconcileError(f"Requested service {selected_service!r} is not a resolved Cloud Run target")
    return selected


def _run(args: Sequence[str], *, json_output: bool = False, dry_run: bool = False) -> Any:
    printable = " ".join(args)
    if dry_run:
        print(f"DRY-RUN {printable}")
        return {} if json_output else ""
    completed = subprocess.run(
        list(args),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ReconcileError(f"Command failed ({completed.returncode}): {printable}\n{detail}")
    if json_output:
        try:
            return json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ReconcileError(f"Command did not return JSON: {printable}") from exc
    return completed.stdout


def _run_optional(args: Sequence[str], *, dry_run: bool = False) -> bool:
    printable = " ".join(args)
    if dry_run:
        print(f"DRY-RUN {printable}")
        return True
    completed = subprocess.run(
        list(args),
        check=False,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _traffic_entries(service: Mapping[str, Any]) -> list[dict[str, Any]]:
    traffic = service.get("status", {}).get("traffic") or []
    if not isinstance(traffic, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in traffic:
        if not isinstance(item, Mapping):
            continue
        try:
            percent = int(item.get("percent") or 0)
        except (TypeError, ValueError):
            percent = 0
        revision = str(item.get("revisionName") or "").strip()
        tag = str(item.get("tag") or "").strip()
        if revision or tag or percent:
            entries.append({"revisionName": revision, "percent": percent, "tag": tag})
    return entries


def _traffic_on_revision(service: Mapping[str, Any], revision_name: str) -> bool:
    for item in _traffic_entries(service):
        if item["percent"] == 100 and item["revisionName"] == revision_name:
            return True
    return False


def _revision_is_ready(revision: Mapping[str, Any]) -> bool:
    conditions = revision.get("status", {}).get("conditions") or []
    if not isinstance(conditions, list):
        return False
    for condition in conditions:
        if not isinstance(condition, Mapping):
            continue
        if str(condition.get("type") or "") == "Ready" and str(condition.get("status") or "") == "True":
            return True
    return False


def _revision_identity_from_payload(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    metadata = payload.get("metadata", {}) if isinstance(payload, Mapping) else {}
    labels = metadata.get("labels") or {}
    containers = payload.get("spec", {}).get("containers", []) if isinstance(payload, Mapping) else []
    image = (
        str(containers[0].get("image") or "").strip()
        if containers and isinstance(containers[0], Mapping)
        else ""
    )
    return (
        str(labels.get("commit-sha") or "").strip(),
        str(labels.get("release-set") or labels.get("release_set") or "").strip(),
        image,
    )


def _resolve_revision_for_commit(
    *,
    project: str,
    region: str,
    service_name: str,
    expected_commit: str,
    dry_run: bool,
) -> tuple[str, str, str, str]:
    """Return (revision_name, commit, release_set, image) for the newest Ready match."""
    if dry_run:
        return (f"{service_name}-dry-run", expected_commit, "", "")
    if not expected_commit:
        raise ReconcileError(f"expected commit is required to route traffic for {service_name}")
    revisions = _run(
        [
            "gcloud",
            "run",
            "revisions",
            "list",
            f"--service={service_name}",
            f"--project={project}",
            f"--region={region}",
            "--format=json",
        ],
        json_output=True,
        dry_run=dry_run,
    )
    if not isinstance(revisions, list):
        raise ReconcileError(f"Unable to list revisions for {service_name}")
    for revision in revisions:
        if not isinstance(revision, Mapping):
            continue
        if not _revision_is_ready(revision):
            continue
        commit, release_set, image = _revision_identity_from_payload(revision)
        if commit != expected_commit:
            continue
        name = str((revision.get("metadata") or {}).get("name") or "").strip()
        if not name:
            continue
        return name, commit, release_set, image
    raise ReconcileError(
        f"No Ready revision for {service_name} with commit {expected_commit!r}"
    )


def read_traffic(
    *,
    project: str,
    region: str,
    targets: Sequence[RuntimeTarget],
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Read serving traffic without mutating it."""
    readbacks: list[dict[str, Any]] = []
    for target in targets:
        target_region = target.region or region
        if not target_region:
            raise ReconcileError(f"Region is required for {target.service_name}")
        service = _run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                target.service_name,
                f"--project={project}",
                f"--region={target_region}",
                "--format=json",
            ],
            json_output=True,
            dry_run=dry_run,
        )
        payload = service if isinstance(service, Mapping) else {}
        status = payload.get("status", {}) if isinstance(payload, Mapping) else {}
        entry = {
            "service_name": target.service_name,
            "region": target_region,
            "latestReadyRevisionName": str(status.get("latestReadyRevisionName") or "").strip(),
            "latestCreatedRevisionName": str(status.get("latestCreatedRevisionName") or "").strip(),
            "traffic": _traffic_entries(payload if isinstance(payload, Mapping) else {}),
        }
        readbacks.append(entry)
        print(json.dumps({"traffic_readback": entry}, sort_keys=True))
    return readbacks


def ensure_latest_traffic(
    *,
    project: str,
    region: str,
    targets: Sequence[RuntimeTarget],
    expected_commit: str,
    expected_release_set: str = "",
    expected_image_digest: str = "",
    dry_run: bool,
) -> None:
    for target in targets:
        target_region = target.region or region
        if not target_region:
            raise ReconcileError(f"Region is required for {target.service_name}")
        service = _run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                target.service_name,
                f"--project={project}",
                f"--region={target_region}",
                "--format=json",
            ],
            json_output=True,
            dry_run=dry_run,
        )
        target_revision, actual_commit, actual_release_set, actual_image = _resolve_revision_for_commit(
            project=project,
            region=target_region,
            service_name=target.service_name,
            expected_commit=expected_commit,
            dry_run=dry_run,
        )
        if expected_release_set and actual_release_set != expected_release_set:
            raise ReconcileError(
                f"{target.service_name} revision {target_revision} release-set {actual_release_set!r} "
                f"does not match expected {expected_release_set!r}"
            )
        if expected_image_digest and actual_image != expected_image_digest:
            raise ReconcileError(
                f"{target.service_name} revision {target_revision} image digest {actual_image!r} "
                f"does not match expected {expected_image_digest!r}"
            )
        if not _traffic_on_revision(service if isinstance(service, Mapping) else {}, target_revision):
            print(
                f"Updating {target.service_name} traffic to commit revision "
                f"{target_revision} ({actual_commit or expected_commit})."
            )
            _run(
                [
                    "gcloud",
                    "run",
                    "services",
                    "update-traffic",
                    target.service_name,
                    f"--project={project}",
                    f"--region={target_region}",
                    f"--to-revisions={target_revision}=100",
                    "--quiet",
                ],
                dry_run=dry_run,
            )
        verified = _run(
            [
                "gcloud",
                "run",
                "services",
                "describe",
                target.service_name,
                f"--project={project}",
                f"--region={target_region}",
                "--format=json",
            ],
            json_output=True,
            dry_run=dry_run,
        )
        if not _traffic_on_revision(verified if isinstance(verified, Mapping) else {}, target_revision):
            raise ReconcileError(
                f"{target.service_name} traffic is not 100% on commit revision {target_revision}"
            )
        print(f"Cloud Run traffic OK for {target.service_name}: {target_revision}")


def _parse_scheduler_jobs(raw: str) -> list[str]:
    jobs: list[str] = []
    for chunk in str(raw or "").replace(";", ",").replace("\n", ",").split(","):
        name = chunk.strip()
        if name:
            jobs.append(name)
    return list(dict.fromkeys(jobs))


def _scheduler_job_state(
    *,
    project: str,
    location: str,
    job_name: str,
    dry_run: bool,
) -> str:
    if dry_run:
        return "ENABLED"
    state = str(
        _run(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "describe",
                job_name,
                f"--project={project}",
                f"--location={location}",
                "--format=value(state)",
            ],
            dry_run=dry_run,
        )
        or ""
    ).strip()
    if not state:
        raise ReconcileError(f"Unable to read Scheduler state for {job_name} in {location}")
    return state


def read_scheduler_enabled(
    *,
    project: str,
    scheduler_location: str,
    job_names: Sequence[str],
    dry_run: bool,
) -> list[dict[str, str]]:
    if not scheduler_location:
        raise ReconcileError("--scheduler-location or CLOUD_SCHEDULER_LOCATION is required")
    names = [name for name in job_names if str(name or "").strip()]
    if not names:
        raise ReconcileError("--scheduler-jobs is required for scheduler enabled readback")
    readbacks: list[dict[str, str]] = []
    for job_name in names:
        if not _run_optional(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "describe",
                job_name,
                f"--project={project}",
                f"--location={scheduler_location}",
            ],
            dry_run=dry_run,
        ):
            entry = {
                "job_name": job_name,
                "location": scheduler_location,
                "state": "MISSING",
            }
        else:
            entry = {
                "job_name": job_name,
                "location": scheduler_location,
                "state": _scheduler_job_state(
                    project=project,
                    location=scheduler_location,
                    job_name=job_name,
                    dry_run=dry_run,
                ),
            }
        readbacks.append(entry)
        print(json.dumps({"scheduler_enabled_readback": entry}, sort_keys=True))
    return readbacks


def apply_scheduler_enabled_action(
    *,
    project: str,
    scheduler_location: str,
    job_names: Sequence[str],
    action: str,
    dry_run: bool,
) -> list[dict[str, str]]:
    """Explicitly pause or resume managed Scheduler jobs, then read back state."""
    normalized = str(action or "").strip().lower()
    if normalized == "preserve":
        return read_scheduler_enabled(
            project=project,
            scheduler_location=scheduler_location,
            job_names=job_names,
            dry_run=dry_run,
        )
    if normalized not in {"pause", "resume"}:
        raise ReconcileError("scheduler enabled action must be preserve, pause, or resume")
    if not scheduler_location:
        raise ReconcileError("--scheduler-location or CLOUD_SCHEDULER_LOCATION is required")
    names = [name for name in job_names if str(name or "").strip()]
    if not names:
        raise ReconcileError("--scheduler-jobs is required for scheduler enabled action")

    expected_state = "PAUSED" if normalized == "pause" else "ENABLED"
    for job_name in names:
        if not _run_optional(
            [
                "gcloud",
                "scheduler",
                "jobs",
                "describe",
                job_name,
                f"--project={project}",
                f"--location={scheduler_location}",
            ],
            dry_run=dry_run,
        ):
            raise ReconcileError(
                f"Scheduler job {job_name} is missing in {scheduler_location}; cannot {normalized}"
            )
        current = _scheduler_job_state(
            project=project,
            location=scheduler_location,
            job_name=job_name,
            dry_run=dry_run,
        )
        if normalized == "pause" and current != "PAUSED":
            print(f"Pausing Cloud Scheduler job {job_name} in {scheduler_location}.")
            _run(
                [
                    "gcloud",
                    "scheduler",
                    "jobs",
                    "pause",
                    job_name,
                    f"--project={project}",
                    f"--location={scheduler_location}",
                    "--quiet",
                ],
                dry_run=dry_run,
            )
        elif normalized == "resume" and current == "PAUSED":
            print(f"Resuming Cloud Scheduler job {job_name} in {scheduler_location}.")
            _run(
                [
                    "gcloud",
                    "scheduler",
                    "jobs",
                    "resume",
                    job_name,
                    f"--project={project}",
                    f"--location={scheduler_location}",
                    "--quiet",
                ],
                dry_run=dry_run,
            )

    readbacks = read_scheduler_enabled(
        project=project,
        scheduler_location=scheduler_location,
        job_names=names,
        dry_run=dry_run,
    )
    for entry in readbacks:
        if entry["state"] != expected_state:
            raise ReconcileError(
                f"Scheduler job {entry['job_name']} state readback {entry['state']!r} "
                f"does not match expected {expected_state!r} after {normalized}"
            )
    return readbacks


def _legacy_jobs_for_target(platform: str, target: RuntimeTarget) -> list[str]:
    service = target.service_name
    jobs: list[str] = []
    if service.endswith("-service"):
        base = service[: -len("-service")]
        jobs.extend([f"{base}-probe-scheduler", f"{base}-precheck-scheduler"])

    if platform == "longbridge":
        scope = service
        if scope.startswith("longbridge-quant-"):
            scope = scope[len("longbridge-quant-") :]
        if scope.endswith("-service"):
            scope = scope[: -len("-service")]
        if scope and scope != service:
            jobs.append(f"lb-{scope}-backup-execution")
    elif platform == "ibkr":
        prefix = "interactive-brokers-quant-live-"
        suffix = service
        if suffix.startswith(prefix):
            suffix = suffix[len(prefix) :]
        if suffix.endswith("-service"):
            suffix = suffix[: -len("-service")]
        if suffix.startswith("u"):
            jobs.append(f"ibkr-{suffix}-backup-execution")

    return list(dict.fromkeys(jobs))


def _regions_from_json_targets(raw: str, *, field_name: str) -> list[str]:
    raw = str(raw or "").strip()
    if not raw:
        return []
    payload = _load_json_object(raw, field_name=field_name)
    entries = payload.get("targets") if isinstance(payload, Mapping) else payload
    if not isinstance(entries, list):
        raise ReconcileError(f"{field_name}.targets must be a list")
    regions: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        region = str(entry.get("region") or entry.get("cloud_run_region") or "").strip()
        if region:
            regions.append(region)
    return regions


def _scheduler_locations(*, region: str, scheduler_location: str, targets: Sequence[RuntimeTarget], env: Mapping[str, str]) -> list[str]:
    locations: list[str] = []
    if scheduler_location:
        locations.append(scheduler_location)
    if region:
        locations.append(region)
    locations.extend(_regions_from_json_targets(env.get("CLOUD_RUN_SERVICE_TARGETS_JSON") or "", field_name="CLOUD_RUN_SERVICE_TARGETS_JSON"))
    locations.extend(_regions_from_json_targets(env.get("SYNC_PLAN_JSON") or "", field_name="SYNC_PLAN_JSON"))
    locations.extend(target.region for target in targets if target.region)
    raw_extra = str(env.get("CLOUD_SCHEDULER_LEGACY_LOCATIONS") or "").strip()
    for chunk in raw_extra.replace(";", ",").split(","):
        if chunk.strip():
            locations.append(chunk.strip())
    return [item for item in dict.fromkeys(locations) if item]


def delete_legacy_schedulers(
    *,
    platform: str,
    project: str,
    region: str,
    scheduler_location: str,
    targets: Sequence[RuntimeTarget],
    env: Mapping[str, str],
    dry_run: bool,
) -> None:
    locations = _scheduler_locations(
        region=region,
        scheduler_location=scheduler_location,
        targets=targets,
        env=env,
    )
    for target in targets:
        for job in _legacy_jobs_for_target(platform, target):
            for location in locations:
                if not _run_optional(
                    [
                        "gcloud",
                        "scheduler",
                        "jobs",
                        "describe",
                        job,
                        f"--project={project}",
                        f"--location={location}",
                    ],
                    dry_run=dry_run,
                ):
                    continue
                print(f"Deleting legacy Cloud Scheduler job {job} in {location}.")
                _run(
                    [
                        "gcloud",
                        "scheduler",
                        "jobs",
                        "delete",
                        job,
                        f"--project={project}",
                        f"--location={location}",
                        "--quiet",
                    ],
                    dry_run=dry_run,
                )


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("ibkr", "longbridge"), required=True)
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", ""))
    parser.add_argument("--region", default=os.environ.get("CLOUD_RUN_REGION", ""))
    parser.add_argument("--scheduler-location", default=os.environ.get("CLOUD_SCHEDULER_LOCATION", ""))
    parser.add_argument("--expected-commit", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--expected-release-set", default=os.environ.get("EXPECTED_RELEASE_SET", ""))
    parser.add_argument("--expected-image-digest", default=os.environ.get("EXPECTED_IMAGE_DIGEST", ""))
    parser.add_argument("--service", default="", help="Restrict reconciliation to one Cloud Run service")
    parser.add_argument(
        "--scheduler-jobs",
        default="",
        help="Comma-separated Scheduler job names for enabled-state read/pause/resume",
    )
    parser.add_argument("--ensure-latest-traffic", action="store_true")
    parser.add_argument("--read-traffic", action="store_true")
    parser.add_argument("--read-scheduler-enabled", action="store_true")
    parser.add_argument(
        "--scheduler-enabled-action",
        choices=("preserve", "pause", "resume"),
        default="",
        help="Explicit pause/resume/preserve for managed Scheduler jobs",
    )
    parser.add_argument("--delete-legacy-schedulers", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if not args.project:
        raise ReconcileError("--project or GCP_PROJECT_ID is required")
    selected_action = str(args.scheduler_enabled_action or "").strip()
    needs_targets = bool(
        args.ensure_latest_traffic
        or args.read_traffic
        or args.delete_legacy_schedulers
        or args.service
    )
    targets: list[RuntimeTarget] = []
    if needs_targets:
        targets = load_targets(env=os.environ)
        if not targets:
            raise ReconcileError(
                "No Cloud Run targets resolved from SYNC_PLAN_JSON, "
                "CLOUD_RUN_SERVICE_TARGETS_JSON, or CLOUD_RUN_SERVICE"
            )
        targets = select_targets(targets, service_name=args.service)
    if args.read_traffic:
        read_traffic(
            project=args.project,
            region=args.region,
            targets=targets,
            dry_run=args.dry_run,
        )
    if args.ensure_latest_traffic:
        ensure_latest_traffic(
            project=args.project,
            region=args.region,
            targets=targets,
            expected_commit=args.expected_commit,
            expected_release_set=args.expected_release_set,
            expected_image_digest=args.expected_image_digest,
            dry_run=args.dry_run,
        )
    job_names = _parse_scheduler_jobs(args.scheduler_jobs)
    if args.read_scheduler_enabled:
        read_scheduler_enabled(
            project=args.project,
            scheduler_location=args.scheduler_location or args.region,
            job_names=job_names,
            dry_run=args.dry_run,
        )
    if selected_action:
        apply_scheduler_enabled_action(
            project=args.project,
            scheduler_location=args.scheduler_location or args.region,
            job_names=job_names,
            action=selected_action,
            dry_run=args.dry_run,
        )
    if args.delete_legacy_schedulers:
        delete_legacy_schedulers(
            platform=args.platform,
            project=args.project,
            region=args.region,
            scheduler_location=args.scheduler_location,
            targets=targets,
            env=os.environ,
            dry_run=args.dry_run,
        )
    if not any(
        (
            args.ensure_latest_traffic,
            args.read_traffic,
            args.read_scheduler_enabled,
            selected_action,
            args.delete_legacy_schedulers,
        )
    ):
        raise ReconcileError(
            "Specify --read-traffic, --ensure-latest-traffic, "
            "--read-scheduler-enabled, --scheduler-enabled-action, "
            "and/or --delete-legacy-schedulers"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconcileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
