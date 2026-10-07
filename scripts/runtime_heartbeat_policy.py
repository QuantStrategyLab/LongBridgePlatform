"""Per-runtime-target schedule and market-session policy for heartbeat checks."""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections.abc import Callable, Mapping
from typing import Any
from zoneinfo import ZoneInfo


SessionDatesLoader = Callable[..., set[dt.date]]
WarningLogger = Callable[[str], None]
ProfileResolver = Callable[[str], str]

_LATEST_DUE_AT_KEY = "_heartbeat_latest_due_at"
_MARKET_DEFAULTS = {
    "US": ("NYSE", "America/New_York"),
    "HK": ("XHKG", "Asia/Hong_Kong"),
    "CN": ("SSE", "Asia/Shanghai"),
    "SG": ("XSES", "Asia/Singapore"),
    "CRYPTO": ("24/7", "UTC"),
}
_TIMEZONE_MARKETS = {
    "America/New_York": "US",
    "Asia/Hong_Kong": "HK",
    "Asia/Shanghai": "CN",
    "Asia/Singapore": "SG",
}
_PLATFORM_MARKETS = {
    "schwab": "US",
    "firstrade": "US",
    "qmt": "CN",
    "binance": "CRYPTO",
}


def _split_values(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = str(raw).replace(";", ",").replace("\n", ",").split(",")
    return [value.strip() for value in values if value.strip()]


def _enabled(value: Any, *, default: bool = True) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "n", "off"}


