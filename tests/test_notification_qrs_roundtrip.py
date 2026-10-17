"""Real LongBridge exports through a pinned, offline, test-only QRS receiver."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from notifications import unified
from notifications.digest_snapshot.digest_candidates import cycle_evidence
from test_notification_consolidation import MemoryStore, binding_env, fake_stores, source_report


VENDOR = Path(__file__).parent / "vendor" / "qrs_notification"
QRS_SHA = "fda2333316593fa6a1585695021ab33ed76a9bcc"


@pytest.fixture
def qrs(monkeypatch):
    names = ("merge_digest_candidates", "notification_candidate_adapter", "daily_digest_notify",
             "display_names", "notification_mode", "notification_snapshot", "unified_daily_digest")
    previous = {name: module for name, module in sys.modules.items()
                if any(name == prefix or name.startswith(prefix + ".") for prefix in names)}
    for name in previous:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(VENDOR))
    monkeypatch.setenv("QRS_UNIFIED_NOTIFICATION_ENABLED", "true")
    try:
        yield SimpleNamespace(
            merge=importlib.import_module("merge_digest_candidates"),
            intake=importlib.import_module("notification_candidate_adapter"),
            digest=importlib.import_module("daily_digest_notify"),
            renderer=importlib.import_module("unified_daily_digest"),
        )
    finally:
        for name in list(sys.modules):
            if any(name == prefix or name.startswith(prefix + ".") for prefix in names):
                del sys.modules[name]
        sys.modules.update(previous)


def archived_report(env, run_id, orders=0):
    """Synthetic broker response facts; no broker, transport or runtime call."""
    report = source_report(env, run_id)
    responses = tuple({"status": "filled", "fills": [{"execution_id": f"synthetic-fill-{index}"}]}
                      for index in range(orders))
    report["summary"] = {"notification_digest": cycle_evidence(
        {"execution_status": "completed" if orders else "no_action", "broker_submission_done": bool(orders)},
        responses, dry_run=False)}
    return report


def export_reports(env, reports, store=None):
    store = store or MemoryStore()
    by_uri = {f"gs://synthetic-reports/archive/longbridge/core/2026-10/{index}.json": report
              for index, report in enumerate(reports)}
    def read(args):
        if args[0] == "ls":
            return [{"url": uri, "metadata": {"updated": "2026-10-16T20:01:00Z"}} for uri in by_uri]
        return by_uri[args[1]]
    result = unified.emit_archived({**env, "EXECUTION_REPORT_GCS_URI": "gs://synthetic-reports/archive"},
        now=unified._stamp("2026-10-17T00:00:00Z"), read=read, store=store)
    assert result["uploaded"]
    return store, candidate_from(store)


def candidate_from(store):
    return next(value for key, (_, value) in store.backing.items() if not key.startswith("candidate_state/"))


def receive(qrs, tmp_path, env, payload, *, duplicate=False):
    directory = tmp_path / "candidates"
    directory.mkdir()
    (directory / "producer.json").write_text(json.dumps(payload))
    if duplicate:
        (directory / "duplicate-observation.json").write_text(json.dumps(payload))
    merged, stats = qrs.merge.merge(None, directory)
    expected = [unified.target_binding(env)[0]]
    qrs.intake.validate_expected(expected)
    raw = qrs.merge._rows(merged)
    for row in raw:
        qrs.intake.validate_run_row(row)
    current, stale = qrs.intake.partition(raw, "2026-10-16")
    assert not stale
    rows, summary = qrs.intake.aggregate(current, expected)
    assert summary["source_inventory"]["longbridge"] == {
        "expected_targets": 1, "covered_targets": 1, "runs": len(rows)}
    assert not summary["run_count_unknown"]
    assert not summary["counts_scope_unknown"]
    return rows, summary, stats


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("run_ids", [("synthetic-a",), ("synthetic-a", "synthetic-b")])
def test_healthy_archived_runs_roundtrip_are_known_zero(qrs, tmp_path, language, run_ids):
    env = binding_env("sg", "SG", False)
    _, payload = export_reports(env, [archived_report(env, run_id) for run_id in run_ids])
    rows, summary, _ = receive(qrs, tmp_path, env, payload)
    assert len(rows) == len(run_ids) and summary["event_count"] == len(run_ids)
    assert summary["immediate_events"] == 0 and not summary["counts_unknown"]
    assert all(row["order_count"] == row["fill_count"] == 0 and row["cycle_count"] == 1 for row in rows)
    entries = qrs.digest.filter_runs_for_digest(rows)
    assert all(entry.order_count_status == entry.fill_count_status == "known" for entry in entries)
    text = qrs.renderer.render(qrs.digest.DailyDigestInput(
        business_day="2026-10-16", locale=language, runs=entries, notification_summary=summary,
        evidence_coverage=qrs.digest.EvidenceCoverage(candidates_configured=True, candidates_loaded=True,
                                                    candidates_count=len(rows))))
    # Healthy zero-order runs render known zero, rather than unknown statistics.
    assert ("订单：0" if language == "zh" else "Orders: 0") in text
    assert ("未知" if language == "zh" else "unknown") not in text
    zero = (("本轮 0 单" if len(run_ids) == 1 else "窗口内各轮均为 0 单") if language == "zh" else
            ("0 orders this run" if len(run_ids) == 1 else "0 orders in every observed run"))
    assert zero in text


def test_zero_and_real_order_runs_roundtrip_with_duplicate_observations(qrs, tmp_path):
    env = binding_env("sg", "SG", False)
    reports = [archived_report(env, "synthetic-zero"), archived_report(env, "synthetic-orders", orders=2)]
    store, _ = export_reports(env, reports)
    # The immediate individual order event is evidence, not another run counter.
    event = unified.make_event("order_submitted", env=env, report=reports[1], order_count=1,
        order_execution_digest=unified._hash("synthetic-order"))
    unified.persist_candidate(event.to_candidate(), env=env, actually_ran=False, store=store)
    store, payload = export_reports(env, reports, store)
    rows, summary, stats = receive(qrs, tmp_path, env, payload, duplicate=True)
    assert stats["files"] == 2 and stats["file_rows"] == 4
    assert len(rows) == 2 and sum(row["order_count"] for row in rows) == 2
    assert sum(row["fill_count"] for row in rows) == 2
    assert summary["event_count"] == 3 and summary["immediate_events"] == 1
    assert not summary["counts_unknown"] and summary["count_conflicts"] == 0
    for row in rows:
        assert row["cycle_count"] == 1
        assert all(fact["source_run_id"] == row["source_run_id"] for fact in row["notification_events"])


def test_next_window_heartbeat_attaches_to_receipt_business_run(qrs, tmp_path, monkeypatch):
    env = binding_env("sg", "SG", False)
    env["GITHUB_RUN_ID"] = "synthetic-monitor-job"
    stores = fake_stores(monkeypatch)
    report = archived_report(env, "synthetic-business-run")
    with patch.dict(os.environ, env, clear=True):
        for _ in range(2):
            unified.heartbeat_observation(now=unified._stamp("2026-10-17T00:20:00Z"), report=report,
                recovery_at=report["started_at"])
    payload = candidate_from(stores[env["DIGEST_CANDIDATES_GCS_URI"]])
    rows, summary, _ = receive(qrs, tmp_path, env, payload)
    assert len(rows) == 1 and summary["event_count"] == 2 and not summary["counts_unknown"]
    assert rows[0]["source_run_id"] == "synthetic-business-run"
    assert rows[0]["business_day"] == "2026-10-16"
    assert {fact["event_kind"] for fact in rows[0]["notification_events"]} == {"routine_cycle", "healthy_heartbeat"}
    assert all(fact["source_run_id"] != env["GITHUB_RUN_ID"] for fact in rows[0]["notification_events"])


def test_missing_receipt_monitor_never_exports_a_synthetic_business_run(monkeypatch):
    env = binding_env("sg", "SG", False)
    stores = fake_stores(monkeypatch)
    calls = []
    with patch.dict(os.environ, env, clear=True):
        assert unified.dispatch_immediate(lambda: calls.append(1) or True, kind="receipt_missing",
            incident="execution_receipt", occurred_at="2026-10-17T00:20:00Z", run_id="synthetic-monitor")
    assert calls == [1]
    assert env["DIGEST_CANDIDATES_GCS_URI"] not in stores
    marker = next(iter(stores[env["QSL_NOTIFICATION_MARKER_GCS_URI"]].backing.values()))[1]
    assert marker["delivery_status"] == "uncertain" and marker["receipt_id"] is None


def test_qrs_test_only_sources_match_recorded_hashes():
    manifest = json.loads((VENDOR / "upstream.json").read_text())
    assert manifest["commit"] == QRS_SHA and manifest["test_only"] is True
    for name, source in manifest["files"].items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == source["sha256"]


@pytest.mark.parametrize("archive_first", [False, True])
def test_archive_counts_resolve_runtime_unknown_for_only_the_same_run(qrs, tmp_path, archive_first):
    env = binding_env("sg", "SG", False)
    report = archived_report(env, "synthetic-orders", orders=2)
    store = MemoryStore()
    event = unified.make_event("routine_cycle", env=env, report=report)
    if not archive_first:
        unified.persist_candidate(event.to_candidate(), env=env, store=store)
    store, _ = export_reports(env, [report, archived_report(env, "synthetic-zero")], store)
    if archive_first:
        unified.persist_candidate(event.to_candidate(), env=env, store=store)
    rows, summary, _ = receive(qrs, tmp_path, env, candidate_from(store))
    counts = {row["source_run_id"]: row["order_count"] for row in rows}
    assert counts == {"synthetic-orders": 2, "synthetic-zero": 0}
    assert not summary["counts_unknown"] and summary["event_count"] == 2


def test_heartbeat_without_business_receipt_stays_unknown_and_off_is_unchanged():
    assert unified.heartbeat_observation(now=None, env={}) is None
    with pytest.raises(ValueError, match="receipt_business_run_unknown"):
        unified.heartbeat_observation(now=unified._stamp("2026-10-17T00:20:00Z"), env=binding_env("sg", "SG", False))
