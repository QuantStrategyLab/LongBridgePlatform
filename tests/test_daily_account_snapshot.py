from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import record_daily_account_snapshot as snapshots  # noqa: E402


T0 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
BINDING = "a" * 64
OTHER_BINDING = "b" * 64
SERVICE_URL = "https://longbridge-quant-paper-service-kcc3gcgmwq-de.a.run.app"
PREFIX = "gs://qsl-runtime-logs-shared/longbridge/account_snapshots"
QRS_URL = "https://qrs.example.test/api/account-facts/sync"
QRS_TOKEN = "synthetic-qrs-token"
SECRET = "synthetic-secret-value"


def _env(target_id="paper", **overrides):
    scope = {"paper": "PAPER", "hk": "HK", "sg": "SG"}[target_id]
    service_url = {
        "paper": SERVICE_URL,
        "hk": "https://longbridge-quant-hk-service-kcc3gcgmwq-de.a.run.app",
        "sg": "https://longbridge-quant-sg-service-kcc3gcgmwq-de.a.run.app",
    }[target_id]
    result = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_SERVICE_URL": service_url,
        "ACCOUNT_HISTORY_GCS_PREFIX": PREFIX,
        "ACCOUNT_HISTORY_TARGET_ID": target_id,
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": scope,
        "ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID": BINDING,
        "GOOGLE_CLOUD_PROJECT": "longbridgequant",
    }
    result.update(overrides)
    return result


def _job(target_id="paper", *, state="ENABLED", service_url=None, region=None, **overrides):
    service = {
        "paper": "longbridge-quant-paper-service",
        "hk": "longbridge-quant-hk-service",
        "sg": "longbridge-quant-sg-service",
    }[target_id]
    region = region or {"paper": "asia-east1", "hk": "asia-east2", "sg": "asia-southeast1"}[target_id]
    service_url = service_url or _env(target_id)["ACCOUNT_HISTORY_SERVICE_URL"]
    job = {
        "name": f"projects/longbridgequant/locations/{region}/jobs/{service}-probe-scheduler",
        "state": state,
        "httpTarget": {
            "httpMethod": "POST",
            "uri": f"{service_url}/probe",
            "oidcToken": {
                "serviceAccountEmail": "longbridge-platform-scheduler@longbridgequant.iam.gserviceaccount.com",
                "audience": service_url,
            },
        },
        "retryConfig": {"retryCount": 0, "maxRetryDuration": "0s"},
    }
    job.update(overrides)
    return job


def _history(started=None, finished=None, *, target_id="paper", binding=BINDING, scope=None, balances=None, cash=None):
    started = started or T0 + timedelta(seconds=1)
    finished = finished or T0 + timedelta(seconds=2)
    return {
        "schema_version": "longbridge_account_snapshot_history.v1",
        "snapshot_schema_version": "longbridge_account_snapshot.v1",
        "account_scope": scope or {"paper": "PAPER", "hk": "HK", "sg": "SG"}[target_id],
        "target_id": target_id,
        "source_binding": {
            "kind": "deployment_scope_token_version",
            "status": "bound",
            "id": binding,
        },
        "observed_started_at": started.isoformat(),
        "observed_finished_at": finished.isoformat(),
        "snapshot_atomic": False,
        "observation_date": started.date().isoformat(),
        "broker_reported_balances": balances
        or [{"currency": "USD", "net_assets": "10.25", "total_cash": "3"}],
        "cash": cash
        or [
            {
                "currency": "HKD",
                "available_cash": "1",
                "frozen_cash": "0",
                "settling_cash": "0",
            }
        ],
    }


def _object(payload, *, path_date=None, raw=None, generation=7, size=None, error=None):
    started = datetime.fromisoformat(payload["observed_started_at"])
    finished = datetime.fromisoformat(payload["observed_finished_at"])
    day = path_date or started.date().isoformat()
    object_name = (
        f"longbridge/account_snapshots/{payload['target_id']}/{payload['source_binding']['id']}/{day}/"
        f"{finished.astimezone(timezone.utc).strftime('%H%M%S%fZ.json')}"
    )
    raw_bytes = raw if raw is not None else json.dumps(payload, separators=(",", ":")).encode()
    return _Blob(object_name, generation, len(raw_bytes) if size is None else size, raw_bytes, error)


class _Blob:
    def __init__(self, name, generation, size, raw, error=None):
        self.name = name
        self.generation = generation
        self.size = size
        self.raw = raw
        self.error = error
        self.reads = []

    def download_as_bytes(self, **kwargs):
        self.reads.append(kwargs)
        if self.error:
            raise self.error
        if kwargs.get("if_generation_match") != self.generation:
            raise RuntimeError("generation condition mismatch")
        return self.raw


