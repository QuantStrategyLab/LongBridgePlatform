"""LB-01: real reporting/finalization, never a hand-written archive envelope."""

import copy
import hashlib
import importlib.metadata
import json
import tomllib
from datetime import datetime, timezone

import pytest

import archive_finalizer_support as support
from application.runtime_reporting_adapters import build_runtime_reporting_adapters
from notifications import unified
from quant_platform_kit.common import runtime_reports
from quant_platform_kit.common.runtime_target import resolve_runtime_target_from_env


bound = support.bound
qrs = support.qrs


@pytest.fixture
def finalized_cycle(monkeypatch, bound, tmp_path):
    env, _, _ = bound
    actual_composer = support.LongBridgeRuntimeComposer

    def run(case="healthy", run_id="synthetic-business-run", status="ok"):
        report = {}

        def compose(**kwargs):
            kwargs.update(
                runtime_target=resolve_runtime_target_from_env(
                    env=env, expected_platform_id="longbridge"
                ),
                report_builder=runtime_reports.build_runtime_report_base,
                run_id_builder=lambda: run_id,
                env_reader=lambda name, default="": (
                    "synthetic-service" if name == "K_SERVICE" else default
                ),
                reporting_adapter_builder=lambda **args: (
                    build_runtime_reporting_adapters(
                        clock=lambda: datetime(2026, 10, 16, 20, tzinfo=timezone.utc),
                        **args,
                    )
                ),
            )
            composer = actual_composer(**kwargs)
            _, started = composer.build_reporting_adapters().start_run()
            report.update(started)
            return composer

        monkeypatch.setattr(support, "LongBridgeRuntimeComposer", compose)
        calls, result, summary = support.run_cycle(monkeypatch, case, report=report)
        before = copy.deepcopy(summary)
        runtime_reports.finalize_runtime_report(
            report,
            status=status,
            finished_at=datetime(2026, 10, 16, 20, 1, tzinfo=timezone.utc),
            summary=summary,
        )
        # Preserve the actual normalized bytes that an archive reader sees.
        serialized = json.dumps(report, sort_keys=True)
        saved = json.loads(serialized)
        (tmp_path / (run_id + "-archive.json")).write_text(serialized)
        (tmp_path / (run_id + "-raw-summary.json")).write_text(json.dumps(before))
        (tmp_path / (run_id + "-calls.json")).write_text(json.dumps(calls))
        return saved, before, calls, result

    return run


def receive_final(qrs, tmp_path, env, payload):
    rows, summary, text, stats = support.receive(
        qrs, tmp_path, env, payload, duplicate=True
    )
    expected = [unified.target_binding(env)[0]]
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "notifications": {
                    "quant_sentinel": {
                        "daily_digest": {"aggregator": {"expected_targets": expected}}
                    }
                }
            }
        )
    )
    merged, _ = qrs.merge.merge(None, tmp_path / "candidates")
    merged_path = tmp_path / "merged.json"
    merged_path.write_text(json.dumps(merged))
    full, full_text, collected = qrs.aggregator.build_digest_payload(
        business_day=payload["runs"][0]["business_day"],
        candidates_path=merged_path,
        config_path=config,
        include_github=False,
    )
    receipt = qrs.aggregator.build_receipt(
        full, collected, message_chars=len(full_text)
    )
    assert receipt["notification_summary"] == summary
    assert receipt["notification_summary"]["source_inventory"]["longbridge"][
        "runs"
    ] == len(rows)
    (tmp_path / "final-qrs-receipt.json").write_text(json.dumps(receipt, default=str))
    assert stats["files"] == 2
    return rows, summary, text


def test_real_finalizer_healthy_two_runs(finalized_cycle, bound, qrs, tmp_path):
    env, _, _ = bound
    reports = []
    for run_id in ("synthetic-a", "synthetic-b"):
        report, raw, calls, _ = finalized_cycle(run_id=run_id)
        raw_event = raw["notification_digest"]["event"]
        event = report["summary"]["notification_digest"]["event"]
        removed = set(raw_event) - set(event)
        assert removed == {
            "incident_reason",
            "order_execution_digest",
            "expected_block_evidence",
        }
        assert all(raw_event[key] is None for key in removed)
        assert not [c for c in calls if c[0] == "send"]
        reports.append(report)
    _, payload = support.export(env, reports)
    rows, summary, text = receive_final(qrs, tmp_path, env, payload)
    assert len(rows) == 2 and summary["event_count"] == 2
    assert not summary["counts_unknown"]
    assert summary["source_inventory"]["longbridge"]["covered_targets"] == 1
    assert all(row["order_count"] == row["fill_count"] == 0 for row in rows)
    assert "窗口内各轮均为 0 单" in text


