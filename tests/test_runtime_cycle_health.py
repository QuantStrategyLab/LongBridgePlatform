"""Synthetic evidence only. No broker, filesystem, transport or scheduler calls."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_platform_kit.common.execution_receipts import build_execution_receipt
from quant_platform_kit.common.broker_reconciliation import (
    build_broker_reconciliation_evidence,
)
from application.broker_reconciliation import LongBridgeReconciliationCandidate
from scripts.runtime_cycle_health import (
    CycleContext,
    build_cycle_health,
    coverage_complete,
    derive_coverage,
    incident_category,
    project_cycle,
    project_resolution,
    reduce_incidents,
)

NOW = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
SLOT = "2026-10-05T15:00:00Z"
COMPLETE = "2026-10-05T15:01:00Z"
CTX = CycleContext(
    target_id="longbridge.paper",
    service="longbridge-quant-paper-service",
    source_binding_id="1" * 64,
    configuration_sha256="2" * 64,
    strategy_profile="russell_top50_leader_rotation",
    strategy_revision="3" * 40,
    scheduler_job_name="projects/synthetic/locations/test/jobs/paper-run",
)


def report(outcome="no_action", confirmation=None, *, completed=COMPLETE, slot=SLOT):
    receipt = build_execution_receipt(
        platform="longbridge",
        strategy_profile=CTX.strategy_profile,
        strategy_revision=CTX.strategy_revision,
        execution_mode="paper",
        outcome=outcome,
        broker_confirmation=confirmation,
        observed_at=completed,
    )
    return {
        "platform": "longbridge",
        "service_name": CTX.service,
        "strategy_profile": CTX.strategy_profile,
        "account_scope": "PAPER",
        "dry_run": False,
        "validation_only": False,
        "runtime_target": {"execution_mode": "paper"},
        "runtime_release_receipt": {
            "attestation_state": "self_attested",
            "strategy_release": {"strategy_revision": CTX.strategy_revision},
        },
        "started_at": slot,
        "finished_at": completed,
        "status": "error" if outcome == "failed" else "ok",
        "errors": ["synthetic_failure"] if outcome == "failed" else [],
        "summary": {"broker_submission_done": False, "orders_pending_count": 0},
        "invocation": {
            "scheduler_job_name": CTX.scheduler_job_name,
            "scheduled_for": slot,
        },
        "execution_receipt": receipt,
    }


def coverage(n=1, *, next_token=None, failed_read=False):
    names = [f"synthetic/{i}" for i in range(n)]
    pages = {
        "synthetic/": [
            {
                "request_token": None,
                "items": [{"name": n} for n in names],
                "nextPageToken": next_token,
            }
        ]
    }
    return derive_coverage(
        context=CTX,
        required_prefixes=("synthetic/",),
        pages=pages,
        reads={n: (None if failed_read else report()) for n in names},
        read_from="2026-10-05T00:00:00Z",
        read_through="2026-10-07T16:00:00Z",
    )


def schedule(**overrides):
    return {
        "state": "not_due",
        "reason": "no_cron_on_business_date",
        "timezone": "America/New_York",
        "latest_due_at": SLOT,
        "next_due_at": "2026-10-12T15:00:00Z",
        "deadline_at": "2026-10-05T15:30:00Z",
        **overrides,
    }


def cycle(value=None):
    return project_cycle(
        CTX, report() if value is None else value, scheduled_for=SLOT, now=NOW
    )


def test_terminal_pagination_not_twenty_is_coverage_proof():
    assert coverage_complete(coverage(20)) is True
    assert coverage(20)["terminal_page_seen"] is True
    assert coverage(20)["limit_hit"] is False
    assert coverage(20, next_token="more")["terminal_page_seen"] is False
    assert coverage(21)["limit_hit"] is True
    assert coverage(failed_read=True)["errors_present"] is True
    assert coverage_complete(coverage(20, next_token="more")) is False
    assert coverage_complete(coverage(21)) is False


def test_actual_canonical_receipt_controls_execution_certainty():
    assert cycle()["execution_state"] == "no_submission"
    operational = cycle(report("failed", "not_applicable"))
    uncertain = cycle(report("failed", "not_observed"))
    assert incident_category(operational) == "operational"
    assert incident_category(uncertain) == "execution_uncertainty"
    assert "errors" not in operational
    bad = report()
    bad["execution_receipt"]["outcome"] = "filled"
    with pytest.raises(ValueError):
        cycle(bad)


def test_minimal_six_member_envelope():
    result = build_cycle_health(
        CTX,
        schedule=schedule(),
        coverage=coverage(),
        observations=[{"scheduled_for": SLOT, "report": report()}],
        now=NOW,
    )
    assert set(result) == {
        "schema_version",
        "configuration_sha256",
        "schedule",
        "coverage",
        "cycles",
        "resolutions",
    }
    assert "run_id" not in result["cycles"][0]
    assert "source_object" not in str(result)
    assert result["cycles"][0]["receipt_ref"].startswith("execution-receipt.")


def test_no_due_does_not_erase_historical_fault():
    failed = cycle(report("failed", "not_observed"))
    state = reduce_incidents(CTX, [failed])
    after = reduce_incidents(CTX, [], previous=state)
    assert after["incidents"] == state["incidents"]
    assert after["execution_authority_granted"] is False
    assert after["unresolved_count"] == 1


def test_same_cycle_retry_resolves_only_known_no_submission():
    failed = cycle(report("failed", "not_applicable"))
    successful = cycle(report(completed="2026-10-05T15:02:00Z"))
    resolution = project_resolution(
        CTX,
        incident=failed,
        kind="same_cycle_retry_succeeded",
        successful=report(completed="2026-10-05T15:02:00Z"),
        coverage=coverage(),
        now=NOW,
    )
    state = reduce_incidents(CTX, [failed])
    recovered = reduce_incidents(
        CTX, [successful], resolutions=[resolution], previous=state
    )
    assert recovered["unresolved_count"] == 0
    assert len(recovered["incidents"]) == 1
    assert (
        recovered["incidents"][0]["resolution"]["evidence_ref"]
        == successful["receipt_ref"]
    )
    with pytest.raises(ValueError, match="uncertain"):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_observed")),
            kind="same_cycle_retry_succeeded",
            successful=report(completed="2026-10-05T15:02:00Z"),
            coverage=coverage(),
            now=NOW,
        )


def test_incomplete_reads_cannot_bootstrap_or_resolve():
    with pytest.raises(ValueError, match="coverage"):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_applicable")),
            kind="same_cycle_retry_succeeded",
            successful=cycle(),
            coverage=coverage(20, next_token="more"),
            now=NOW,
        )


def reconciliation(*, observed_at=NOW, **overrides):
    digests = {
        name: str(i) * 64
        for i, name in enumerate(
            (
                "account_scope_sha256",
                "positions_sha256",
                "cash_sha256",
                "open_orders_sha256",
                "recent_executions_sha256",
                "local_execution_ledger_sha256",
            ),
            start=1,
        )
    }
    baseline = {
        "source_binding_id": CTX.source_binding_id,
        "configuration_sha256": CTX.configuration_sha256,
        "baseline_id": "synthetic-paper-baseline",
        "runtime_target_sha256": "7" * 64,
        "digests": digests,
    }
    fields = dict(
        platform_id="longbridge",
        strategy_profile=CTX.strategy_profile,
        baseline_id=baseline["baseline_id"],
        baseline_target_sha256="7" * 64,
        runtime_target_sha256="7" * 64,
        observed_at=observed_at,
        broker_connected=True,
        account_identity_match=True,
        positions_match=True,
        cash_match=True,
        open_orders_match=True,
        recent_executions_match=True,
        local_execution_ledger_match=True,
        **digests,
    )
    fields.update(overrides)
    evidence = build_broker_reconciliation_evidence(**fields)
    candidate = LongBridgeReconciliationCandidate(evidence, (), True, 1).to_safe_dict()
    return candidate, baseline


def test_later_current_state_reconciliation_closes_old_incident_without_erasing_it():
    failed = cycle(report("failed", "not_observed"))
    candidate, baseline = reconciliation()
    result = project_resolution(
        CTX,
        incident=failed,
        kind="current_state_reconciled",
        reconciliation_candidate=candidate,
        baseline=baseline,
        coverage=coverage(),
        now=NOW,
    )
    state = reduce_incidents(CTX, [failed])
    recovered = reduce_incidents(CTX, [], resolutions=[result], previous=state)
    assert recovered["unresolved_count"] == 0
    assert recovered["incidents"][0]["cycle"] == failed
    assert recovered["incidents"][0]["resolution"]["kind"] == "current_state_reconciled"
    assert recovered["execution_authority_granted"] is False
    assert "positions_sha256" not in result
    assert result["evidence_ref"] == candidate["evidence"]["evidence_sha256"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"positions_match": False},
        {"cash_match": False},
        {"open_orders_match": False},
        {"recent_executions_match": False},
        {"local_execution_ledger_match": False},
        {"account_identity_match": False},
        {"broker_connected": False},
        {"account_scope_sha256": "a" * 64},
        {"runtime_target_sha256": "b" * 64},
        {"positions_sha256": "c" * 64},
        {"strategy_profile": "wrong_profile"},
        {"baseline_id": "wrong-baseline"},
        {"observed_at": NOW - timedelta(hours=1)},
        {"observed_at": NOW + timedelta(seconds=1)},
    ],
)
def test_reconciliation_is_recomputed_not_trusted_from_positive_wrapper(overrides):
    candidate, baseline = reconciliation(**overrides)
    # The deliberately optimistic wrapper cannot override actual QPK validation.
    assert candidate["permits_active_lkg"] is True
    with pytest.raises(ValueError, match="reconciliation"):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_observed")),
            kind="current_state_reconciled",
            reconciliation_candidate=candidate,
            baseline=baseline,
            coverage=coverage(),
            now=NOW,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_digests_configured", False),
        ("recovery_blockers", ["synthetic-blocker"]),
        ("permits_active_lkg", False),
        ("execution_ledger_records_count", True),
    ],
)
def test_candidate_wrapper_conflicts_are_rejected(field, value):
    candidate, baseline = reconciliation()
    candidate[field] = value
    with pytest.raises(ValueError):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_observed")),
            kind="current_state_reconciled",
            reconciliation_candidate=candidate,
            baseline=baseline,
            coverage=coverage(),
            now=NOW,
        )


def test_missing_recovery_baseline_and_forged_digest_fail():
    candidate, baseline = reconciliation()
    candidate["evidence"]["evidence_sha256"] = "a" * 64
    for selected, expected in [
        (candidate, baseline),
        (reconciliation()[0], None),
        (reconciliation()[0], {**baseline, "source_binding_id": "9" * 64}),
    ]:
        with pytest.raises((ValueError, TypeError)):
            project_resolution(
                CTX,
                incident=cycle(report("failed", "not_observed")),
                kind="current_state_reconciled",
                reconciliation_candidate=selected,
                baseline=expected,
                coverage=coverage(),
                now=NOW,
            )


def test_original_failed_fallback_shape_is_uncertain_even_without_summary():
    value = report("failed", "not_observed")
    value["summary"] = {}
    value["finished_at"] = "2026-10-05T15:00:59Z"
    assert cycle(value)["execution_state"] == "unresolved"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(service_name="another-service"),
        lambda r: r.update(account_scope="SG"),
        lambda r: r.update(dry_run=True),
        lambda r: r.update(validation_only=True),
        lambda r: r["runtime_target"].update(execution_mode="live"),
        lambda r: r["runtime_release_receipt"]["strategy_release"].update(
            strategy_revision="9" * 40
        ),
        lambda r: r["summary"].update(orders_pending_count=1),
        lambda r: r["summary"].update(broker_submission_done=True),
        lambda r: r.update(errors=["unmasked failure"]),
        lambda r: r["invocation"].update(scheduler_job_name="other-job"),
        lambda r: r["invocation"].update(scheduled_for="2026-10-06T15:00:00Z"),
        lambda r: r.update(started_at="2026-10-05T15:02:00Z"),
    ],
)
def test_source_binding_lane_revision_and_errors_are_never_excused(mutation):
    value = report()
    mutation(value)
    with pytest.raises(ValueError):
        cycle(value)


def test_plain_later_cycle_and_legacy_window_match_do_not_clear_failure():
    failed = cycle(report("failed", "not_applicable"))
    later = project_cycle(
        CTX,
        report(slot="2026-10-06T15:00:00Z", completed="2026-10-06T15:01:00Z"),
        scheduled_for="2026-10-06T15:00:00Z",
        now=NOW,
    )
    state = reduce_incidents(CTX, [failed])
    assert reduce_incidents(CTX, [later], previous=state)["unresolved_count"] == 1
    legacy = report(completed="2026-10-05T15:02:00Z")
    del legacy["invocation"]
    for successful in (
        report(slot="2026-10-06T15:00:00Z", completed="2026-10-06T15:01:00Z"),
        legacy,
    ):
        with pytest.raises(ValueError, match="retry"):
            project_resolution(
                CTX,
                incident=failed,
                kind="same_cycle_retry_succeeded",
                successful=successful,
                coverage=coverage(),
                now=NOW,
            )


def test_binding_partition_and_configuration_revision_do_not_reset_faults():
    prior = reduce_incidents(CTX, [cycle(report("failed", "not_observed"))])
    copy = deepcopy(prior)
    with pytest.raises(ValueError, match="binding_changed_unconfirmed"):
        reduce_incidents(replace(CTX, source_binding_id="a" * 64), [], previous=prior)
    new_config = replace(CTX, configuration_sha256="b" * 64)
    assert reduce_incidents(new_config, [], previous=prior)["unresolved_count"] == 1
    assert prior == copy
    with pytest.raises(ValueError, match="configuration"):
        project_resolution(
            new_config,
            incident=prior["incidents"][0]["cycle"],
            kind="current_state_reconciled",
            coverage=coverage(),
            now=NOW,
        )


def test_missing_due_receipt_is_uncertain_and_requires_positive_coverage():
    complete = coverage(0)
    due = schedule(state="due", reason="publication_grace_ended")
    result = build_cycle_health(CTX, schedule=due, coverage=complete, now=NOW)
    missing = result["cycles"][0]
    assert missing["outcome"] == "missing_report"
    assert incident_category(missing) == "execution_uncertainty"
    assert missing["receipt_ref"] is None
    for partial in (coverage(20, next_token="more"), coverage(failed_read=True)):
        incomplete = build_cycle_health(CTX, schedule=due, coverage=partial, now=NOW)
        assert incomplete["cycles"] == []
        assert not coverage_complete(incomplete["coverage"])


def test_no_due_today_does_not_require_daily_report_or_infer_cadence():
    for next_due in (
        "2026-10-09T15:00:00Z",
        "2026-10-12T15:00:00Z",
        "2026-11-02T15:00:00Z",
    ):
        result = build_cycle_health(
            CTX,
            schedule=schedule(
                latest_due_at=None, deadline_at=None, next_due_at=next_due
            ),
            coverage=coverage(0),
            now=NOW,
        )
        assert result["cycles"] == []
        assert result["schedule"]["next_due_at"] == next_due


def test_provider_page_tokens_all_prefixes_and_aborts_are_checked():
    args = dict(
        context=CTX,
        required_prefixes=("a/", "b/"),
        pages={
            "a/": [
                {
                    "request_token": None,
                    "items": [{"name": "a/1"}],
                    "nextPageToken": "p2",
                },
                {"request_token": "p2", "items": [], "nextPageToken": None},
            ],
            "b/": [{"request_token": None, "items": []}],
        },
        reads={"a/1": report()},
        read_from="2026-10-05T00:00:00Z",
        read_through=NOW,
    )
    assert coverage_complete(derive_coverage(**args))
    for variant in [
        {**args, "aborted": True},
        {**args, "pages": {"a/": args["pages"]["a/"]}},
        {
            **args,
            "pages": {
                "a/": [{"request_token": "wrong", "items": []}],
                "b/": args["pages"]["b/"],
            },
        },
        {**args, "reads": {"a/1": {"verified": True}}},
    ]:
        assert not coverage_complete(derive_coverage(**variant))


def test_partial_interval_future_times_and_limits_cannot_be_promoted():
    narrow = coverage()
    narrow["from"] = "2026-10-06T00:00:00Z"
    with pytest.raises(ValueError, match="coverage"):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_applicable")),
            kind="same_cycle_retry_succeeded",
            successful=cycle(report(completed="2026-10-05T15:02:00Z")),
            coverage=narrow,
            now=NOW,
        )
    for invalid in [
        schedule(next_due_at=SLOT),
        schedule(latest_due_at="2026-10-08T00:00:00Z"),
        schedule(deadline_at="2026-10-04T00:00:00Z"),
        schedule(reason="unsupported"),
    ]:
        with pytest.raises(ValueError):
            build_cycle_health(CTX, schedule=invalid, coverage=coverage(), now=NOW)
    with pytest.raises(ValueError, match="limit"):
        build_cycle_health(
            CTX,
            schedule=schedule(),
            coverage=coverage(),
            observations=[{"scheduled_for": SLOT, "report": report()}] * 21,
            now=NOW,
        )


def test_public_projection_is_immutable_private_and_non_authoritative(monkeypatch):
    import socket
    import subprocess

    monkeypatch.setattr(
        socket, "socket", lambda *a, **k: pytest.fail("network forbidden")
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: pytest.fail("subprocess forbidden")
    )
    value = report()
    original = deepcopy(value)
    value["private_note"] = "must-never-be-exported"
    result = build_cycle_health(
        CTX,
        schedule=schedule(),
        coverage=coverage(),
        observations=[{"scheduled_for": SLOT, "report": value}],
        now=NOW,
    )
    assert "must-never-be-exported" not in str(result)
    assert value == {**original, "private_note": "must-never-be-exported"}
    assert (
        reduce_incidents(CTX, result["cycles"])["execution_authority_granted"] is False
    )


def test_forged_projected_success_is_not_recovery_evidence():
    with pytest.raises(ValueError, match="raw"):
        project_resolution(
            CTX,
            incident=cycle(report("failed", "not_applicable")),
            kind="same_cycle_retry_succeeded",
            successful=cycle(),
            coverage=coverage(),
            now=NOW,
        )


def test_latest_failure_cannot_be_cleared_by_an_older_success():
    first = cycle(report("failed", "not_applicable"))
    later_failure = cycle(
        report("failed", "not_applicable", completed="2026-10-05T15:03:00Z")
    )
    successful_report = report(completed="2026-10-05T15:02:00Z")
    old_resolution = project_resolution(
        CTX,
        incident=first,
        kind="same_cycle_retry_succeeded",
        successful=successful_report,
        coverage=coverage(),
        now=NOW,
    )
    state = reduce_incidents(CTX, [first, later_failure])
    assert state["incidents"][0]["cycle"] == later_failure
    with pytest.raises(ValueError, match="precedes"):
        reduce_incidents(
            CTX,
            [cycle(successful_report)],
            resolutions=[old_resolution],
            previous=state,
        )


def test_exact_replay_of_admitted_incident_and_resolution_is_idempotent():
    failed = cycle(report("failed", "not_applicable"))
    successful_report = report(completed="2026-10-05T15:02:00Z")
    success = cycle(successful_report)
    resolution = project_resolution(
        CTX,
        incident=failed,
        kind="same_cycle_retry_succeeded",
        successful=successful_report,
        coverage=coverage(),
        now=NOW,
    )
    first = reduce_incidents(CTX, [failed, success], resolutions=[resolution])
    second = reduce_incidents(
        CTX, [failed, success], resolutions=[resolution], previous=first
    )
    assert first == second


def test_real_schedule_classifier_weekly_monthly_and_dst_values_are_accepted():
    from scripts.runtime_heartbeat_policy import classify_business_date_schedule

    for clock, cron in [
        (NOW, "45 15 * * 1"),
        (NOW, "45 15 1 * *"),
        (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), "45 15 * * 1-5"),
    ]:
        target = {
            "service": CTX.service,
            "strategy_profile": CTX.strategy_profile,
            "account_scope": "paper",
            "market_timezone": "America/New_York",
            "market_calendar": "NYSE",
            "scheduler": {"main_time": cron, "timezone": "America/New_York"},
        }
        original = classify_business_date_schedule(
            target,
            now=clock,
            session_dates_loader=lambda calendar, **kw: {kw["start_date"]},
        )
        supplied = {
            name: original[name]
            for name in ["state", "reason", "timezone", "latest_due_at", "next_due_at"]
        }
        supplied["deadline_at"] = original["grace_ends_at"]
        cov = derive_coverage(
            context=CTX,
            required_prefixes=("synthetic/",),
            pages={"synthetic/": [{"request_token": None, "items": []}]},
            reads={},
            read_from=clock - timedelta(days=1),
            read_through=clock,
        )
        output = build_cycle_health(CTX, schedule=supplied, coverage=cov, now=clock)
        assert output["schedule"]["reason"] == "no_cron_on_business_date"
        assert output["cycles"] == []


def test_review_known_older_fault_replay_does_not_resurrect_resolved_cycle():
    from itertools import permutations

    first = cycle(report("failed", "not_applicable"))
    second = cycle(report("failed", "not_applicable", completed="2026-10-05T15:02:00Z"))
    raw_success = report(completed="2026-10-05T15:03:00Z")
    successful = cycle(raw_success)
    resolution = project_resolution(
        CTX,
        incident=second,
        kind="same_cycle_retry_succeeded",
        successful=raw_success,
        coverage=coverage(),
        now=NOW,
    )
    state = reduce_incidents(CTX, [first, second, successful], resolutions=[resolution])
    assert len(state["incidents"][0]["fault_attempts"]) == 2
    for ordering in permutations([first, second, successful]):
        assert reduce_incidents(CTX, ordering, previous=state) == state
        assert (
            reduce_incidents(CTX, ordering, resolutions=[resolution], previous=state)
            == state
        )


def _early_reconciliation(incident, minute=2):
    observed = datetime(2026, 10, 5, 15, minute, tzinfo=timezone.utc)
    candidate, baseline = reconciliation(observed_at=observed)
    cov = derive_coverage(
        context=CTX,
        required_prefixes=("synthetic/",),
        pages={
            "synthetic/": [
                {"request_token": None, "items": [{"name": "synthetic/fault"}]}
            ]
        },
        reads={"synthetic/fault": report("failed", "not_observed")},
        read_from="2026-10-05T00:00:00Z",
        read_through=observed,
    )
    resolution = project_resolution(
        CTX,
        incident=incident,
        kind="current_state_reconciled",
        reconciliation_candidate=candidate,
        baseline=baseline,
        coverage=cov,
        now=observed,
    )
    return resolution


def test_review_new_fault_watermark_is_independent_of_historical_severity():
    uncertain = cycle(report("failed", "not_observed"))
    old_resolution = _early_reconciliation(uncertain)
    resolved = reduce_incidents(CTX, [uncertain], resolutions=[old_resolution])
    later = cycle(report("failed", "not_applicable", completed="2026-10-05T15:03:00Z"))
    reopened = reduce_incidents(CTX, [later], previous=resolved)
    item = reopened["incidents"][0]
    assert (
        item["cycle"] == uncertain
    )  # Historical representative is deliberately older.
    assert item["category"] == "execution_uncertainty"
    assert item["active_category"] == "operational"
    assert item["latest_fault_at"] == "2026-10-05T15:03:00Z"
    assert reopened["unresolved_count"] == 1
    assert (
        reduce_incidents(CTX, [], resolutions=[old_resolution], previous=reopened)
        == reopened
    )
    # A changed timestamp cannot give the same proof wider coverage.
    changed = {**old_resolution, "verified_through": "2026-10-05T15:04:00Z"}
    with pytest.raises(ValueError, match="conflicting recovery"):
        reduce_incidents(CTX, [], resolutions=[changed], previous=reopened)
    # The new known-no-submission attempt can recover without resurrecting the
    # already reconciled uncertain attempt.
    raw_success = report(completed="2026-10-05T15:04:00Z")
    fresh = project_resolution(
        CTX,
        incident=later,
        kind="same_cycle_retry_succeeded",
        successful=raw_success,
        coverage=coverage(),
        now=NOW,
    )
    closed = reduce_incidents(
        CTX, [cycle(raw_success)], resolutions=[fresh], previous=reopened
    )
    assert closed["unresolved_count"] == 0
    assert len(closed["incidents"][0]["resolution_history"]) == 2
    assert len(closed["incidents"][0]["fault_attempts"]) == 2
    assert closed["execution_authority_granted"] is False


def test_new_stale_proof_cannot_cover_latest_fault_even_if_representative_is_older():
    uncertain = cycle(report("failed", "not_observed"))
    later = cycle(report("failed", "not_applicable", completed="2026-10-05T15:03:00Z"))
    state = reduce_incidents(CTX, [uncertain, later])
    older_proof = _early_reconciliation(uncertain)
    original = deepcopy(state)
    with pytest.raises(ValueError, match="fault watermark"):
        reduce_incidents(CTX, [], resolutions=[older_proof], previous=state)
    assert state == original


def test_success_for_newer_operational_attempt_cannot_clear_other_active_uncertainty():
    uncertain = cycle(report("failed", "not_observed"))
    later = cycle(report("failed", "not_applicable", completed="2026-10-05T15:03:00Z"))
    raw_success = report(completed="2026-10-05T15:04:00Z")
    resolution = project_resolution(
        CTX,
        incident=later,
        kind="same_cycle_retry_succeeded",
        successful=raw_success,
        coverage=coverage(),
        now=NOW,
    )
    state = reduce_incidents(CTX, [uncertain, later])
    with pytest.raises(ValueError, match="retry resolution"):
        reduce_incidents(
            CTX, [cycle(raw_success)], resolutions=[resolution], previous=state
        )


def test_genuinely_new_uncertain_attempt_after_coverage_reopens():
    uncertain = cycle(report("failed", "not_observed"))
    resolution = _early_reconciliation(uncertain)
    prior = reduce_incidents(CTX, [uncertain], resolutions=[resolution])
    new_attempt = cycle(
        report("failed", "not_observed", completed="2026-10-05T15:03:00Z")
    )
    current = reduce_incidents(CTX, [new_attempt], previous=prior)
    assert current["unresolved_count"] == 1
    assert current["incidents"][0]["active_category"] == "execution_uncertainty"
    assert current["incidents"][0]["latest_fault_at"] == new_attempt["completed_at"]
    assert (
        reduce_incidents(CTX, [], resolutions=[resolution], previous=current) == current
    )


def test_newly_discovered_old_fault_is_not_silently_covered_by_prior_proof():
    first = cycle(report("failed", "not_applicable"))
    raw_success = report(completed="2026-10-05T15:03:00Z")
    resolution = project_resolution(
        CTX,
        incident=first,
        kind="same_cycle_retry_succeeded",
        successful=raw_success,
        coverage=coverage(),
        now=NOW,
    )
    prior = reduce_incidents(CTX, [first, cycle(raw_success)], resolutions=[resolution])
    late_unknown = cycle(
        report("failed", "not_observed", completed="2026-10-05T15:02:00Z")
    )
    current = reduce_incidents(CTX, [late_unknown], previous=prior)
    assert current["unresolved_count"] == 1
    assert (
        reduce_incidents(CTX, [], resolutions=[resolution], previous=current) == current
    )


def test_same_receipt_changed_projection_is_a_conflict_not_a_new_attempt():
    original = report("failed", "not_applicable")
    changed = deepcopy(original)
    changed["finished_at"] = "2026-10-05T15:02:00Z"  # Canonical receipt unchanged.
    prior = reduce_incidents(CTX, [cycle(original)])
    with pytest.raises(ValueError, match="conflicting fault receipt replay"):
        reduce_incidents(CTX, [cycle(changed)], previous=prior)
    assert len(prior["incidents"][0]["fault_attempts"]) == 1


def test_attempt_limit_fails_closed_without_forgetting_dedup_history():
    attempts = [
        cycle(
            report(
                "failed", "not_applicable", completed=f"2026-10-05T15:{minute:02d}:00Z"
            )
        )
        for minute in range(1, 21)
    ]
    prior = reduce_incidents(CTX, attempts)
    with pytest.raises(ValueError, match="attempt history"):
        reduce_incidents(
            CTX,
            [
                cycle(
                    report("failed", "not_applicable", completed="2026-10-05T15:21:00Z")
                )
            ],
            previous=prior,
        )
    assert len(prior["incidents"][0]["fault_attempts"]) == 20


@pytest.mark.parametrize(
    "timezone_value,reason",
    [
        (None, "missing_timezone"),
        ("", "missing_timezone"),
        ("Not/A-Timezone", "invalid_timezone"),
    ],
)
def test_unevaluable_timezone_is_explicit_null_not_a_default_zone(
    timezone_value, reason
):
    supplied = schedule(
        state="unevaluable",
        reason=reason,
        timezone=timezone_value,
        latest_due_at=None,
        next_due_at=None,
        deadline_at=None,
    )
    result = build_cycle_health(CTX, schedule=supplied, coverage=coverage(0), now=NOW)
    assert result["schedule"]["state"] == "unevaluable"
    assert result["schedule"]["timezone"] is None
    assert result["schedule"]["reason"] == reason
    assert result["cycles"] == []


def test_real_classifier_missing_and_bad_scheduler_timezone_are_representable():
    from scripts.runtime_heartbeat_policy import classify_business_date_schedule

    targets = [
        {},
        {
            "market_timezone": "America/New_York",
            "scheduler": {"main_time": "45 15 * * 1-5", "timezone": "Not/A-Timezone"},
        },
    ]
    for target in targets:
        raw = classify_business_date_schedule(target, now=NOW)
        supplied = {
            name: raw[name]
            for name in ("state", "reason", "timezone", "latest_due_at", "next_due_at")
        }
        supplied["deadline_at"] = raw["grace_ends_at"]
        result = build_cycle_health(
            CTX, schedule=supplied, coverage=coverage(0), now=NOW
        )
        assert result["schedule"]["state"] == "unevaluable"
        assert result["schedule"]["reason"] in {"missing_timezone", "invalid_timezone"}


@pytest.mark.parametrize("timezone_value", [None, "", "Not/A-Timezone"])
def test_evaluable_schedule_cannot_smuggle_unknown_timezone(timezone_value):
    with pytest.raises(ValueError):
        build_cycle_health(
            CTX,
            schedule=schedule(timezone=timezone_value),
            coverage=coverage(0),
            now=NOW,
        )


def test_prototype_checkpoint_without_attempt_history_is_not_silently_migrated():
    first = cycle(report("failed", "not_applicable"))
    old = {
        "target_id": CTX.target_id,
        "source_binding_id": CTX.source_binding_id,
        "incidents": [{"cycle": first, "category": "operational", "resolution": None}],
    }
    with pytest.raises(ValueError, match="attempt history"):
        reduce_incidents(CTX, [], previous=old)
