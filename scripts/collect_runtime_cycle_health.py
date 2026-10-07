"""Bounded, injected LB PAPER collector. No default/cloud/broker transport.

The caller supplies admitted context, serving artifact contracts and explicit
read-only callbacks. This is not workflow wiring, credential discovery or an
account-health endpoint. Paired reads bound drift; they are not an atomic cloud
snapshot. All operational output is a projection, never execution authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import datetime as dt
import hashlib
import json
import math
import time
from typing import Any

from scripts.runtime_cycle_health import (
    CycleContext,
    MAX_CYCLES,
    build_cycle_health,
    coverage_complete,
    derive_coverage,
    project_cycle,
    project_resolution,
    reduce_incidents,
)
from scripts.runtime_heartbeat_policy import (
    canonical_serving_route,
    enumerate_cycle_expectations,
    load_runtime_targets,
    resolve_bound_scheduler,
)

# Match the existing daily reader's bounds. Callback implementations must also
# enforce wire/download limits before decoding; this guards injected objects.
_MAX_REPORT_BYTES = 1_048_576
_MAX_LIST_BYTES = 262_144


def _json_size(value):
    return len(json.dumps(value, separators=(",", ":"), allow_nan=False).encode())


class CollectionUnavailable(ValueError):
    """Fixed, public-safe collection reason. Never contains provider details."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _instant(value: Any) -> dt.datetime:
    parsed = (
        value
        if isinstance(value, dt.datetime)
        else dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_unavailable")
    return parsed.astimezone(dt.timezone.utc)


def _source_material(source: Mapping[str, Any]) -> tuple[dict, dict, dict]:
    """Use serving revision fields, never a staged service template."""
    try:
        target = source["target"]
        context = source["serving_context"]
        route = canonical_serving_route(context)
        resolved, error = resolve_bound_scheduler(
            target, source["jobs"], serving_context=context
        )
        if error:
            raise CollectionUnavailable(error)
        containers = context["revision"]["spec"]["containers"]
        if not isinstance(containers, list) or len(containers) != 1:
            raise ValueError()
        names = [
            row.get("name") for row in containers[0]["env"] if isinstance(row, Mapping)
        ]
        if len(names) != len(set(names)):
            raise ValueError()
        values = {row["name"]: row.get("value") for row in containers[0]["env"]}
        deployed = json.loads(values["RUNTIME_TARGET_JSON"])
        if (
            not isinstance(deployed, dict)
            or deployed.get("service_name") != route["service"]
            or str(deployed.get("account_scope", "")).upper() != "PAPER"
            or deployed.get("execution_mode") != "paper"
            or deployed.get("strategy_profile") != target.get("strategy_profile")
        ):
            raise ValueError()
        if (
            deployed.get("runtime_target_enabled") is not True
            or values.get("RUNTIME_TARGET_ENABLED", "true") != "true"
        ):
            raise CollectionUnavailable("target_not_enabled")
        # Reuse existing normalization on serving revision values only. Never
        # accept a caller's altered calendar merely because it was re-hashed.
        normalized = load_runtime_targets(
            {**values, "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "PAPER"}
        )
        if len(normalized) != 1 or any(
            normalized[0].get(key) != target.get(key)
            for key in (
                "service",
                "strategy_profile",
                "account_scope",
                "market",
                "market_calendar",
                "market_timezone",
            )
        ):
            raise ValueError()
        release = source["release_contract"]
        if release.get("source_commit") != route["source_commit"]:
            raise ValueError()
        # The binding-to-revision association must already be supplied by the
        # protected admission boundary; a valid-looking digest alone is not it.
        binding = source["source_binding"]
        if (
            binding.get("service") != route["service"]
            or binding.get("revision") != route["revision"]
        ):
            raise ValueError()
        # The producer supplies its effective existing heartbeat setting. An
        # omitted monitor policy is unknown, not permission to invent a grace.
        grace = float(
            source["monitor_policy"]["RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES"]
        )
        if not math.isfinite(grace) or not 0 <= grace <= 1440:
            raise ValueError()
        material = {
            "route": route,
            "target": {
                key: resolved.get(key)
                for key in (
                    "service",
                    "strategy_profile",
                    "account_scope",
                    "market",
                    "market_calendar",
                    "market_timezone",
                    "expected_window_policy",
                )
            },
            "scheduler": resolved["scheduler"],
            "release": {
                "source_commit": release["source_commit"],
                "strategy_revision": release["strategy_revision"],
            },
            "monitor_policy": {"publication_grace_minutes": grace},
            "runtime_target_enabled": True,
            "execution_mode": "paper",
        }
        return (
            resolved,
            material,
            {
                "id": binding["id"],
                "service": binding["service"],
                "revision": binding["revision"],
            },
        )
    except CollectionUnavailable:
        raise
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CollectionUnavailable("serving_context_unevaluable") from None


def cycle_configuration_sha256(source: Mapping[str, Any]) -> str:
    """Digest the allowlisted effective facts; this does not admit a source."""
    return _digest(_source_material(source)[1])


def _admit(source, context):
    target, material, binding = _source_material(source)
    if (
        target.get("service") != context.service
        or target.get("strategy_profile") != context.strategy_profile
        or str(target.get("account_scope", "")).upper() != "PAPER"
        or material["release"]["strategy_revision"] != context.strategy_revision
        or binding["id"] != context.source_binding_id
        or target["scheduler"]["job_name"] != context.scheduler_job_name
    ):
        raise CollectionUnavailable("source_binding_unconfirmed")
    if _digest(material) != context.configuration_sha256:
        raise CollectionUnavailable("configuration_unconfirmed")
    return (
        target,
        _digest({"material": material, "binding": binding}),
        dt.timedelta(minutes=material["monitor_policy"]["publication_grace_minutes"]),
    )


def collect_runtime_cycle_health(
    *,
    context: CycleContext,
    read_source: Callable[[], Mapping[str, Any]],
    list_page: Callable[[str, str | None], Mapping[str, Any]],
    read_report: Callable[[str], Mapping[str, Any] | None],
    required_prefixes: Sequence[str],
    since: dt.datetime,
    now: dt.datetime,
    session_dates_loader: Callable,
    expected_window: Callable[[dt.datetime], bool] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
    recovery_requests: Sequence[Mapping[str, Any]] = (),
    monotonic: Callable[[], float] = time.monotonic,
    max_seconds: float = 20.0,
) -> dict[str, Any]:
    """Collect one bounded same-configuration interval through injected reads.

    since/cutoff and required prefixes come from the admitted source/checkpoint,
    not a freshness TTL. No existing binding or initial baseline is invented.
    Missing context and material drift prevent even a partial ready projection.
    """
    preserved = deepcopy(checkpoint)

    def blocked(reason, status="unevaluable"):
        return {
            "data_status": status,
            "reason": reason,
            "cycle_health": None,
            "checkpoint": preserved,
            "execution_authority_granted": False,
            "source_snapshot_atomic": False,
        }

    try:
        since, now = _instant(since), _instant(now)
        if (
            not required_prefixes
            or len(required_prefixes) > 366
            or len(set(required_prefixes)) != len(required_prefixes)
            or any(
                not isinstance(prefix, str) or not prefix or len(prefix) > 1024
                for prefix in required_prefixes
            )
            or not 0 < max_seconds <= 60
            or len(recovery_requests) > MAX_CYCLES
        ):
            raise CollectionUnavailable("collection_scope_invalid")
        started = monotonic()
        first = read_source()
        if monotonic() - started > max_seconds:
            return blocked("collection_deadline_exceeded", "incomplete")
        target, fingerprint, grace = _admit(first, context)
        if checkpoint is not None:
            if checkpoint.get("configuration_sha256") != context.configuration_sha256:
                return blocked("configuration_changed_unconfirmed")
            old_state = checkpoint.get("state")
            if (
                not isinstance(old_state, Mapping)
                or old_state.get("target_id") != context.target_id
                or old_state.get("source_binding_id") != context.source_binding_id
            ):
                return blocked("source_binding_unconfirmed")
        expectation = enumerate_cycle_expectations(
            target,
            publication_grace=grace,
            since=since,
            now=now,
            session_dates_loader=session_dates_loader,
            expected_window=expected_window,
        )
        if expectation["state"] != "ready":
            return blocked(expectation["reason"], expectation["state"])
        pages, reads, found = {}, {}, set()
        aborted = False
        listed_bytes = 0
        for prefix in required_prefixes:
            pages[prefix] = []
            token, seen_tokens = None, set()
            for _ in range(MAX_CYCLES + 1):
                if monotonic() - started > max_seconds:
                    aborted = True
                    break
                try:
                    page = list_page(prefix, token)
                    if not isinstance(page, Mapping):
                        raise ValueError()
                    listed_bytes += _json_size(page)
                    if (
                        listed_bytes > _MAX_LIST_BYTES
                        or monotonic() - started > max_seconds
                    ):
                        aborted = True
                        break
                    page = {**page, "request_token": token}
                    pages[prefix].append(page)
                    items = page.get("items")
                    if not isinstance(items, list) or len(items) > MAX_CYCLES + 1:
                        aborted = True
                        break
                    for item in items:
                        name = item.get("name") if isinstance(item, Mapping) else None
                        if isinstance(name, str) and name.startswith(prefix):
                            found.add(name)
                    if len(found) > MAX_CYCLES:
                        aborted = True
                        break
                    following = page.get("nextPageToken")
                    if following is None:
                        break
                    if (
                        not isinstance(following, str)
                        or not following
                        or following in seen_tokens
                    ):
                        aborted = True
                        break
                    seen_tokens.add(following)
                    token = following
                except Exception:
                    aborted = True
                    break
            else:
                aborted = True
            if aborted:
                break
        for name in sorted(found)[:MAX_CYCLES]:
            if monotonic() - started > max_seconds:
                aborted = True
                break
            try:
                report = read_report(name)
                reads[name] = (
                    report if _json_size(report) <= _MAX_REPORT_BYTES else None
                )
            except Exception:
                reads[name] = None
            if monotonic() - started > max_seconds:
                return blocked("collection_deadline_exceeded", "incomplete")
        coverage = derive_coverage(
            context=context,
            required_prefixes=required_prefixes,
            pages=pages,
            reads=reads,
            read_from=since,
            read_through=now,
            aborted=aborted,
        )
        slots = expectation["slots"]
        observations = []
        for report in reads.values():
            if not isinstance(report, Mapping):
                continue
            try:
                # Current deployed producer has no admitted authenticated
                # invocation contract. Field presence cannot upgrade authority.
                report = {
                    key: value for key, value in report.items() if key != "invocation"
                }
                begin, finish = (
                    _instant(report.get("started_at")),
                    _instant(report.get("finished_at")),
                )
                selected = next(
                    (
                        slot
                        for index, slot in enumerate(slots)
                        if _instant(slot["scheduled_for"]) <= begin
                        and finish
                        < _instant(
                            slots[index + 1]["scheduled_for"]
                            if index + 1 < len(slots)
                            else expectation["schedule"]["next_due_at"]
                        )
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError()
                project_cycle(
                    context, report, scheduled_for=selected["scheduled_for"], now=now
                )
                observations.append(
                    {"scheduled_for": selected["scheduled_for"], "report": report}
                )
            except (ValueError, TypeError, KeyError):
                coverage["errors_present"] = True
        # Re-read the same admitted metadata boundary, including job identity,
        # actual schedule and source/configuration. Never mix two revisions.
        if monotonic() - started > max_seconds:
            return blocked("collection_deadline_exceeded", "incomplete")
        try:
            _, after, _ = _admit(read_source(), context)
        except Exception:
            return blocked("source_changed_during_read")
        if after != fingerprint:
            return blocked("source_changed_during_read")
        if monotonic() - started > max_seconds:
            return blocked("collection_deadline_exceeded", "incomplete")
        if coverage_complete(coverage):
            missing = sum(
                1
                for slot in slots
                if _instant(slot["deadline_at"]) <= now
                and not any(
                    row["scheduled_for"] == slot["scheduled_for"]
                    for row in observations
                )
            )
            if len(observations) + missing > MAX_CYCLES:
                return blocked("projection_limit_exceeded", "incomplete")
        payload = build_cycle_health(
            context,
            schedule=expectation["schedule"],
            coverage=coverage,
            observations=observations,
            now=now,
        )
        cycles = {json.dumps(row, sort_keys=True): row for row in payload["cycles"]}
        # The reviewed helper only synthesizes its latest slot. Compose each
        # source-enumerated expected slot to account for more than one missed run.
        for slot in slots:
            if _instant(slot["deadline_at"]) > now:
                continue
            per_slot = {
                **expectation["schedule"],
                "state": "due",
                "reason": "publication_grace_ended",
                "latest_due_at": slot["scheduled_for"],
                "deadline_at": slot["deadline_at"],
            }
            projection = build_cycle_health(
                context,
                schedule=per_slot,
                coverage=coverage,
                observations=[
                    row
                    for row in observations
                    if row["scheduled_for"] == slot["scheduled_for"]
                ],
                now=now,
            )
            for row in projection["cycles"]:
                cycles[json.dumps(row, sort_keys=True)] = row
        if len(cycles) > MAX_CYCLES:
            return blocked("projection_limit_exceeded", "incomplete")
        payload["cycles"] = list(cycles.values())
        if any(
            request.get("kind") == "same_cycle_retry_succeeded"
            for request in recovery_requests
        ):
            return blocked("invocation_provenance_unavailable")
        if recovery_requests:
            payload["resolutions"] = [
                project_resolution(context, coverage=coverage, now=now, **request)
                for request in recovery_requests
            ]
        state = reduce_incidents(
            context,
            payload["cycles"],
            resolutions=payload["resolutions"],
            previous=checkpoint["state"] if checkpoint is not None else None,
        )
        if monotonic() - started > max_seconds:
            return blocked("collection_deadline_exceeded", "incomplete")
        complete = coverage_complete(coverage)
        return {
            "data_status": "ready" if complete else "incomplete",
            "reason": None if complete else "report_coverage_incomplete",
            "cycle_health": payload,
            "checkpoint": {
                "configuration_sha256": context.configuration_sha256,
                "state": state,
            },
            "execution_authority_granted": False,
            "source_snapshot_atomic": False,
        }
    except CollectionUnavailable as exc:
        return blocked(str(exc))
    except Exception:
        return blocked("collection_unevaluable")
