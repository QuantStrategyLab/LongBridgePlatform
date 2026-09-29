from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.record_daily_account_snapshot import record_projected_daily_account


SOURCE = {"kind": "deployment_scope_token_version", "status": "bound", "id": "a" * 64}


def _inputs(scope="HK", **overrides):
    now = datetime.now(timezone.utc)
    values = {
        "env": {
            "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
            "ACCOUNT_HISTORY_GCS_PREFIX": "gs://history/longbridge/account_snapshots",
            "ACCOUNT_HISTORY_TARGET_ID": scope.lower(),
            "ACCOUNT_HISTORY_EXPECTED_SCOPE": scope,
            "GOOGLE_CLOUD_PROJECT": "longbridgequant",
        },
        "account_scope": scope,
        "source_binding": SOURCE,
        "balances": [{"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"}],
        "cash": [{"currency": "HKD", "available_cash": "200.25", "frozen_cash": "20", "settling_cash": "14.25"}],
        "started": now - timedelta(seconds=2),
        "finished": now - timedelta(seconds=1),
    }
    values.update(overrides)
    return values


class _Store:
    def __init__(self, error=None):
        self.calls = []
        self.error = error
        self.client = self

    def _parse_uri(self, uri):
        bucket, blob = uri.removeprefix("gs://").split("/", 1)
        return bucket, blob

    def bucket(self, _bucket):
        return self

    def blob(self, name):
        self.name = name
        return self

    def upload_from_string(self, data, *, content_type, if_generation_match, timeout, retry):
        self.calls.append((data, content_type, if_generation_match, timeout, retry))
        if self.error is not None:
            raise self.error


@pytest.mark.parametrize("scope", ["HK", "SG"])
def test_writer_records_only_matching_longbridge_scope(scope):
    store = _Store()
    values = _inputs(scope)
    result = record_projected_daily_account(**values, open_store=lambda _project: store)

    assert result.status == "recorded"
    assert len(store.calls) == 1
    body, content_type, generation, timeout, retry = store.calls[0]
    record = json.loads(body)
    assert record["account_scope"] == scope
    assert record["target_id"] == scope.lower()
    assert record["broker_reported_balances"] == values["balances"]
    assert record["cash"] == values["cash"]
    assert content_type == "application/json"
    assert (generation, timeout, retry) == (0, 20.0, None)
    assert store.name.startswith(f"longbridge/account_snapshots/{scope.lower()}/{'a' * 64}/")
    assert "positions" not in record


@pytest.mark.parametrize(
    "scope,env_scope,target",
    [("HK", "SG", "hk"), ("SG", "HK", "sg"), ("HK", "HK", "sg"), ("SG", "SG", "hk")],
)
def test_cross_scope_target_or_expected_scope_never_opens_store(scope, env_scope, target):
    values = _inputs(scope)
    values["env"] = {**values["env"], "ACCOUNT_HISTORY_EXPECTED_SCOPE": env_scope, "ACCOUNT_HISTORY_TARGET_ID": target}
    opened = []

    result = record_projected_daily_account(**values, open_store=lambda project: opened.append(project))

    assert result.status == "skipped"
    assert opened == []


def test_unbound_source_never_opens_store():
    opened = []
    result = record_projected_daily_account(
        **_inputs(source_binding={"kind": "deployment_scope_token_version", "status": "unavailable", "id": None}),
        open_store=lambda project: opened.append(project),
    )
    assert result.status == "skipped"
    assert opened == []


def test_disabled_history_does_not_open_store():
    values = _inputs("SG")
    values["env"] = {**values["env"], "ACCOUNT_HISTORY_RECORDING_ENABLED": "false"}
    opened = []
    result = record_projected_daily_account(**values, open_store=lambda project: opened.append(project))
    assert result.status == "skipped"
    assert opened == []


def test_conflict_is_unknown_safe_and_upload_is_not_retried():
    from google.api_core.exceptions import PreconditionFailed

    store = _Store(PreconditionFailed("already exists"))
    result = record_projected_daily_account(**_inputs("SG"), open_store=lambda _project: store)

    assert result.status == "error"
    assert result.category == "object_conflict"
    assert len(store.calls) == 1
    assert store.calls[0][2:] == (0, 20.0, None)


def test_unknown_store_error_is_redacted_and_not_retried():
    store = _Store(RuntimeError("synthetic-private-error"))
    result = record_projected_daily_account(**_inputs("HK"), open_store=lambda _project: store)

    assert result.status == "error"
    assert result.category == "store_unknown"
    assert len(store.calls) == 1
    assert "synthetic-private-error" not in repr(result)
