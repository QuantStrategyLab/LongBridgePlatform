#!/usr/bin/env python3
"""Project one LongBridge business day from execution reports already read.

The scheduled heartbeat remains alerts-only. This entry does not list cloud
objects, read secrets, or send notifications. Callers pass reports they have
already loaded; a later batch can feed ``execution_report_heartbeat``'s
existing read helpers into ``project_daily_runtime``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from zoneinfo import ZoneInfo

try:
    from scripts.runtime_heartbeat_policy import (
        SessionDatesLoader,
        classify_business_date_schedule,
        match_payload_target,
    )
except ModuleNotFoundError:
    from runtime_heartbeat_policy import (  # type: ignore[no-redef]
        SessionDatesLoader,
        classify_business_date_schedule,
        match_payload_target,
    )

_NON_REAL_LANES = frozenset({"dry_run", "shadow", "validation"})
_QUIET_ACTIVITIES = frozenset({"no_submission", "no_signal", "no_rebalance"})
_ORDER_ACTIVITIES = frozenset(
    {"submitted", "broker_acknowledged", "partially_filled", "filled"}
)
_ANOMALY_RANK = {
    "reconciliation_required": 0,
    "unknown": 1,
    "blocked": 2,
    "failed": 3,
}
_UNRESOLVED_ACTIVITIES = frozenset({"unknown", "reconciliation_required"})
_ORDER_RANK = {
    "filled": 0,
    "partially_filled": 1,
    "broker_acknowledged": 2,
    "submitted": 3,
}
_FILLS = {"source": "not_connected", "records": [], "count": None}


def project_daily_runtime(
    *,
    targets: Sequence[Mapping[str, Any]],
    reports: Sequence[Mapping[str, Any]],
    observed_at: dt.datetime,
    publication_grace: dt.timedelta = dt.timedelta(minutes=30),
    market_aware: bool = True,
    session_dates_loader: SessionDatesLoader | None = None,
    within_expected_window: bool | None = None,
    business_date: dt.date | None = None,
    read_errors: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return a JSON-ready daily runtime projection for the given targets."""

    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    observed = observed_at.astimezone(dt.timezone.utc)
    errors = [str(item) for item in (read_errors or []) if str(item).strip()]
    schedule_kwargs: dict[str, Any] = {
        "publication_grace": publication_grace,
        "market_aware": market_aware,
        "within_expected_window": within_expected_window,
    }
    if session_dates_loader is not None:
        schedule_kwargs["session_dates_loader"] = session_dates_loader

    normalized_targets = [dict(target) for target in targets if isinstance(target, Mapping)]
    prepared = [_prepare_report(item) for item in reports]
    unmatched: list[dict[str, Any]] = []
    matched: dict[str, list[dict[str, Any]]] = {
        _target_identity(target): [] for target in normalized_targets
    }
    for item in prepared:
        if item["reject_reason"]:
            unmatched.append(_unmatched(item))
            continue
        target_key, _reason = match_payload_target(item["payload"], normalized_targets)
        if not target_key or target_key not in matched:
            unmatched.append(_unmatched(item, reason="wrong_target"))
            continue
        matched[target_key].append(item)

    records = []
    for target in normalized_targets:
        identity = _target_identity(target)
        schedule = classify_business_date_schedule(
            target,
            now=observed,
            business_date=business_date,
            **schedule_kwargs,
        )
        records.append(
            _project_target(
                target,
                identity=identity,
                schedule=schedule,
                items=matched.get(identity, []),
                observed_at=observed,
                read_failed=bool(errors),
            )
        )

    completeness = "complete"
    if errors or any(record["completeness"] != "complete" for record in records):
        completeness = "incomplete"
    if not normalized_targets:
        completeness = "incomplete"
    return {
        "platform": "longbridge",
        "observed_at": _iso(observed),
        "completeness": completeness,
        "read_errors": errors,
        "records": records,
        "unmatched_reports": unmatched,
    }


