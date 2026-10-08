"""LongBridge serving/scheduler policy built on shared heartbeat rules."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from typing import Any
from zoneinfo import ZoneInfo

from quant_platform_kit.common.runtime_heartbeat_policy import (
    ProfileResolver,
    SessionDatesLoader,
    WarningLogger,
    _mapping,
    _market_session_dates,
    cron_matches,
    filter_due_targets,
    filter_services_for_targets,
    load_runtime_targets as _shared_load_runtime_targets,
    match_payload_target,
    runtime_target_configuration_has_enabled_targets as _shared_has_enabled_targets,
    runtime_target_configuration_present,
    runtime_target_permits_standard_execution,
    target_key,
    target_label,
    target_latest_due_at,
)


def _scoped_target_environ(environ: Mapping[str, str]) -> Mapping[str, str]:
    if str(environ.get("RUNTIME_TARGET_JSON") or "").strip() and str(environ.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") or "").strip():
        scoped = dict(environ)
        scoped.pop("CLOUD_RUN_SERVICE_TARGETS_JSON", None)
        return scoped
    return environ


def load_runtime_targets(
    environ: Mapping[str, str],
    *,
    include_disabled: bool = False,
    profile_resolver: ProfileResolver | None = None,
) -> list[dict[str, Any]]:
    return _shared_load_runtime_targets(
        _scoped_target_environ(environ),
        include_disabled=include_disabled,
        profile_resolver=profile_resolver,
    )


def runtime_target_configuration_has_enabled_targets(environ: Mapping[str, str]) -> bool:
    return _shared_has_enabled_targets(_scoped_target_environ(environ))


def classify_business_date_schedule(
    target: Mapping[str, Any],
    *,
    now: dt.datetime,
    business_date: dt.date | None = None,
    publication_grace: dt.timedelta = dt.timedelta(minutes=30),
    market_aware: bool = True,
    session_dates_loader: SessionDatesLoader = _market_session_dates,
    within_expected_window: bool | None = None,
) -> dict[str, Any]:
    """Describe one target's schedule on one business date.

    This does not decide whether a report is acceptable. Heartbeat due filtering
    stays in ``_target_due_status``; callers that only need a skip/require bit
    should keep using ``filter_due_targets``.
    """

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(dt.timezone.utc)
    grace = publication_grace if publication_grace > dt.timedelta() else dt.timedelta()
    scheduler = target.get("scheduler")
    scheduler = scheduler if isinstance(scheduler, Mapping) else {}
    timezone_name = (
        str(target.get("market_timezone") or "").strip()
        or str(scheduler.get("timezone") or "").strip()
    )
    expected_window = "unspecified"
    if within_expected_window is True:
        expected_window = "inside"
    elif within_expected_window is False:
        expected_window = "outside"
    empty = {
        "state": "unevaluable",
        "business_date": business_date,
        "timezone": timezone_name,
        "latest_due_at": None,
        "next_due_at": None,
        "grace_ends_at": None,
        "publication_grace_ended": None,
        "expected_window": expected_window,
        "reason": "",
    }
    if not timezone_name:
        empty["reason"] = "missing_timezone"
        return empty
    try:
        market_timezone = ZoneInfo(timezone_name)
    except Exception:
        empty["reason"] = "invalid_timezone"
        return empty
    schedule = str(scheduler.get("main_time") or "").strip()
    if len(schedule.split()) != 5:
        empty["timezone"] = timezone_name
        empty["reason"] = "missing_schedule"
        return empty
    resolved_date = business_date or now_utc.astimezone(market_timezone).date()
    try:
        scheduler_timezone = ZoneInfo(str(scheduler.get("timezone") or timezone_name).strip() or timezone_name)
    except Exception:
        empty["business_date"] = resolved_date
        empty["timezone"] = timezone_name
        empty["reason"] = "invalid_timezone"
        return empty

    day_start = dt.datetime.combine(resolved_date, dt.time.min, tzinfo=market_timezone)
    day_end = day_start + dt.timedelta(days=1)
    cursor = day_start.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    if cursor < day_start.astimezone(dt.timezone.utc):
        cursor += dt.timedelta(minutes=1)
    stop = day_end.astimezone(dt.timezone.utc)
    matches: list[dt.datetime] = []
    while cursor < stop:
        local_time = cursor.astimezone(scheduler_timezone)
        try:
            matched = cron_matches(schedule, local_time)
        except (TypeError, ValueError):
            empty["business_date"] = resolved_date
            empty["timezone"] = timezone_name
            empty["reason"] = "invalid_schedule"
            return empty
        if matched and local_time.astimezone(market_timezone).date() == resolved_date:
            matches.append(cursor)
        cursor += dt.timedelta(minutes=1)

    occurred = [item for item in matches if item <= now_utc]
    upcoming = [item for item in matches if item > now_utc]
    latest_due_at = occurred[-1] if occurred else None
    next_due_at = upcoming[0] if upcoming else None
    result = {
        "state": "not_due",
        "business_date": resolved_date,
        "timezone": timezone_name,
        "latest_due_at": latest_due_at,
        "next_due_at": next_due_at,
        "grace_ends_at": None,
        "publication_grace_ended": None,
        "expected_window": expected_window,
        "reason": "no_cron_on_business_date" if not matches else "before_schedule",
    }
    if latest_due_at is None:
        return _apply_expected_window(result, within_expected_window)

    session_open: bool | None = True
    market_calendar = str(target.get("market_calendar") or "").strip()
    if market_aware and not market_calendar:
        result["state"] = "unevaluable"
        result["reason"] = "missing_market_calendar"
        return _apply_expected_window(result, within_expected_window)
    if market_aware and market_calendar:
        try:
            session_dates = session_dates_loader(
                market_calendar,
                start_date=resolved_date,
                end_date=resolved_date,
            )
        except Exception:
            result["state"] = "unevaluable"
            result["reason"] = "market_calendar_unavailable"
            return _apply_expected_window(result, within_expected_window)
        session_open = resolved_date in session_dates
    if session_open is False:
        result["state"] = "market_closed"
        result["reason"] = "market_closed"
        return _apply_expected_window(result, within_expected_window)

    grace_ends_at = latest_due_at + grace
    result["grace_ends_at"] = grace_ends_at
    result["next_due_at"] = None
    if now_utc < grace_ends_at:
        result["state"] = "within_grace"
        result["publication_grace_ended"] = False
        result["reason"] = "publication_grace_open"
        return _apply_expected_window(result, within_expected_window)
    result["state"] = "due"
    result["publication_grace_ended"] = True
    result["reason"] = "publication_grace_ended"
    return _apply_expected_window(result, within_expected_window)

def _apply_expected_window(result: dict[str, Any], within_expected_window: bool | None) -> dict[str, Any]:
    if within_expected_window is not False:
        return result
    if result["state"] not in {"not_due", "within_grace", "due"}:
        return result
    result["state"] = "outside_window"
    result["publication_grace_ended"] = None
    result["reason"] = "outside_expected_window"
    return result

def _canonical_https_origin(
    value: Any, *, service_url: bool = False
) -> tuple[str, str]:
    import re
    from urllib.parse import urlsplit

    if not isinstance(value, str) or not value or any(ord(char) < 33 for char in value):
        raise ValueError("scheduler_context_unevaluable")
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", host)
        or ".." in host
        or (service_url and parsed.path not in {"", "/"})
    ):
        raise ValueError("scheduler_context_unevaluable")
    return f"https://{host}", parsed.path or "/"

def canonical_serving_route(
    serving_context: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Validate agreement with an independently supplied serving route contract.

    The caller must obtain route_contract from the admitted serving artifact,
    never infer it from a Scheduler job, template, or repository main. This pure
    function checks agreement of supplied facts; it does not authenticate them.
    """
    import re

    try:
        context = _mapping(serving_context)
        service = _mapping(context.get("service"))
        revision = _mapping(context.get("revision"))
        contract = _mapping(context.get("route_contract"))
        name = service["metadata"]["name"]
        revision_name = revision["metadata"]["name"]
        commit = revision["metadata"]["labels"]["commit-sha"]
        traffic = service["status"]["traffic"]
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(revision_name, str)
            or not revision_name
            or not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40}", commit)
        ):
            raise ValueError()
        if (
            not isinstance(traffic, list)
            or len(traffic) != 1
            or traffic[0].get("revisionName") != revision_name
            or type(traffic[0].get("percent")) is not int
            or traffic[0]["percent"] != 100
        ):
            raise ValueError()
        ready = [
            item
            for item in revision["status"]["conditions"]
            if isinstance(item, Mapping) and item.get("type") == "Ready"
        ]
        if len(ready) != 1 or ready[0].get("status") != "True":
            raise ValueError()
        path = contract.get("path")
        if (
            contract.get("service") != name
            or contract.get("source_commit") != commit
            or contract.get("http_method") != "POST"
            or not isinstance(path, str)
            or len(path) > 128
            or not re.fullmatch(r"/[A-Za-z0-9_./~-]*", path)
            or "//" in path
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            raise ValueError()
        origin, _ = _canonical_https_origin(service["status"]["url"], service_url=True)
        return {
            "service": name,
            "origin": origin,
            "path": path,
            "http_method": "POST",
            "revision": revision_name,
            "source_commit": commit,
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("scheduler_context_unevaluable") from None

def resolve_bound_scheduler(
    target: Mapping[str, Any],
    jobs: Any,
    *,
    serving_context: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Select one exact strategy-run job or erase the unproven template cron."""
    from urllib.parse import urlsplit

    unknown = {**target, "scheduler": {}}
    try:
        binding = canonical_serving_route(serving_context)
        if target.get("service") != binding["service"] or not isinstance(jobs, list):
            raise ValueError()
    except ValueError:
        return unknown, "scheduler_context_unevaluable"
    matched = []
    for job in jobs:
        if not isinstance(job, Mapping):
            continue
        http = _mapping(job.get("httpTarget"))
        uri = http.get("uri")
        parsed = None
        try:
            parsed = urlsplit(uri) if isinstance(uri, str) else None
            origin, path = _canonical_https_origin(uri)
        except (ValueError, TypeError):
            if (
                isinstance(uri, str)
                and parsed is not None
                and parsed.hostname == urlsplit(binding["origin"]).hostname
                and parsed.path == binding["path"]
            ):
                return unknown, "scheduler_conflict"
            continue
        if origin != binding["origin"] or path != binding["path"]:
            continue
        if http.get("httpMethod") != binding["http_method"]:
            return unknown, "scheduler_conflict"
        matched.append(job)
    if not matched:
        return unknown, "scheduler_missing"
    if len(matched) != 1:
        return unknown, "scheduler_conflict"
    job = matched[0]
    if job.get("state") == "PAUSED":
        return unknown, "scheduler_paused"
    try:
        cron = job.get("schedule")
        timezone_name = job.get("timeZone")
        name = job.get("name")
        if (
            job.get("state") != "ENABLED"
            or not isinstance(cron, str)
            or len(cron.split()) != 5
            or not isinstance(name, str)
            or not name
            or len(name) > 512
            or not isinstance(timezone_name, str)
            or not timezone_name
        ):
            raise ValueError()
        cron_matches(cron, dt.datetime(2026, 1, 1, tzinfo=ZoneInfo(timezone_name)))
    except (ValueError, TypeError, KeyError):
        return unknown, "scheduler_conflict"
    return {
        **target,
        "scheduler": {"main_time": cron, "timezone": timezone_name, "job_name": name},
    }, None

def enumerate_cycle_expectations(
    target: Mapping[str, Any],
    *,
    since: dt.datetime,
    now: dt.datetime,
    coverage_through: dt.datetime | None = None,
    publication_grace: dt.timedelta = dt.timedelta(minutes=30),
    session_dates_loader: SessionDatesLoader = _market_session_dates,
    expected_window: Callable[[dt.datetime], bool] | None = None,
    max_slots: int = 20,
    horizon_days: int = 366,
) -> dict[str, Any]:
    """Enumerate a caller-established same-configuration interval, not TTL.

    since is the earliest required known configuration/checkpoint boundary. No
    historical validity or initial baseline is inferred before it. Future search
    is bounded; exhausting a bound remains incomplete, never a fabricated skip.
    """
    empty = {
        "state": "unevaluable",
        "reason": "invalid_schedule",
        "slots": [],
        "schedule": None,
    }
    try:
        if (
            since.tzinfo is None
            or now.tzinfo is None
            or since.utcoffset() is None
            or now.utcoffset() is None
            or (
                coverage_through is not None
                and (
                    coverage_through.tzinfo is None
                    or coverage_through.utcoffset() is None
                )
            )
        ):
            raise ValueError()
        since, now = since.astimezone(dt.timezone.utc), now.astimezone(dt.timezone.utc)
        coverage_end = (
            now
            if coverage_through is None
            else coverage_through.astimezone(dt.timezone.utc)
        )
        if (
            since > coverage_end
            or coverage_end > now
            or type(max_slots) is not int
            or not 1 <= max_slots <= 20
            or type(horizon_days) is not int
            or not 1 <= horizon_days <= 366
            or publication_grace < dt.timedelta()
        ):
            raise ValueError()
        if coverage_end - since > dt.timedelta(days=horizon_days):
            return {
                **empty,
                "state": "incomplete",
                "reason": "expectation_horizon_exceeded",
            }
        scheduler = _mapping(target.get("scheduler"))
        cron = str(scheduler.get("main_time") or "")
        zone = ZoneInfo(str(scheduler.get("timezone") or ""))
        market_zone = ZoneInfo(
            str(target.get("market_timezone") or scheduler.get("timezone") or "")
        )
        calendar = str(target.get("market_calendar") or "")
        if len(cron.split()) != 5 or not calendar:
            raise ValueError()
        cron_matches(cron, now.astimezone(zone))
        end = now + dt.timedelta(days=horizon_days)
        try:
            sessions = session_dates_loader(
                calendar,
                start_date=since.astimezone(market_zone).date(),
                end_date=end.astimezone(market_zone).date(),
            )
        except Exception:
            return {**empty, "reason": "market_calendar_unavailable"}
        if not isinstance(sessions, set) or any(
            type(day) is not dt.date for day in sessions
        ):
            raise ValueError()

        def eligible(instant):
            # The report/action window describes the current observation. It
            # cannot erase a source-scheduled invocation (no_action is a valid
            # receipt) or an older outstanding slot.
            return instant.astimezone(market_zone).date() in sessions and cron_matches(
                cron, instant.astimezone(zone)
            )

        cursor = since.replace(second=0, microsecond=0)
        if cursor < since:
            cursor += dt.timedelta(minutes=1)
        slots = []
        while cursor <= coverage_end:
            if eligible(cursor):
                if len(slots) == max_slots:
                    return {
                        **empty,
                        "state": "incomplete",
                        "reason": "expectation_limit_exceeded",
                        "slots": slots,
                        "overflow_at": cursor.isoformat().replace("+00:00", "Z"),
                    }
                slots.append(
                    {
                        "scheduled_for": cursor.isoformat().replace("+00:00", "Z"),
                        "deadline_at": (cursor + publication_grace)
                        .isoformat()
                        .replace("+00:00", "Z"),
                    }
                )
            cursor += dt.timedelta(minutes=1)
        next_due = None
        cursor = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        while cursor <= end:
            if eligible(cursor):
                next_due = cursor
                break
            cursor += dt.timedelta(minutes=1)
        if next_due is None:
            return {
                **empty,
                "state": "incomplete",
                "reason": "next_due_outside_horizon",
            }

        def cached_sessions(_calendar, *, start_date, end_date):
            return {day for day in sessions if start_date <= day <= end_date}

        window_now = None if expected_window is None else expected_window(now)
        if window_now is not None and type(window_now) is not bool:
            raise ValueError()
        today = classify_business_date_schedule(
            target,
            now=now,
            publication_grace=publication_grace,
            session_dates_loader=cached_sessions,
            within_expected_window=window_now,
        )
        latest_due = today["latest_due_at"]
        state, reason = today["state"], today["reason"]
        if state in {"due", "within_grace"} and latest_due is None:
            state, reason = "not_due", "before_schedule"
        schedule = {
            "state": state,
            "reason": reason,
            "timezone": str(market_zone),
        "latest_due_at": latest_due.isoformat().replace("+00:00", "Z")
        if latest_due
        else None,
        "next_due_at": next_due.isoformat().replace("+00:00", "Z"),
        "deadline_at": today["grace_ends_at"].isoformat().replace("+00:00", "Z")
        if latest_due
        else None,
        }
        return {"state": "ready", "reason": None, "slots": slots, "schedule": schedule}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return empty