class _PageIterator:
    def __init__(self, objects, page_size, before_page=None):
        self.objects = objects
        self.page_size = page_size
        self.before_page = before_page
        self.next_page_token = "next" if len(objects) > page_size else None

    @property
    def pages(self):
        def iterate():
            if self.before_page:
                self.before_page()
            yield self.objects[: self.page_size]

        return iterate()


class _Storage:
    def __init__(self, objects=()):
        self.client = self
        self.objects = list(objects)
        self.list_calls = []
        self.blob_calls = []

    def list_blobs(self, bucket, **kwargs):
        self.list_calls.append((bucket, kwargs))
        prefix = kwargs["prefix"]
        return _PageIterator(
            [obj for obj in self.objects if obj.name.startswith(prefix)],
            kwargs["page_size"],
        )

    def bucket(self, bucket):
        assert bucket == "qsl-runtime-logs-shared"
        return self

    def blob(self, name, *, generation):
        self.blob_calls.append((name, generation))
        return next(obj for obj in self.objects if obj.name == name)


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.is_redirect = 300 <= status_code < 400
        self._payload = payload
        self.closed = False

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=8192):
        if self._payload is not None:
            yield json.dumps(self._payload).encode()

    def close(self):
        self.closed = True


class _Session:
    def __init__(self, job=None, run_response=None, run_error=None):
        self.job = job or _job()
        self.run_response = run_response or _Response()
        self.run_error = run_error
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return _Response(payload=self.job)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if self.run_error:
            raise self.run_error
        return self.run_response

    def close(self):
        self.closed = True


class _Clock:
    def __init__(self, values):
        self.values = iter(values)
        self.last = values[-1]

    def __call__(self):
        return next(self.values, self.last)


class _Spies:
    def __init__(self, *, objects=(), job=None, run_error=None, post_error=None, post_response=None):
        self.storage = _Storage(objects)
        self.session = _Session(job=job, run_error=run_error)
        self.open_calls = []
        self.posts = []
        self.post_error = post_error
        self.post_response = post_response

    def open_store(self, project):
        self.open_calls.append(project)
        return self.storage

    def session_factory(self):
        return self.session

    def http_post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_error:
            raise self.post_error
        if self.post_response is not None:
            return self.post_response
        sent = json.loads(kwargs["data"])
        return _Response(
            payload={
                "ok": True,
                "stored": True,
                "target_id": sent["target_id"],
                "observation_date": sent["observation_date"],
                "observed_finished_at": sent["observed_finished_at"],
            }
        )


def _record(env=None, spies=None, *, times=None, monotonic=None, sleep=None):
    spies = spies or _Spies()
    now_reader = _Clock(times or [T0, T0 + timedelta(seconds=5)])
    clock = {"now": 0.0}

    def monotonic_fn():
        return clock["now"] if monotonic is None else monotonic()

    def sleep_fn(seconds):
        if sleep is not None:
            sleep(seconds)
        else:
            clock["now"] += seconds

    result = snapshots.record_daily_account_snapshot(
        env or _env(),
        open_store=spies.open_store,
        session_factory=spies.session_factory,
        http_post=spies.http_post,
        now_reader=now_reader,
        monotonic=monotonic_fn,
        sleep=sleep_fn,
    )
    return result, spies


def test_disabled_and_invalid_binding_do_not_make_cloud_calls():
    result, spies = _record(_env(ACCOUNT_HISTORY_RECORDING_ENABLED="false"))
    assert result.status == "disabled"
    assert spies.session.calls == [] and spies.open_calls == []

    result, spies = _record(_env(ACCOUNT_HISTORY_SERVICE_URL="http://longbridge-quant-paper-service-kcc3gcgmwq-de.a.run.app"))
    assert result.category == "config_invalid"
    assert spies.session.calls == [] and spies.open_calls == []

    result, spies = _record(_env(ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID=""))
    assert result.category == "config_invalid"
    assert spies.session.calls == [] and spies.open_calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda j: j.update(name="projects/other/locations/asia-east1/jobs/wrong"),
        lambda j: j.update(state="PAUSED"),
        lambda j: j["httpTarget"].update(httpMethod="GET"),
        lambda j: j["httpTarget"].update(uri=f"{SERVICE_URL}/run"),
        lambda j: j["httpTarget"].update(body="e30="),
        lambda j: j["httpTarget"]["oidcToken"].update(serviceAccountEmail="other@example.com"),
        lambda j: j["httpTarget"]["oidcToken"].update(audience="https://other.run.app"),
        lambda j: j["retryConfig"].update(retryCount=1),
        lambda j: j["retryConfig"].update(maxRetryDuration="1s"),
    ],
)
def test_scheduler_job_mismatch_fails_before_run_or_gcs(mutate):
    job = _job()
    mutate(job)
    result, spies = _record(spies=_Spies(job=job))
    assert result.category == "scheduler_job_mismatch"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


