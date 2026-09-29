from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts.record_daily_account_snapshot import record_projected_daily_account


SOURCE = {"kind": "deployment_scope_token_version", "status": "bound", "id": "a" * 64}


def _inputs(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "env": {
            "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
            "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-history/longbridge/account_snapshots",
            "ACCOUNT_HISTORY_TARGET_ID": "paper",
            "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
            "GOOGLE_CLOUD_PROJECT": "longbridgequant",
        },
        "account_scope": "PAPER",
        "source_binding": SOURCE,
        "balances": [{"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"}],
        "cash": [{
            "currency": "USD",
            "available_cash": "200.25",
            "frozen_cash": "20",
            "settling_cash": "14.25",
        }],
        "started": now - timedelta(seconds=2),
        "finished": now - timedelta(seconds=1),
    }
    values.update(overrides)
    return values


class _Store:
    def __init__(self, *, error=None):
        self.error = error
        self.uploads = []
        self.calls = 0
        self.client = self

    def _parse_uri(self, uri):
        bucket, blob = uri.removeprefix("gs://").split("/", 1)
        return bucket, blob

    def bucket(self, bucket):
        return self

    def blob(self, name):
        self.name = name
        return self

    def upload_from_string(
        self,
        data,
        *,
        content_type,
        if_generation_match,
        timeout,
        retry,
    ):
        self.calls += 1
        if self.error:
            raise self.error
        self.uploads.append((data, content_type, if_generation_match, timeout, retry))


def test_writer_creates_complete_redacted_history_with_bounded_create_only_upload():
    store = _Store()
    values = _inputs()
    result = record_projected_daily_account(
        **values, open_store=lambda project_id: store
    )

    assert result.status == "recorded"
    assert result.category == ""
    assert len(store.uploads) == 1
    body, content_type, generation, timeout, retry = store.uploads[0]
    record = json.loads(body)
    assert content_type == "application/json"
    assert generation == 0
    assert timeout == 20.0
    assert retry is None
    assert record == {
        "schema_version": "longbridge_account_snapshot_history.v1",
        "snapshot_schema_version": "longbridge_account_snapshot.v1",
        "account_scope": "PAPER",
        "target_id": "paper",
        "source_binding": SOURCE,
        "observed_started_at": values["started"].isoformat(),
        "observed_finished_at": values["finished"].isoformat(),
        "snapshot_atomic": False,
        "observation_date": record["observed_started_at"][:10],
        "broker_reported_balances": [
            {"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"}
        ],
        "cash": [{
            "currency": "USD",
            "available_cash": "200.25",
            "frozen_cash": "20",
            "settling_cash": "14.25",
        }],
    }
    assert store.name.startswith(f"longbridge/account_snapshots/paper/{'a' * 64}/")
    assert store.name.endswith("Z.json")
    assert "positions" not in record
    assert "token" not in record


@pytest.mark.parametrize(
    "overrides",
    [
        {"env": {"ACCOUNT_HISTORY_RECORDING_ENABLED": "true"}},
        {"env": {**_inputs()["env"], "ACCOUNT_HISTORY_RECORDING_ENABLED": "false"}},
        {"source_binding": {"kind": "deployment_scope_token_version", "status": "unavailable", "id": None}},
        {"balances": []},
        {"cash": []},
        {"account_scope": "HK"},
        {"env": {**_inputs()["env"], "ACCOUNT_HISTORY_TARGET_ID": "hk"}},
    ],
)
def test_invalid_config_source_scope_or_incomplete_projection_never_writes(overrides):
    called = []
    result = record_projected_daily_account(
        **_inputs(**overrides), open_store=lambda project_id: called.append(project_id)
    )
    assert result.status == "skipped"
    assert called == []


def test_conflict_never_reports_recorded():
    from google.api_core.exceptions import PreconditionFailed

    store = _Store(error=PreconditionFailed("exists"))
    result = record_projected_daily_account(
        **_inputs(), open_store=lambda project_id: store
    )
    assert result.status == "error"
    assert result.category == "object_conflict"
    assert store.calls == 1


def test_store_exception_is_redacted_and_not_retried():
    store = _Store(error=RuntimeError("synthetic-sensitive-error"))
    calls = []
    original = store.upload_from_string

    def counted_upload(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    store.upload_from_string = counted_upload
    result = record_projected_daily_account(
        **_inputs(), open_store=lambda project_id: store
    )
    assert result.status == "error"
    assert result.category == "store_unknown"
    assert len(calls) == 1
    assert store.calls == 1
    assert len(store.uploads) == 0
