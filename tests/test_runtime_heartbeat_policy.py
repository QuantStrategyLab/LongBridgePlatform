from __future__ import annotations

import datetime as dt
import json

import pytest

from scripts.runtime_heartbeat_policy import (
    classify_business_date_schedule,
    filter_due_targets,
    load_runtime_targets,
    match_payload_target,
    runtime_target_configuration_present,
    runtime_target_configuration_has_enabled_targets,
    target_key,
    target_latest_due_at,
)
from scripts import execution_report_heartbeat as heartbeat


@pytest.mark.parametrize("legacy_inventory", [
    [{"service": "old-sg", "account_scope": "SG", "runtime_target_enabled": False}],
    [{"service": "old-paper", "account_scope": "PAPER"}],
])
def test_scoped_current_target_precedes_legacy_inventory(legacy_inventory):
    environ = {
        "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "SG",
        "RUNTIME_TARGET_JSON": json.dumps({
            "service_name": "current-sg",
            "strategy_profile": "soxl_soxx_trend_income",
            "account_scope": "SG",
            "live_continuity": {"state": "ACTIVE_LKG"},
        }),
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(legacy_inventory),
    }

    assert runtime_target_configuration_has_enabled_targets(environ)
    targets = load_runtime_targets(environ)
    assert [target["service"] for target in targets] == ["current-sg"]
    assert targets[0]["strategy_profile"] == "soxl_soxx_trend_income"


def test_unscoped_heartbeat_keeps_multi_target_inventory():
    environ = {
        "RUNTIME_TARGET_JSON": json.dumps({"service_name": "single"}),
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps([
            {"service": "first"}, {"service": "second"},
        ]),
    }
    assert [target["service"] for target in load_runtime_targets(environ)] == [
        "first", "second",
    ]


def test_invalid_scoped_target_does_not_fall_back_to_legacy_inventory():
    with pytest.raises(ValueError, match="RUNTIME_TARGET_JSON"):
        load_runtime_targets({
            "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "SG",
            "RUNTIME_TARGET_JSON": "invalid",
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps([{"service": "old-sg"}]),
        })


def _target(
    *,
    service: str,
    strategy: str,
    scope: str,
    timezone: str,
    calendar: str,
) -> dict[str, object]:
    return {
        "service": service,
        "runtime_target": {
            "service_name": service,
            "strategy_profile": strategy,
            "account_scope": scope,
            "scheduler": {
                "timezone": timezone,
                "main_time": "45 15 * * *",
            },
            "market_calendar": calendar,
            "market_timezone": timezone,
        },
    }


def test_due_targets_use_each_strategy_market_calendar() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        _target(
                            service="svc-us",
                            strategy="us-strategy",
                            scope="US",
                            timezone="America/New_York",
                            calendar="NYSE",
                        ),
                        _target(
                            service="svc-hk",
                            strategy="hk-strategy",
                            scope="HK",
                            timezone="Asia/Hong_Kong",
                            calendar="XHKG",
                        ),
                    ]
                }
            )
        }
    )

    due, evaluated = filter_due_targets(
        targets,
        since=dt.datetime(2026, 7, 3, 0, 0, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 7, 3, 22, 0, tzinfo=dt.timezone.utc),
        session_dates_loader=lambda calendar, **_kwargs: (
            {dt.date(2026, 7, 3)} if calendar == "XHKG" else set()
        ),
    )

    assert evaluated is True
    assert [target["strategy_profile"] for target in due] == ["hk-strategy"]


def test_real_exchange_calendars_distinguish_us_holiday_from_hk_session() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        _target(
                            service="svc-us",
                            strategy="us-strategy",
                            scope="US",
                            timezone="America/New_York",
                            calendar="NYSE",
                        ),
                        _target(
                            service="svc-hk",
                            strategy="hk-strategy",
                            scope="HK",
                            timezone="Asia/Hong_Kong",
                            calendar="XHKG",
                        ),
                    ]
                }
            )
        }
    )

    due, evaluated = filter_due_targets(
        targets,
        since=dt.datetime(2026, 7, 3, 0, 0, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 7, 3, 22, 0, tzinfo=dt.timezone.utc),
    )

    assert evaluated is True
    assert [target["strategy_profile"] for target in due] == ["hk-strategy"]


