import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from application.account_snapshot import (
    begin_natural_cycle_history,
    cycle_history_observation,
    end_natural_cycle_history,
    read_cycle_account_balance,
)
from scripts import record_daily_account_snapshot as history


def _balance(currency, net_assets, cash):
    return SimpleNamespace(
        currency=currency,
        net_assets=Decimal(net_assets),
        total_cash=Decimal("12.25"),
        cash_infos=[SimpleNamespace(currency=c, available_cash=Decimal(a), frozen_cash=Decimal("0"), settling_cash=Decimal("0")) for c, a in cash],
    )


def test_capture_reuses_the_same_native_balance_and_preserves_currencies():
    token = begin_natural_cycle_history()
    calls = []
    response = [_balance("USD", "100.10", [("USD", "50"), ("HKD", "3")]), _balance("HKD", "600", [("SGD", "4")])]
    try:
        first = read_cycle_account_balance(lambda: calls.append(1) or response)
        second = read_cycle_account_balance(lambda: calls.append(2) or [])
        observed = cycle_history_observation()
        assert first is response and second is response
        assert len(calls) == 1
        assert observed["projection"]["broker_reported_balances"] == [
            {"currency": "HKD", "net_assets": "600", "total_cash": "12.25"},
            {"currency": "USD", "net_assets": "100.1", "total_cash": "12.25"},
        ]
        assert {row["currency"] for row in observed["projection"]["cash"]} == {"USD", "HKD", "SGD"}
        assert observed["started"].tzinfo is timezone.utc and observed["finished"].tzinfo is timezone.utc
    finally:
        end_natural_cycle_history(token)
    assert cycle_history_observation() is None


def test_produced_history_is_accepted_by_existing_consumer_validator():
    now = datetime.now(timezone.utc)
    binding_id = "a" * 64
    env = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true", "ACCOUNT_HISTORY_GCS_PREFIX": "gs://qsl-runtime-logs-shared/longbridge/account_snapshots",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK", "ACCOUNT_HISTORY_TARGET_ID": "hk", "GOOGLE_CLOUD_PROJECT": "longbridge-prod",
    }
    config = history._ProducerConfig("gs://qsl-runtime-logs-shared/longbridge/account_snapshots", "hk", "HK", "longbridge-prod")
    body, uri = history._producer_history_object(
        config,
        {"kind": history.SOURCE_KIND, "status": "bound", "id": binding_id},
        [{"currency": "USD", "net_assets": "100.10", "total_cash": "90"}, {"currency": "HKD", "net_assets": "800", "total_cash": "700"}],
        [{"currency": "USD", "available_cash": "20", "frozen_cash": "0", "settling_cash": "0"}, {"currency": "HKD", "available_cash": "30", "frozen_cash": "0", "settling_cash": "0"}, {"currency": "SGD", "available_cash": "4", "frozen_cash": "0", "settling_cash": "0"}],
        now, now,
    )
    consumer_config = history._Config("longbridge-prod", "asia-east1", "service", "https://service.run.app", "gs://qsl-runtime-logs-shared/longbridge/account_snapshots", "qsl-runtime-logs-shared", "longbridge/account_snapshots", "hk", "HK", binding_id, "job", "resource")
    payload = json.loads(body)
    candidate = {"name": uri.removeprefix("gs://qsl-runtime-logs-shared/")}
    history._validate_history_object(payload, consumer_config, candidate, now, now)


def test_producer_does_not_write_unbound_or_invalid_data():
    writes = []
    env = {"ACCOUNT_HISTORY_RECORDING_ENABLED": "true", "ACCOUNT_HISTORY_GCS_PREFIX": "gs://qsl-runtime-logs-shared/longbridge/account_snapshots", "ACCOUNT_HISTORY_EXPECTED_SCOPE": "SG", "ACCOUNT_HISTORY_TARGET_ID": "sg", "GOOGLE_CLOUD_PROJECT": "longbridge-prod"}
    result = history.record_projected_daily_account(env, account_scope="SG", source_binding={"status": "unavailable"}, balances=[], cash=[], started=datetime.now(timezone.utc), finished=datetime.now(timezone.utc), open_store=lambda _project: writes.append(1))
    assert result.status == "skipped"
    assert writes == []


def test_capture_failure_is_not_retried_and_context_is_reset():
    token = begin_natural_cycle_history()
    calls = []
    try:
        try:
            read_cycle_account_balance(lambda: calls.append(1) or (_ for _ in ()).throw(ValueError("offline")))
        except ValueError:
            pass
        try:
            read_cycle_account_balance(lambda: calls.append(2) or [])
        except RuntimeError:
            pass
        assert calls == [1]
    finally:
        end_natural_cycle_history(token)
    assert read_cycle_account_balance(lambda: calls.append(3) or []) == []
    assert calls == [1, 3]


def test_create_only_upload_has_finite_timeout_and_no_retry():
    received = {}

    class Blob:
        def upload_from_string(self, body, *, content_type, if_generation_match, timeout, retry):
            received.update(body=body, content_type=content_type, generation=if_generation_match, timeout=timeout, retry=retry)

    class Client:
        def bucket(self, _bucket):
            return SimpleNamespace(blob=lambda _name: Blob())

    store = SimpleNamespace(client=Client(), _parse_uri=lambda _uri: ("bucket-name", "path/object"))
    assert history._producer_create_bounded(store, "gs://bucket-name/path/object", "{}") is True
    assert received == {"body": "{}", "content_type": "application/json", "generation": 0, "timeout": 20.0, "retry": None}
