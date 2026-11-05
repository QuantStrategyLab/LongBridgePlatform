"""P10 callback and PAPER routing regressions; isolated stores and fake senders."""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from application.runtime_notification_adapters import build_runtime_notification_adapters
from notifications import unified
from notifications.order_alerts import build_order_lifecycle_event, monitor_submitted_order_status
from notifications.telegram import build_translator
from scripts.paper_observation.core import Watchdog

NOW = datetime(2026, 11, 4, 22, tzinfo=timezone.utc)


def adapters(fetch, sent):
    return build_runtime_notification_adapters(with_prefix=lambda text: text,
        send_message=lambda text: sent.append(text) or True,
        translator=build_translator("en"), fetch_order_status=fetch,
        order_poll_interval_sec=0, order_poll_max_attempts=2, sleeper=lambda _: None,
        log_message=lambda _: None)


@pytest.mark.parametrize("enabled", [False, True])
def test_filled_callback_never_sends_but_persists(monkeypatch, enabled):
    sent, facts = [], []
    monkeypatch.setattr(unified, "enabled", lambda: enabled)
    monkeypatch.setattr(unified, "record_order_resolution", lambda event: facts.append(event) or True)
    runner = adapters(lambda *_: dict(status="Filled", reason="", executed_qty="10", executed_price="12"), sent)
    runner.post_submit_order(None, SimpleNamespace(symbol="FAKE.US", side="buy", quantity=10),
                             SimpleNamespace(broker_order_id="fake-order"))
    assert not sent and len(facts) == 1


def test_verified_partial_then_filled_never_sends(monkeypatch):
    sent, facts = [], []
    statuses = iter([dict(status="PartialFilled", reason="", executed_qty="4", executed_price="12"),
                     dict(status="Filled", reason="", executed_qty="10", executed_price="12")])
    monkeypatch.setattr(unified, "record_order_resolution", lambda event: facts.append(event) or True)
    runner = adapters(lambda *_: next(statuses), sent)
    runner.post_submit_order(None, SimpleNamespace(symbol="FAKE.US", side="buy", quantity=10),
                             SimpleNamespace(broker_order_id="fake-order"))
    assert not sent and len(facts) == 2


def test_partial_does_not_hide_timeout():
    events = []
    monitor_submitted_order_status(None, "FAKE.US", "Buy", 10, "fake-order",
        fetch_order_status=lambda *_: dict(status="PartialFilled", reason="", executed_qty="4", executed_price="12"),
        order_poll_interval_sec=0, order_poll_max_attempts=1, publish_order_event=events.append,
        notify_issue=lambda *_: pytest.fail("unexpected poll exception"), sleeper=lambda _: None)
    assert [event.status for event in events] == ["PartialFilled", "StatusCheckTimeout"]


@pytest.mark.parametrize("quantity", ["NaN", "11", "0", "bad"])
def test_inconsistent_partial_stays_error(monkeypatch, quantity):
    sent, facts = [], []
    monkeypatch.setattr(unified, "enabled", lambda: False)
    monkeypatch.setattr(unified, "record_order_resolution", lambda event: facts.append(event) or True)
    runner = adapters(lambda *_: dict(status="PartialFilled", reason="", executed_qty=quantity, executed_price="12"), sent)
    runner.post_submit_order(None, SimpleNamespace(symbol="FAKE.US", side="buy", quantity=10),
                             SimpleNamespace(broker_order_id="fake-order"))
    assert sent and not facts


def test_candidate_failure_is_visible(monkeypatch):
    sent = []
    monkeypatch.setattr(unified, "enabled", lambda: False)
    monkeypatch.setattr(unified, "record_order_resolution", lambda event: False)
    runner = adapters(lambda *_: dict(status="Filled", reason="", executed_qty="10", executed_price="12"), sent)
    runner.post_submit_order(None, SimpleNamespace(symbol="FAKE.US", side="buy", quantity=10),
                             SimpleNamespace(broker_order_id="fake-order"))
    assert len(sent) == 1 and "【资产管理｜运行错误】" in sent[0]


class Store:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def create(self, key, value):
        if key in self.data:
            return False
        self.data[key] = value
        return True


def test_paper_recovery_and_maintenance_are_persisted_without_send():
    store, sent = Store(), []
    watch = Watchdog(store, lambda text: sent.append(text) or True, None, date(2026, 11, 4), NOW)
    watch.notify("fake-incident", True, NOW)
    assert len(sent) == 1
    watch.notify("fake-incident", False, NOW)
    watch.notify("fake-incident", False, NOW)
    watch.notify("calendar/coverage_7", True, NOW)
    assert len(sent) == 1
    assert store.get("alerts/fake-incident/recovered.json")
    records = [value for value in store.data.values() if value.get("delivery_status") == "pending_central"]
    assert {value["event_kind"] for value in records} == {"recovery", "maintenance_reminder"}
    assert all(value["actually_ran"] is False and value["cycle_count"] == 0 for value in records)


@pytest.mark.parametrize("locale,title", [("zh-CN", "【资产管理｜运行错误】"), ("en", "[Asset management | Runtime error]")])
def test_fixed_error_body_omits_diagnostics_and_unknown_counts(locale, title):
    from notifications.renderers import render_runtime_error
    text = render_runtime_error(kind="order_status_unknown", occurred_at=NOW,
        report={"strategy_profile": "fake-private-strategy", "account_id": "fake-private-account",
                "error": "fake-private-diagnostic"}, locale=locale)
    assert text.startswith(title)
    assert "fake-private" not in text
    assert ("未知" in text) if locale == "zh-CN" else ("unknown" in text)


def test_recovery_write_failure_does_not_claim_recovery():
    class FailingStore(Store):
        def create(self, key, value):
            if "/digest/" in key:
                raise RuntimeError("fake write failure")
            return super().create(key, value)
    store = FailingStore()
    watch = Watchdog(store, lambda _: True, None, NOW.date(), NOW)
    watch.notify("fake-incident", True, NOW)
    with pytest.raises(RuntimeError):
        watch.notify("fake-incident", False, NOW)
    assert not store.get("alerts/fake-incident/recovered.json")


class FileStore:
    def __init__(self, root):
        self.root = root

    def get(self, key):
        import json
        path = self.root / key
        return json.loads(path.read_text()) if path.exists() else None

    def create(self, key, value):
        import json
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x") as output:
                json.dump(value, output)
            return True
        except FileExistsError:
            return False


def _recover_in_process(root):
    def send(text):
        with (root / "sent.txt").open("a") as output:
            output.write(text + "\n")
        return True
    watch = Watchdog(FileStore(root), send, None, NOW.date(), NOW)
    watch.notify("fake-incident", False, NOW)


def test_recovery_survives_restart_and_two_processes(tmp_path):
    import multiprocessing
    store = FileStore(tmp_path)
    store.create("alerts/fake-incident/ack.json", {"sender_ack": True})
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_recover_in_process, args=(tmp_path,)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    _recover_in_process(tmp_path)
    assert not (tmp_path / "sent.txt").exists()
    assert store.get("alerts/fake-incident/digest/recovery.json")["delivery_status"] == "pending_central"
