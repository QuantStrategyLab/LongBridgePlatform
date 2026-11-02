"""Synthetic immutable replay, including restart and create-only process races."""

import copy
from datetime import timedelta
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.paper_observation.core import ObservationError, validate_stored
from test_paper_cloud_observation import NOW, MemoryStore, collector, payload

DAY = NOW.date()
SNAPSHOT = f"snapshots/US/{DAY}.json"
HEALTH = f"health/days/{DAY}.json"


def observed_payload(now=NOW):
    raw = payload(now)
    raw["positions"] = [{"symbol": "FAKE.US", "currency": "USD", "quantity": "2"}]
    raw["recent_orders_7d"] = [{
        "symbol": "FAKE.US", "currency": "USD", "status": "NEW", "side": "BUY",
        "order_type": "LO", "quantity": "2", "executed_quantity": "0",
        "submitted_at": (now - timedelta(days=1)).isoformat(),
        "updated_at": None, "order_digest": "b" * 64,
    }]
    return raw


@pytest.fixture
def record():
    store = MemoryStore()
    assert collector(store, fetch=observed_payload).run(NOW) == "stored"
    return store.get(SNAPSHOT)


def change(record, path, value):
    target = record
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def assert_rejected(record):
    store = MemoryStore()
    store.create(SNAPSHOT, record)
    store.create(f"health/attempts/{DAY}/0.json", {"started_at": NOW.isoformat()})
    store.create(f"health/gaps/{DAY}.json", {"status": "unrecoverable_point_in_time"})
    before = copy.deepcopy(store.data)
    with pytest.raises(ObservationError) as caught:
        collector(store).complete(DAY, store.get(SNAPSHOT))
    assert str(caught.value) == "stored_record_invalid"
    assert store.get("t0.json") is None and store.get(HEALTH) is None
    assert store.data == before


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1" * 65, 20, None])
def test_reject_nonfinite_stored_cash_before_completion(record, value):
    record["cash"][0]["available_cash"] = value
    assert_rejected(record)


@pytest.mark.parametrize("path,value", [
    (("positions",), ["FAKE-SENSITIVE-ROW"]),
    (("positions", 0, "quantity"), "NaN"),
    (("positions", 0, "symbol"), "invalid"),
    (("cash", 0, "currency"), "usd"),
    (("cash",), []),
    (("broker_reported_balances",), []),
    (("broker_reported_balances", 0, "net_assets"), "Infinity"),
    (("positions",), [{}] * 5001),
    (("recent_orders_7d", 0, "quantity"), "NaN"),
    (("recent_orders_7d", 0, "side"), "UNKNOWN"),
    (("recent_orders_7d", 0, "status"), "lowercase"),
    (("recent_orders_7d", 0, "order_type"), "lowercase"),
    (("recent_orders_7d", 0, "order_digest"), "FAKE-DIGEST"),
    (("recent_orders_7d", 0, "submitted_at"), (NOW - timedelta(days=8)).isoformat()),
    (("recent_orders_7d", 0, "updated_at"), False),
    (("known_non_terminal_orders_7d",), []),
    (("orders_query_start",), (NOW - timedelta(days=6)).isoformat()),
    (("orders_query_end",), (NOW + timedelta(seconds=1)).isoformat()),
], ids=[
    "row-type", "position-amount", "symbol", "currency", "empty-cash",
    "empty-balances", "balance-amount", "row-limit", "order-amount", "side",
    "status", "order-type", "digest", "order-window", "nullable-time",
    "working-subset", "query-duration", "query-end",
])
def test_reject_invalid_stored_rows_and_query(record, path, value):
    change(record, path, value)
    assert_rejected(record)