def test_july_29_us_month_end_target_is_due_at_1545_eastern() -> None:
    raw_target = _target(
        service="svc-us-monthly",
        strategy="us-monthly",
        scope="US",
        timezone="America/New_York",
        calendar="NYSE",
    )
    raw_target["runtime_target"]["scheduler"]["main_time"] = "45 15 25-29 * *"
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {"targets": [raw_target]}
            )
        }
    )

    due, evaluated = filter_due_targets(
        targets,
        since=dt.datetime(2026, 7, 29, 19, 40, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 7, 29, 20, 20, tzinfo=dt.timezone.utc),
    )

    assert evaluated is True
    assert [target["strategy_profile"] for target in due] == ["us-monthly"]
    assert target_latest_due_at(due[0]) == dt.datetime(
        2026,
        7,
        29,
        19,
        45,
        tzinfo=dt.timezone.utc,
    )


def test_neutral_daily_heartbeat_tracks_latest_due_time_per_market() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        _target(
                            service="svc-us",
                            strategy="us-strategy",
                            scope="US",
                            timezone="America/New_York",
                            calendar="NYSE",
                        ),
                        _target(
                            service="svc-hk",
                            strategy="hk-strategy",
                            scope="HK",
                            timezone="Asia/Hong_Kong",
                            calendar="XHKG",
                        ),
                    ]
                }
            )
        }
    )

    due, evaluated = filter_due_targets(
        targets,
        since=dt.datetime(2026, 7, 28, 10, 20, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 7, 29, 22, 20, tzinfo=dt.timezone.utc),
        session_dates_loader=lambda _calendar, **_kwargs: {
            dt.date(2026, 7, 28),
            dt.date(2026, 7, 29),
        },
    )

    assert evaluated is True
    assert {
        target["strategy_profile"]: target_latest_due_at(target)
        for target in due
    } == {
        "us-strategy": dt.datetime(
            2026,
            7,
            29,
            19,
            45,
            tzinfo=dt.timezone.utc,
        ),
        "hk-strategy": dt.datetime(
            2026,
            7,
            29,
            7,
            45,
            tzinfo=dt.timezone.utc,
        ),
    }


def test_same_service_strategies_require_distinct_reports() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        _target(
                            service="shared-service",
                            strategy="strategy-a",
                            scope="US",
                            timezone="America/New_York",
                            calendar="NYSE",
                        ),
                        _target(
                            service="shared-service",
                            strategy="strategy-b",
                            scope="US",
                            timezone="America/New_York",
                            calendar="NYSE",
                        ),
                    ]
                }
            )
        }
    )

    matched, reason = match_payload_target(
        {
            "service_name": "shared-service",
            "strategy_profile": "strategy-a",
            "account_scope": "US",
        },
        targets,
    )

    assert matched == target_key(targets[0])
    assert reason == "matched runtime target"
    missing_strategy, _ = match_payload_target(
        {"service_name": "shared-service", "account_scope": "US"},
        targets,
    )
    assert missing_strategy is None

    matched_by_heartbeat, matched_key, _ = heartbeat._payload_matches(
        {
            "service_name": "shared-service",
            "strategy_profile": "strategy-a",
            "account_scope": "US",
        },
        ["shared-service"],
        required_targets=targets,
    )
    assert matched_by_heartbeat is True
    assert matched_key == target_key(targets[0])
    missing_by_heartbeat, _, _ = heartbeat._payload_matches(
        {"service_name": "shared-service", "account_scope": "US"},
        ["shared-service"],
        required_targets=targets,
    )
    assert missing_by_heartbeat is False


def test_scheduler_timezone_beats_account_region_when_market_is_not_explicit() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        {
                            "service": "longbridge-sg-us-service",
                            "account_scope": "SG",
                            "runtime_target": {
                                "service_name": "longbridge-sg-us-service",
                                "strategy_profile": "us-strategy",
                                "account_scope": "SG",
                                "scheduler": {
                                    "timezone": "America/New_York",
                                    "main_time": "45 15 * * *",
                                },
                            },
                        }
                    ]
                }
            )
        }
    )

    assert targets[0]["market"] == "US"
    assert targets[0]["market_calendar"] == "NYSE"
    assert targets[0]["market_timezone"] == "America/New_York"


