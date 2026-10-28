"""Offline entrypoint comparisons captured from main at 20e1f52657a6."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from application import rebalance_service as service
from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
from notifications.telegram import build_translator
from scripts import execution_report_heartbeat as heartbeat
from notifications import unified


def cycle_case(case, language, *, notification_report=None):
    calls = []
    execution = {"execution_status": "no_action"}
    orders, pending = (), ()
    action = case in {"order", "pending"}
    if action:
        execution["execution_status"] = "completed"
        orders = ({"status": "filled", "fills": [{"execution_id": "synthetic-fill"}]},)
    if case == "pending":
        pending = ({"status": "pending_reconciliation"},)
    if case == "blocked":
        execution.update(execution_status="blocked", blocked_reason="synthetic_rejection")
    plan = {"execution": {}, "portfolio": {}, "allocation": {"target_mode": "value"}}
    result = service.ExecutionCycleResult(
        plan=plan, portfolio={}, execution=execution, allocation=plan["allocation"],
        logs=(), skip_logs=(), note_logs=(), action_done=action,
        pending_orders=pending,
    )

    def record(name, value=None):
        calls.append([name, value])

    def bootstrap():
        record("bootstrap")
        return "synthetic-quote", "synthetic-trade", {}

    def resolve(**kwargs):
        record("plan", sorted(kwargs))
        return plan

    def snapshot():
        record("snapshot")
        return SimpleNamespace(as_of="2026-10-06", metadata={})

    def forbidden(*args, **kwargs):
        raise AssertionError("broker call in notification test")

    def execute(**kwargs):
        record("execute", {key: kwargs[key] for key in (
            "dry_run_only", "symbol_suffix", "limit_sell_discount", "limit_buy_premium",
            "cash_only_execution", "fractional_buy_execution", "buy_quantity_step",
        )})
        return result

    runtime = LongBridgeRebalanceRuntime(
        bootstrap=bootstrap, resolve_rebalance_plan=resolve,
        market_data_port_factory=lambda *_: SimpleNamespace(get_quote=forbidden),
        estimate_max_purchase_quantity=forbidden,
        notifications=SimpleNamespace(send_text=lambda text: record("send", text) or True),
        notify_issue=lambda *args: record("issue", list(args)),
        portfolio_port_factory=lambda *_: SimpleNamespace(get_portfolio_snapshot=snapshot),
        execution_port_factory=lambda *_: SimpleNamespace(submit_order=forbidden),
    )
    config = LongBridgeRebalanceConfig(
        limit_sell_discount=1.0, limit_buy_premium=1.0, separator="-",
        translator=build_translator(language), with_prefix=lambda text: text,
        dry_run_only=case == "preview", notify_no_trade_cycles=True,
    )
    with patch.object(service, "execute_rebalance_cycle", execute), patch.object(
        service, "build_signal_snapshot", return_value={}
    ), unified.bind_report(notification_report):
        returned = service.run_strategy(runtime=runtime, config=config)
    assert returned is result
    return {"calls": calls, "execution": copy.deepcopy(result.execution),
            "action_done": result.action_done, "pending": list(result.pending_orders)}


def heartbeat_case(case, language):
    calls = []
    now = dt.datetime(2026, 10, 16, 23, tzinfo=dt.timezone.utc)
    payload = {"platform": "longbridge", "service_name": "synthetic-service", "status": "ok"}
    if case == "rejected":
        payload.update(summary={"execution_status": "blocked"})

    def listed(*args, **kwargs):
        calls.append(["list", list(args), kwargs])
        return [] if case == "missing" else [{
            "url": "gs://synthetic-reports/archive/longbridge/core/2026-10/test.json",
            "metadata": {"updated": "2026-10-16T22:40:00Z"},
        }]

    def cat(*args, **kwargs):
        calls.append(["cat", list(args), kwargs])
        return payload

    env = {"RUNTIME_HEARTBEAT_NAME": "synthetic", "RUNTIME_HEARTBEAT_REPORT_PLATFORM": "longbridge",
           "RUNTIME_HEARTBEAT_REQUIRED_SERVICES": "synthetic-service",
           "RUNTIME_HEARTBEAT_GCS_URIS": "gs://synthetic-reports/archive",
           "RUNTIME_HEARTBEAT_SCHEDULER_AWARE": "false", "GCP_PROJECT_ID": "synthetic",
           "NOTIFY_LANG": language, "RUNTIME_TARGET_ENABLED": "false" if case == "disabled" else "true"}
    env.update({key: value for key, value in os.environ.items() if key.startswith(("LONGBRIDGE_UNIFIED_", "QSL_"))})
    with patch.dict(os.environ, env, clear=True), patch.object(heartbeat, "_list_gcs_objects", listed), patch.object(
        heartbeat, "_cat_gcs_json", cat
    ), patch.object(heartbeat, "_send_telegram", lambda text: calls.append(["send", text]) or True):
        code = heartbeat.main(now=now)
    return {"calls": calls, "exit_code": code}


@pytest.mark.parametrize("flag", [None, "", "false", "invalid"])
@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("mode", [None, "daily_digest"])
def test_flags_off_entrypoints_match_main_golden(monkeypatch, flag, language, mode):
    if mode is None:
        monkeypatch.delenv("QSL_ROUTINE_NOTIFICATION_MODE", raising=False)
    else:
        monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", mode)
    monkeypatch.delenv("QSL_DIGEST_ROUTINE_ORDERS", raising=False)
    if flag is None:
        monkeypatch.delenv("LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED", raising=False)
    else:
        monkeypatch.setenv("LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED", flag)
    golden = json.loads((Path(__file__).parent / "fixtures" / "notification_main_20e1f526.json").read_text())
    for case in ("healthy", "order", "pending", "blocked", "preview"):
        assert cycle_case(case, language) == golden[language]["cycle_legacy_digest" if mode else "cycle"][case]
    for case in ("healthy", "missing", "rejected", "disabled"):
        assert heartbeat_case(case, language) == golden[language]["heartbeat"][case]


def binding_env(target_id="paper", scope="PAPER", dry_run=True):
    target = {"platform_id": "longbridge", "strategy_profile": "synthetic-core",
              "service_name": "synthetic-service", "account_scope": scope,
              "account_selector": ["synthetic-selector"], "deployment_selector": "synthetic-slot",
              "dry_run_only": dry_run}
    return {unified.FLAG: "true", "RUNTIME_TARGET_ENABLED": "true",
            "RUNTIME_TARGET_JSON": json.dumps(target), "DIGEST_TARGET_ID": target_id,
            "DIGEST_OPAQUE_ACCOUNT_UID": "synthetic-uid", "DIGEST_MATRIX_TARGET_ID": target_id,
            "DIGEST_MATRIX_ACCOUNT_SCOPE": scope,
            "DIGEST_CANDIDATES_GCS_URI": "gs://synthetic-reports/candidates", "DIGEST_STORE_TARGET": target_id,
            "QSL_NOTIFICATION_MARKER_GCS_URI": "gs://synthetic-reports/markers"}


@pytest.mark.parametrize("value", [None, "", "invalid", "false", "0", "off", "1", "yes"])
def test_selector_off_preserves_existing_single_mode(value):
    env = {"QSL_ROUTINE_NOTIFICATION_MODE": "daily_digest"}
    if value is not None:
        env[unified.FLAG] = value
    assert not unified.enabled(env)
    assert unified.routing_env(env) is env


def test_selector_on_maps_to_existing_mode_and_keeps_orders_immediate():
    env = {unified.FLAG: "true", "QSL_ROUTINE_NOTIFICATION_MODE": "immediate"}
    mapped = unified.routing_env(env)
    assert mapped["QSL_ROUTINE_NOTIFICATION_MODE"] == "daily_digest"
    assert mapped["QSL_DIGEST_ROUTINE_ORDERS"] == "false"
    assert env["QSL_ROUTINE_NOTIFICATION_MODE"] == "immediate"


@pytest.mark.parametrize("target_id,scope,dry_run", [("paper", "PAPER", True), ("hk", "HK", False), ("sg", "SG", False)])
def test_environment_binding_never_mixes_targets(target_id, scope, dry_run):
    env = binding_env(target_id, scope, dry_run)
    identity, target = unified.target_binding(env)
    assert identity["target_id"] == target_id
    report = {"runtime_target": target, "platform": target["platform_id"],
              "strategy_profile": target["strategy_profile"], "service_name": target["service_name"],
              "dry_run": dry_run}
    assert unified.target_binding(env, report=report)[0] == identity
    report["runtime_target"] = {**target, "account_scope": "OTHER"}
    with pytest.raises(ValueError, match="source_binding_mismatch"):
        unified.target_binding(env, report=report)
    with pytest.raises(ValueError, match="not_enabled"):
        unified.target_binding({**env, "RUNTIME_TARGET_ENABLED": "false"})
    with pytest.raises(ValueError, match="matrix_binding_mismatch"):
        unified.target_binding({**env, "DIGEST_MATRIX_TARGET_ID": "other"})
    with pytest.raises(ValueError, match="identity_unknown"):
        unified.target_binding({**env, "DIGEST_OPAQUE_ACCOUNT_UID": ""})


class MemoryStore:
    """Test-only atomic storage; reconstructing it shares persisted backing."""
    def __init__(self, backing=None):
        self.backing = backing if backing is not None else {}
        self.calls = []

    def read(self, key):
        self.calls.append(["read", key])
        return copy.deepcopy(self.backing.get(key, (None, None)))

    def compare_and_set(self, key, version, value):
        self.calls.append(["cas", key])
        current, _ = self.backing.get(key, (None, None))
        if current != version:
            return False
        self.backing[key] = ((current or 0) + 1, copy.deepcopy(value))
        return True


def source_report(env, run_id="synthetic-run", stamp="2026-10-16T20:00:00Z"):
    target = json.loads(env["RUNTIME_TARGET_JSON"])
    return {"schema_version": "runtime_report.v1", "run_id": run_id, "started_at": stamp,
            "runtime_target": target, "platform": target["platform_id"], "strategy_profile": target["strategy_profile"],
            "service_name": target["service_name"], "dry_run": target["dry_run_only"], "status": "ok"}


def fake_stores(monkeypatch):
    stores = {}
    def factory(prefix):
        return stores.setdefault(prefix, MemoryStore())
    monkeypatch.setattr(unified, "GCSNotificationStore", factory)
    return stores


def test_on_entrypoint_writes_candidate_before_deferral_and_keeps_broker_calls(monkeypatch):
    env = binding_env("sg", "SG", False)
    stores = fake_stores(monkeypatch)
    with patch.dict(os.environ, env, clear=True):
        report = source_report(env)
        observed = cycle_case("healthy", "en", notification_report=report)
        assert [call[0] for call in observed["calls"]] == ["bootstrap", "snapshot", "plan", "execute"]
        candidate = next(iter(stores[env["DIGEST_CANDIDATES_GCS_URI"]].backing.values()))[1]
        row = candidate["runs"][0]
        assert (row["order_count"], row["fill_count"], row["cycle_count"], row["actually_ran"]) == (0, 0, 1, True)
        assert row["target_id"] == "sg" and row["business_day"] == "2026-10-16"
        # Another worker observing the same run writes no second run.
        cycle_case("healthy", "en", notification_report=report)
        assert next(iter(stores[env["DIGEST_CANDIDATES_GCS_URI"]].backing.values()))[1] == candidate


@pytest.mark.parametrize("target_id,scope,dry_run", [("paper", "PAPER", True), ("hk", "HK", False), ("sg", "SG", False)])
def test_candidate_rows_are_target_bound_and_never_invent_order_counts(monkeypatch, target_id, scope, dry_run):
    env = binding_env(target_id, scope, dry_run)
    store = MemoryStore()
    report = source_report(env)
    fact = unified.make_event("routine_cycle", env=env, report=report, order_count=None, fill_count=None).to_candidate()
    assert unified.persist_candidate(fact, env=env, store=store, dry_run=dry_run, preview_count=2 if dry_run else None)
    first = next(iter(store.backing.values()))[1]
    assert first["runs"][0]["order_count"] is None and first["runs"][0]["fill_count"] is None
    second_report = source_report(env, "synthetic-run-2")
    second = unified.make_event("routine_cycle", env=env, report=second_report, order_count=0, fill_count=0).to_candidate()
    unified.persist_candidate(second, env=env, store=store, dry_run=dry_run)
    rows = next(iter(store.backing.values()))[1]["runs"]
    assert [row["source_run_id"] for row in rows] == ["synthetic-run", "synthetic-run-2"]
    assert all(row["cycle_count"] == 1 and row["dry_run"] is dry_run for row in rows)
    assert rows[0]["order_count"] is None and rows[0]["fill_count"] is None
    assert rows[1]["order_count"] == 0 and rows[1]["fill_count"] == 0
    foreign = {**fact, "target_id": "different-target"}
    with pytest.raises(Exception, match="identity_unknown"):
        unified.persist_candidate(foreign, env=env, store=store)


def test_real_orders_stay_immediate_when_on_and_counts_stay_unknown(monkeypatch):
    env = binding_env("sg", "SG", False)
    env["QSL_DIGEST_ROUTINE_ORDERS"] = "true"
    stores = fake_stores(monkeypatch)
    with patch.dict(os.environ, env, clear=True):
        observed = cycle_case("order", "en", notification_report=source_report(env))
    assert [call[0] for call in observed["calls"]] == ["bootstrap", "snapshot", "plan", "execute", "send"]
    row = next(iter(stores[env["DIGEST_CANDIDATES_GCS_URI"]].backing.values()))[1]["runs"][0]
    assert row["order_count"] is None and row["fill_count"] is None


def test_candidate_failure_alerts_and_is_not_a_healthy_confirmation(monkeypatch):
    env = binding_env("sg", "SG", False)
    stores = fake_stores(monkeypatch)
    def failed(*args, **kwargs):
        raise RuntimeError("synthetic write outcome unknown")
    monkeypatch.setattr(unified, "persist_candidate", failed)
    with patch.dict(os.environ, env, clear=True):
        report = source_report(env)
        observed = cycle_case("healthy", "en", notification_report=report)
    assert "candidate_persistence_unconfirmed" in report["notification_unified_errors"]
    assert len([call for call in observed["calls"] if call[0] == "issue"]) == 1
    assert not [call for call in observed["calls"] if call[0] == "send"]
    marker = next(iter(stores[env["QSL_NOTIFICATION_MARKER_GCS_URI"]].backing.values()))[1]
    assert marker["delivery_status"] == "uncertain"  # notify_issue has no positive transport ACK


def test_real_order_notification_survives_candidate_storage_failure(monkeypatch):
    env = binding_env("sg", "SG", False)
    fake_stores(monkeypatch)
    monkeypatch.setattr(unified, "persist_candidate", lambda *a, **kw: (_ for _ in ()).throw(TimeoutError("synthetic write uncertainty")))
    with patch.dict(os.environ, env, clear=True):
        report = source_report(env)
        observed = cycle_case("order", "en", notification_report=report)
    assert len([call for call in observed["calls"] if call[0] == "send"]) == 1
    assert "candidate_persistence_unconfirmed" in report["notification_unified_errors"]


def test_immediate_markers_survive_workers_recovery_and_severity_up(monkeypatch):
    env = binding_env("sg", "SG", False)
    backing = {}
    sent = []
    event = unified.make_event("receipt_missing", env=env, report=source_report(env), incident="execution_receipt")
    def send():
        sent.append("alert")
        return True
    assert unified.send_incident(event, send, env=env, store=MemoryStore(backing))
    repeated = unified.replace(event, source_run_id="another-worker")
    assert not unified.send_incident(repeated, send, env=env, store=MemoryStore(backing))
    assert unified.send_incident(repeated, send, env=env, store=MemoryStore(backing), severity=2)
    recovered = unified.replace(event, occurred_at=event.occurred_at + dt.timedelta(minutes=2))
    assert unified.observe_incident(recovered, env=env, store=MemoryStore(backing), state="accepted", recovered=True)[0].action == "recovered"
    worse = unified.replace(recovered, occurred_at=recovered.occurred_at + dt.timedelta(minutes=1))
    assert unified.send_incident(worse, send, env=env, store=MemoryStore(backing))
    assert len(sent) == 3
    # A late report cannot establish recovery or create another event.
    assert unified.observe_incident(event, env=env, store=MemoryStore(backing), state="accepted", recovered=True)[0].action == "duplicate"
    assert not unified.send_incident(worse, send, env=env, store=MemoryStore(backing))


@pytest.mark.parametrize("outcome", [False, None, "raise"])
def test_uncertain_or_failed_send_is_never_retried(monkeypatch, outcome):
    env = binding_env("sg", "SG", False)
    backing = {}
    event = unified.make_event("auth_failure", env=env, report=source_report(env), incident="auth_failure")
    calls = []
    def send():
        calls.append(1)
        if outcome == "raise":
            raise TimeoutError("synthetic uncertain ACK")
        return outcome
    if outcome == "raise":
        with pytest.raises(TimeoutError):
            unified.send_incident(event, send, env=env, store=MemoryStore(backing))
    else:
        assert not unified.send_incident(event, send, env=env, store=MemoryStore(backing))
    assert not unified.send_incident(event, send, env=env, store=MemoryStore(backing))
    assert calls == [1]
    assert next(iter(backing.values()))[1]["delivery_status"] == "uncertain"


def test_marker_storage_failure_never_falls_back_to_untracked_send():
    env = binding_env("sg", "SG", False)
    class Unreadable(MemoryStore):
        def read(self, key):
            raise OSError("synthetic incomplete history")
    event = unified.make_event("real_error", env=env, report=source_report(env), incident="failure")
    with pytest.raises(OSError):
        unified.send_incident(event, lambda: pytest.fail("untracked send"), env=env, store=Unreadable())


def test_incident_scope_separates_account_target_strategy_and_reason():
    env = binding_env("sg", "SG", False)
    event = unified.make_event("real_error", env=env, report=source_report(env), incident="failure")
    store = MemoryStore()
    variants = [event, unified.replace(event, target_id="other-target"),
        unified.replace(event, opaque_account_uid="other-account"), unified.replace(event, strategy_profile="other-strategy"),
        unified.replace(event, incident_reason="other-reason")]
    for item in variants:
        assert unified.send_incident(item, lambda: True, env=env, store=store)
    assert len(store.backing) == 5


def test_partial_candidate_conflict_keeps_earlier_alert_and_unknown_counts():
    env = binding_env("sg", "SG", False)
    event = unified.make_event("blocked_run", env=env, report=source_report(env), incident="cycle_attention", order_count=1, fill_count=0)
    store = MemoryStore()
    unified.persist_candidate(event.to_candidate(), env=env, store=store)
    changed = unified.replace(event, order_count=0, fill_count=0)
    unified.persist_candidate(changed.to_candidate(), env=env, store=store)
    payload = next(iter(store.backing.values()))[1]
    assert payload["notification_conflicts"] and len(payload["notification_events"]) == 2
    assert payload["runs"][0]["status"] == "alert" and payload["runs"][0]["order_count"] is None


def test_snapshot_matches_the_pinned_qpk_contract():
    import hashlib
    from notifications.digest_snapshot import UNIFIED_CONTRACT_SHA256, UNIFIED_CONTRACT_QPK_SHA
    path = Path(__file__).parents[1] / "notifications" / "digest_snapshot" / "unified_contract.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == UNIFIED_CONTRACT_SHA256
    assert UNIFIED_CONTRACT_QPK_SHA == "07e24c0c804a9b47316b6ec3c75bb63820d8f421"


@pytest.mark.parametrize("language", ["zh", "en"])
def test_heartbeat_entrypoint_dedupes_across_runs_and_recovers_only_from_new_receipt(monkeypatch, language):
    env = binding_env("sg", "SG", False)
    env.update(RUNTIME_HEARTBEAT_REPORT_PLATFORM="longbridge", RUNTIME_HEARTBEAT_SCHEDULER_AWARE="false",
               RUNTIME_HEARTBEAT_REQUIRED_SERVICES="synthetic-service", NOTIFY_LANG=language, GITHUB_RUN_ID="monitor-1")
    fake_stores(monkeypatch)
    sent, source = [], {"payload": None}
    now = dt.datetime(2026, 10, 17, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(heartbeat, "load_runtime_targets", lambda env: [])
    monkeypatch.setattr(heartbeat, "_resolve_required_services", lambda **kw: (["synthetic-service"], None, False))
    monkeypatch.setattr(heartbeat, "_report_globs", lambda *args: ["gs://synthetic-reports/archive/*"])
    monkeypatch.setattr(heartbeat, "_list_gcs_objects", lambda *args, **kw: [] if source["payload"] is None else [{
        "url": "gs://synthetic-reports/archive/test.json", "metadata": {"updated": source["updated"]}}])
    monkeypatch.setattr(heartbeat, "_cat_gcs_json", lambda *args, **kw: source["payload"])
    monkeypatch.setattr(heartbeat, "_send_telegram", lambda text: sent.append(text) or True)
    with patch.dict(os.environ, env, clear=True):
        assert heartbeat.main(now=now) == 1
        os.environ["GITHUB_RUN_ID"] = "monitor-2"
        assert heartbeat.main(now=now + dt.timedelta(minutes=5)) == 1
        assert len(sent) == 1
        # A rewritten old object may pass the unchanged heartbeat lookback;
        # its upload timestamp cannot establish incident recovery.
        source.update(payload=source_report(env, "old-runtime", "2026-10-16T20:00:00Z"), updated="2026-10-17T00:06:00Z")
        os.environ["GITHUB_RUN_ID"] = "monitor-old-object"
        assert heartbeat.main(now=now + dt.timedelta(minutes=7)) == 0
        source["payload"] = None
        os.environ["GITHUB_RUN_ID"] = "monitor-still-unrecovered"
        assert heartbeat.main(now=now + dt.timedelta(minutes=8)) == 1
        assert len(sent) == 1
        source.update(payload=source_report(env, "new-runtime", "2026-10-17T00:09:00Z"), updated="2026-10-17T00:09:00Z")
        os.environ["GITHUB_RUN_ID"] = "monitor-3"
        assert heartbeat.main(now=now + dt.timedelta(minutes=10)) == 0
        source["payload"] = None
        os.environ["GITHUB_RUN_ID"] = "monitor-4"
        assert heartbeat.main(now=now + dt.timedelta(minutes=15)) == 1
    assert len(sent) == 2
    assert all("gs://" not in text and "synthetic-service" not in text for text in sent)
    if language == "en":
        assert not any("执行" in text for text in sent)
    else:
        assert all("Execution" not in text and "receipt_missing" not in text for text in sent)


def test_heartbeat_disabled_on_still_skips_all_io(monkeypatch):
    env = binding_env()
    env["RUNTIME_TARGET_ENABLED"] = "false"
    monkeypatch.setattr(heartbeat, "_list_gcs_objects", lambda *a, **kw: pytest.fail("disabled GCS read"))
    monkeypatch.setattr(unified, "GCSNotificationStore", lambda *a: pytest.fail("disabled marker/candidate access"))
    with patch.dict(os.environ, env, clear=True):
        assert heartbeat.main() == 0


@pytest.mark.parametrize("paper", [False, True])
def test_archive_and_runtime_share_run_identity_without_double_counting(paper):
    env = binding_env("paper" if paper else "sg", "PAPER" if paper else "SG", paper)
    env["EXECUTION_REPORT_GCS_URI"] = "gs://synthetic-reports/archive"
    store = MemoryStore()
    report = source_report(env)
    event = unified.make_event("routine_cycle", env=env, report=report,
        order_count=None if paper else 0, fill_count=None if paper else 0)
    facts = {"schema_version": "qsl.notification_cycle.v1", "dry_run": paper,
             "order_count": event.order_count, "fill_count": event.fill_count,
             "event": event.to_candidate(), "preview_count": 2 if paper else None}
    report["summary"] = {"notification_digest": facts}
    unified.persist_candidate(event.to_candidate(), env=env, store=store, dry_run=paper, preview_count=facts["preview_count"])
    before = copy.deepcopy(store.backing)
    def read(args):
        if args[0] == "ls":
            return [{"url": "gs://synthetic-reports/archive/longbridge/core/2026-10/test.json",
                     "metadata": {"updated": "2026-10-16T20:01:00Z"}}]
        return report
    result = unified.emit_archived(env, now=dt.datetime(2026, 10, 17, 0, tzinfo=dt.timezone.utc), read=read, store=store)
    assert result["runs"] == 1 and store.backing == before
    row = next(iter(store.backing.values()))[1]["runs"][0]
    assert row["cycle_count"] == 1 and row["preview_count"] == (2 if paper else None)


def test_no_run_window_is_not_fabricated_and_proved_idle_has_no_message_intent(monkeypatch):
    env = binding_env("sg", "SG", False)
    env["EXECUTION_REPORT_GCS_URI"] = "gs://synthetic-reports/archive"
    store = MemoryStore()
    now = dt.datetime(2026, 10, 17, 0, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="candidate_source_absent"):
        unified.emit_archived(env, now=now, read=lambda args: [], store=store)
    assert not store.backing
    env["DIGEST_NOTIFICATION_SCHEDULE_JSON"] = json.dumps({"scheduler": {"main_time": "0 20 * * 1-5", "timezone": "UTC"}})
    import quant_platform_kit.common.runtime_heartbeat_policy as policy
    monkeypatch.setattr(policy, "filter_due_targets", lambda *args, **kw: ([], True))
    unified.emit_archived(env, now=now, read=lambda args: [], store=store)
    payload = next(iter(store.backing.values()))[1]
    assert payload["runs"][0]["actually_ran"] is False and payload["runs"][0]["scheduled_idle"] is True
    assert "order_count" not in payload["runs"][0] and not payload["notification_events"]
    monkeypatch.setattr(policy, "filter_due_targets", lambda *args, **kw: ([{}], True))
    with pytest.raises(ValueError, match="candidate_due_source_absent"):
        unified.emit_archived(env, now=now, read=lambda args: [], store=MemoryStore())


def test_workflow_keeps_schedule_target_matrix_and_canary_unchanged():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/execution-report-heartbeat.yml").read_text()
    assert '- cron: "20 22 * * *"' in workflow
    assert "matrix: ${{ fromJSON(needs.resolve-matrix.outputs.matrix) }}" in workflow
    assert "LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED: ${{ vars.LONGBRIDGE_UNIFIED_NOTIFICATION_ENABLED || 'false' }}" in workflow
    assert "DIGEST_MATRIX_TARGET_ID: ${{ matrix.target.id }}" in workflow
    assert "DIGEST_MATRIX_ACCOUNT_SCOPE: ${{ matrix.target.label }}" in workflow
    assert "INSPECT_SG_PROBE_PERMISSIONS != 'true'" in workflow
    assert "QSL_NOTIFICATION_MARKER_GCS_URI:" in workflow


def test_new_cycle_reason_alerts_and_healthy_run_recovers_all_observed_reasons():
    env = binding_env("sg", "SG", False)
    store, calls = MemoryStore(), []
    event = unified.make_event("blocked_run", env=env, report=source_report(env), incident="cycle_attention:blocked_run:reason_a")
    second = unified.replace(event, incident_reason="cycle_attention:blocked_run:reason_b")
    for item in (event, second):
        assert unified.send_incident(item, lambda: calls.append(1) or True, env=env, store=store)
    unified.recover_cycle_incidents(unified.replace(event, occurred_at=event.occurred_at + dt.timedelta(minutes=1)), env=env, store=store)
    assert unified.send_incident(unified.replace(event, occurred_at=event.occurred_at + dt.timedelta(minutes=2)),
                                 lambda: calls.append(1) or True, env=env, store=store)
    assert len(calls) == 3


def test_gcs_adapter_uses_atomic_generations_and_does_not_retry_uncertain_writes(monkeypatch):
    from google.api_core.exceptions import NotFound, PreconditionFailed, Forbidden
    from google.cloud import storage
    from notifications.unified_store import GCSNotificationStore
    objects, operations = {}, []
    class Blob:
        def __init__(self, uri):
            self.uri, self.generation = uri, None
        def reload(self, **kwargs):
            operations.append(["reload", kwargs])
            if self.uri not in objects:
                raise NotFound("synthetic absent")
            self.generation = objects[self.uri][0]
        def download_as_bytes(self, **kwargs):
            operations.append(["download", kwargs])
            assert kwargs["if_generation_match"] == objects[self.uri][0]
            return objects[self.uri][1].encode()
        def upload_from_string(self, body, **kwargs):
            operations.append(["upload", kwargs])
            generation = objects.get(self.uri, (0, ""))[0]
            if kwargs["if_generation_match"] != generation:
                raise PreconditionFailed("synthetic CAS race")
            objects[self.uri] = (generation + 1, body)
    monkeypatch.setattr(storage.Blob, "from_string", lambda uri, **kwargs: Blob(uri))
    store = GCSNotificationStore("gs://synthetic-reports/markers", client=object())
    assert store.read("synthetic") == (None, None)
    assert store.compare_and_set("synthetic", None, {"active": True})
    version, value = store.read("synthetic")
    assert version == 1 and value == {"active": True}
    assert not store.compare_and_set("synthetic", None, {"active": False})
    assert all(operation[1]["retry"] is None for operation in operations)
    assert next(operation for operation in operations if operation[0] == "upload")[1]["if_generation_match"] == 0
    failures = []
    class Uncertain(Blob):
        def upload_from_string(self, *args, **kwargs):
            failures.append(1)
            raise TimeoutError("synthetic unknown write result")
    monkeypatch.setattr(storage.Blob, "from_string", lambda uri, **kwargs: Uncertain(uri))
    with pytest.raises(TimeoutError):
        store.compare_and_set("uncertain", None, {})
    assert failures == [1]
    class Incomplete(Blob):
        def reload(self, **kwargs):
            raise Forbidden("synthetic unreadable history")
    monkeypatch.setattr(storage.Blob, "from_string", lambda uri, **kwargs: Incomplete(uri))
    with pytest.raises(Forbidden):
        store.read("unreadable")
    class Disappeared(Blob):
        def download_as_bytes(self, **kwargs):
            raise NotFound("synthetic marker disappeared after metadata read")
    monkeypatch.setattr(storage.Blob, "from_string", lambda uri, **kwargs: Disappeared(uri))
    with pytest.raises(NotFound):
        store.read("synthetic")


def test_central_payload_exports_per_run_events_and_keeps_ledger_private():
    env = binding_env("sg", "SG", False)
    candidate, ledger = MemoryStore(), MemoryStore()
    fact = unified.make_event("routine_cycle", env=env, report=source_report(env), order_count=0, fill_count=0).to_candidate()
    unified.persist_candidate(fact, env=env, store=candidate, state_store=ledger)
    payload = next(iter(candidate.backing.values()))[1]
    assert set(payload) == {"schema_version", "producer_status", "runs"}
    row = payload["runs"][0]
    assert row["execution_status"] == "ok_no_order"
    assert row["evidence_persistence_status"] == "persisted" and row["evidence_delivery_status"] == "pending_central"
    assert not ({"source_run_ids", "dry_run", "preview_count", "window_id"} & set(row))
    assert row["source_run_id"] == fact["source_run_id"]
    assert row["notification_events"] == [fact]
    assert next(iter(ledger.backing.values()))[1]["notification_events"]


def test_candidate_export_cannot_overwrite_a_newer_ledger_or_legacy_history():
    env = binding_env("sg", "SG", False)
    candidate, ledger = MemoryStore(), MemoryStore()
    fact = unified.make_event("routine_cycle", env=env, report=source_report(env), order_count=0, fill_count=0).to_candidate()
    key = "longbridge/2026-10-16/sg"
    candidate.compare_and_set(key, None, {"schema_version": "qsl.digest_candidates.v1", "runs": []})
    with pytest.raises(ValueError, match="history_unknown"):
        unified.persist_candidate(fact, env=env, store=candidate, state_store=ledger)
    assert not ledger.backing


@pytest.mark.parametrize("language", ["zh", "en"])
def test_risk_attention_persists_transitions_without_raw_strategy_keys(monkeypatch, language):
    from quant_platform_kit.risk.attention import AttentionAxes, evaluate_attention
    env = binding_env("sg", "SG", False)
    env["NOTIFY_LANG"] = language
    fake_stores(monkeypatch)
    sent = []
    action = evaluate_attention(AttentionAxes(new_risk_prohibited=True))
    halt = evaluate_attention(AttentionAxes(hard_park=True))
    healthy = evaluate_attention(AttentionAxes(new_risk_prohibited=False, operational_uncertain=False))
    def send(**kwargs):
        sent.append(kwargs["text"])
        return True
    with patch.dict(os.environ, env, clear=True):
        for index, (decision, recover, expected) in enumerate([
            (action, False, 1), (action, False, 0), (halt, False, 1),
            (healthy, True, 0), (action, False, 1),
        ]):
            report = source_report(env, f"risk-run-{index}", f"2026-10-16T20:0{index}:00Z")
            with unified.bind_report(report):
                assert unified.publish_attention(decision, recover=recover, telegram_sender=send)["sent"] == expected
    assert len(sent) == 3 and all("synthetic-core" not in text and "synthetic-uid" not in text for text in sent)
    assert all(("未命名策略" in text) if language == "zh" else ("Unnamed strategy" in text) for text in sent)


def test_runtime_incident_recovery_requires_successfully_persisted_terminal_proof(monkeypatch):
    env = binding_env("sg", "SG", False)
    fake_stores(monkeypatch)
    sent = []
    with patch.dict(os.environ, env, clear=True):
        report = source_report(env)
        def send():
            sent.append(1)
            return True
        assert unified.dispatch_immediate(send, kind="real_error", incident="runtime_failure:strategy_cycle:unknown", report=report)
        assert not unified.dispatch_immediate(send, kind="real_error", incident="runtime_failure:strategy_cycle:unknown", report=report)
        failed = source_report(env, "failed", "2026-10-16T20:01:00Z")
        failed["status"] = "error"
        unified.recover_runtime(report=failed)
        assert not unified.dispatch_immediate(send, kind="real_error", incident="runtime_failure:strategy_cycle:unknown", report=failed)
        good = source_report(env, "healthy", "2026-10-16T20:02:00Z")
        good["summary"] = {"execution_status": "no_action", "orders_pending_count": 0}
        unified.recover_runtime(report=good)
        worse = source_report(env, "worse", "2026-10-16T20:03:00Z")
        assert unified.dispatch_immediate(send, kind="real_error", incident="runtime_failure:strategy_cycle:unknown", report=worse)
    assert len(sent) == 2


def test_runtime_issue_callback_dedupes_categories_and_recovers_after_a_clean_run(monkeypatch):
    from application.runtime_notification_adapters import build_runtime_notification_adapters
    env = binding_env("sg", "SG", False)
    fake_stores(monkeypatch)
    sent = []
    adapters = build_runtime_notification_adapters(with_prefix=lambda text: text,
        send_message=lambda text: sent.append(text) or True, translator=build_translator("en"),
        fetch_order_status=lambda *a, **kw: pytest.fail("broker request"),
        order_poll_interval_sec=0, order_poll_max_attempts=0, sleeper=lambda seconds: None,
        log_message=lambda text: None)
    with patch.dict(os.environ, env, clear=True):
        for run in ("run-a", "run-b"):
            with unified.bind_report(source_report(env, run)):
                adapters.notify_issue("Synthetic failure", "synthetic detail")
        assert len(sent) == 1
        with unified.bind_report(source_report(env, "run-b")):
            adapters.notify_issue("Another synthetic failure", "synthetic detail")
        assert len(sent) == 2
        report = source_report(env, "clean-run", "2026-10-16T20:01:00Z")
        report["summary"] = {"execution_status": "no_action"}
        unified.recover_runtime(report=report)
        with unified.bind_report(source_report(env, "run-c", "2026-10-16T20:02:00Z")):
            adapters.notify_issue("Synthetic failure", "synthetic detail")
    assert len(sent) == 3 and len(adapters.delivery_events) == 3


def test_boolean_ack_is_uncertain_without_receipt_and_not_resent():
    env = binding_env("sg", "SG", False)
    store, calls = MemoryStore(), []
    event = unified.make_event("real_error", env=env, report=source_report(env), incident="failure")
    assert unified.send_incident(event, lambda: calls.append(1) or True, env=env, store=store)
    marker = next(iter(store.backing.values()))[1]
    assert marker["delivery_status"] == "uncertain"
    assert marker["receipt_id"] is None
    assert not unified.send_incident(event, lambda: calls.append(1) or True, env=env, store=store)
    assert calls == [1]


def test_only_sender_supplied_receipt_marks_delivered():
    env = binding_env("sg", "SG", False)
    store = MemoryStore()
    event = unified.make_event("real_error", env=env, report=source_report(env), incident="failure")
    receipt = "synthetic-send-history-receipt-1"
    assert unified.send_incident(event, lambda: {"receipt_id": receipt}, env=env, store=store)
    marker = next(iter(store.backing.values()))[1]
    assert marker["delivery_status"] == "delivered"
    assert marker["receipt_id"] == receipt


def test_export_keeps_one_row_and_inline_events_for_each_business_run():
    env = binding_env("sg", "SG", False)
    candidate, ledger = MemoryStore(), MemoryStore()
    for run_id, count in (("synthetic-a", 0), ("synthetic-b", 2), ("synthetic-b", 2)):
        event = unified.make_event("routine_cycle", env=env, report=source_report(env, run_id),
                                   order_count=count, fill_count=count)
        unified.persist_candidate(event.to_candidate(), env=env, store=candidate, state_store=ledger)
    payload = next(iter(candidate.backing.values()))[1]
    assert len(payload["runs"]) == 2
    rows = {row["source_run_id"]: row for row in payload["runs"]}
    for run_id, count in (("synthetic-a", 0), ("synthetic-b", 2)):
        row = rows[run_id]
        assert row["order_count"] == count
        assert row["cycle_count"] == 1
        assert len(row["notification_events"]) == 1
        assert row["notification_events"][0]["source_run_id"] == run_id


def test_conflicting_run_counts_do_not_taint_another_run():
    env = binding_env("sg", "SG", False)
    candidate, ledger = MemoryStore(), MemoryStore()
    for run_id, count in (("synthetic-a", 1), ("synthetic-b", 0), ("synthetic-a", 2), ("synthetic-a", 1)):
        event = unified.make_event("routine_cycle", env=env, report=source_report(env, run_id),
                                   order_count=count, fill_count=0)
        unified.persist_candidate(event.to_candidate(), env=env, store=candidate, state_store=ledger)
    rows = {row["source_run_id"]: row for row in next(iter(candidate.backing.values()))[1]["runs"]}
    assert rows["synthetic-a"]["order_count"] is None
    assert rows["synthetic-a"]["field_status"]["order_count"] == "unknown"
    assert rows["synthetic-a"]["fill_count"] == 0
    assert rows["synthetic-b"]["order_count"] == rows["synthetic-b"]["fill_count"] == 0
    assert rows["synthetic-b"]["status"] == "ok"


@pytest.mark.parametrize("receipt_kind", ["transport_ack", "transition", "event"])
def test_local_identity_is_not_a_delivery_receipt(receipt_kind):
    env = binding_env("sg", "SG", False)
    store = MemoryStore()
    event = unified.make_event("real_error", env=env, report=source_report(env), incident="failure")
    def send():
        marker = next(iter(store.backing.values()))[1]
        receipt = {"transport_ack": f"transport-ack:{marker['transition_key']}",
                   "transition": marker["transition_key"], "event": event.to_candidate()["dedupe_key"]}[receipt_kind]
        return {"receipt_id": receipt}
    assert not unified.send_incident(event, send, env=env, store=store)
    marker = next(iter(store.backing.values()))[1]
    assert marker["receipt_id"] is None and marker["delivery_status"] == "uncertain"


def test_archived_skipped_report_is_a_not_run_with_unknown_counts():
    env = binding_env('paper', 'PAPER', False)
    env['EXECUTION_REPORT_GCS_URI'] = 'gs://synthetic-reports/archive'
    report = source_report(env)
    report.update(status='skipped', diagnostics={'skip_reason': 'market_closed'})
    store = MemoryStore()
    def read(args):
        if args[0] == 'ls':
            return [{'url': 'gs://synthetic-reports/archive/longbridge/core/2026-10/test.json',
                     'metadata': {'updated': '2026-10-16T20:01:00Z'}}]
        return report
    unified.emit_archived(env, now=dt.datetime(2026, 10, 17, 0, tzinfo=dt.timezone.utc), read=read, store=store)
    row = next(iter(store.backing.values()))[1]['runs'][0]
    assert (row['actually_ran'], row['execution_status'], row['cycle_count']) == (False, 'not_run', 0)
    assert row['order_count'] is None and row['fill_count'] is None
    assert row['field_status'] == {'order_count': 'unknown', 'fill_count': 'unknown', 'cycle_count': 'known'}


@pytest.mark.parametrize('reason', ['market_closed', 'fractional_share_execution_unsupported'])
def test_skipped_archive_is_idempotent_and_preserves_run_conflicts(reason):
    env = binding_env('paper', 'PAPER', False)
    report = source_report(env)
    report.update(status='skipped', diagnostics={'skip_reason': reason})
    store = MemoryStore()
    assert unified.persist_archived_report(report, env=env, store=store)
    before = copy.deepcopy(store.backing)
    assert unified.persist_archived_report(report, env=env, store=store)
    assert store.backing == before
    report['status'] = 'ok'
    with pytest.raises(ValueError, match='candidate_non_execution_conflict'):
        unified.persist_archived_report(report, env=env, store=store)


def test_skipped_archive_rejects_unproven_or_contradictory_skip():
    env = binding_env('paper', 'PAPER', False)
    report = source_report(env)
    report.update(status='skipped', diagnostics={'skip_reason': 'unknown'})
    with pytest.raises(ValueError, match='candidate_non_execution_conflict'):
        unified.persist_archived_report(report, env=env, store=MemoryStore())
    report.update(diagnostics={'skip_reason': 'market_closed'}, summary={'notification_digest': {'order_count': 1}})
    with pytest.raises(ValueError, match='candidate_non_execution_conflict'):
        unified.persist_archived_report(report, env=env, store=MemoryStore())


def test_archived_dry_run_preserves_preview_and_live_does_not():
    for dry_run in (True, False):
        env = binding_env('paper', 'PAPER', dry_run)
        report = source_report(env)
        report['summary'] = {'notification_digest': {'schema_version': 'qsl.notification_cycle.v1',
                             'dry_run': dry_run, 'preview_count': 3}}
        store = MemoryStore()
        unified.persist_archived_report(report, env=env, store=store)
        state = next(value for key, (_, value) in store.backing.items() if key.startswith('candidate_state/'))
        source = state['notification_source_runs']['synthetic-run']
        assert source['dry_run'] is dry_run
        assert source['preview_count'] == (3 if dry_run else None)
        assert state['runs'][0]['actually_ran'] is True


def test_market_closed_not_run_is_not_alert():
    env = binding_env('paper', 'PAPER', False)
    report = source_report(env)
    report.update(status='skipped', diagnostics={'skip_reason': 'market_closed'})
    store = MemoryStore()
    unified.persist_archived_report(report, env=env, store=store)
    row = next(iter(store.backing.values()))[1]['runs'][0]
    assert row['status'] == 'ok' and row['reason_code'] == 'market_closed'