@pytest.mark.parametrize("field,value", [
    ("account_id", "FAKE-ACCOUNT"),
    ("identity_verification", "verified"),
    ("snapshot_atomic", True),
    ("snapshot_atomic", 0),
    ("recent_orders_complete", True),
    ("open_orders_complete", True),
    ("open_orders_complete", 0),
    ("account_scope", "LIVE"),
    ("schema_version", "unknown"),
    ("trading_date", "2026-11-03"),
])
def test_preserve_partial_identity_and_order_coverage(record, field, value):
    record[field] = value
    assert_rejected(record)


@pytest.mark.parametrize("field,value", [
    ("observed_finished_at", (NOW - timedelta(seconds=1)).isoformat()),
    ("observed_finished_at", (NOW + timedelta(minutes=6)).isoformat()),
    ("observed_started_at", "invalid"),
    ("job_started_at", NOW.replace(tzinfo=None).isoformat()),
    ("job_started_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("store_requested_at", (NOW - timedelta(minutes=6)).isoformat()),
    ("scheduled_at", (NOW + timedelta(seconds=1)).isoformat()),
    ("scheduled_at", (NOW - timedelta(days=1)).isoformat()),
    ("lateness_seconds", -1),
    ("lateness_seconds", float("nan")),
    ("lateness_seconds", False),
    ("lateness_seconds", "0"),
    ("timing", "late_snapshot"),
])
def test_reject_invalid_stored_timing(record, field, value):
    record[field] = value
    assert_rejected(record)


@pytest.mark.parametrize("path", [(), ("cash", 0), ("positions", 0),
                                  ("recent_orders_7d", 0),
                                  ("known_non_terminal_orders_7d", 0)])
def test_reject_unknown_sensitive_stored_fields(record, path, capsys):
    change(record, (*path, "account_alias"), "FAKE-SENSITIVE-ALIAS")
    assert_rejected(record)
    assert capsys.readouterr() == ("", "")


def test_missing_fields_are_errors(record):
    for field in record:
        broken = copy.deepcopy(record)
        del broken[field]
        assert_rejected(broken)
    for field in record["recent_orders_7d"][0]:
        broken = copy.deepcopy(record)
        del broken["recent_orders_7d"][0][field]
        assert_rejected(broken)


def test_duplicate_order_digest_is_not_normalized_during_replay(record):
    record["recent_orders_7d"].append(copy.deepcopy(record["recent_orders_7d"][0]))
    assert_rejected(record)


@pytest.mark.parametrize("seconds", [0, 300, 301, 5400])
def test_valid_historical_replay_preserves_first_t0(seconds):
    now = NOW + timedelta(seconds=seconds)
    store = MemoryStore()
    assert collector(store, now, lambda: observed_payload(now)).run(now) == "stored"
    record = store.get(SNAPSHOT)
    assert record["timing"] == ("on_time" if seconds <= 300 else "late_snapshot")
    assert validate_stored(record, DAY) is record
    first = store.get("t0.json")
    del store.data[HEALTH]
    future = NOW + timedelta(days=1, seconds=-1)
    assert collector(store, future, lambda: pytest.fail("unexpected fetch")).run(future) == "no_due_gap"
    assert store.get("t0.json") == first
    assert store.get(HEALTH)["observed_at"] == first["observed_at"]
    collector(store, NOW + timedelta(days=3650)).complete(DAY, record)
    assert store.get("t0.json") == first


@pytest.mark.parametrize("started,finished,clock", [(0, 330, 300), (0, 0, 300), (30, 30, 0)])
def test_existing_sanitize_clock_tolerance_remains_valid(started, finished, clock):
    raw = observed_payload(NOW + timedelta(seconds=started))
    raw["observed_finished_at"] = (NOW + timedelta(seconds=finished)).isoformat()
    store = MemoryStore()
    instance = collector(store, fetch=lambda: raw)
    instance.clock = lambda: NOW + timedelta(seconds=clock)
    assert instance.run(NOW) == "stored"
    stored = store.get(SNAPSHOT)
    assert validate_stored(stored, DAY) is stored


def test_invalid_replay_propagates_nonzero_cli_result(record, monkeypatch, capsys):
    from scripts.paper_observation import __main__ as cli

    record["open_orders_complete"] = True
    store = MemoryStore()
    store.create(SNAPSHOT, record)
    monkeypatch.setattr(cli, "Store", lambda _: store)
    monkeypatch.setattr(cli, "snapshot_fetcher", lambda *_: lambda: pytest.fail("unexpected fetch"))
    monkeypatch.setattr(cli, "datetime", type("Clock", (), {"now": staticmethod(lambda _: NOW)}))
    monkeypatch.setattr(cli.sys, "argv", ["observation", "collect"])
    for key, value in {"OBSERVATION_BUCKET": "synthetic-bucket", "OBSERVATION_START": str(DAY),
                       "PAPER_SERVICE_URL": "https://synthetic.run.app",
                       "EXPECTED_SOURCE_BINDING": "a" * 64}.items():
        monkeypatch.setenv(key, value)
    assert cli.main() == 1
    assert capsys.readouterr() == ('{"result":"observation_failed"}\n', "")
    assert store.get("t0.json") is None and store.get(HEALTH) is None


class LocalStore:
    """Fake local create-only state, serialized by a kernel lock across processes."""

    def __init__(self, root):
        self.root = Path(root)

    def access(self, key, value=None, *, create=False):
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / "state.json"
            data = json.loads(path.read_text()) if path.exists() else {}
            if not create:
                return data.get(key)
            if key in data:
                return False
            data[key] = value
            path.write_text(json.dumps(data))
            return True

    def get(self, key):
        return self.access(key)

    def create(self, key, value):
        return self.access(key, value, create=True)


def worker(root, seconds=0):
    return [sys.executable, str(Path(__file__).resolve()), str(root), str(seconds)]


def test_restart_rejects_invalid_persisted_snapshot(record, tmp_path):
    store = LocalStore(tmp_path)
    record["cash"][0]["available_cash"] = "NaN"
    store.create(SNAPSHOT, record)
    facts = {f"health/attempts/{DAY}/0.json": {"started_at": NOW.isoformat()},
             f"health/gaps/{DAY}.json": {"status": "unrecoverable_point_in_time"}}
    for key, value in facts.items():
        store.create(key, value)
    restarted = subprocess.run(worker(tmp_path), capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 1
    assert restarted.stdout == "" and restarted.stderr == "stored_record_invalid\n"
    assert store.get("t0.json") is None and store.get(HEALTH) is None
    assert store.get(SNAPSHOT) == record
    assert all(store.get(key) == value for key, value in facts.items())
    assert any(key.startswith("health/heartbeat/") for key in json.loads((tmp_path / "state.json").read_text()))


def test_concurrent_processes_preserve_canonical_snapshot_and_t0(tmp_path):
    children = [subprocess.Popen(worker(tmp_path, seconds), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
                for seconds in (0, 60, 120, 180)]
    for child in children:
        stdout, stderr = child.communicate(timeout=30)
        assert child.returncode == 0 and stdout == "" and stderr == ""
    store = LocalStore(tmp_path)
    first = store.get("t0.json")
    snapshot = store.get(SNAPSHOT)
    assert first["observed_at"] == snapshot["observed_started_at"]
    assert store.get(HEALTH)["observed_at"] == first["observed_at"]
    assert len([key for key in json.loads((tmp_path / "state.json").read_text())
                if key.startswith("snapshots/")]) == 1
    replay = subprocess.run(worker(tmp_path, 600), capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0 and replay.stdout == "" and replay.stderr == ""
    assert store.get("t0.json") == first and store.get(SNAPSHOT) == snapshot


if __name__ == "__main__":
    now = NOW + timedelta(seconds=int(sys.argv[2]))
    try:
        collector(LocalStore(sys.argv[1]), now, lambda: observed_payload(now)).run(now)
    except ObservationError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
