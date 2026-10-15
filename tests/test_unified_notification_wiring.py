from types import SimpleNamespace

from application.rebalance_service import _defer_cycle_notification


def test_only_healthy_confirmed_cycles_defer_and_pending_remains_live(monkeypatch):
    monkeypatch.setenv("QSL_ROUTINE_NOTIFICATION_MODE", "daily_digest")
    result = SimpleNamespace(allocation={}, execution={"execution_status": "completed"},
        action_done=True, pending_orders=(), submitted_orders=({"status": "filled"},))
    config = SimpleNamespace(dry_run_only=False)
    assert _defer_cycle_notification(result, config=config)
    result.pending_orders = ({"status": "pending_reconciliation"},)
    assert not _defer_cycle_notification(result, config=config)
    result.pending_orders = ()
    result.execution["report_persistence_error"] = "synthetic_failure"
    assert not _defer_cycle_notification(result, config=config)