@pytest.mark.parametrize(
    "case", ["healthy", "order", "pending", "blocked", "unknown", "error"]
)
def test_real_finalizer_terminal_cases(finalized_cycle, bound, qrs, tmp_path, case):
    env, _, _ = bound
    report, raw, calls, _ = finalized_cycle(case)
    event = report["summary"]["notification_digest"]["event"]
    assert "expected_block_evidence" not in event
    _, payload = support.export(env, [report, report])
    rows, summary, text = receive_final(qrs, tmp_path, env, payload)
    assert len(rows) == 1 and summary["event_count"] == 1
    assert rows[0]["source_run_id"] == report["run_id"]
    assert summary["source_inventory"]["longbridge"]["covered_targets"] == 1
    for name in ("order_count", "fill_count"):
        expected = raw["notification_digest"][name]
        assert rows[0][name] == expected
        if case != "healthy":
            assert expected is None and name not in event
    if case == "healthy":
        assert not summary["counts_unknown"] and "本轮 0 单" in text
    else:
        assert summary["counts_unknown"] and "未知" in text
        assert len([c for c in calls if c[0] == "send"]) == 1


def test_real_policy_zero_run_window(bound, qrs, tmp_path):
    env, target, _ = bound
    store = support.MemoryStore()
    # Sunday has no due weekday invocation; use the original policy, no mock.
    configured = {
        **env,
        "EXECUTION_REPORT_GCS_URI": "gs://synthetic-archive/archive",
        "DIGEST_NOTIFICATION_SCHEDULE_JSON": json.dumps(
            {
                **target,
                "scheduler": {
                    "main_time": "0 10 * * 1-5",
                    "timezone": "Asia/Singapore",
                },
            }
        ),
    }
    result = unified.emit_archived(
        configured,
        now=datetime(2026, 10, 19, tzinfo=timezone.utc),
        read=lambda args: [],
        store=store,
    )
    payload = next(
        v for k, (_, v) in store.backing.items() if not k.startswith("candidate_state/")
    )
    assert result["runs"] == 0
    rows, summary, text = receive_final(qrs, tmp_path, env, payload)
    assert rows == [] and summary["event_count"] == 0
    assert not summary["counts_unknown"]
    assert "本轮 0 单" not in text


@pytest.mark.parametrize(
    "field,value",
    [
        ("opaque_account_uid", "other-account"),
        ("target_id", "other-target"),
        ("source_run_id", "other-run"),
        ("occurred_at", "2026-10-16T20:01:00Z"),
        ("business_day", "2026-10-15"),
        ("window_id", "different-window"),
        ("schema_version", "different-schema"),
        ("coverage_status", "unknown"),
        ("expected_block_evidence", "unexpected-nonempty-evidence"),
        ("order_execution_digest", "a" * 64),
        ("order_count", 1),
        ("fill_count", 1),
        ("order_count", False),
        ("unexpected_field", None),
    ],
)
def test_archive_rejects_nonempty_or_identity_and_count_changes(
    finalized_cycle, bound, field, value
):
    env, _, _ = bound
    report, _, _, _ = finalized_cycle()
    report["summary"]["notification_digest"]["event"][field] = value
    with pytest.raises(ValueError):
        support.export(env, [report])


@pytest.mark.parametrize(
    "field",
    [
        "platform_id",
        "opaque_account_uid",
        "target_id",
        "strategy_profile",
        "source_run_id",
        "occurred_at",
        "schema_version",
        "event_kind",
        "dedupe_key",
        "business_day",
        "report_date",
        "window_id",
        "coverage_status",
        "order_count",
        "fill_count",
    ],
)
def test_archive_rejects_required_or_known_count_omissions(
    finalized_cycle, bound, field
):
    env, _, _ = bound
    report, _, _, _ = finalized_cycle()
    del report["summary"]["notification_digest"]["event"][field]
    with pytest.raises((ValueError, KeyError)):
        support.export(env, [report])


