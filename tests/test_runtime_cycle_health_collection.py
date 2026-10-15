"""Injected provider-shaped evidence only; never query a cloud or broker."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import importlib.util
import json
from pathlib import Path
import socket
import subprocess

import pytest

from scripts.collect_runtime_cycle_health import (
    bounded_cycle_window_end,
    collect_runtime_cycle_health,
    cycle_configuration_sha256,
    runtime_report_prefix_ranges,
)
from scripts.runtime_cycle_health import project_cycle, reduce_incidents

_spec = importlib.util.spec_from_file_location(
    "cycle_fixtures", Path(__file__).with_name("test_runtime_cycle_health.py")
)
f = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f)
UTC = dt.timezone.utc
SINCE = dt.datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("external transport forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


def calendar(_name, *, start_date, end_date):
    return {
        start_date + dt.timedelta(days=i)
        for i in range((end_date - start_date).days + 1)
        if (start_date + dt.timedelta(days=i)).weekday() < 5
    }


def source(cron="0 15 * * 1,3,5"):
    target = {
        "service": f.CTX.service,
        "strategy_profile": f.CTX.strategy_profile,
        "account_scope": "PAPER",
        "market": "US",
        "market_calendar": "NYSE",
        "market_timezone": "UTC",
    }
    deployed = {
        **target,
        "service_name": target["service"],
        "execution_mode": "paper",
        "runtime_target_enabled": True,
    }
    revision = {
        "metadata": {"name": "paper-r1", "labels": {"commit-sha": "a" * 40}},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        "spec": {
            "containers": [
                {
                    "env": [
                        {"name": "RUNTIME_TARGET_JSON", "value": json.dumps(deployed)}
                    ]
                }
            ]
        },
    }
    return {
        "monitor_policy": {"RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES": "30"},
        "target": target,
        "serving_context": {
            "service": {
                "metadata": {"name": f.CTX.service},
                "status": {
                    "url": "https://paper.example.run.app",
                    "traffic": [{"revisionName": "paper-r1", "percent": 100}],
                },
            },
            "revision": revision,
            "route_contract": {
                "service": f.CTX.service,
                "source_commit": "a" * 40,
                "path": "/run",
                "http_method": "POST",
            },
        },
        "jobs": [
            {
                "name": f.CTX.scheduler_job_name,
                "state": "ENABLED",
                "schedule": cron,
                "timeZone": "UTC",
                "httpTarget": {
                    "uri": "https://paper.example.run.app/run",
                    "httpMethod": "POST",
                },
            }
        ],
        "source_binding": {
            "id": f.CTX.source_binding_id,
            "service": f.CTX.service,
            "revision": "paper-r1",
        },
        "release_contract": {
            "source_commit": "a" * 40,
            "strategy_revision": f.CTX.strategy_revision,
        },
    }


def observed_cycle(ctx, report, **kwargs):
    return project_cycle(
        ctx,
        {key: value for key, value in report.items() if key != "invocation"},
        **kwargs,
    )


def context(value):
    return replace(f.CTX, configuration_sha256=cycle_configuration_sha256(value))


def test_report_root_hash_is_optional_but_changes_admitted_configuration_digest():
    value = source()
    legacy = cycle_configuration_sha256(value)
    reports_a = cycle_configuration_sha256(value, report_root_uri="gs://synthetic-a/reports")
    reports_b = cycle_configuration_sha256(value, report_root_uri="gs://synthetic-b/reports")
    assert reports_a != legacy
    assert reports_a != reports_b


def collect(value=None, reports=None, **kwargs):
    value = source() if value is None else value
    reports = {} if reports is None else reports
    args = dict(
        context=context(value),
        read_source=lambda: deepcopy(value),
        list_page=lambda prefix, token: {"items": [{"name": name} for name in reports]},
        read_report=lambda name: deepcopy(reports[name]),
        required_prefixes=["synthetic/"],
        since=SINCE,
        now=f.NOW,
        session_dates_loader=calendar,
    )
    args.update(kwargs)
    return collect_runtime_cycle_health(**args)


def test_multiple_missing_slots_are_covered_without_today_daily_gate():
    result = collect()
    assert result["data_status"] == "ready"
    assert {row["scheduled_for"] for row in result["cycle_health"]["cycles"]} == {
        f.SLOT,
        "2026-10-07T15:00:00Z",
    }
    assert result["checkpoint"]["state"]["unresolved_count"] == 2
    assert result["execution_authority_granted"] is False
    assert result["source_snapshot_atomic"] is False
    assert set(result["cycle_health"]) == {
        "schema_version",
        "configuration_sha256",
        "schedule",
        "coverage",
        "cycles",
        "resolutions",
    }


def test_historical_chunk_keeps_current_schedule_separate_from_coverage_end():
    historical_through = dt.datetime(2026, 10, 6, 16, tzinfo=UTC)
    result = collect(coverage_through=historical_through)
    assert result["data_status"] == "ready"
    health = result["cycle_health"]
    assert health["coverage"]["through"] == "2026-10-06T16:00:00Z"
    assert health["schedule"]["latest_due_at"] == "2026-10-07T15:00:00Z"
    assert health["schedule"]["next_due_at"] == "2026-10-09T15:00:00Z"
    assert all(
        row["scheduled_for"] <= health["coverage"]["through"]
        and (row["completed_at"] is None or row["completed_at"] <= health["coverage"]["through"])
        for row in health["cycles"]
    )
def test_daily_invocation_with_monthly_no_action_still_has_receipt():
    value = source("0 15 * * 1-5")
    reports = {
        f"synthetic/{day}": f.report(
            slot=f"2026-10-{day:02}T15:00:00Z", completed=f"2026-10-{day:02}T15:01:00Z"
        )
        for day in [5, 6, 7]
    }
    result = collect(value, reports)
    assert result["data_status"] == "ready"
    assert len(result["cycle_health"]["cycles"]) == 3
    assert {row["outcome"] for row in result["cycle_health"]["cycles"]} == {"no_action"}
    assert result["checkpoint"]["state"]["unresolved_count"] == 0


def test_runtime_report_listing_ranges_exclude_old_objects_and_cover_month_boundary():
    ranges = runtime_report_prefix_ranges(
        "gs://synthetic-bucket/reports",
        strategy_profile="paper_profile",
        account_scope="PAPER",
        since=dt.datetime(2026, 9, 30, 23, 59, 59, 500000, tzinfo=UTC),
        through=dt.datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC),
    )
    assert list(ranges) == [
        "reports/longbridge/paper_profile/PAPER/2026-09/",
        "reports/longbridge/paper_profile/PAPER/2026-10/",
    ]
    assert ranges["reports/longbridge/paper_profile/PAPER/2026-09/"] == (
        "reports/longbridge/paper_profile/PAPER/2026-09/20260930T235959Z",
        "reports/longbridge/paper_profile/PAPER/2026-09/20261001T000000Z",
    )
    assert ranges["reports/longbridge/paper_profile/PAPER/2026-10/"] == (
        "reports/longbridge/paper_profile/PAPER/2026-10/20261001T000000Z",
        "reports/longbridge/paper_profile/PAPER/2026-10/20261001T000001Z",
    )


def test_long_initial_history_is_split_before_twenty_first_scheduled_cycle():
    from scripts.runtime_heartbeat_policy import enumerate_cycle_expectations

    target = {
        "scheduler": {"main_time": "0 15 * * 1-5", "timezone": "UTC"},
        "market_timezone": "UTC",
        "market_calendar": "TEST",
    }
    end, reason = bounded_cycle_window_end(
        target,
        since=dt.datetime(2026, 9, 1, tzinfo=UTC),
        through=dt.datetime(2026, 10, 8, 16, tzinfo=UTC),
        publication_grace=dt.timedelta(minutes=30),
        session_dates_loader=calendar,
    )
    assert reason is None
    bounded = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 9, 1, tzinfo=UTC),
        now=end,
        publication_grace=dt.timedelta(minutes=30),
        session_dates_loader=calendar,
    )
    assert bounded["state"] == "ready"
    assert len(bounded["slots"]) == 20
    current = enumerate_cycle_expectations(
        target,
        since=dt.datetime(2026, 9, 1, tzinfo=UTC),
        now=dt.datetime(2026, 10, 8, 16, tzinfo=UTC),
        coverage_through=end,
        publication_grace=dt.timedelta(minutes=30),
        session_dates_loader=calendar,
    )
    assert current["state"] == "ready"
    assert len(current["slots"]) == 20
    assert current["schedule"]["latest_due_at"] == "2026-10-08T15:00:00Z"
    assert current["schedule"]["next_due_at"] == "2026-10-09T15:00:00Z"


def test_baseline_horizon_is_backfilled_in_bounded_year_segments():
    target = {
        "scheduler": {"main_time": "0 15 1 * *", "timezone": "UTC"},
        "market_timezone": "UTC",
        "market_calendar": "TEST",
    }
    start = dt.datetime(2024, 1, 1, tzinfo=UTC)
    requested_through = dt.datetime(2026, 10, 8, 16, tzinfo=UTC)
    end, reason = bounded_cycle_window_end(
        target,
        since=start,
        through=requested_through,
        publication_grace=dt.timedelta(minutes=30),
        session_dates_loader=calendar,
    )
    assert reason is None
    assert end == start + dt.timedelta(days=365)


def test_collector_uses_provider_object_name_offsets_for_bounded_interval():
    value = source("0 15 * * 1-5")
    ranges = {
        "synthetic/2026-10/": (
            "synthetic/2026-10/20261005T000000Z",
            "synthetic/2026-10/20261008T000000Z",
        )
    }
    observed = []
    reports = {
        "synthetic/2026-10/20261006T150000Z.json": f.report(
            slot="2026-10-06T15:00:00Z", completed="2026-10-06T15:01:00Z"
        )
    }
    reports.update(
        {
            f"synthetic/2026-10/202609{day:02}T150000Z.json": f.report(
                slot=f"2026-09-{day:02}T15:00:00Z",
                completed=f"2026-09-{day:02}T15:01:00Z",
            )
            for day in range(1, 22)
        }
    )

    def list_range(prefix, token, start_offset, end_offset):
        observed.append((prefix, token, start_offset, end_offset))
        # Old historical names must be filtered by the provider range, not
        # loaded and rejected after consuming the collector's 20-report cap.
        names = [
            name
            for name in reports
            if name.startswith(prefix) and start_offset <= name < end_offset
        ]
        return {"items": [{"name": name} for name in names]}

    result = collect(
        value,
        reports,
        list_page_range=list_range,
        list_ranges=ranges,
        required_prefixes=list(ranges),
    )
    assert observed == [(next(iter(ranges)), None, *next(iter(ranges.values())))]
    assert result["data_status"] == "ready"
    assert result["cycle_health"]["coverage"]["listed_count"] == 1
    assert sum(row["receipt_ref"] is not None for row in result["cycle_health"]["cycles"]) == 1


def test_weekly_not_due_still_reads_and_keeps_old_uncertainty():
    value = source("0 15 * * 1")
    ctx = context(value)
    old = reduce_incidents(
        ctx,
        [
            project_cycle(
                ctx, f.report("failed", "not_observed"), scheduled_for=f.SLOT, now=f.NOW
            )
        ],
    )
    checkpoint = {"configuration_sha256": ctx.configuration_sha256, "state": old}
    calls = []
    result = collect(
        value,
        checkpoint=checkpoint,
        since=dt.datetime(2026, 10, 6, tzinfo=UTC),
        list_page=lambda p, t: calls.append((p, t)) or {"items": []},
    )
    assert result["data_status"] == "ready" and calls == [("synthetic/", None)]
    assert result["cycle_health"]["schedule"]["state"] == "not_due"
    assert result["checkpoint"]["state"]["incidents"] == old["incidents"]
    assert result["checkpoint"]["state"]["unresolved_count"] == 1


def test_outside_action_window_cannot_erase_scheduled_invocation():
    result = collect(expected_window=lambda instant: False)
    assert result["data_status"] == "ready"
    assert result["cycle_health"]["schedule"]["state"] == "outside_window"
    assert result["checkpoint"]["state"]["unresolved_count"] == 2


def test_terminal_provider_pages_not_cap_prove_coverage():
    reports = {
        f"synthetic/{i}": f.report(completed=f"2026-10-05T15:{i + 1:02}:00Z")
        for i in range(20)
    }
    first = list(reports)[:10]
    second = list(reports)[10:]
    seen = []

    def listing(prefix, token):
        seen.append(token)
        return {
            "items": [{"name": n} for n in (first if token is None else second)],
            "nextPageToken": "page2" if token is None else None,
        }

    result = collect(reports=reports, list_page=listing)
    assert result["data_status"] == "incomplete"
    assert result["reason"] == "projection_limit_exceeded"
    assert seen == [None, "page2"]
    value = source("0 15 * * 1")
    result = collect(value, reports, list_page=listing)
    assert result["data_status"] == "ready"
    assert result["cycle_health"]["coverage"]["terminal_page_seen"] is True

    def capped(prefix, token):
        if token is not None:
            raise TimeoutError("secret provider detail")
        return {"items": [{"name": n} for n in reports], "nextPageToken": "more"}

    partial = collect(value, reports, list_page=capped)
    assert partial["data_status"] == "incomplete"
    assert partial["cycle_health"]["coverage"]["limit_hit"] is True
    assert partial["cycle_health"]["coverage"]["terminal_page_seen"] is False
    assert "secret" not in json.dumps(partial)


@pytest.mark.parametrize(
    "mode", ["read", "wrong_target", "late", "list", "token_loop", "time", "byte_limit"]
)
def test_partial_reads_never_synthesize_missing_or_resolve(mode):
    reports = {"synthetic/one": f.report()}
    kwargs = {}
    if mode in {"read", "byte_limit"}:

        def fail(name):
            raise ValueError("private provider detail")

        kwargs["read_report"] = fail
    elif mode == "wrong_target":
        reports["synthetic/one"]["service_name"] = "other"
    elif mode == "late":
        reports["synthetic/one"] = f.report(completed="2026-10-07T16:01:00Z")
    elif mode == "list":

        def fail(p, t):
            raise TimeoutError()

        kwargs["list_page"] = fail
    elif mode == "token_loop":
        kwargs["list_page"] = lambda p, t: {"items": [], "nextPageToken": "same"}
    elif mode == "time":
        times = iter([0, 100, 100, 100])
        kwargs["monotonic"] = lambda: next(times)
    result = collect(reports=reports, **kwargs)
    assert result["data_status"] == "incomplete"
    if mode == "time":
        assert result["cycle_health"] is None and result["checkpoint"] is None
        return
    assert not any(
        row["outcome"] == "missing_report" for row in result["cycle_health"]["cycles"]
    )
    assert result["cycle_health"]["resolutions"] == []
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda v: v.pop("serving_context"),
        lambda v: v.pop("source_binding"),
        lambda v: v["source_binding"].update(id="4" * 64),
        lambda v: v["source_binding"].update(revision="other"),
        lambda v: v["release_contract"].update(source_commit="b" * 40),
        lambda v: v["target"].update(market_calendar="WRONG"),
        lambda v: v["jobs"][0].update(state="PAUSED"),
    ],
)
def test_unconfirmed_source_never_reads_reports(mutate):
    value = source()
    ctx = context(value)
    mutate(value)
    calls = []
    result = collect_runtime_cycle_health(
        context=ctx,
        read_source=lambda: value,
        list_page=lambda *a: calls.append(a),
        read_report=lambda *a: calls.append(a),
        required_prefixes=["synthetic/"],
        since=SINCE,
        now=f.NOW,
        session_dates_loader=calendar,
    )
    assert result["data_status"] == "unevaluable" and result["cycle_health"] is None
    assert calls == []


def test_forged_calendar_cannot_be_admitted_by_rehashing():
    value = source()
    value["target"]["market_calendar"] = "WRONG"
    with pytest.raises(ValueError):
        cycle_configuration_sha256(value)


def test_changed_config_does_not_migrate_or_erase_checkpoint():
    value = source()
    ctx = context(value)
    old = reduce_incidents(
        ctx,
        [
            project_cycle(
                ctx, f.report("failed", "not_observed"), scheduled_for=f.SLOT, now=f.NOW
            )
        ],
    )
    checkpoint = {"configuration_sha256": "9" * 64, "state": old}
    result = collect(value, checkpoint=checkpoint)
    assert result["reason"] == "configuration_changed_unconfirmed"
    assert result["checkpoint"] == checkpoint


def test_paired_source_drift_keeps_checkpoint_and_does_not_mix_evidence():
    value = source()
    second = deepcopy(value)
    second["jobs"][0]["schedule"] = "0 16 * * 1,3,5"
    sequence = iter([value, second])
    result = collect(value, read_source=lambda: next(sequence))
    assert result["reason"] == "source_changed_during_read"
    assert result["checkpoint"] is None and result["cycle_health"] is None


def test_staged_template_and_secret_fields_cannot_enter_public_projection():
    value = source()
    value["serving_context"]["service"]["spec"] = {
        "template": {"secret": "private", "profile": "WRONG"}
    }
    raw = f.report()
    raw["private"] = "private"
    before = deepcopy((value, raw))
    result = collect(value, {"synthetic/one": raw})
    assert result["data_status"] == "ready"
    assert "private" not in json.dumps(result) and "WRONG" not in json.dumps(result)
    assert (value, raw) == before


def test_legacy_correlation_stays_window_matched_and_does_not_resolve():
    raw = f.report()
    raw.pop("invocation")
    result = collect(reports={"synthetic/one": raw})
    assert result["cycle_health"]["cycles"][0]["correlation"] == "window_matched"
    assert result["cycle_health"]["resolutions"] == []


@pytest.mark.parametrize("uncertain", [False, True])
def test_same_cycle_retry_checks_real_certainty_and_preserves_prior_on_rejection(
    uncertain,
):
    value = source("0 15 * * 1")
    ctx = context(value)
    failed = f.report("failed", "not_observed" if uncertain else "not_applicable")
    incident = observed_cycle(ctx, failed, scheduled_for=f.SLOT, now=f.NOW)
    checkpoint = {
        "configuration_sha256": ctx.configuration_sha256,
        "state": reduce_incidents(ctx, [incident]),
    }
    success = f.report(completed="2026-10-05T15:02:00Z")
    result = collect(
        value,
        {"synthetic/fail": failed, "synthetic/success": success},
        checkpoint=checkpoint,
        recovery_requests=[
            {
                "incident": incident,
                "kind": "same_cycle_retry_succeeded",
                "successful": success,
            }
        ],
    )
    assert result["data_status"] == "unevaluable"
    assert result["reason"] == "invocation_provenance_unavailable"
    assert result["checkpoint"] == checkpoint


@pytest.mark.parametrize("valid", [True, False])
def test_later_reconciliation_uses_existing_validator_not_positive_wrapper(valid):
    value = source("0 15 * * 1")
    ctx = context(value)
    failed = f.report("failed", "not_observed")
    incident = observed_cycle(ctx, failed, scheduled_for=f.SLOT, now=f.NOW)
    checkpoint = {
        "configuration_sha256": ctx.configuration_sha256,
        "state": reduce_incidents(ctx, [incident]),
    }
    candidate, baseline = f.reconciliation(positions_match=valid)
    baseline["configuration_sha256"] = ctx.configuration_sha256
    result = collect(
        value,
        {"synthetic/fail": failed},
        checkpoint=checkpoint,
        recovery_requests=[
            {
                "incident": incident,
                "kind": "current_state_reconciled",
                "reconciliation_candidate": candidate,
                "baseline": baseline,
            }
        ],
    )
    if valid:
        assert (
            result["data_status"] == "ready"
            and result["checkpoint"]["state"]["unresolved_count"] == 0
        )
        assert result["execution_authority_granted"] is False
    else:
        assert (
            result["data_status"] == "unevaluable"
            and result["checkpoint"] == checkpoint
        )


def test_newer_mixed_severity_fault_cannot_be_cleared_by_old_reconciliation():
    value = source("0 15 * * 1")
    ctx = context(value)
    failed = f.report("failed", "not_observed")
    incident = observed_cycle(ctx, failed, scheduled_for=f.SLOT, now=f.NOW)
    candidate, baseline = f.reconciliation(
        observed_at=dt.datetime(2026, 10, 5, 15, 2, tzinfo=UTC)
    )
    baseline["configuration_sha256"] = ctx.configuration_sha256
    first = collect(
        value,
        {"synthetic/fail": failed},
        now=dt.datetime(2026, 10, 5, 15, 2, tzinfo=UTC),
        recovery_requests=[
            {
                "incident": incident,
                "kind": "current_state_reconciled",
                "reconciliation_candidate": candidate,
                "baseline": baseline,
            }
        ],
    )
    assert first["checkpoint"]["state"]["unresolved_count"] == 0
    new = f.report("failed", "not_applicable", completed="2026-10-05T15:03:00Z")
    replay = collect(
        value,
        {"synthetic/fail": failed, "synthetic/new": new},
        checkpoint=first["checkpoint"],
    )
    assert replay["checkpoint"]["state"]["unresolved_count"] == 1
    assert (
        replay["checkpoint"]["state"]["incidents"][0]["latest_fault_at"]
        == "2026-10-05T15:03:00Z"
    )


@pytest.mark.parametrize("phase", ["source", "last_report", "source_readback"])
def test_callback_crossing_time_budget_never_returns_ready(phase):
    value = source("0 15 * * 1")
    clock = [0]
    source_reads = [0]

    def metadata():
        source_reads[0] += 1
        if (phase == "source" and source_reads[0] == 1) or (
            phase == "source_readback" and source_reads[0] == 2
        ):
            clock[0] = 100
        return deepcopy(value)

    def report_read(name):
        if phase == "last_report":
            clock[0] = 100
        return f.report()

    result = collect(
        value,
        {"synthetic/one": f.report()},
        read_source=metadata,
        read_report=report_read,
        monotonic=lambda: clock[0],
    )
    assert result["data_status"] == "incomplete"
    assert result["checkpoint"] is None


def test_oversized_report_and_listing_never_supply_complete_coverage():
    raw = f.report()
    raw["private"] = "x" * 1_048_576
    result = collect(reports={"synthetic/one": raw})
    assert result["data_status"] == "incomplete"
    assert result["cycle_health"]["coverage"]["errors_present"] is True
    result = collect(list_page=lambda p, t: {"items": [], "private": "x" * 262_144})
    assert result["data_status"] == "incomplete"
    assert result["cycle_health"]["coverage"]["terminal_page_seen"] is False


def test_invocation_fields_alone_never_upgrade_legacy_correlation():
    raw = f.report()
    result = collect(source("0 15 * * 1"), {"synthetic/one": raw})
    assert result["data_status"] == "ready"
    assert result["cycle_health"]["cycles"][0]["correlation"] == "window_matched"
    raw["invocation"]["scheduled_for"] = "2026-10-07T15:00:00Z"
    changed = collect(source("0 15 * * 1"), {"synthetic/one": raw})
    assert changed["cycle_health"] == result["cycle_health"]


def test_effective_source_grace_is_required_hashed_and_used():
    value = source("0 15 * * 1")
    normal = context(value)
    value["monitor_policy"]["RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES"] = "5"
    assert context(value).configuration_sha256 != normal.configuration_sha256
    result = collect(value, now=dt.datetime(2026, 10, 5, 15, 6, tzinfo=UTC))
    assert result["data_status"] == "ready"
    assert result["cycle_health"]["schedule"]["deadline_at"] == "2026-10-05T15:05:00Z"
    assert result["checkpoint"]["state"]["unresolved_count"] == 1
    value.pop("monitor_policy")
    with pytest.raises(ValueError):
        cycle_configuration_sha256(value)