def _project_target(
    target: Mapping[str, Any],
    *,
    identity: str,
    schedule: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    observed_at: dt.datetime,
    read_failed: bool,
) -> dict[str, Any]:
    business_date = schedule.get("business_date")
    timezone_name = str(schedule.get("timezone") or "")
    dated: list[dict[str, Any]] = []
    carried_unresolved: list[dict[str, Any]] = []
    undated_anomalies: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in items:
        run = _run_view(item)
        run_at = item["run_at"]
        if run_at is None:
            if run["activity"] in _ANOMALY_RANK:
                undated_anomalies.append(run)
            else:
                excluded.append({"run_id": run["run_id"], "reason": "missing_run_time"})
            continue
        if not isinstance(business_date, dt.date) or not timezone_name:
            excluded.append({"run_id": run["run_id"], "reason": "undated_business_day"})
            continue
        try:
            market_zone = ZoneInfo(timezone_name)
        except Exception:
            excluded.append({"run_id": run["run_id"], "reason": "invalid_timezone"})
            continue
        time_problem = _observed_run_time_problem(run, observed_at)
        if time_problem:
            excluded.append({"run_id": run["run_id"], "reason": time_problem})
            continue
        run_date = run_at.astimezone(market_zone).date()
        if run_date != business_date:
            if run["activity"] in _UNRESOLVED_ACTIVITIES:
                carried_unresolved.append(run)
            else:
                excluded.append(
                    {
                        "run_id": run["run_id"],
                        "reason": "other_business_date",
                        "business_date": run_date.isoformat(),
                    }
                )
            continue
        dated.append(run)

    dated = _dedupe_runs(dated)
    carried_unresolved = _dedupe_runs(carried_unresolved)
    latest_due_at = schedule.get("latest_due_at")
    if not isinstance(latest_due_at, dt.datetime):
        latest_due_at = None
    covering = [
        run
        for run in dated
        if latest_due_at is not None and _covers_latest_due(run, latest_due_at, observed_at)
    ]
    status, kind, completeness, conflicts = _judge(
        dated,
        undated_anomalies,
        covering=covering,
        carried_unresolved=carried_unresolved,
        schedule_state=str(schedule.get("state") or "unevaluable"),
        read_failed=read_failed,
    )
    lane_source = dated or carried_unresolved or list(undated_anomalies)
    lanes = {run["execution_lane"] for run in lane_source}
    if len(lanes) == 1:
        execution_lane = next(iter(lanes))
    else:
        execution_lane = "insufficient"
    if read_failed:
        completeness = "incomplete"
    return {
        "platform": "longbridge",
        "target_key": identity,
        "target": {
            "service": str(target.get("service") or ""),
            "strategy_profile": str(target.get("strategy_profile") or ""),
            "account_scope": str(target.get("account_scope") or ""),
        },
        "business_date": business_date.isoformat() if isinstance(business_date, dt.date) else None,
        "timezone": timezone_name,
        "observed_at": _iso(observed_at),
        "status": status,
        "kind": kind,
        "completeness": completeness,
        "execution_lane": execution_lane,
        "schedule": _schedule_view(schedule),
        "runs": dated + carried_unresolved + undated_anomalies,
        "excluded_reports": excluded,
        "conflicts": conflicts,
        "fills": dict(_FILLS),
    }