def test_archive_rejects_cross_target_report(finalized_cycle, bound):
    env, _, _ = bound
    report, _, _, _ = finalized_cycle()
    report["runtime_target"]["account_scope"] = "OTHER"
    with pytest.raises(ValueError, match="source_binding_mismatch"):
        unified._archived_event(report, env)


def test_finalized_alert_bool_ack_remains_uncertain_and_not_resent(
    finalized_cycle, bound
):
    _, _, stores = bound
    for attempt in range(2):
        report, _raw, calls, _result = finalized_cycle("unknown")
        if attempt == 0:
            assert len([c for c in calls if c[0] == "send"]) == 1
        else:
            assert not [c for c in calls if c[0] == "send"]
    markers = [
        value
        for store in stores.values()
        for _, value in store.backing.values()
        if "delivery_status" in value and "transition_key" in value
    ]
    assert markers and all(
        m["delivery_status"] == "uncertain" and m["receipt_id"] is None for m in markers
    )
    assert report["summary"]["notification_digest"].get("order_count") is None


def test_finalizer_and_receiver_use_exact_recorded_sources(tmp_path):
    lock = tomllib.loads((support.ROOT / "uv.lock").read_text())
    expected = next(
        p["source"]["git"].rsplit("#", 1)[1]
        for p in lock["package"]
        if p["name"] == "quant-platform-kit"
    )
    installed = json.loads(
        importlib.metadata.distribution("quant-platform-kit").read_text(
            "direct_url.json"
        )
    )
    assert installed["vcs_info"]["commit_id"] == expected
    path = support.ROOT / "tests/vendor/backport_qrs/upstream.json"
    manifest = json.loads(path.read_text())
    assert manifest["commit"] == support.QRS_SHA
    for name, source in manifest["files"].items():
        assert (
            hashlib.sha256((path.parent / name).read_bytes()).hexdigest()
            == source["sha256"]
        )
    (tmp_path / "finalizer-source.json").write_text(
        json.dumps(
            {
                "qpk_commit": expected,
                "module": runtime_reports.__file__,
                "module_sha256": hashlib.sha256(
                    __import__("pathlib").Path(runtime_reports.__file__).read_bytes()
                ).hexdigest(),
                "qrs_commit": support.QRS_SHA,
            }
        )
    )


def test_finalized_report_heartbeat_keeps_business_run(
    finalized_cycle, bound, qrs, tmp_path
):
    env, _, stores = bound
    report, _, _, _ = finalized_cycle()
    for _ in range(2):
        unified.heartbeat_observation(
            now=datetime(2026, 10, 17, 0, 20, tzinfo=timezone.utc),
            report=report,
            recovery_at=report["started_at"],
        )
    payload = next(
        v
        for k, (_, v) in stores[env["DIGEST_CANDIDATES_GCS_URI"]].backing.items()
        if not k.startswith("candidate_state/")
    )
    rows, summary, _ = receive_final(qrs, tmp_path, env, payload)
    assert len(rows) == 1 and summary["event_count"] == 2
    assert rows[0]["source_run_id"] == report["run_id"]
    assert {f["event_kind"] for f in rows[0]["notification_events"]} == {
        "routine_cycle",
        "healthy_heartbeat",
    }


def test_failed_finalization_retains_nonempty_incident(
    finalized_cycle, bound, qrs, tmp_path
):
    env, _, _ = bound
    report, raw, calls, _ = finalized_cycle("error", status="error")
    reason = raw["notification_digest"]["event"]["incident_reason"]
    assert (
        reason
        and report["summary"]["notification_digest"]["event"]["incident_reason"]
        == reason
    )
    _, payload = support.export(env, [report])
    rows, summary, _ = receive_final(qrs, tmp_path, env, payload)
    assert rows[0]["notification_events"][0]["incident_reason"] == reason
    assert rows[0]["order_count"] is None and summary["immediate_events"] == 1
    assert len([c for c in calls if c[0] == "send"]) == 1
