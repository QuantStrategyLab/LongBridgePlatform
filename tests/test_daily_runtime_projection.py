from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.daily_runtime_projection import main, project_daily_runtime, project_listed_reports


HK = ZoneInfo("Asia/Hong_Kong")
US = ZoneInfo("America/New_York")


def _hk_target() -> dict:
    return {
        "service": "lb-hk",
        "strategy_profile": "hk-profile",
        "account_scope": "HK",
        "scheduler": {"main_time": "5 16 * * 1-5", "timezone": "Asia/Hong_Kong"},
        "market": "HK",
        "market_calendar": "XHKG",
        "market_timezone": "Asia/Hong_Kong",
    }


def _us_target() -> dict:
    return {
        "service": "lb-us",
        "strategy_profile": "us-profile",
        "account_scope": "US",
        "scheduler": {"main_time": "5 16 * * 1-5", "timezone": "America/New_York"},
        "market": "US",
        "market_calendar": "NYSE",
        "market_timezone": "America/New_York",
    }


def _open_calendar(calendar: str, **kwargs) -> set[dt.date]:
    del calendar
    return {kwargs["start_date"]}


def _closed_calendar(calendar: str, **kwargs) -> set[dt.date]:
    del calendar, kwargs
    return set()


def _report(
    *,
    run_id: str = "run-1",
    service: str = "lb-hk",
    strategy: str = "hk-profile",
    scope: str = "HK",
    started_at: str = "2026-09-28T16:06:00+08:00",
    finished_at: str = "2026-09-28T16:07:00+08:00",
    execution_status: str = "no_action",
    broker_submission_done: bool | None = False,
    action_done: bool | None = False,
    orders_pending_count: int | None = 0,
    dry_run: bool = False,
    execution_mode: str = "paper",
    mode: str | None = None,
    validation_only: bool = False,
    errors: list | None = None,
    receipt: dict | None = None,
    platform: str = "longbridge",
    extra_summary: dict | None = None,
) -> dict:
    summary = {
        "execution_status": execution_status,
        "orders_pending_count": orders_pending_count,
    }
    if broker_submission_done is not None:
        summary["broker_submission_done"] = broker_submission_done
    if action_done is not None:
        summary["action_done"] = action_done
    if extra_summary:
        summary.update(extra_summary)
    runtime_target = {"execution_mode": execution_mode, "service_name": service}
    if mode:
        runtime_target["mode"] = mode
    payload = {
        "platform": platform,
        "service_name": service,
        "strategy_profile": strategy,
        "account_scope": scope,
        "run_id": run_id,
        "run_source": "cloud_run",
        "status": "ok",
        "dry_run": dry_run,
        "validation_only": validation_only,
        "started_at": started_at,
        "finished_at": finished_at,
        "runtime_target": runtime_target,
        "summary": summary,
        "errors": [] if errors is None else errors,
    }
    if receipt is not None:
        payload["execution_receipt"] = receipt
    return payload


def _project(reports, *, observed_at, targets=None, **kwargs):
    return project_daily_runtime(
        targets=targets if targets is not None else [_hk_target()],
        reports=reports,
        observed_at=observed_at,
        session_dates_loader=kwargs.pop("session_dates_loader", _open_calendar),
        **kwargs,
    )


def _record(projected, service="lb-hk"):
    return next(item for item in projected["records"] if item["target"]["service"] == service)


def test_no_action_requires_submission_and_pending_fields() -> None:
    observed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    projected = _project(
        [_report(extra_summary={"order_events_count": 4, "orders_previewed_count": 2, "quote_snapshot": {"quotes": [{}]}})],
        observed_at=observed,
    )
    record = _record(projected)

    assert record["status"] == "no_submission"
    assert record["kind"] == "run"
    assert record["execution_lane"] == "paper"
    assert record["fills"] == {"source": "not_connected", "records": [], "count": None}
    assert record["runs"][0]["started_at"] == "2026-09-28T08:06:00Z"
    assert record["business_date"] == "2026-09-28"
    assert record["schedule"]["state"] == "due"