def _judge(
    dated: Sequence[Mapping[str, Any]],
    undated_anomalies: Sequence[Mapping[str, Any]],
    *,
    covering: Sequence[Mapping[str, Any]],
    carried_unresolved: Sequence[Mapping[str, Any]],
    schedule_state: str,
    read_failed: bool,
) -> tuple[str, str, str, list[str]]:
    conflicts = _same_run_conflicts([*dated, *carried_unresolved])
    dated_activities = [str(run.get("activity") or "") for run in dated]
    carried_activities = [str(run.get("activity") or "") for run in carried_unresolved]
    anomaly = _best([*dated_activities, *carried_activities], _ANOMALY_RANK)
    undated_anomaly = _best(
        [str(run.get("activity") or "") for run in undated_anomalies],
        _ANOMALY_RANK,
    )
    if anomaly is not None:
        completeness = "insufficient" if conflicts else "complete"
        return anomaly, "run", completeness, conflicts
    if undated_anomaly is not None:
        return undated_anomaly, "incomplete", "insufficient", conflicts
    if conflicts:
        return "conflict", "incomplete", "insufficient", conflicts

    covering_activities = [str(run.get("activity") or "") for run in covering]
    distinct = set(covering_activities)
    order_activity = _best(covering_activities, _ORDER_RANK)
    lanes = {str(run.get("execution_lane") or "") for run in covering}
    if order_activity is not None:
        if lanes and lanes <= _NON_REAL_LANES and len(lanes) == 1:
            return next(iter(lanes)), "run", "complete", conflicts
        return order_activity, "run", "complete", conflicts

    if (
        covering
        and lanes <= _NON_REAL_LANES
        and len(lanes) == 1
        and distinct <= (_QUIET_ACTIVITIES | {"previewed"})
    ):
        return next(iter(lanes)), "run", "complete", conflicts

    quiet = [activity for activity in covering_activities if activity in _QUIET_ACTIVITIES]
    if (
        quiet
        and len(distinct) == 1
        and schedule_state in {"due", "within_grace"}
    ):
        return next(iter(distinct)), "run", "complete", conflicts
    historical_quiet = [activity for activity in dated_activities if activity in _QUIET_ACTIVITIES]
    if (
        len(dated) == 1
        and historical_quiet
        and schedule_state not in {
            "not_due",
            "market_closed",
            "outside_window",
            "within_grace",
            "due",
        }
    ):
        return historical_quiet[0], "run", "insufficient", conflicts
    if dated and set(dated_activities) <= {"insufficient"}:
        return "insufficient", "incomplete", "insufficient", conflicts

    if schedule_state in {"not_due", "market_closed", "outside_window", "within_grace"}:
        if read_failed:
            return "read_incomplete", "incomplete", "incomplete", conflicts
        return schedule_state, "schedule", "complete", conflicts
    if schedule_state == "due":
        if read_failed:
            return "read_incomplete", "incomplete", "incomplete", conflicts
        return "missing_report", "incomplete", "incomplete", conflicts
    if read_failed:
        return "read_incomplete", "incomplete", "incomplete", conflicts
    return "insufficient", "incomplete", "insufficient", conflicts



def _same_run_conflicts(runs: Sequence[Mapping[str, Any]]) -> list[str]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        run_id = str(run.get("run_id") or "")
        if not run_id:
            continue
        grouped.setdefault(run_id, []).append(run)
    conflicts: list[str] = []
    for run_id, copies in grouped.items():
        if len({_signature(copy) for copy in copies}) < 2:
            continue
        conflicts.extend(f"{run_id}:{copy.get('activity')}" for copy in copies)
    return conflicts


def _observed_run_time_problem(run: Mapping[str, Any], observed_at: dt.datetime) -> str | None:
    started = _parse_aware(run.get("started_at"))
    finished = _parse_aware(run.get("finished_at"))
    if started is not None and finished is not None and finished < started:
        return "inverted_run_time"
    if (started is not None and started > observed_at) or (
        finished is not None and finished > observed_at
    ):
        return "future_run_time"
    return None


def _covers_latest_due(
    run: Mapping[str, Any],
    latest_due_at: dt.datetime,
    observed_at: dt.datetime,
) -> bool:
    started = _parse_aware(run.get("started_at"))
    finished = _parse_aware(run.get("finished_at"))
    instant = finished or started
    if instant is None:
        return False
    return latest_due_at <= instant <= observed_at


def _best(activities: Sequence[str], ranks: Mapping[str, int]) -> str | None:
    found = [activity for activity in activities if activity in ranks]
    if not found:
        return None
    return min(found, key=lambda activity: ranks[activity])