def test_scheduler_zero_retry_protobuf_defaults_are_accepted():
    job = _job()
    job.pop("retryConfig")
    result, spies = _record(spies=_Spies(objects=[_object(_history())]))
    assert result.status == "recorded"
    assert [call[0] for call in spies.session.calls] == ["get", "post"]


@pytest.mark.parametrize(("target_id", "state"), [("hk", "PAUSED"), ("sg", "ENABLED")])
def test_sghk_targets_use_exact_manifest_identity_and_publish_matching_history(target_id, state):
    payload = _history(target_id=target_id)
    job = _job(target_id, state=state)
    spies = _Spies(objects=[_object(payload)], job=job)
    result, spies = _record(
        _env(
            target_id,
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "published"
    expected_region = {"hk": "asia-east2", "sg": "asia-southeast1"}[target_id]
    expected_service = {"hk": "longbridge-quant-hk-service", "sg": "longbridge-quant-sg-service"}[target_id]
    assert spies.session.calls[0][1] == (
        f"https://cloudscheduler.googleapis.com/v1/projects/longbridgequant/locations/"
        f"{expected_region}/jobs/{expected_service}-probe-scheduler"
    )
    assert spies.session.calls[1][1].endswith(f"/{expected_service}-probe-scheduler:run")
    assert spies.storage.list_calls[0][1]["prefix"].startswith(
        f"longbridge/account_snapshots/{target_id}/{BINDING}/"
    )
    assert json.loads(spies.posts[0][1]["data"])["account_scope"] == {"hk": "HK", "sg": "SG"}[target_id]


def test_paused_hk_probe_is_the_only_paused_scheduler_accepted():
    hk_job = _job("hk", state="PAUSED")
    hk_result, hk_spies = _record(_env("hk"), _Spies(objects=[_object(_history(target_id="hk"))], job=hk_job))
    assert hk_result.status == "recorded"
    assert [call[0] for call in hk_spies.session.calls] == ["get", "post"]

    sg_job = _job("sg", state="PAUSED")
    sg_result, sg_spies = _record(_env("sg"), _Spies(job=sg_job))
    assert sg_result.category == "scheduler_job_mismatch"
    assert [call[0] for call in sg_spies.session.calls] == ["get"]
    assert sg_spies.open_calls == []


@pytest.mark.parametrize(("target_id", "wrong_scope"), [("hk", "SG"), ("sg", "HK")])
def test_cross_scope_snapshot_is_never_published(monkeypatch, target_id, wrong_scope):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    payload = _history(target_id=target_id, scope=wrong_scope)
    result, spies = _record(
        _env(target_id),
        _Spies(objects=[_object(payload)], job=_job(target_id, state="PAUSED" if target_id == "hk" else "ENABLED")),
    )
    assert result.category == "observation_timeout"
    assert spies.posts == []


def test_unknown_scheduler_trigger_is_attempted_once_and_never_lists():
    result, spies = _record(spies=_Spies(run_error=TimeoutError(SECRET)))
    assert result.category == "scheduler_run_unknown"
    assert [call[0] for call in spies.session.calls] == ["get", "post"]
    assert spies.open_calls == [] and spies.posts == []


def test_record_selects_latest_valid_object_and_posts_exact_original_bytes():
    older = _history(T0 + timedelta(seconds=1), T0 + timedelta(seconds=2))
    latest = _history(T0 + timedelta(seconds=3), T0 + timedelta(seconds=4))
    raw = json.dumps(latest, indent=2).encode()
    spies = _Spies(objects=[_object(older), _object(latest, raw=raw)])
    result, spies = _record(
        _env(ACCOUNT_FACTS_SYNC_ENABLED="true", ACCOUNT_FACTS_SYNC_URL=QRS_URL, ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN),
        spies,
    )
    assert result.publish_status == "published"
    assert len(spies.posts) == 1
    url, kwargs = spies.posts[0]
    assert url == QRS_URL and kwargs["data"] == raw
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == f"Bearer {QRS_TOKEN}"
    assert all(call[0] == "post" or call[0] == "get" for call in spies.session.calls)
    assert all(call[2]["timeout"] <= snapshots.HTTP_TIMEOUT_SECONDS for call in spies.session.calls)
    assert all(kwargs["timeout"] <= snapshots.GCS_TIMEOUT_SECONDS for _bucket, kwargs in spies.storage.list_calls)
    assert all(blob.reads[0]["retry"] is None for blob in spies.storage.objects if blob.reads)


@pytest.mark.parametrize(
    ("status", "receipt", "expected"),
    [
        (200, {"target_id": "other", "observation_date": "2026-09-28", "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat()}, "unknown"),
        (200, {"target_id": "paper", "observation_date": "2026-09-27", "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat()}, "unknown"),
        (200, {"target_id": "paper", "observation_date": "2026-09-28", "observed_finished_at": (T0 + timedelta(seconds=3)).isoformat()}, "unknown"),
        (302, {"target_id": "paper", "observation_date": "2026-09-28", "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat()}, "rejected"),
        (401, {"target_id": "paper", "observation_date": "2026-09-28", "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat()}, "rejected"),
        (500, {"target_id": "paper", "observation_date": "2026-09-28", "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat()}, "unknown"),
    ],
)
def test_qrs_receipt_identity_and_failure_matrix_is_single_post(status, receipt, expected):
    response = _Response(status, {"ok": True, "stored": True, **receipt})
    spies = _Spies(objects=[_object(_history())], post_response=response)
    result, spies = _record(
        _env(ACCOUNT_FACTS_SYNC_ENABLED="true", ACCOUNT_FACTS_SYNC_URL=QRS_URL, ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN),
        spies,
    )
    assert result.status == "recorded"
    assert result.publish_status == expected
    assert len(spies.posts) == 1


def test_invalid_qrs_config_is_rejected_before_scheduler_or_gcs():
    result, spies = _record(
        _env(ACCOUNT_FACTS_SYNC_ENABLED="true", ACCOUNT_FACTS_SYNC_URL="https://qrs.example.test/other", ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN)
    )
    assert result.category == "qrs_config_invalid"
    assert spies.session.calls == [] and spies.open_calls == []


def test_listing_uses_expected_source_and_both_utc_days_at_midnight():
    t0 = datetime(2026, 9, 28, 23, 59, 59, tzinfo=timezone.utc)
    started = datetime(2026, 9, 29, 0, 0, 0, tzinfo=timezone.utc)
    finished = started + timedelta(seconds=1)
    obj = _object(_history(started, finished))
    result, spies = _record(spies=_Spies(objects=[obj]), times=[t0, finished + timedelta(seconds=3)])
    prefixes = {kwargs["prefix"] for _bucket, kwargs in spies.storage.list_calls}
    assert result.status == "recorded"
    assert any(prefix.endswith("/2026-09-28/") for prefix in prefixes)
    assert any(prefix.endswith("/2026-09-29/") for prefix in prefixes)


def test_wrong_source_and_observation_started_before_trigger_are_ignored(monkeypatch):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    wrong_source = _history(binding=OTHER_BINDING)
    old = _history(T0 - timedelta(seconds=2), T0 - timedelta(seconds=1))
    result, spies = _record(spies=_Spies(objects=[_object(wrong_source), _object(old)]))
    assert result.category == "observation_timeout"
    assert spies.posts == []

    t0 = datetime(2026, 9, 28, 23, 59, 59, tzinfo=timezone.utc)
    started = t0 + timedelta(microseconds=100)
    finished = t0 + timedelta(microseconds=200)
    wrong_path = _object(_history(started, finished), path_date="2026-09-29")
    result, spies = _record(
        spies=_Spies(objects=[wrong_path]),
        times=[t0, datetime(2026, 9, 29, 0, 0, 5, tzinfo=timezone.utc)],
    )
    assert result.category == "observation_timeout"
    assert spies.posts == []


def test_started_time_controls_fifteen_minute_freshness():
    now = T0 + timedelta(minutes=20)
    record = _history(T0, now)
    assert not snapshots._is_fresh(record, now)


def test_fake_storage_read_crossing_deadline_cannot_be_accepted(monkeypatch):
    obj = _object(_history())
    spies = _Spies(objects=[obj])
    ticks = {"now": 0.0}

    def mono():
        return ticks["now"]

    original = obj.download_as_bytes

    def slow_read(**kwargs):
        ticks["now"] += snapshots.WAIT_SECONDS + 1
        return original(**kwargs)

    obj.download_as_bytes = slow_read
    result, spies = _record(spies=spies, monotonic=mono)
    assert result.category == "observation_timeout"
    assert spies.posts == []


def test_scheduler_and_gcs_calls_are_bounded_and_generation_pinned():
    obj = _object(_history())
    result, spies = _record(spies=_Spies(objects=[obj]))
    assert result.status == "recorded"
    assert spies.session.calls[0][2]["timeout"] <= snapshots.HTTP_TIMEOUT_SECONDS
    assert spies.session.calls[1][2]["timeout"] <= snapshots.HTTP_TIMEOUT_SECONDS
    assert spies.storage.list_calls[0][1]["retry"] is None
    assert spies.storage.list_calls[0][1]["max_results"] == snapshots.MAX_OBJECTS_PER_DAY + 1
    read = obj.reads[0]
    assert read["if_generation_match"] == obj.generation
    assert read["retry"] is None and read["end"] == snapshots.MAX_OBJECT_BYTES - 1


def test_generation_change_is_fail_closed():
    obj = _object(_history(), error=RuntimeError("conditionNotMet generation changed"))
    result, spies = _record(spies=_Spies(objects=[obj]))
    assert result.category == "generation_changed"
    assert spies.posts == []


def test_oversized_or_truncated_listing_never_posts():
    oversized = _object(_history(), size=snapshots.MAX_OBJECT_BYTES + 1)
    result, spies = _record(spies=_Spies(objects=[oversized]))
    assert result.category == "gcs_object_too_large"
    assert spies.posts == []

    many = []
    for index in range(snapshots.MAX_OBJECTS_PER_DAY + 1):
        finished = T0 + timedelta(seconds=2, microseconds=index)
        many.append(_object(_history(T0 + timedelta(seconds=1), finished)))
    result, spies = _record(spies=_Spies(objects=many))
    assert result.category == "gcs_listing_truncated"
    assert spies.posts == []

    many = []
    for index in range(snapshots.MAX_OBJECTS_PER_DAY + 2):
        finished = T0 + timedelta(seconds=2, microseconds=index)
        many.append(_object(_history(T0 + timedelta(seconds=1), finished)))
    result, spies = _record(spies=_Spies(objects=many))
    assert result.category == "gcs_listing_truncated"
    assert spies.posts == []


def test_slow_gcs_page_crossing_deadline_is_not_consumed_or_posted():
    obj = _object(_history())
    storage = _Storage([obj])
    spies = _Spies()
    spies.storage = storage
    ticks = {"now": 0.0}
    original_list = storage.list_blobs

    def slow_list(bucket, **kwargs):
        result = original_list(bucket, **kwargs)
        result.before_page = lambda: ticks.update(now=snapshots.WAIT_SECONDS + 1)
        return result

    storage.list_blobs = slow_list
    result, spies = _record(spies=spies, monotonic=lambda: ticks["now"])
    assert result.category == "observation_timeout"
    assert obj.reads == [] and spies.posts == []


def test_cash_currency_set_may_differ_from_balance_currency_set():
    payload = _history(
        balances=[{"currency": "USD", "net_assets": "10", "total_cash": "3"}],
        cash=[{"currency": "HKD", "available_cash": "1", "frozen_cash": "0", "settling_cash": "0"}],
    )
    result, spies = _record(spies=_Spies(objects=[_object(payload)]))
    assert result.status == "recorded"
    assert spies.posts == []


def test_qrs_unknown_is_reported_once_and_secrets_are_redacted(capsys):
    spies = _Spies(objects=[_object(_history())], post_error=TimeoutError(f"{SECRET} {QRS_TOKEN}"))
    code = snapshots.main(
        [],
        environ=_env(ACCOUNT_FACTS_SYNC_ENABLED="true", ACCOUNT_FACTS_SYNC_URL=QRS_URL, ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN),
        open_store=spies.open_store,
        session_factory=spies.session_factory,
        http_post=spies.http_post,
        now_reader=_Clock([T0, T0 + timedelta(seconds=5)]),
    )
    output = capsys.readouterr().out
    assert code == 1
    assert output.strip() == "record=recorded account_facts_publish=unknown"
    assert SECRET not in output and QRS_TOKEN not in output
    assert len(spies.posts) == 1


def test_authorized_session_disables_transport_retries(monkeypatch):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    class Credentials:
        pass

    monkeypatch.setattr(google.auth, "default", lambda **_kwargs: (Credentials(), "project"))
    session = snapshots._authorized_session()
    try:
        assert isinstance(session, AuthorizedSession)
        assert session._max_refresh_attempts == 0
        assert session.adapters["https://"].max_retries.total == 0
    finally:
        session.close()