def test_execution_status_no_action_with_submission_is_submitted() -> None:
    projected = _project(
        [_report(broker_submission_done=True, action_done=True)],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "submitted"
    assert record["runs"][0]["activity"] == "submitted"
    assert record["fills"]["count"] is None


def test_pending_order_report_does_not_populate_execution_fills() -> None:
    projected = _project(
        [
            _report(
                broker_submission_done=True,
                action_done=True,
                execution_status="pending_reconciliation",
                orders_pending_count=1,
                receipt={"outcome": "submitted", "broker_confirmation": "accepted"},
            )
        ],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "reconciliation_required"
    assert record["fills"] == {"source": "not_connected", "records": [], "count": None}


def test_partial_receipt_is_not_filled() -> None:
    projected = _project(
        [
            _report(
                broker_submission_done=True,
                action_done=True,
                execution_status="no_action",
                receipt={"outcome": "partially_filled", "broker_confirmation": "partially_filled"},
            )
        ],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "partially_filled"
    assert record["status"] != "filled"
    assert record["fills"]["records"] == []
    assert record["fills"]["count"] is None


def test_unknown_and_reconciliation_outrank_market_closed() -> None:
    closed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    unknown = _project(
        [_report(execution_status="unknown")],
        observed_at=closed,
        session_dates_loader=_closed_calendar,
    )
    reconciling = _project(
        [_report(execution_status="pending_reconciliation", orders_pending_count=1, broker_submission_done=True)],
        observed_at=closed,
        session_dates_loader=_closed_calendar,
    )

    assert _record(unknown)["status"] == "unknown"
    assert _record(unknown)["schedule"]["state"] == "market_closed"
    assert _record(reconciling)["status"] == "reconciliation_required"
    assert _record(reconciling)["schedule"]["state"] == "market_closed"


def test_errors_block_a_no_action_claim() -> None:
    projected = _project(
        [_report(errors=[{"stage": "strategy_cycle", "message": "strategy_cycle_failed"}])],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    assert _record(projected)["status"] == "failed"


def test_schedule_observations_are_not_execution_success() -> None:
    not_due = _project([], observed_at=dt.datetime(2026, 9, 28, 10, 0, tzinfo=HK))
    within_grace = _project([], observed_at=dt.datetime(2026, 9, 28, 16, 20, tzinfo=HK))
    closed = _project(
        [],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
        session_dates_loader=_closed_calendar,
    )
    outside = _project(
        [],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
        within_expected_window=False,
    )
    missing = _project([], observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK))

    assert _record(not_due)["status"] == "not_due"
    assert _record(not_due)["kind"] == "schedule"
    assert _record(within_grace)["status"] == "within_grace"
    assert _record(within_grace)["schedule"]["publication_grace_ended"] is False
    assert _record(closed)["status"] == "market_closed"
    assert _record(outside)["status"] == "outside_window"
    assert _record(outside)["schedule"]["publication_grace_ended"] is None
    assert _record(missing)["status"] == "missing_report"
    assert _record(missing)["completeness"] == "incomplete"
    assert _record(missing)["fills"]["count"] is None
    assert all(item["records"][0]["status"] != "no_submission" for item in (not_due, within_grace, closed, outside, missing))


def test_quiet_report_does_not_turn_market_closed_into_no_submission() -> None:
    projected = _project(
        [_report()],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
        session_dates_loader=_closed_calendar,
    )

    record = _record(projected)
    assert record["status"] == "market_closed"
    assert record["runs"][0]["activity"] == "no_submission"


def test_report_arriving_during_grace_is_the_run_fact() -> None:
    projected = _project(
        [_report()],
        observed_at=dt.datetime(2026, 9, 28, 16, 20, tzinfo=HK),
    )

    assert _record(projected)["status"] == "no_submission"
    assert _record(projected)["schedule"]["state"] == "within_grace"


def test_old_report_and_upload_time_do_not_complete_the_business_date() -> None:
    projected = _project(
        [
            {
                "payload": _report(started_at="2026-09-27T16:06:00+08:00", finished_at="2026-09-27T16:07:00+08:00"),
                "object_uri": "gs://bucket/old.json",
                "object_updated_at": "2026-09-28T08:40:00Z",
            }
        ],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "missing_report"
    assert record["runs"] == []
    assert record["excluded_reports"][0]["reason"] == "other_business_date"
    assert record["excluded_reports"][0]["business_date"] == "2026-09-27"


def test_wrong_target_is_not_completion() -> None:
    projected = _project(
        [_report(service="lb-us", strategy="us-profile", scope="US")],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "missing_report"
    assert projected["unmatched_reports"][0]["reason"] == "wrong_target"


def test_read_failure_is_incomplete_and_not_a_closed_day() -> None:
    closed = project_daily_runtime(
        targets=[_hk_target()],
        reports=[],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
        session_dates_loader=_closed_calendar,
        read_errors=["gs://bucket/2026-09: listing failed"],
    )
    listed = project_listed_reports(
        targets=[_hk_target()],
        objects=[("gs://bucket/unread.json", dt.datetime(2026, 9, 28, 8, 40, tzinfo=dt.timezone.utc))],
        read_payload=lambda uri: None,
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
        session_dates_loader=_open_calendar,
    )

    assert _record(closed)["status"] == "read_incomplete"
    assert _record(closed)["schedule"]["state"] == "market_closed"
    assert closed["completeness"] == "incomplete"
    assert _record(listed)["status"] == "read_incomplete"
    assert listed["read_errors"] == ["gs://bucket/unread.json: unreadable"]


def test_duplicate_run_is_collapsed() -> None:
    same = _project(
        [_report(run_id="run-1"), _report(run_id="run-1")],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    assert len(_record(same)["runs"]) == 1
    assert _record(same)["status"] == "no_submission"
    assert _record(same)["conflicts"] == []


def test_prior_day_unresolved_outranks_closed_market_and_ordinary_success() -> None:
    observed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    unknown = _report(
        run_id="prior-unknown",
        execution_status="unknown",
        started_at="2026-09-27T16:06:00+08:00",
        finished_at="2026-09-27T16:07:00+08:00",
    )
    pending = _report(
        run_id="prior-pending",
        execution_status="pending_reconciliation",
        orders_pending_count=1,
        broker_submission_done=True,
        started_at="2026-09-27T16:06:00+08:00",
        finished_at="2026-09-27T16:07:00+08:00",
    )
    closed_unknown = _project([unknown], observed_at=observed, session_dates_loader=_closed_calendar)
    closed_pending = _project([pending], observed_at=observed, session_dates_loader=_closed_calendar)
    success = _project(
        [unknown, _report(run_id="today-quiet")],
        observed_at=observed,
    )
    submitted = _project(
        [unknown, _report(run_id="today-submitted", broker_submission_done=True, action_done=True)],
        observed_at=observed,
    )

    unknown_record = _record(closed_unknown)
    pending_record = _record(closed_pending)
    success_record = _record(success)
    submitted_record = _record(submitted)
    assert unknown_record["status"] == "unknown"
    assert unknown_record["schedule"]["state"] == "market_closed"
    assert unknown_record["excluded_reports"] == []
    assert unknown_record["runs"][0]["activity"] == "unknown"
    assert unknown_record["runs"][0]["started_at"] == "2026-09-27T08:06:00Z"
    assert pending_record["status"] == "reconciliation_required"
    assert pending_record["schedule"]["state"] == "market_closed"
    assert pending_record["runs"][0]["activity"] == "reconciliation_required"
    assert success_record["business_date"] == "2026-09-28"
    assert success_record["status"] == "unknown"
    assert {run["run_id"] for run in success_record["runs"]} == {"prior-unknown", "today-quiet"}
    assert submitted_record["status"] == "unknown"
    assert {run["activity"] for run in submitted_record["runs"]} == {"unknown", "submitted"}
    assert submitted_record["conflicts"] == []


def test_earlier_same_day_and_future_runs_do_not_cover_due_slot() -> None:
    observed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    early = _project(
        [_report(run_id="morning", started_at="2026-09-28T09:00:00+08:00", finished_at="2026-09-28T09:01:00+08:00")],
        observed_at=observed,
    )
    future = _project(
        [_report(run_id="later", started_at="2026-09-28T23:00:00+08:00", finished_at="2026-09-28T23:01:00+08:00")],
        observed_at=observed,
    )
    inverted = _project(
        [_report(run_id="backwards", started_at="2026-09-28T16:10:00+08:00", finished_at="2026-09-28T16:06:00+08:00")],
        observed_at=observed,
    )

    early_record = _record(early)
    future_record = _record(future)
    inverted_record = _record(inverted)
    assert early_record["schedule"]["state"] == "due"
    assert early_record["status"] == "missing_report"
    assert early_record["completeness"] == "incomplete"
    assert early_record["runs"][0]["activity"] == "no_submission"
    assert early_record["runs"][0]["started_at"] == "2026-09-28T01:00:00Z"
    assert future_record["status"] == "missing_report"
    assert future_record["completeness"] == "incomplete"
    assert future_record["runs"] == []
    assert future_record["excluded_reports"] == [{"run_id": "later", "reason": "future_run_time"}]
    assert inverted_record["status"] == "missing_report"
    assert inverted_record["completeness"] == "incomplete"
    assert inverted_record["runs"] == []
    assert inverted_record["excluded_reports"] == [{"run_id": "backwards", "reason": "inverted_run_time"}]


def test_distinct_runs_keep_independent_facts_and_same_run_conflict_stays() -> None:
    observed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    independent = _project(
        [
            _report(run_id="run-quiet"),
            _report(run_id="run-submitted", broker_submission_done=True, action_done=True),
        ],
        observed_at=observed,
    )
    contradiction = _project(
        [
            _report(run_id="run-1"),
            _report(run_id="run-1", broker_submission_done=True, action_done=True),
        ],
        observed_at=observed,
    )

    independent_record = _record(independent)
    contradiction_record = _record(contradiction)
    assert independent_record["status"] == "submitted"
    assert independent_record["completeness"] == "complete"
    assert independent_record["conflicts"] == []
    assert {run["run_id"]: run["activity"] for run in independent_record["runs"]} == {
        "run-quiet": "no_submission",
        "run-submitted": "submitted",
    }
    assert contradiction_record["status"] == "conflict"
    assert contradiction_record["completeness"] == "insufficient"
    assert {run["activity"] for run in contradiction_record["runs"]} == {"no_submission", "submitted"}
    assert set(contradiction_record["conflicts"]) == {"run-1:no_submission", "run-1:submitted"}


def test_timezones_keep_separate_business_dates_and_accounts() -> None:
    observed = dt.datetime(2026, 9, 28, 21, 0, tzinfo=dt.timezone.utc)
    projected = project_daily_runtime(
        targets=[_hk_target(), _us_target()],
        reports=[
            _report(started_at="2026-09-28T16:06:00+08:00", finished_at="2026-09-28T16:07:00+08:00"),
            _report(
                run_id="us-run",
                service="lb-us",
                strategy="us-profile",
                scope="US",
                started_at="2026-09-28T16:06:00-04:00",
                finished_at="2026-09-28T16:07:00-04:00",
            ),
        ],
        observed_at=observed,
        session_dates_loader=_open_calendar,
    )
    hk = _record(projected, "lb-hk")
    us = _record(projected, "lb-us")

    assert hk["business_date"] == "2026-09-29"
    assert hk["status"] == "not_due"
    assert hk["excluded_reports"][0]["business_date"] == "2026-09-28"
    assert us["business_date"] == "2026-09-28"
    assert us["status"] == "no_submission"
    assert hk["target_key"] != us["target_key"]


def test_dry_run_shadow_and_validation_stay_off_paper_live() -> None:
    observed = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    dry = _project(
        [_report(dry_run=True, broker_submission_done=True, action_done=False, execution_status="previewed")],
        observed_at=observed,
    )
    shadow = _project(
        [_report(mode="shadow", execution_mode="paper")],
        observed_at=observed,
    )
    validation = _project(
        [_report(validation_only=True, dry_run=True)],
        observed_at=observed,
    )

    assert _record(dry)["status"] == "dry_run"
    assert _record(dry)["execution_lane"] == "dry_run"
    assert _record(dry)["runs"][0]["activity"] == "previewed"
    assert _record(shadow)["status"] == "shadow"
    assert _record(shadow)["execution_lane"] == "shadow"
    assert _record(validation)["status"] == "validation"
    assert _record(validation)["execution_lane"] == "validation"


def test_missing_submission_fact_is_insufficient() -> None:
    projected = _project(
        [_report(broker_submission_done=None)],
        observed_at=dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK),
    )

    record = _record(projected)
    assert record["status"] == "insufficient"
    assert record["completeness"] == "insufficient"


def test_cli_prints_projection_without_changing_incomplete_into_failure(tmp_path: Path, capsys) -> None:
    reports = tmp_path / "reports.json"
    targets = tmp_path / "targets.json"
    reports.write_text(json.dumps([_report()]), encoding="utf-8")
    targets.write_text(json.dumps([_hk_target()]), encoding="utf-8")

    exit_code = main(
        [
            "--reports",
            str(reports),
            "--targets",
            str(targets),
            "--observed-at",
            "2026-09-28T16:40:00+08:00",
            "--no-market-aware",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["records"][0]["status"] == "no_submission"
    assert payload["records"][0]["platform"] == "longbridge"