def test_ambiguous_sg_account_does_not_guess_a_stock_exchange() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "targets": [
                        {
                            "service": "longbridge-sg-service",
                            "account_scope": "SG",
                            "runtime_target": {
                                "service_name": "longbridge-sg-service",
                                "strategy_profile": "unknown-strategy",
                                "account_scope": "SG",
                                "scheduler": {
                                    "timezone": "UTC",
                                    "main_time": "0 12 * * *",
                                },
                            },
                        }
                    ]
                }
            )
        }
    )

    assert targets[0]["market"] == ""
    assert targets[0]["market_calendar"] == ""


def test_calendar_failure_keeps_target_due_fail_closed() -> None:
    targets = load_runtime_targets(
        {
            "RUNTIME_TARGET_JSON": json.dumps(
                _target(
                    service="svc-us",
                    strategy="us-strategy",
                    scope="US",
                    timezone="America/New_York",
                    calendar="INVALID",
                )["runtime_target"]
            )
        }
    )

    def fail_calendar(_calendar: str, **_kwargs: object) -> set[dt.date]:
        raise RuntimeError("calendar unavailable")

    due, evaluated = filter_due_targets(
        targets,
        since=dt.datetime(2026, 7, 3, 0, 0, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 7, 3, 22, 0, tzinfo=dt.timezone.utc),
        session_dates_loader=fail_calendar,
        warning_logger=lambda _message: None,
    )

    assert len(due) == 1
    assert target_latest_due_at(due[0]) == dt.datetime(
        2026,
        7,
        3,
        19,
        45,
        tzinfo=dt.timezone.utc,
    )
    assert evaluated is False


def test_target_defaults_and_scheduler_aliases_are_normalized() -> None:
    targets = load_runtime_targets(
        {
            "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
                {
                    "defaults": {
                        "env": {
                            "RUNTIME_TARGET_ENABLED": "false",
                            "CLOUD_SCHEDULER_MAIN_TIME": "45 15 25-29 * *",
                        },
                        "market": "US",
                    },
                    "targets": [
                        {
                            "service": "disabled-service",
                            "runtime_target": {
                                "strategy_profile": "disabled-strategy",
                            },
                        },
                        {
                            "service": "enabled-service",
                            "RUNTIME_TARGET_ENABLED": "true",
                            "runtime_target": {
                                "strategy_profile": "enabled-strategy",
                            },
                        },
                    ],
                }
            )
        }
    )

    assert len(targets) == 1
    assert targets[0]["service"] == "enabled-service"
    assert targets[0]["market"] == "US"
    assert targets[0]["scheduler"] == {
        "main_time": "45 15 25-29 * *",
        "timezone": "America/New_York",
    }


def test_reconcile_only_target_is_not_an_execution_heartbeat_target() -> None:
    environ = {
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
            {
                "targets": [
                    {
                        "service": "reconcile-only-service",
                        "runtime_target": {
                            "service_name": "reconcile-only-service",
                            "strategy_profile": "strategy-a",
                            "live_continuity": {"state": "RECONCILE_ONLY"},
                        },
                    }
                ]
            }
        )
    }

    assert load_runtime_targets(environ) == []
    assert runtime_target_configuration_present(environ) is True


def test_strategy_profile_resolver_canonicalizes_aliases() -> None:
    targets = load_runtime_targets(
        {
            "RUNTIME_TARGET_JSON": json.dumps(
                {
                    "service_name": "alias-service",
                    "strategy_profile": "supported-alias",
                    "scheduler": {
                        "main_time": "45 15 * * *",
                        "timezone": "UTC",
                    },
                }
            )
        },
        profile_resolver=lambda value: (
            "canonical-strategy" if value == "supported-alias" else value
        ),
    )

    assert targets[0]["strategy_profile"] == "canonical-strategy"