def _dedupe_runs(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for run in runs:
        run_id = str(run.get("run_id") or "")
        if run_id not in grouped:
            order.append(run_id)
            grouped[run_id] = []
        grouped[run_id].append(dict(run))
    selected: list[dict[str, Any]] = []
    for run_id in order:
        copies = grouped[run_id]
        signatures = {_signature(copy) for copy in copies}
        if len(signatures) == 1:
            kept = dict(copies[-1])
            objects = [
                str(copy.get("source_object"))
                for copy in copies
                if copy.get("source_object")
            ]
            kept["source_objects"] = list(dict.fromkeys(objects))
            selected.append(kept)
            continue
        selected.extend(dict(copy) for copy in copies)
    return selected


def _signature(run: Mapping[str, Any]) -> str:
    evidence = run.get("evidence") if isinstance(run.get("evidence"), Mapping) else {}
    return json.dumps(
        {
            "activity": run.get("activity"),
            "execution_lane": run.get("execution_lane"),
            "evidence": evidence,
        },
        sort_keys=True,
        default=str,
    )


def _prepare_report(item: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item.get("payload"), Mapping):
        payload = dict(item["payload"])
        source_object = _text(item.get("object_uri") or item.get("source_object"))
        object_updated_at = _parse_aware(item.get("object_updated_at"))
    else:
        payload = dict(item)
        source_object = _text(item.get("object_uri") or item.get("source_object"))
        object_updated_at = _parse_aware(item.get("object_updated_at"))
    platform = _text(payload.get("platform")).lower()
    reject_reason = ""
    if platform and platform != "longbridge":
        reject_reason = "wrong_platform"
    elif not platform:
        reject_reason = "missing_platform"
    run_at = _parse_aware(payload.get("started_at")) or _parse_aware(payload.get("finished_at"))
    return {
        "payload": payload,
        "source_object": source_object or None,
        "object_updated_at": object_updated_at,
        "run_at": run_at,
        "reject_reason": reject_reason,
    }


def _run_view(item: Mapping[str, Any]) -> dict[str, Any]:
    payload = item["payload"]
    assert isinstance(payload, Mapping)
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    lane = _execution_lane(payload, summary)
    activity = _activity(payload, summary, lane)
    pending_count = _pending_count(summary)
    receipt = payload.get("execution_receipt") if isinstance(payload.get("execution_receipt"), Mapping) else {}
    return {
        "run_id": _text(payload.get("run_id")) or None,
        "source_object": item.get("source_object"),
        "source_objects": [item["source_object"]] if item.get("source_object") else [],
        "started_at": _iso(_parse_aware(payload.get("started_at"))),
        "finished_at": _iso(_parse_aware(payload.get("finished_at"))),
        "object_updated_at": _iso(item.get("object_updated_at")),
        "report_status": _text(payload.get("status")) or None,
        "execution_lane": lane,
        "activity": activity,
        "run_time_known": item.get("run_at") is not None,
        "evidence": {
            "execution_status": _text(summary.get("execution_status")).lower() or None,
            "broker_submission_done": _optional_bool(summary.get("broker_submission_done")),
            "action_done": _optional_bool(summary.get("action_done")),
            "orders_pending_count": pending_count,
            "errors_present": _errors_present(payload),
            "receipt_outcome": _text(receipt.get("outcome")).lower() or None,
            "receipt_broker_confirmation": _text(receipt.get("broker_confirmation")).lower() or None,
        },
    }


def _activity(payload: Mapping[str, Any], summary: Mapping[str, Any], lane: str) -> str:
    execution_status = _text(summary.get("execution_status")).lower()
    broker = _optional_bool(summary.get("broker_submission_done"))
    action = _optional_bool(summary.get("action_done"))
    pending = _pending_count(summary)
    pending_positive = pending is not None and pending > 0
    errors = _errors_present(payload)
    receipt = payload.get("execution_receipt") if isinstance(payload.get("execution_receipt"), Mapping) else {}
    outcome = _text(receipt.get("outcome")).lower()
    confirmation = _text(receipt.get("broker_confirmation")).lower()
    report_status = _text(payload.get("status")).lower()

    if (
        execution_status in {"pending_reconciliation", "reconciliation_required"}
        or outcome == "reconciliation_required"
        or pending_positive
    ):
        return "reconciliation_required"
    if execution_status in {"unknown", "unknown_order"} or outcome == "unknown":
        return "unknown"
    if execution_status == "blocked" or execution_status.endswith("_blocked") or outcome == "risk_blocked":
        return "blocked"
    if errors or execution_status in {"error", "failed", "failure"} or report_status in {"error", "failed"}:
        return "failed"
    if outcome == "filled" and confirmation == "filled":
        return "filled"
    if outcome == "partially_filled" or execution_status in {"partial", "partially_filled"}:
        return "partially_filled"
    if outcome == "broker_acknowledged":
        return "broker_acknowledged"
    if outcome == "submitted" or broker is True or action is True or execution_status == "previewed":
        if lane in _NON_REAL_LANES or execution_status == "previewed":
            return "previewed"
        return "submitted"

    quiet_name = ""
    if execution_status in {"no_signal", "no_rebalance"}:
        quiet_name = execution_status
    elif outcome in {"no_signal", "no_rebalance"}:
        quiet_name = outcome
    elif execution_status in {"no_action", "skipped", "outside_market_hours"} or outcome == "no_action":
        quiet_name = "no_submission"
    if (
        quiet_name
        and broker is False
        and action is not True
        and pending == 0
        and outcome not in _ORDER_ACTIVITIES | {"reconciliation_required", "filled"}
    ):
        return quiet_name
    return "insufficient"


def _execution_lane(payload: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    runtime_target = payload.get("runtime_target") if isinstance(payload.get("runtime_target"), Mapping) else {}
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
    explicit = _explicit_lane(payload, summary, runtime_target, diagnostics)
    if explicit in _NON_REAL_LANES:
        return explicit
    if payload.get("dry_run") is True or runtime_target.get("dry_run_only") is True:
        return "dry_run"
    if explicit in {"paper", "live"}:
        return explicit
    mode = _text(runtime_target.get("execution_mode")).lower()
    if mode in {"paper", "live"}:
        return mode
    return "insufficient"


def _explicit_lane(*sources: Mapping[str, Any]) -> str:
    for source in sources:
        for key in ("execution_lane", "run_kind", "validation_label", "mode"):
            value = _text(source.get(key)).lower()
            if value in {"dry_run", "shadow", "validation", "paper", "live"}:
                return value
        if source.get("validation_only") is True:
            return "validation"
        run_source = _text(source.get("run_source")).lower()
        if run_source in {"dry_run", "shadow", "validation"}:
            return run_source
    return ""


def _pending_count(summary: Mapping[str, Any]) -> int | None:
    if "orders_pending_count" in summary and isinstance(summary.get("orders_pending_count"), int):
        return summary["orders_pending_count"]
    pending = summary.get("orders_pending")
    if isinstance(pending, list):
        return len(pending)
    return None


def _errors_present(payload: Mapping[str, Any]) -> bool:
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        return True
    error_summary = payload.get("error_summary")
    if isinstance(error_summary, Mapping):
        nested = error_summary.get("errors")
        if isinstance(nested, list) and nested:
            return True
    return bool(payload.get("error"))


def _schedule_view(schedule: Mapping[str, Any]) -> dict[str, Any]:
    business_date = schedule.get("business_date")
    return {
        "state": schedule.get("state"),
        "business_date": business_date.isoformat() if isinstance(business_date, dt.date) else None,
        "timezone": schedule.get("timezone") or None,
        "latest_due_at": _iso(schedule.get("latest_due_at")),
        "next_due_at": _iso(schedule.get("next_due_at")),
        "grace_ends_at": _iso(schedule.get("grace_ends_at")),
        "publication_grace_ended": schedule.get("publication_grace_ended"),
        "expected_window": schedule.get("expected_window"),
        "reason": schedule.get("reason") or None,
    }


def _unmatched(item: Mapping[str, Any], *, reason: str | None = None) -> dict[str, Any]:
    payload = item["payload"]
    assert isinstance(payload, Mapping)
    runtime_target = payload.get("runtime_target") if isinstance(payload.get("runtime_target"), Mapping) else {}
    return {
        "run_id": _text(payload.get("run_id")) or None,
        "platform": _text(payload.get("platform")).lower() or None,
        "service": _text(payload.get("service_name") or runtime_target.get("service_name") or payload.get("service")) or None,
        "strategy_profile": _text(payload.get("strategy_profile") or runtime_target.get("strategy_profile")) or None,
        "account_scope": _text(payload.get("account_scope") or runtime_target.get("account_scope")) or None,
        "reason": reason or item.get("reject_reason") or "wrong_target",
    }


def _target_identity(target: Mapping[str, Any]) -> str:
    service = str(target.get("service") or "").strip().lower()
    strategy = str(target.get("strategy_profile") or "").strip().lower()
    scope = str(target.get("account_scope") or "").strip().lower()
    return f"{service}|{strategy or '*'}|{scope or '*'}" if service else ""


def _parse_aware(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        text = _text(value)
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: Any) -> str | None:
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def project_listed_reports(
    *,
    targets: Sequence[Mapping[str, Any]],
    objects: Sequence[tuple[str, dt.datetime]],
    read_payload: Callable[[str], Mapping[str, Any] | None],
    read_errors: Sequence[str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Project reports using a caller-supplied reader.

    ``objects`` are ``(uri, object_updated_at)`` pairs from an existing listing.
    ``object_updated_at`` is stored as listing metadata and is not a run time.
    ``read_payload`` can be ``execution_report_heartbeat._cat_gcs_json`` in a
    later wired caller. This function does not contact a broker or cloud API.
    """

    reports: list[dict[str, Any]] = []
    errors = [str(item) for item in (read_errors or []) if str(item).strip()]
    for uri, updated_at in objects:
        try:
            payload = read_payload(uri)
        except Exception as exc:
            errors.append(f"{uri}: {type(exc).__name__}")
            continue
        if not isinstance(payload, Mapping):
            errors.append(f"{uri}: unreadable")
            continue
        reports.append(
            {
                "payload": payload,
                "object_uri": uri,
                "object_updated_at": updated_at,
            }
        )
    return project_daily_runtime(
        targets=targets,
        reports=reports,
        read_errors=errors,
        **kwargs,
    )


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Project a LongBridge daily runtime record from local report JSON.")
    parser.add_argument("--reports", required=True, help="JSON array of execution reports")
    parser.add_argument("--targets", required=True, help="JSON array of normalized runtime targets")
    parser.add_argument("--observed-at", required=True, help="Timezone-aware observation time")
    parser.add_argument("--business-date", default="", help="Optional YYYY-MM-DD applied to every target")
    parser.add_argument("--publication-grace-minutes", type=float, default=30)
    parser.add_argument("--expected-window", choices=("unspecified", "inside", "outside"), default="unspecified")
    parser.add_argument("--market-aware", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--read-error", action="append", default=[])
    args = parser.parse_args(list(argv) if argv is not None else None)
    observed_at = _parse_aware(args.observed_at)
    if observed_at is None:
        print("observed-at must be a timezone-aware timestamp", file=sys.stderr)
        return 2
    if args.publication_grace_minutes < 0:
        print("publication grace must be non-negative", file=sys.stderr)
        return 2
    business_date = None
    if args.business_date:
        try:
            business_date = dt.date.fromisoformat(args.business_date)
        except ValueError:
            print("business-date must be YYYY-MM-DD", file=sys.stderr)
            return 2
    window = {"inside": True, "outside": False}.get(args.expected_window)
    try:
        reports = _load_json(args.reports)
        targets = _load_json(args.targets)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"unable to read projection input: {type(exc).__name__}", file=sys.stderr)
        return 2
    if not isinstance(reports, list) or not isinstance(targets, list):
        print("reports and targets must be JSON arrays", file=sys.stderr)
        return 2
    projected = project_daily_runtime(
        targets=targets,
        reports=reports,
        observed_at=observed_at,
        business_date=business_date,
        publication_grace=dt.timedelta(minutes=args.publication_grace_minutes),
        market_aware=args.market_aware,
        within_expected_window=window,
        read_errors=args.read_error,
    )
    json.dump(projected, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
