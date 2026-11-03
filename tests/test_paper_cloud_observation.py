import copy
from datetime import date, datetime, timedelta, timezone
import json
from threading import Lock
from concurrent.futures import ThreadPoolExecutor

import pytest
from unittest.mock import Mock

from scripts.paper_observation.core import (
    Calendar,
    Collector,
    ObservationError,
    Watchdog,
    sanitize,
)

UTC = timezone.utc
BINDING = "a" * 64
NOW = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = Lock()

    def get(self, key):
        return copy.deepcopy(self.data.get(key))

    def create(self, key, value):
        with self.lock:
            if key in self.data:
                return False
            self.data[key] = copy.deepcopy(value)
            return True


def payload(now=NOW):
    return {
        "schema_version": "longbridge_account_snapshot.v1",
        "status": "partial",
        "account_scope": "PAPER",
        "no_order": True,
        "live_authority_granted": False,
        "snapshot_atomic": False,
        "cash_complete": True,
        "positions_complete": True,
        "observed_started_at": now.isoformat(),
        "observed_finished_at": now.isoformat(),
        "source_binding": {"status": "bound", "id": BINDING},
        "cash": [
            {
                "currency": "USD",
                "available_cash": "20",
                "frozen_cash": "0",
                "settling_cash": "0",
            }
        ],
        "broker_reported_balances": [
            {"currency": "USD", "net_assets": "20", "total_cash": "20"}
        ],
        "positions": [],
        "recent_orders_7d": [],
        "recent_orders_complete": False,
        "recent_orders_query": {
            "start_at": (now - timedelta(days=7)).isoformat(),
            "end_at": now.isoformat(),
            "paging_complete": False,
        },
    }


def collector(store, now=NOW, fetch=None, start=date(2026, 11, 2)):
    return Collector(
        store,
        fetch or (lambda: payload(now)),
        BINDING,
        Calendar(),
        start,
        clock=lambda: now,
    )


@pytest.mark.parametrize(
    "day,expected",
    [
        ("2026-07-06", "2026-07-06T20:30:00+00:00"),
        ("2026-11-02", "2026-11-02T21:30:00+00:00"),
        ("2026-11-27", "2026-11-27T18:30:00+00:00"),
        ("2026-12-24", "2026-12-24T18:30:00+00:00"),
        ("2028-07-03", "2028-07-03T17:30:00+00:00"),
    ],
)
def test_calendar_dst_and_early_closes(day, expected):
    assert Calendar().due(date.fromisoformat(day)).isoformat() == expected


def test_holiday_and_expiry():
    assert Calendar().due(date(2026, 11, 26)) is None
    assert Calendar().due(date(2027, 12, 31)) is not None
    with pytest.raises(ObservationError, match="calendar_unavailable"):
        Calendar().due(date(2029, 1, 1))


def test_concurrent_collectors_preserve_single_snapshot_t0_and_slot():
    store = MemoryStore()
    calls = []

    def fetch():
        calls.append(True)
        return payload()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: collector(store, fetch=fetch).run(NOW), range(4)))
    assert len(calls) == 1
    assert len([k for k in store.data if k.startswith("snapshots/")]) == 1
    first = store.get("t0.json")
    collector(store, NOW + timedelta(days=1)).run(NOW + timedelta(days=1))
    assert store.get("t0.json") == first


def test_missing_health_marker_repairs_without_account_request():
    store = MemoryStore()
    collector(store).run(NOW)
    del store.data["health/days/2026-11-02.json"]
    collector(store, fetch=lambda: pytest.fail("unexpected GET")).run(NOW)
    assert store.get("health/days/2026-11-02.json")


@pytest.mark.parametrize("days", [1, 3, 8])
def test_gaps_remain_null_without_historical_snapshots(days):
    store = MemoryStore()
    now = NOW + timedelta(days=days, hours=2)
    collector(store, now).run(now)
    assert not store.get("snapshots/US/2026-11-02.json")
    gap = store.get("health/gaps/2026-11-02.json")
    assert gap["cash"] is None and gap["positions"] is None
    assert gap["status"] == "unrecoverable_point_in_time"


def test_authorization_failure_stops_later_slots():
    store, calls = MemoryStore(), []

    def denied():
        calls.append(1)
        raise ObservationError("http_403")

    for minutes in (0, 15, 30, 90):
        collector(store, NOW + timedelta(minutes=minutes), denied).run(
            NOW + timedelta(minutes=minutes)
        )
    assert len(calls) == 1


def test_transient_failure_has_three_ordinary_and_one_recovery_attempt():
    store, calls = MemoryStore(), []

    def failed():
        calls.append(1)
        raise ObservationError("http_failure")

    for minutes in (0, 1, 15, 30, 45, 60, 75, 90):
        collector(store, NOW + timedelta(minutes=minutes), failed).run(
            NOW + timedelta(minutes=minutes)
        )
    assert len(calls) == 4


def test_nested_identifiers_are_removed_and_invalid_amounts_rejected():
    raw = payload()
    raw["account_number"] = "SECRET-ACCOUNT-12345678"
    raw["cash"][0]["nested"] = {"token": "SECRET-TOKEN"}
    result = json.dumps(sanitize(raw, BINDING, NOW))
    assert "SECRET" not in result and BINDING not in result
    raw["cash"][0]["available_cash"] = "SECRET-TOKEN"
    with pytest.raises(ObservationError, match="schema_invalid"):
        sanitize(raw, BINDING, NOW)