def runtime_target_permits_standard_execution(runtime_target: Mapping[str, Any]) -> bool:
    """Return whether the generic execution heartbeat should require a receipt."""

    continuity = _mapping(runtime_target.get("live_continuity"))
    state = str(continuity.get("state") or "").strip().upper()
    return not state or state in {"ACTIVE_LKG", "ROLLBACK_LKG"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _target_field(
    item: Mapping[str, Any],
    defaults: Mapping[str, Any],
    *names: str,
) -> Any:
    for source in (
        item,
        _mapping(item.get("env")),
        defaults,
        _mapping(defaults.get("env")),
    ):
        for name in names:
            if name in source:
                return source[name]
    return None


def _runtime_target(
    item: Mapping[str, Any],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    value = _target_field(item, defaults, "runtime_target", "runtime_target_json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"runtime target JSON is invalid: {exc}") from exc
    if isinstance(value, Mapping):
        return dict(value)
    return dict(item)


def _first_value(sources: list[Mapping[str, Any]], keys: tuple[str, ...]) -> str:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _suffix_value(sources: list[Mapping[str, Any]], suffix: str) -> str:
    for source in sources:
        for key, value in source.items():
            if str(key).upper().endswith(suffix) and value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _service_values(
    item: Mapping[str, Any],
    runtime_target: Mapping[str, Any],
    defaults: Mapping[str, Any],
    environ: Mapping[str, str],
) -> list[str]:
    value = _target_field(
        item,
        defaults,
        "service",
        "service_name",
        "cloud_run_service",
    )
    if value is None:
        value = _first_value(
            [runtime_target],
            ("service", "service_name", "cloud_run_service"),
        )
    if value:
        return _split_values(str(value))
    services = _split_values(environ.get("CLOUD_RUN_SERVICES"))
    services.extend(_split_values(environ.get("CLOUD_RUN_SERVICE")))
    return list(dict.fromkeys(services))


def _normalize_target(
    item: Mapping[str, Any],
    runtime_target: Mapping[str, Any],
    defaults: Mapping[str, Any],
    service: str,
    environ: Mapping[str, str],
    *,
    use_global_market_fallback: bool,
    profile_resolver: ProfileResolver | None,
) -> dict[str, Any]:
    sources = [
        runtime_target,
        item,
        _mapping(item.get("env")),
        defaults,
        _mapping(defaults.get("env")),
    ]
    scheduler = next(
        (
            value
            for source in sources
            if isinstance((value := source.get("scheduler")), dict)
        ),
        {},
    )
    scheduler = dict(scheduler)
    if not str(scheduler.get("main_time") or "").strip():
        main_time = _target_field(
            item,
            defaults,
            "CLOUD_SCHEDULER_MAIN_TIME",
            "cloud_scheduler_main_time",
        )
        if main_time is None:
            main_time = environ.get("CLOUD_SCHEDULER_MAIN_TIME")
        if main_time is not None and str(main_time).strip():
            scheduler["main_time"] = str(main_time).strip()
    account_scope = _first_value(
        sources,
        (
            "account_scope",
            "account_group",
            "account_region",
            "ACCOUNT_GROUP",
            "ACCOUNT_REGION",
        ),
    )
    target_market_timezone = (
        _first_value(sources, ("market_timezone", "MARKET_TIMEZONE"))
        or _suffix_value(sources, "_MARKET_TIMEZONE")
    )
    global_market_timezone = (
        str(environ.get("RUNTIME_HEARTBEAT_MARKET_TIMEZONE") or "").strip()
        or _suffix_value([environ], "_MARKET_TIMEZONE")
    )
    market_timezone = target_market_timezone or (
        global_market_timezone if use_global_market_fallback else ""
    )
    market = (
        _first_value(sources, ("market", "MARKET"))
        or _suffix_value(sources, "_MARKET")
    ).upper()
    if not market:
        market = _TIMEZONE_MARKETS.get(target_market_timezone, "")
    if market not in _MARKET_DEFAULTS:
        market = _TIMEZONE_MARKETS.get(str(scheduler.get("timezone") or "").strip(), "")
    if not market:
        platform_id = _first_value(sources, ("platform_id",)).lower()
        market = _PLATFORM_MARKETS.get(platform_id, "")
    if not market and account_scope.upper() in {"US", "HK", "CN"}:
        market = account_scope.upper()
    if market not in _MARKET_DEFAULTS and use_global_market_fallback:
        market = str(environ.get("RUNTIME_HEARTBEAT_MARKET") or "").strip().upper()
    target_market_calendar = (
        _first_value(sources, ("market_calendar", "MARKET_CALENDAR"))
        or _suffix_value(sources, "_MARKET_CALENDAR")
    )
    global_market_calendar = (
        str(environ.get("RUNTIME_HEARTBEAT_MARKET_CALENDAR") or "").strip()
        or _suffix_value([environ], "_MARKET_CALENDAR")
    )
    default_calendar, default_timezone = _MARKET_DEFAULTS.get(market, ("", ""))
    market_calendar = (
        target_market_calendar
        or (global_market_calendar if use_global_market_fallback else "")
        or default_calendar
    )
    market_timezone = (
        market_timezone
        or default_timezone
        or str(scheduler.get("timezone") or "").strip()
    )
    if scheduler and not str(scheduler.get("timezone") or "").strip() and market_timezone:
        scheduler["timezone"] = market_timezone
    strategy_profile = _first_value(
        sources,
        ("strategy_profile", "strategy", "profile"),
    )
    if strategy_profile and profile_resolver is not None:
        strategy_profile = profile_resolver(strategy_profile)
    return {
        "service": str(service).strip(),
        "strategy_profile": strategy_profile,
        "account_scope": account_scope,
        "scheduler": scheduler,
        "market": market,
        "market_calendar": market_calendar,
        "market_timezone": market_timezone,
    }


def _load_runtime_target_items(
    environ: Mapping[str, str],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any]]:
    raw_runtime_target = str(environ.get("RUNTIME_TARGET_JSON") or "").strip()
    raw_targets = str(environ.get("CLOUD_RUN_SERVICE_TARGETS_JSON") or "").strip()
    if raw_runtime_target and str(environ.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") or "").strip():
        # A scoped environment's current target takes precedence over the
        # repository's legacy multi-target inventory.
        raw_targets = ""
    items: list[Mapping[str, Any]] = []
    defaults: Mapping[str, Any] = {}
    if raw_targets:
        try:
            payload = json.loads(raw_targets)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CLOUD_RUN_SERVICE_TARGETS_JSON is invalid: {exc}") from exc
        if isinstance(payload, Mapping):
            targets = payload.get("targets")
            defaults = _mapping(payload.get("defaults"))
        else:
            targets = payload
        if isinstance(targets, list):
            items = [target for target in targets if isinstance(target, Mapping)]
        else:
            raise ValueError(
                "CLOUD_RUN_SERVICE_TARGETS_JSON must be an array or object with targets"
            )

    if not items:
        if raw_runtime_target:
            try:
                runtime_target = json.loads(raw_runtime_target)
            except json.JSONDecodeError as exc:
                raise ValueError(f"RUNTIME_TARGET_JSON is invalid: {exc}") from exc
            if isinstance(runtime_target, Mapping):
                items = [runtime_target]
            else:
                raise ValueError("RUNTIME_TARGET_JSON must decode to an object")
    return items, defaults


def runtime_target_configuration_present(environ: Mapping[str, str]) -> bool:
    return bool(
        str(environ.get("CLOUD_RUN_SERVICE_TARGETS_JSON") or "").strip()
        or str(environ.get("RUNTIME_TARGET_JSON") or "").strip()
    )


def runtime_target_configuration_has_enabled_targets(
    environ: Mapping[str, str],
) -> bool:
    items, defaults = _load_runtime_target_items(environ)
    if not items:
        return False
    expected_scope = str(environ.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") or "").strip().lower()
    for item in items:
        runtime_target = _runtime_target(item, defaults)
        target_scope = _first_value(
            [
                runtime_target,
                item,
                _mapping(item.get("env")),
                defaults,
                _mapping(defaults.get("env")),
            ],
            (
                "account_scope",
                "account_group",
                "account_region",
                "ACCOUNT_GROUP",
                "ACCOUNT_REGION",
            ),
        )
        if expected_scope and target_scope and target_scope.lower() != expected_scope:
            continue
        enabled_value = _target_field(
            item,
            defaults,
            "runtime_target_enabled",
            "RUNTIME_TARGET_ENABLED",
        )
        if enabled_value is None:
            enabled_value = runtime_target.get("runtime_target_enabled")
        if _enabled(enabled_value) and runtime_target_permits_standard_execution(runtime_target):
            return True
    return False


def load_runtime_targets(
    environ: Mapping[str, str],
    *,
    include_disabled: bool = False,
    profile_resolver: ProfileResolver | None = None,
) -> list[dict[str, Any]]:
    items, defaults = _load_runtime_target_items(environ)

    expected_scope = str(environ.get("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE") or "").strip().lower()
    eligible: list[tuple[Mapping[str, Any], dict[str, Any], bool]] = []
    for item in items:
        runtime_target = _runtime_target(item, defaults)
        enabled_value = _target_field(
            item,
            defaults,
            "runtime_target_enabled",
            "RUNTIME_TARGET_ENABLED",
        )
        if enabled_value is None:
            enabled_value = runtime_target.get("runtime_target_enabled")
        enabled = (
            _enabled(enabled_value)
            and runtime_target_permits_standard_execution(runtime_target)
        )
        if not enabled and not include_disabled:
            continue
        target_scope = _first_value(
            [
                runtime_target,
                item,
                _mapping(item.get("env")),
                defaults,
                _mapping(defaults.get("env")),
            ],
            (
                "account_scope",
                "account_group",
                "account_region",
                "ACCOUNT_GROUP",
                "ACCOUNT_REGION",
            ),
        )
        if expected_scope and target_scope and target_scope.lower() != expected_scope:
            continue
        eligible.append((item, runtime_target, enabled))

    normalized: list[dict[str, Any]] = []
    enabled_count = sum(1 for _item, _runtime_target_value, enabled in eligible if enabled)
    for item, runtime_target, enabled in eligible:
        for service in _service_values(item, runtime_target, defaults, environ):
            target = _normalize_target(
                item,
                runtime_target,
                defaults,
                service,
                environ,
                use_global_market_fallback=enabled_count == 1,
                profile_resolver=profile_resolver,
            )
            if include_disabled:
                target["enabled"] = enabled
            key = target_key(target)
            if key and all(target_key(existing) != key for existing in normalized):
                normalized.append(target)
    return normalized


def target_key(target: Mapping[str, Any]) -> str:
    service = str(target.get("service") or "").strip().lower()
    strategy = str(target.get("strategy_profile") or "").strip().lower()
    scope = str(target.get("account_scope") or "").strip().lower()
    return f"{service}|{strategy or '*'}|{scope or '*'}" if service else ""


def target_label(target: Mapping[str, Any]) -> str:
    service = str(target.get("service") or "").strip() or "<unknown-service>"
    strategy = str(target.get("strategy_profile") or "").strip()
    scope = str(target.get("account_scope") or "").strip()
    qualifiers = "/".join(value for value in (strategy, scope) if value)
    return f"{service}[{qualifiers}]" if qualifiers else service


def target_latest_due_at(target: Mapping[str, Any]) -> dt.datetime | None:
    value = target.get(_LATEST_DUE_AT_KEY)
    return value if isinstance(value, dt.datetime) else None


def _payload_value(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    runtime_target = payload.get("runtime_target")
    sources = [payload]
    if isinstance(runtime_target, Mapping):
        sources.append(runtime_target)
    return _first_value(sources, keys)


def match_payload_target(
    payload: Mapping[str, Any],
    targets: list[dict[str, Any]],
) -> tuple[str | None, str]:
    service = _payload_value(
        payload,
        ("service_name", "service", "cloud_run_service"),
    ).lower()
    strategy = _payload_value(
        payload,
        ("strategy_profile", "strategy", "profile"),
    ).lower()
    scope = _payload_value(
        payload,
        ("account_scope", "account_group", "account_region"),
    ).lower()
    for target in targets:
        expected_service = str(target.get("service") or "").strip().lower()
        expected_strategy = str(target.get("strategy_profile") or "").strip().lower()
        expected_scope = str(target.get("account_scope") or "").strip().lower()
        if service != expected_service:
            continue
        if expected_strategy and strategy != expected_strategy:
            continue
        if expected_scope and scope != expected_scope:
            continue
        return target_key(target), "matched runtime target"
    return None, (
        f"runtime_target={service or '-'}/{strategy or '-'}/{scope or '-'}"
    )


def _cron_token_value(token: str, *, names: dict[str, int] | None = None) -> int:
    normalized = token.strip().lower()
    if names and normalized in names:
        return names[normalized]
    return int(normalized)


def _cron_field_values(
    field: str,
    *,
    minimum: int,
    maximum: int,
    names: dict[str, int] | None = None,
) -> set[int] | None:
    text = str(field or "").strip().lower()
    if text in {"", "*"}:
        return None
    values: set[int] = set()
    for raw_part in text.split(","):
        part = raw_part.strip()
        if not part:
            continue
        base, raw_step = part, "1"
        if "/" in part:
            base, raw_step = part.split("/", 1)
        step = max(1, int(raw_step))
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            start = _cron_token_value(raw_start, names=names)
            end = _cron_token_value(raw_end, names=names)
        else:
            start = end = _cron_token_value(base, names=names)
        for value in range(start, end + 1, step):
            if minimum <= value <= maximum:
                values.add(value)
            elif maximum == 6 and value == 7:
                values.add(0)
    return values


def cron_matches(schedule: str, value: dt.datetime) -> bool:
    fields = str(schedule or "").split()
    if len(fields) == 2:
        fields.extend(("*", "*", "*"))
    if len(fields) != 5:
        return False
    minute, hour, day_of_month, month, day_of_week = fields
    dow_names = {
        "sun": 0,
        "mon": 1,
        "tue": 2,
        "wed": 3,
        "thu": 4,
        "fri": 5,
        "sat": 6,
    }
    minute_values = _cron_field_values(minute, minimum=0, maximum=59)
    hour_values = _cron_field_values(hour, minimum=0, maximum=23)
    dom_values = _cron_field_values(day_of_month, minimum=1, maximum=31)
    month_values = _cron_field_values(month, minimum=1, maximum=12)
    dow_values = _cron_field_values(day_of_week, minimum=0, maximum=6, names=dow_names)
    if minute_values is not None and value.minute not in minute_values:
        return False
    if hour_values is not None and value.hour not in hour_values:
        return False
    if month_values is not None and value.month not in month_values:
        return False
    dom_matches = dom_values is None or value.day in dom_values
    dow_matches = dow_values is None or value.isoweekday() % 7 in dow_values
    if dom_values is not None and dow_values is not None:
        return dom_matches or dow_matches
    return dom_matches and dow_matches


def _market_session_dates(
    calendar: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
) -> set[dt.date]:
    import pandas_market_calendars as mcal

    schedule = mcal.get_calendar(calendar).schedule(
        start_date=start_date,
        end_date=end_date,
    )
    return {value.date() for value in schedule.index}


def _target_due_status(
    target: Mapping[str, Any],
    *,
    since: dt.datetime,
    now: dt.datetime,
    market_aware: bool,
    session_dates_loader: SessionDatesLoader,
    warning_logger: WarningLogger,
    publication_grace: dt.timedelta,
) -> tuple[bool | None, dt.datetime | None]:
    scheduler = target.get("scheduler")
    if not isinstance(scheduler, Mapping):
        return None, None
    schedule = str(scheduler.get("main_time") or "").strip()
    fields = schedule.split()
    if len(fields) != 5:
        return None, None
    timezone_name = str(scheduler.get("timezone") or "UTC").strip() or "UTC"
    try:
        scheduler_timezone = ZoneInfo(timezone_name)
    except Exception as exc:  # noqa: BLE001
        warning_logger(
            f"Unable to evaluate heartbeat scheduler timezone {timezone_name}: "
            f"{type(exc).__name__}; keeping target required"
        )
        return None, None

    since_utc = since.astimezone(dt.timezone.utc)
    now_utc = now.astimezone(dt.timezone.utc)
    cursor = since_utc.replace(second=0, microsecond=0)
    if cursor < since_utc:
        cursor += dt.timedelta(minutes=1)
    matured_at = now_utc - max(publication_grace, dt.timedelta())
    cron_due_at: list[dt.datetime] = []
    while cursor <= now_utc:
        local_time = cursor.astimezone(scheduler_timezone)
        try:
            matches = cron_matches(schedule, local_time)
        except (TypeError, ValueError) as exc:
            warning_logger(
                f"Unable to evaluate heartbeat cron for {target_label(target)}: "
                f"{type(exc).__name__}; keeping target required"
            )
            return None, None
        if matches and cursor <= matured_at:
            cron_due_at.append(cursor)
        cursor += dt.timedelta(minutes=1)
    latest_cron_due_at = cron_due_at[-1] if cron_due_at else None

    session_dates: set[dt.date] | None = None
    market_calendar = str(target.get("market_calendar") or "").strip()
    market_timezone_name = (
        str(target.get("market_timezone") or "").strip() or timezone_name
    )
    if market_aware and market_calendar:
        try:
            market_timezone = ZoneInfo(market_timezone_name)
            session_dates = session_dates_loader(
                market_calendar,
                start_date=since_utc.astimezone(market_timezone).date(),
                end_date=now_utc.astimezone(market_timezone).date(),
            )
        except Exception as exc:  # noqa: BLE001
            warning_logger(
                f"Unable to evaluate heartbeat market calendar {market_calendar}: "
                f"{type(exc).__name__}; keeping target required"
            )
            return None, latest_cron_due_at

    latest_due_at = latest_cron_due_at
    if session_dates is not None:
        market_timezone = ZoneInfo(market_timezone_name)
        latest_due_at = next(
            (
                due_at
                for due_at in reversed(cron_due_at)
                if due_at.astimezone(market_timezone).date() in session_dates
            ),
            None,
        )
    return latest_due_at is not None, latest_due_at


def filter_due_targets(
    targets: list[dict[str, Any]],
    *,
    since: dt.datetime,
    now: dt.datetime,
    market_aware: bool = True,
    publication_grace: dt.timedelta = dt.timedelta(minutes=30),
    session_dates_loader: SessionDatesLoader = _market_session_dates,
    warning_logger: WarningLogger = lambda message: print(message, file=sys.stderr),
) -> tuple[list[dict[str, Any]], bool]:
    due: list[dict[str, Any]] = []
    evaluated = False
    for target in targets:
        status, latest_due_at = _target_due_status(
            target,
            since=since,
            now=now,
            market_aware=market_aware,
            session_dates_loader=session_dates_loader,
            warning_logger=warning_logger,
            publication_grace=publication_grace,
        )
        if status is not None:
            evaluated = True
        if status is not False:
            due_target = dict(target)
            if latest_due_at is not None:
                due_target[_LATEST_DUE_AT_KEY] = latest_due_at
            due.append(due_target)
    return due, evaluated


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


def filter_services_for_targets(
    services: list[str],
    targets: list[dict[str, Any]],
    *,
    all_targets: list[dict[str, Any]] | None = None,
) -> list[str]:
    if not targets:
        return services
    target_services = {
        str(target.get("service") or "").strip()
        for target in targets
        if str(target.get("service") or "").strip()
    }
    configured_services = {
        str(target.get("service") or "").strip()
        for target in (all_targets or targets)
        if str(target.get("service") or "").strip()
    }
    return [
        service
        for service in services
        if service not in configured_services or service in target_services
    ]


# Source-policy helpers for the optional cycle-health adapter. Existing callers
# do not acquire a route contract merely by importing these functions.
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
        ):
            raise ValueError()
        since, now = since.astimezone(dt.timezone.utc), now.astimezone(dt.timezone.utc)
        if (
            since > now
            or type(max_slots) is not int
            or not 1 <= max_slots <= 20
            or type(horizon_days) is not int
            or not 1 <= horizon_days <= 366
            or publication_grace < dt.timedelta()
        ):
            raise ValueError()
        if now - since > dt.timedelta(days=horizon_days):
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
        while cursor <= now:
            if eligible(cursor):
                if len(slots) == max_slots:
                    return {
                        **empty,
                        "state": "incomplete",
                        "reason": "expectation_limit_exceeded",
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
        latest = slots[-1] if slots else None
        state, reason = today["state"], today["reason"]
        if state in {"due", "within_grace"} and latest is None:
            state, reason = "not_due", "before_schedule"
        schedule = {
            "state": state,
            "reason": reason,
            "timezone": str(market_zone),
            "latest_due_at": latest["scheduled_for"] if latest else None,
            "next_due_at": next_due.isoformat().replace("+00:00", "Z"),
            "deadline_at": latest["deadline_at"] if latest else None,
        }
        return {"state": "ready", "reason": None, "slots": slots, "schedule": schedule}
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return empty