def test_publication_grace_uses_previous_matured_schedule_cutoff() -> None:
    targets = load_runtime_targets(
        {
            "RUNTIME_TARGET_JSON": json.dumps(
                {
                    "service_name": "grace-service",
                    "strategy_profile": "grace-strategy",
                    "scheduler": {
                        "main_time": "0 12 * * *",
                        "timezone": "UTC",
                    },
                }
            )
        }
    )
    since = dt.datetime(2026, 7, 28, 11, 30, tzinfo=dt.timezone.utc)

    within_grace, evaluated = filter_due_targets(
        targets,
        since=since,
        now=dt.datetime(2026, 7, 29, 12, 5, tzinfo=dt.timezone.utc),
        market_aware=False,
        publication_grace=dt.timedelta(minutes=30),
    )
    after_grace, _ = filter_due_targets(
        targets,
        since=since,
        now=dt.datetime(2026, 7, 29, 12, 31, tzinfo=dt.timezone.utc),
        market_aware=False,
        publication_grace=dt.timedelta(minutes=30),
    )

    assert evaluated is True
    assert target_latest_due_at(within_grace[0]) == dt.datetime(
        2026,
        7,
        28,
        12,
        0,
        tzinfo=dt.timezone.utc,
    )
    assert target_latest_due_at(after_grace[0]) == dt.datetime(
        2026,
        7,
        29,
        12,
        0,
        tzinfo=dt.timezone.utc,
    )


def test_runtime_target_configuration_presence_is_preserved_when_all_disabled() -> None:
    environ = {
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(
            {
                "defaults": {"runtime_target_enabled": False},
                "targets": [{"service": "disabled-service"}],
            }
        )
    }

    assert runtime_target_configuration_present(environ) is True
    assert load_runtime_targets(environ) == []
    assert load_runtime_targets(environ, include_disabled=True)[0]["enabled"] is False


def test_business_date_schedule_separates_grace_closed_and_not_due() -> None:
    targets = load_runtime_targets(
        {
            "RUNTIME_TARGET_JSON": json.dumps(
                {
                    "service_name": "lb-hk",
                    "strategy_profile": "hk-profile",
                    "account_scope": "HK",
                    "market": "HK",
                    "market_calendar": "XHKG",
                    "market_timezone": "Asia/Hong_Kong",
                    "scheduler": {"main_time": "5 16 * * 1-5", "timezone": "Asia/Hong_Kong"},
                }
            )
        }
    )
    target = targets[0]
    before = classify_business_date_schedule(
        target,
        now=dt.datetime(2026, 9, 28, 2, 0, tzinfo=dt.timezone.utc),
        session_dates_loader=lambda calendar, **_kwargs: {dt.date(2026, 9, 28)},
    )
    within_grace = classify_business_date_schedule(
        target,
        now=dt.datetime(2026, 9, 28, 8, 20, tzinfo=dt.timezone.utc),
        session_dates_loader=lambda calendar, **_kwargs: {dt.date(2026, 9, 28)},
    )
    closed = classify_business_date_schedule(
        target,
        now=dt.datetime(2026, 9, 28, 8, 40, tzinfo=dt.timezone.utc),
        session_dates_loader=lambda calendar, **_kwargs: set(),
    )
    outside = classify_business_date_schedule(
        target,
        now=dt.datetime(2026, 9, 28, 8, 40, tzinfo=dt.timezone.utc),
        within_expected_window=False,
        session_dates_loader=lambda calendar, **_kwargs: {dt.date(2026, 9, 28)},
    )

    assert before["state"] == "not_due"
    assert before["publication_grace_ended"] is None
    assert within_grace["state"] == "within_grace"
    assert within_grace["publication_grace_ended"] is False
    assert within_grace["latest_due_at"] == dt.datetime(2026, 9, 28, 8, 5, tzinfo=dt.timezone.utc)
    assert closed["state"] == "market_closed"
    assert outside["state"] == "outside_window"
    assert outside["publication_grace_ended"] is None