def test_unknown_binding_fails_closed():
    with pytest.raises(ObservationError, match="identity_failure"):
        sanitize(payload(), "b" * 64, NOW)


def test_watchdog_missing_recovery_and_dedup():
    store, messages = MemoryStore(), []
    watch = Watchdog(
        store,
        lambda message: (messages.append(message), True)[1],
        Calendar(),
        date(2026, 11, 2),
        NOW,
    )
    watch.run(NOW + timedelta(minutes=61))
    watch.run(NOW + timedelta(minutes=76))
    assert len([m for m in messages if "alert" in m and "missing_snapshot" in m]) == 1
    collector(store, NOW + timedelta(minutes=90)).run(NOW + timedelta(minutes=90))
    watch.run(NOW + timedelta(minutes=91))
    watch.run(NOW + timedelta(minutes=106))
    assert (
        len([m for m in messages if "recovery" in m and "missing_snapshot" in m]) == 1
    )
    assert store.get("alerts/watch-heartbeat/20261102T2245Z.json")


def test_watchdog_storage_failure_still_notifies():
    store, messages = MemoryStore(), []

    def unavailable(*_):
        raise ObservationError("storage_unavailable")

    store.create = unavailable
    watch = Watchdog(
        store, lambda m: messages.append(m), Calendar(), date(2026, 11, 2), NOW
    )
    assert watch.run(NOW) == "storage_unavailable"
    assert len(messages) == 1


def test_ambiguous_store_write_reads_back_without_retry():
    from scripts.paper_observation.__main__ import Store

    store = Store.__new__(Store)
    store.bucket = Mock()
    blob = store.bucket.blob.return_value
    blob.upload_from_string.side_effect = TimeoutError("sensitive error")
    store.get = Mock(return_value={"value": 1})
    assert store.create("synthetic/key.json", {"value": 1}) is True
    assert blob.upload_from_string.call_count == 1
    assert blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    assert blob.upload_from_string.call_args.kwargs["retry"] is None


def test_competing_write_does_not_overwrite_existing_value():
    from google.api_core.exceptions import PreconditionFailed
    from scripts.paper_observation.__main__ import Store

    store = Store.__new__(Store)
    store.bucket = Mock()
    blob = store.bucket.blob.return_value
    blob.upload_from_string.side_effect = PreconditionFailed("collision")
    store.get = Mock(return_value={"original": True})
    assert store.create("synthetic/key.json", {"original": False}) is False
    assert blob.upload_from_string.call_count == 1


def test_watchdog_permission_failure_alerts_on_next_tick():
    store, messages = MemoryStore(), []
    store.create("health/stopped/2026-11-02.json", {"reason": "http_403"})
    watch = Watchdog(
        store,
        lambda message: (messages.append(message), True)[1],
        Calendar(),
        date(2026, 11, 2),
        NOW,
    )
    watch.run(NOW + timedelta(minutes=7))
    assert any("permission_identity_schema" in message for message in messages)


def test_manual_verification_does_not_create_official_snapshot(monkeypatch, capsys):
    from scripts.paper_observation import __main__ as cli

    store = MemoryStore()
    now = datetime.now(UTC)
    monkeypatch.setattr(cli, "Store", lambda _: store)
    monkeypatch.setattr(cli, "snapshot_fetcher", lambda *_: lambda: payload(now))
    monkeypatch.setattr(cli.sys, "argv", ["observation", "verify"])
    for key, value in {
        "OBSERVATION_BUCKET": "synthetic-bucket",
        "OBSERVATION_START": "2026-11-02",
        "PAPER_SERVICE_URL": "https://synthetic.run.app",
        "EXPECTED_SOURCE_BINDING": BINDING,
    }.items():
        monkeypatch.setenv(key, value)
    assert cli.main() == 0
    assert all(key.startswith("health/verification/") for key in store.data)
    assert "official_snapshot_created" in capsys.readouterr().out


def test_expired_calendar_alert_is_deduplicated_when_storage_works():
    store, messages = MemoryStore(), []
    now = datetime(2029, 1, 2, 22, tzinfo=UTC)
    watch = Watchdog(
        store,
        lambda message: (messages.append(message), True)[1],
        Calendar(),
        date(2028, 12, 31),
        now,
    )
    assert watch.run(now) == "calendar_unavailable"
    assert watch.run(now + timedelta(minutes=15)) == "calendar_unavailable"
    assert (
        len([message for message in messages if "calendar_unavailable" in message]) == 1
    )


def test_notification_verification_does_not_consume_production_incident():
    store, messages = MemoryStore(), []

    def send(message):
        messages.append(message)
        return True

    watch = Watchdog(store, send, Calendar(), date(2026, 11, 2), NOW, verification=True)
    watch.notify("synthetic_failure", True, NOW)
    assert messages == ["[verification] PAPER observation alert: synthetic_failure"]
    assert store.get("alerts/verification/synthetic_failure/ack.json")
    assert not store.get("alerts/synthetic_failure/ack.json")
    Watchdog(store, send, Calendar(), date(2026, 11, 2), NOW).notify(
        "synthetic_failure", True, NOW
    )
    assert len(messages) == 2