# Exact serving-origin/route contract, independent of a Scheduler URI.
def _serving_route_case(path="/run"):
    service = {
        "metadata": {"name": "lb-paper"},
        "status": {
            "url": "https://lb-paper.example.run.app",
            "traffic": [{"revisionName": "lb-paper-r1", "percent": 100}],
        },
    }
    revision = {
        "metadata": {"name": "lb-paper-r1", "labels": {"commit-sha": "a" * 40}},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    context = {
        "service": service,
        "revision": revision,
        "route_contract": {
            "service": "lb-paper",
            "source_commit": "a" * 40,
            "path": path,
            "http_method": "POST",
        },
    }
    job = {
        "name": "projects/synthetic/locations/test/jobs/paper",
        "state": "ENABLED",
        "schedule": "45 15 * * 1",
        "timeZone": "America/New_York",
        "httpTarget": {
            "uri": f"https://lb-paper.example.run.app{path}",
            "httpMethod": "POST",
        },
    }
    return context, job


def test_verified_route_selects_only_declared_path():
    from scripts.runtime_heartbeat_policy import resolve_bound_scheduler

    target = {"service": "lb-paper", "scheduler": {"main_time": "wrong template"}}
    for path, other in [("/run", "/"), ("/", "/run")]:
        context, job = _serving_route_case(path)
        resolved, error = resolve_bound_scheduler(
            target, [job], serving_context=context
        )
        assert error is None
        assert resolved["scheduler"]["main_time"] == job["schedule"]
        job["httpTarget"]["uri"] = "https://lb-paper.example.run.app" + other
        unknown, error = resolve_bound_scheduler(target, [job], serving_context=context)
        assert error is not None and unknown["scheduler"] == {}
    unknown, error = resolve_bound_scheduler(target, [], serving_context=None)
    assert error == "scheduler_context_unevaluable" and unknown["scheduler"] == {}


@pytest.mark.parametrize(
    "uri",
    [
        "https://lb-paper.example.run.app.attacker.test/run",
        "http://lb-paper.example.run.app/run",
        "https://lb-paper.example.run.app:8443/run",
        "https://user:secret@lb-paper.example.run.app/run",
        "https://lb-paper.example.run.app/run?key=private",
        "https://lb-paper.example.run.app/run#fragment",
        "https://lb-paper.example.run.app/run/",
        "https://lb-paper.example.run.app/%72un",
    ],
)
def test_scheduler_uri_lookalikes_and_ambiguous_paths_are_not_ready(uri):
    from scripts.runtime_heartbeat_policy import resolve_bound_scheduler

    context, job = _serving_route_case()
    job["httpTarget"]["uri"] = uri
    resolved, error = resolve_bound_scheduler(
        {"service": "lb-paper"}, [job], serving_context=context
    )
    assert error is not None and resolved["scheduler"] == {}


def test_missing_changed_serving_route_and_duplicate_jobs_are_unknown():
    from copy import deepcopy
    from scripts.runtime_heartbeat_policy import resolve_bound_scheduler

    context, job = _serving_route_case()
    for mutate in [
        lambda c: c.pop("route_contract"),
        lambda c: c["route_contract"].update(source_commit="b" * 40),
        lambda c: c["service"]["status"]["traffic"].append(
            {"revisionName": "other", "percent": 1}
        ),
        lambda c: c["revision"]["status"].update(conditions=[]),
        lambda c: c["service"]["metadata"].update(name="other"),
    ]:
        changed = deepcopy(context)
        mutate(changed)
        assert resolve_bound_scheduler(
            {"service": "lb-paper"}, [job], serving_context=changed
        )[1]
    assert resolve_bound_scheduler(
        {"service": "lb-paper"}, [job, deepcopy(job)], serving_context=context
    )[1]
    for field, value in [
        ("state", ""),
        ("timeZone", "Not/AZone"),
        ("schedule", "bad"),
        ("name", ""),
    ]:
        changed = deepcopy(job)
        changed[field] = value
        assert resolve_bound_scheduler(
            {"service": "lb-paper"}, [changed], serving_context=context
        )[1]


def test_expectations_are_multiple_actual_slots_not_one_daily_requirement():
    from scripts.runtime_heartbeat_policy import enumerate_cycle_expectations

    target = {
        "scheduler": {"main_time": "0 15 * * 1,3,5", "timezone": "UTC"},
        "market_timezone": "UTC",
        "market_calendar": "TEST",
    }

    def calendar(name, **kw):
        return {
            kw["start_date"] + dt.timedelta(days=n)
            for n in range((kw["end_date"] - kw["start_date"]).days + 1)
        }

    result = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 10, 9, 16, tzinfo=dt.timezone.utc),
        session_dates_loader=calendar,
    )
    assert result["state"] == "ready"
    assert [slot["scheduled_for"][:10] for slot in result["slots"]] == [
        "2026-10-05",
        "2026-10-07",
        "2026-10-09",
    ]
    assert result["schedule"]["next_due_at"] == "2026-10-12T15:00:00Z"


def _all_weekday_sessions(_name, *, start_date, end_date):
    return {
        start_date + dt.timedelta(days=i)
        for i in range((end_date - start_date).days + 1)
        if (start_date + dt.timedelta(days=i)).weekday() < 5
    }


def test_monthly_expectations_and_explicit_interval_do_not_demand_today():
    from scripts.runtime_heartbeat_policy import enumerate_cycle_expectations

    target = {
        "scheduler": {"main_time": "0 15 1 * *", "timezone": "UTC"},
        "market_timezone": "UTC",
        "market_calendar": "TEST",
    }
    result = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 10, 2, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 10, 7, 16, tzinfo=dt.timezone.utc),
        session_dates_loader=_all_weekday_sessions,
    )
    assert result["state"] == "ready" and result["slots"] == []
    assert result["schedule"]["state"] == "not_due"
    # Nov 1 falls on a closed test session; do not invent a shifted Nov 2 run.
    assert result["schedule"]["next_due_at"] == "2026-12-01T15:00:00Z"


def test_closed_session_keeps_prior_expectation_and_dst_uses_scheduler_timezone():
    from scripts.runtime_heartbeat_policy import enumerate_cycle_expectations

    target = {
        "scheduler": {"main_time": "0 15 * * 1", "timezone": "America/New_York"},
        "market_timezone": "America/New_York",
        "market_calendar": "TEST",
    }
    result = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 11, 2, 22, tzinfo=dt.timezone.utc),
        session_dates_loader=_all_weekday_sessions,
    )
    assert [x["scheduled_for"] for x in result["slots"]] == [
        "2026-10-26T19:00:00Z",
        "2026-11-02T20:00:00Z",
    ]

    def holiday(name, **kw):
        return _all_weekday_sessions(name, **kw) - {dt.date(2026, 11, 2)}

    result = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 11, 2, 22, tzinfo=dt.timezone.utc),
        session_dates_loader=holiday,
    )
    assert result["schedule"]["state"] == "market_closed"
    assert [x["scheduled_for"] for x in result["slots"]] == ["2026-10-26T19:00:00Z"]


def test_cap_horizon_calendar_failure_and_bad_timezone_never_skip_ready():
    from scripts.runtime_heartbeat_policy import enumerate_cycle_expectations

    target = {
        "scheduler": {"main_time": "* * * * *", "timezone": "UTC"},
        "market_timezone": "UTC",
        "market_calendar": "TEST",
    }
    kwargs = dict(
        since=dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc),
        now=dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc),
        session_dates_loader=_all_weekday_sessions,
    )
    assert (
        enumerate_cycle_expectations(target, **kwargs)["reason"]
        == "expectation_limit_exceeded"
    )
    assert (
        enumerate_cycle_expectations(target, **kwargs, horizon_days=1)["reason"]
        == "expectation_horizon_exceeded"
    )

    def fail(*a, **kw):
        raise RuntimeError("private provider detail")

    result = enumerate_cycle_expectations(
        target, **{**kwargs, "session_dates_loader": fail}
    )
    assert result["state"] == "unevaluable" and "private" not in str(result)
    target["scheduler"]["timezone"] = "Invalid/Zone"
    assert enumerate_cycle_expectations(target, **kwargs)["state"] == "unevaluable"


def test_malformed_uri_and_conflicting_ready_conditions_are_unknown():
    from scripts.runtime_heartbeat_policy import resolve_bound_scheduler

    context, job = _serving_route_case()
    job["httpTarget"]["uri"] = "https://[broken/run"
    assert resolve_bound_scheduler(
        {"service": "lb-paper"}, [job], serving_context=context
    )[1]
    context, job = _serving_route_case()
    context["revision"]["status"]["conditions"].append(
        {"type": "Ready", "status": "False"}
    )
    assert resolve_bound_scheduler(
        {"service": "lb-paper"}, [job], serving_context=context
    )[1]
