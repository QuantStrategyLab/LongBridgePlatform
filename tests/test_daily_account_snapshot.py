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
        "RUNTIME_TARGET_ENABLED": "true",
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
    if target_id == "sg":
        job.update(schedule="35 9,15 * * 1-5", timeZone="America/New_York", description="synthetic control field")
    elif target_id == "hk":
        job.update(schedule="35 9,15 * * 1-5", timeZone="Asia/Hong_Kong", description="synthetic control field")
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
    def __init__(self, objects, page_size, before_page=None, prefixes=()):
        self.objects = objects
        self.page_size = page_size
        self.before_page = before_page
        self.prefixes = set(prefixes)
        self.next_page_token = "next" if max(len(objects), len(self.prefixes)) > page_size else None

    @property
    def pages(self):
        def iterate():
            if self.before_page:
                self.before_page()
            yield _Page(self.objects[: self.page_size], self.prefixes)

        return iterate()


class _Page(list):
    def __init__(self, objects, prefixes):
        super().__init__(objects)
        self.prefixes = prefixes


class _Storage:
    def __init__(self, objects=(), *, before_list=None, list_error=None):
        self.client = self
        self.objects = list(objects)
        self.list_calls = []
        self.blob_calls = []
        self.before_list = before_list
        self.list_error = list_error
        self.events = None

    def list_blobs(self, bucket, **kwargs):
        if self.events is not None:
            self.events.append("gcs_list")
        self.list_calls.append((bucket, kwargs))
        if self.before_list is not None:
            self.before_list(self, kwargs)
        if self.list_error:
            raise self.list_error
        prefix = kwargs["prefix"]
        if kwargs.get("delimiter") == "/":
            prefixes = {
                f"{prefix}{obj.name[len(prefix):].split('/', 1)[0]}/"
                for obj in self.objects
                if obj.name.startswith(prefix) and "/" in obj.name[len(prefix):]
            }
            return _PageIterator([], kwargs["page_size"], prefixes=prefixes)
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
    def __init__(
        self,
        job=None,
        run_response=None,
        run_error=None,
        resume_response=None,
        resume_error=None,
        resume_applies=True,
        pause_response=None,
        pause_error=None,
        pause_applies=True,
        unexpected_resume_attempt=False,
        change_control_on_resume=False,
        read_error_number=None,
        read_error_numbers=(),
        events=None,
    ):
        self.job = job or _job()
        self.run_response = run_response or _Response()
        self.run_error = run_error
        self.resume_response = resume_response or _Response()
        self.resume_error = resume_error
        self.resume_applies = resume_applies
        self.pause_response = pause_response or _Response()
        self.pause_error = pause_error
        self.pause_applies = pause_applies
        self.unexpected_resume_attempt = unexpected_resume_attempt
        self.change_control_on_resume = change_control_on_resume
        self.read_error_number = read_error_number
        self.read_error_numbers = set(read_error_numbers)
        self.events = events
        self.calls = []
        self.read_count = 0
        self.closed = False

    def get(self, url, **kwargs):
        if self.events is not None:
            self.events.append("scheduler_get")
        self.calls.append(("get", url, kwargs))
        self.read_count += 1
        if self.read_count == self.read_error_number or self.read_count in self.read_error_numbers:
            raise TimeoutError("synthetic scheduler read timeout")
        return _Response(payload=self.job)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if url.endswith(":resume"):
            if self.events is not None:
                self.events.append("scheduler_resume")
            if self.resume_applies:
                self.job["state"] = "ENABLED"
                if self.unexpected_resume_attempt:
                    self.job["lastAttemptTime"] = (T0 + timedelta(seconds=1)).isoformat()
                if self.change_control_on_resume:
                    self.job["description"] = "changed synthetic control field"
            if self.resume_error:
                raise self.resume_error
            return self.resume_response
        if url.endswith(":pause"):
            if self.events is not None:
                self.events.append("scheduler_pause")
            if self.pause_applies:
                self.job["state"] = "PAUSED"
            if self.pause_error:
                raise self.pause_error
            return self.pause_response
        if self.events is not None:
            self.events.append("scheduler_run")
        if self.run_error:
            raise self.run_error
        self.job["lastAttemptTime"] = (T0 + timedelta(seconds=8)).isoformat()
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
    def __init__(
        self, *, objects=(), job=None, run_error=None, post_error=None, post_response=None,
        store_error=None, storage_before_list=None, storage_list_error=None, **session_options,
    ):
        self.events = []
        self.storage = _Storage(objects, before_list=storage_before_list, list_error=storage_list_error)
        self.storage.events = self.events
        self.session = _Session(job=job, run_error=run_error, events=self.events, **session_options)
        self.open_calls = []
        self.open_state_at_open = []
        self.scheduler_call_count_at_open = []
        self.posts = []
        self.post_error = post_error
        self.post_response = post_response
        self.store_error = store_error

    def open_store(self, project):
        self.events.append("storage_open")
        self.open_calls.append(project)
        self.open_state_at_open.append(self.session.job.get("state"))
        self.scheduler_call_count_at_open.append(len(self.session.calls))
        if self.store_error:
            raise self.store_error
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


class _PermissionSession:
    def __init__(self, *, status=200, payload=None, error=None):
        self.status = status
        self.payload = payload
        self.error = error
        self.calls = []
        self.closed = False

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return _Response(self.status, self.payload)

    def close(self):
        self.closed = True


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


@pytest.mark.parametrize(("target_id", "state"), [("hk", "ENABLED"), ("sg", "ENABLED")])
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


def test_paused_scheduler_is_rejected_before_run_or_gcs_for_paper():
    result, spies = _record(
        _env("paper"),
        _Spies(job=_job("paper", state="PAUSED")),
    )

    assert result.category == "scheduler_job_mismatch"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


@pytest.mark.parametrize("target_id", ["sg", "hk"])
def test_paused_live_observes_delayed_archive_before_restoring_then_publishes(target_id):
    payload = _history(T0 + timedelta(seconds=6), T0 + timedelta(seconds=7), target_id=target_id)
    raw = json.dumps(payload, indent=2).encode()
    delayed_object = _object(payload, raw=raw)
    list_count = {"value": 0}

    def reveal_after_first_empty_list(storage, _kwargs):
        list_count["value"] += 1
        if list_count["value"] == 2:
            storage.objects.append(delayed_object)

    job = _job(target_id, state="PAUSED", lastAttemptTime=(T0 - timedelta(days=2)).isoformat())
    original = json.loads(json.dumps(job))
    spies = _Spies(objects=[], job=job, storage_before_list=reveal_after_first_empty_list)
    result, spies = _record(
        _env(
            target_id,
            RUNTIME_TARGET_ENABLED="false",
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies,
        times=[T0, T0 + timedelta(seconds=5), T0 + timedelta(seconds=10)],
    )

    assert result.status == "recorded"
    assert result.publish_status == "published"
    assert [call[0] for call in spies.session.calls] == ["get", "post", "get", "post", "post", "get"]
    actions = [
        "get" if method == "get" else url.rsplit(":", 1)[-1]
        for method, url, _kwargs in spies.session.calls
    ]
    assert actions == ["get", "resume", "get", "run", "pause", "get"]
    assert spies.session.job["state"] == "PAUSED"
    for field, value in original.items():
        if field not in snapshots._SCHEDULER_OUTPUT_FIELDS:
            assert spies.session.job[field] == value
    assert spies.storage.list_calls
    assert spies.open_state_at_open == ["ENABLED"]
    assert spies.scheduler_call_count_at_open == [4]
    assert len(spies.posts) == 1 and spies.posts[0][1]["data"] == raw
    assert list_count["value"] == 2, "RunJob ACK precedes asynchronous archive visibility"
    assert spies.events.index("scheduler_run") < spies.events.index("storage_open")
    assert spies.events.index("gcs_list") < spies.events.index("scheduler_pause")
    assert spies.events.index("scheduler_pause") < len(spies.events) - 1
    assert spies.events[-1] == "scheduler_get"


@pytest.mark.parametrize(
    ("spies_kwargs", "expected"),
    [
        ({"store_error": RuntimeError(SECRET)}, "gcs_read_failed"),
        ({"storage_list_error": RuntimeError(SECRET)}, "gcs_list_failed"),
    ],
)
def test_paused_sg_storage_failures_restore_before_returning(spies_kwargs, expected):
    spies = _Spies(job=_job("sg", state="PAUSED"), **spies_kwargs)
    result, spies = _record(_env("sg", RUNTIME_TARGET_ENABLED="false"), spies)

    assert result.category == expected
    actions = [call[1].rsplit(":", 1)[-1] for call in spies.session.calls if call[0] == "post"]
    assert actions == ["resume", "run", "pause"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.events.index("scheduler_run") < spies.events.index("scheduler_pause")
    assert spies.events[-1] == "scheduler_get"
    assert spies.posts == []


def test_paused_sg_observation_timeout_restores_before_returning(monkeypatch):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    spies = _Spies(job=_job("sg", state="PAUSED"))
    result, spies = _record(_env("sg", RUNTIME_TARGET_ENABLED="false"), spies)

    assert result.category == "observation_timeout"
    actions = [call[1].rsplit(":", 1)[-1] for call in spies.session.calls if call[0] == "post"]
    assert actions == ["resume", "run", "pause"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.events.index("gcs_list") < spies.events.index("scheduler_pause")
    assert spies.posts == []


def test_paused_sg_deadline_includes_storage_initialization(monkeypatch):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    ticks = {"now": 0.0}
    spies = _Spies(job=_job("sg", state="PAUSED"))
    original_open = spies.open_store

    def slow_open(project):
        storage = original_open(project)
        ticks["now"] = snapshots.WAIT_SECONDS + 1
        return storage

    spies.open_store = slow_open
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        spies,
        monotonic=lambda: ticks["now"],
    )

    assert result.category == "observation_timeout"
    actions = [call[1].rsplit(":", 1)[-1] for call in spies.session.calls if call[0] == "post"]
    assert actions == ["resume", "run", "pause"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.open_calls == ["longbridgequant"]
    assert spies.storage.list_calls == []
    assert spies.posts == []


def test_non_sg_observation_budget_starts_after_storage_initialization(monkeypatch):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    ticks = {"now": 0.0}
    spies = _Spies(objects=[_object(_history())])
    original_open = spies.open_store

    def slow_open(project):
        storage = original_open(project)
        ticks["now"] = snapshots.WAIT_SECONDS + 1
        return storage

    spies.open_store = slow_open
    result, spies = _record(_env("paper"), spies, monotonic=lambda: ticks["now"])

    assert result.status == "recorded"
    assert spies.storage.list_calls
    assert spies.session.job["state"] == "ENABLED"
    assert spies.posts == []


def test_paused_sg_restore_failure_blocks_publish_after_archive_is_observed():
    payload = _history(target_id="sg")
    spies = _Spies(
        objects=[_object(payload)],
        job=_job("sg", state="PAUSED"),
        pause_response=_Response(500),
    )
    result, spies = _record(
        _env(
            "sg",
            RUNTIME_TARGET_ENABLED="false",
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies,
    )

    assert result.category == "scheduler_pause_unknown"
    assert spies.storage.list_calls
    assert spies.events.index("gcs_list") < spies.events.index("scheduler_pause")
    assert spies.session.job["state"] == "PAUSED"
    assert spies.posts == []


@pytest.mark.parametrize("target_id", ["sg", "hk"])
@pytest.mark.parametrize("runtime_flag", ["true", "False", " false", ""])
def test_paused_live_requires_exact_false_runtime_flag(target_id, runtime_flag):
    result, spies = _record(
        _env(target_id, RUNTIME_TARGET_ENABLED=runtime_flag),
        _Spies(job=_job(target_id, state="PAUSED")),
    )
    assert result.category == "runtime_target_not_disabled"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


@pytest.mark.parametrize("target_id", ["sg", "hk"])
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda job: job.update(schedule="35 9,16 * * 1-5"), "scheduler_schedule_mismatch"),
        (lambda job: job.update(timeZone="UTC"), "scheduler_schedule_mismatch"),
        (lambda job: job.update(name="projects/other/locations/asia-southeast1/jobs/other"), "scheduler_job_mismatch"),
        (lambda job: job["httpTarget"].update(httpMethod="GET"), "scheduler_job_mismatch"),
        (lambda job: job["httpTarget"].update(uri=job["httpTarget"]["uri"].replace("/probe", "/run")), "scheduler_job_mismatch"),
        (lambda job: job["httpTarget"].update(body="e30="), "scheduler_job_mismatch"),
        (lambda job: job["httpTarget"]["oidcToken"].update(serviceAccountEmail="other@example.com"), "scheduler_job_mismatch"),
        (lambda job: job["httpTarget"]["oidcToken"].update(audience="https://other.run.app"), "scheduler_job_mismatch"),
        (lambda job: job["retryConfig"].update(retryCount=1), "scheduler_job_mismatch"),
        (lambda job: job["retryConfig"].update(maxRetryDuration="1s"), "scheduler_job_mismatch"),
    ],
)
def test_paused_live_mismatched_job_or_schedule_fails_before_mutation(target_id, mutate, expected):
    job = _job(target_id, state="PAUSED")
    mutate(job)
    result, spies = _record(
        _env(target_id, RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=job),
    )
    assert result.category == expected
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


@pytest.mark.parametrize(
    "near_trigger",
    [
        datetime(2026, 9, 28, 13, 34, 59, tzinfo=timezone.utc),
        datetime(2026, 9, 28, 13, 35, 1, tzinfo=timezone.utc),
    ],
)
def test_paused_sg_natural_schedule_guard_blocks_within_ten_minutes(near_trigger):
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=_job("sg", state="PAUSED")),
        times=[near_trigger],
    )
    assert result.category == "scheduler_natural_window"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


@pytest.mark.parametrize(
    "near_trigger",
    [
        # Asia/Hong_Kong is UTC+8; 09:35 HKT == 01:35 UTC.
        datetime(2026, 9, 28, 1, 34, 59, tzinfo=timezone.utc),
        datetime(2026, 9, 28, 1, 35, 1, tzinfo=timezone.utc),
    ],
)
def test_paused_hk_natural_schedule_guard_blocks_within_ten_minutes(near_trigger):
    result, spies = _record(
        _env("hk", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=_job("hk", state="PAUSED")),
        times=[near_trigger],
    )
    assert result.category == "scheduler_natural_window"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


def test_paused_sg_resume_rejection_does_not_run_or_publish():
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(
            job=_job("sg", state="PAUSED"),
            resume_response=_Response(409),
            resume_applies=False,
        ),
    )
    assert result.category == "scheduler_resume_rejected"
    assert [call[0] for call in spies.session.calls] == ["get", "post"]
    assert spies.open_calls == [] and spies.posts == []


@pytest.mark.parametrize("resume_applies", [False, True])
def test_paused_sg_unknown_resume_reads_once_and_never_runs_or_publishes(resume_applies):
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(
            job=_job("sg", state="PAUSED"),
            resume_error=TimeoutError(SECRET),
            resume_applies=resume_applies,
        ),
    )
    assert result.category == "scheduler_resume_unknown"
    assert [call[0] for call in spies.session.calls] == (
        ["get", "post", "get"] if not resume_applies else ["get", "post", "get", "post", "get"]
    )
    assert all(not call[1].endswith(":run") for call in spies.session.calls)
    assert spies.open_calls == [] and spies.posts == []
    assert spies.session.job["state"] == "PAUSED"


def test_paused_sg_confirmed_resume_readback_failure_contains_with_one_pause_and_no_run():
    original = _job("sg", state="PAUSED", lastAttemptTime=(T0 - timedelta(days=2)).isoformat())
    expected = json.loads(json.dumps(original))
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=original, read_error_number=2),
    )
    assert result.category == "scheduler_resume_readback_unknown"
    actions = [
        "get" if method == "get" else url.rsplit(":", 1)[-1]
        for method, url, _kwargs in spies.session.calls
    ]
    assert actions == ["get", "resume", "get", "pause", "get"]
    assert spies.session.job["state"] == "PAUSED"
    for field, value in expected.items():
        if field not in snapshots._SCHEDULER_OUTPUT_FIELDS:
            assert spies.session.job[field] == value
    assert spies.open_calls == [] and spies.posts == []


@pytest.mark.parametrize(
    ("pause_response", "pause_applies", "expected_category", "expected_state"),
    [
        (_Response(500), True, "scheduler_pause_unknown", "PAUSED"),
        (_Response(409), False, "scheduler_pause_rejected", "ENABLED"),
    ],
)
def test_paused_sg_readback_containment_pause_failure_is_not_retried_or_published(
    pause_response, pause_applies, expected_category, expected_state
):
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(
            job=_job("sg", state="PAUSED"),
            read_error_number=2,
            pause_response=pause_response,
            pause_applies=pause_applies,
        ),
    )
    assert result.category == expected_category
    actions = [
        "get" if method == "get" else url.rsplit(":", 1)[-1]
        for method, url, _kwargs in spies.session.calls
    ]
    assert actions == ["get", "resume", "get", "pause", "get"]
    assert spies.session.job["state"] == expected_state
    assert "run" not in actions
    assert spies.open_calls == [] and spies.posts == []


def test_paused_sg_containment_readback_failure_is_explicit_and_not_retried():
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(
            job=_job("sg", state="PAUSED"),
            read_error_numbers={2, 3},
        ),
    )
    assert result.category == "scheduler_restore_readback_unknown"
    actions = [
        "get" if method == "get" else url.rsplit(":", 1)[-1]
        for method, url, _kwargs in spies.session.calls
    ]
    assert actions == ["get", "resume", "get", "pause", "get"]
    assert spies.session.job["state"] == "PAUSED"
    assert "run" not in actions
    assert spies.open_calls == [] and spies.posts == []


def test_paused_sg_unexpected_attempt_is_paused_and_never_run_or_publish():
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=_job("sg", state="PAUSED"), unexpected_resume_attempt=True),
    )
    assert result.category == "scheduler_unexpected_attempt"
    assert [call[0] for call in spies.session.calls] == ["get", "post", "get", "post", "get"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.open_calls == [] and spies.posts == []


def test_paused_sg_control_drift_is_not_overwritten_or_published():
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=_job("sg", state="PAUSED"), change_control_on_resume=True),
    )
    assert result.category == "scheduler_restore_mismatch"
    assert [call[0] for call in spies.session.calls] == ["get", "post", "get", "post", "get"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.session.job["description"] == "changed synthetic control field"
    assert spies.open_calls == [] and spies.posts == []


def test_paused_sg_unknown_run_is_not_retried_and_restores_before_returning():
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(job=_job("sg", state="PAUSED"), run_error=TimeoutError(SECRET)),
    )
    assert result.category == "scheduler_run_unknown"
    actions = [call[1].rsplit(":", 1)[-1] for call in spies.session.calls if call[0] == "post"]
    assert actions == ["resume", "run", "pause"]
    assert [call[0] for call in spies.session.calls] == ["get", "post", "get", "post", "post", "get"]
    assert spies.session.job["state"] == "PAUSED"
    assert spies.open_calls == [] and spies.posts == []


@pytest.mark.parametrize(
    ("pause_response", "pause_applies", "expected"),
    [(_Response(500), True, "scheduler_pause_unknown"), (_Response(409), False, "scheduler_pause_rejected")],
)
def test_paused_sg_pause_failure_never_publishes(pause_response, pause_applies, expected):
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
        _Spies(
            job=_job("sg", state="PAUSED"),
            pause_response=pause_response,
            pause_applies=pause_applies,
        ),
    )
    assert result.category == expected
    assert [call[0] for call in spies.session.calls] == ["get", "post", "get", "post", "post", "get"]
    assert spies.open_calls == ["longbridgequant"]
    assert spies.events.index("gcs_list") < spies.events.index("scheduler_pause")
    assert spies.posts == []


@pytest.mark.parametrize(("target_id", "wrong_scope"), [("hk", "SG"), ("sg", "HK")])
def test_cross_scope_snapshot_is_never_published(monkeypatch, target_id, wrong_scope):
    monkeypatch.setattr(snapshots, "WAIT_SECONDS", 1)
    payload = _history(target_id=target_id, scope=wrong_scope)
    result, spies = _record(
        _env(target_id),
        _Spies(objects=[_object(payload)], job=_job(target_id, state="ENABLED")),
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


def test_sg_permission_inspection_returns_only_fixed_project_permission_booleans():
    granted = [
        "cloudscheduler.jobs.get",
        "cloudscheduler.jobs.enable",
    ]
    session = _PermissionSession(payload={"permissions": granted})
    result = snapshots.inspect_sg_probe_permissions(
        _env("sg"), session_factory=lambda: session
    )

    assert result == {
        "context": "project",
        "cloudscheduler.jobs.get": True,
        "cloudscheduler.jobs.run": False,
        "cloudscheduler.jobs.enable": True,
        "cloudscheduler.jobs.pause": False,
    }
    assert len(session.calls) == 1
    url, kwargs = session.calls[0]
    assert url == "https://cloudresourcemanager.googleapis.com/v1/projects/longbridgequant:testIamPermissions"
    assert kwargs == {
        "json": {"permissions": list(snapshots._SG_PROBE_REQUIRED_PERMISSIONS)},
        "timeout": snapshots.HTTP_TIMEOUT_SECONDS,
        "allow_redirects": False,
    }
    assert session.closed


def test_sg_permission_inspection_accepts_empty_grants_as_four_denials():
    result = snapshots.inspect_sg_probe_permissions(
        _env("sg"), session_factory=lambda: _PermissionSession(payload={})
    )
    assert result == {
        "context": "project",
        "cloudscheduler.jobs.get": False,
        "cloudscheduler.jobs.run": False,
        "cloudscheduler.jobs.enable": False,
        "cloudscheduler.jobs.pause": False,
    }
    assert all(type(value) is bool for key, value in result.items() if key != "context")


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"permissions": "cloudscheduler.jobs.get"},
        {"permissions": [1]},
        {"permissions": ["cloudscheduler.jobs.delete"]},
        {"permissions": ["cloudscheduler.jobs.get", "cloudscheduler.jobs.get"]},
    ],
)
def test_sg_permission_inspection_rejects_invalid_permission_response(payload):
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots.inspect_sg_probe_permissions(
            _env("sg"), session_factory=lambda: _PermissionSession(payload=payload)
        )
    assert raised.value.category == "permission_inspection_response_invalid"


@pytest.mark.parametrize("status", [302, 403, 500])
def test_sg_permission_inspection_reports_non_success_status_without_body(status):
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots.inspect_sg_probe_permissions(
            _env("sg"),
            session_factory=lambda: _PermissionSession(status=status, payload={"sensitive": SECRET}),
        )
    assert raised.value.category == "permission_inspection_unknown"


def test_sg_permission_inspection_rejects_other_target_before_session_creation():
    calls = []
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots.inspect_sg_probe_permissions(
            _env("paper"), session_factory=lambda: calls.append("created")
        )
    assert raised.value.category == "permission_inspection_target_invalid"
    assert calls == []


def test_permission_inspection_cli_does_not_call_record_or_publish(capsys):
    session = _PermissionSession(payload={"permissions": ["cloudscheduler.jobs.get"]})
    code = snapshots.main(
        ["--inspect-sg-probe-permissions"],
        environ=_env("sg"),
        session_factory=lambda: session,
        open_store=lambda *_args: pytest.fail("inspection must not read GCS"),
        http_post=lambda *_args, **_kwargs: pytest.fail("inspection must not publish"),
        now_reader=lambda: pytest.fail("inspection must not run the probe"),
    )
    output = capsys.readouterr().out
    assert code == 0
    assert json.loads(output) == {
        "context": "project",
        "cloudscheduler.jobs.get": True,
        "cloudscheduler.jobs.run": False,
        "cloudscheduler.jobs.enable": False,
        "cloudscheduler.jobs.pause": False,
    }
    assert SECRET not in output and "longbridgequant" not in output
    assert len(session.calls) == 1 and session.closed


def test_permission_inspection_cli_redacts_unknown_transport_details(capsys):
    session = _PermissionSession(error=TimeoutError(SECRET))
    code = snapshots.main(
        ["--inspect-sg-probe-permissions"],
        environ=_env("sg"),
        session_factory=lambda: session,
    )
    output = capsys.readouterr().out
    assert code == 1
    assert output.strip() == "permission_inspection=error:permission_inspection_unknown"
    assert SECRET not in output
    assert len(session.calls) == 1 and session.closed


def test_archive_inspection_uses_expected_source_and_same_candidate_validator():
    payload = _history(started=T0 + timedelta(seconds=1), finished=T0 + timedelta(seconds=2), target_id="sg")
    storage = _Storage([_object(payload)])
    result = snapshots.inspect_archived_account_snapshot(
        _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="sg",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(seconds=4)).isoformat(),
        open_store=lambda project: storage if project == "longbridgequant" else pytest.fail("wrong project"),
        now_reader=lambda: T0 + timedelta(seconds=5),
        monotonic=lambda: 0.0,
    )
    assert result["inspection"] == "complete"
    assert result["candidate_count"] == result["historical_window_match_count"] == 1
    assert result["source_binding_matches"] is True
    assert result["observations"] == [
        {
            "historical_window_match": True,
            "current_stale": False,
            "reason": "historical_match_currently_fresh",
            "source_binding_matches": True,
            "scope_matches": True,
            "target_matches": True,
            "schema_matches": True,
            "trigger_time_matches": True,
            "object_path_matches": True,
            "observed_started_at": (T0 + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
            "balance_currency_rows": 1,
            "cash_currency_rows": 1,
        }
    ]
    assert all(call[1].get("retry") is None for call in storage.list_calls if "retry" in call[1])
    assert storage.blob_calls
    assert not any(call[1].get("upload") for call in storage.list_calls)


def test_archive_inspection_reports_source_binding_mismatch_without_disclosing_it():
    payload = _history(
        started=T0 + timedelta(seconds=1),
        finished=T0 + timedelta(seconds=2),
        target_id="sg",
        binding=OTHER_BINDING,
    )
    result = snapshots.inspect_archived_account_snapshot(
        _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="sg",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(seconds=4)).isoformat(),
        open_store=lambda _project: _Storage([_object(payload)]),
        now_reader=lambda: T0 + timedelta(seconds=5),
        monotonic=lambda: 0.0,
    )
    observation = result["observations"][0]
    assert result["candidate_count"] == 1 and result["historical_window_match_count"] == 0
    assert result["source_binding_matches"] is False
    assert observation["reason"] == "source_binding_mismatch"
    assert observation["source_binding_matches"] is False
    assert OTHER_BINDING not in json.dumps(result)


def test_archive_inspection_reuses_trigger_and_freshness_validation():
    old = _history(
        started=T0 - timedelta(seconds=1),
        finished=T0 + timedelta(seconds=1),
        target_id="sg",
    )
    result = snapshots.inspect_archived_account_snapshot(
        _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="sg",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(seconds=4)).isoformat(),
        open_store=lambda _project: _Storage([_object(old)]),
        now_reader=lambda: T0 + timedelta(seconds=5),
        monotonic=lambda: 0.0,
    )
    assert result["historical_window_match_count"] == 0
    assert result["observations"][0]["reason"] == "gcs_object_invalid"
    assert result["observations"][0]["source_binding_matches"] is True
    assert result["observations"][0]["trigger_time_matches"] is False


def test_archive_inspection_distinguishes_historical_window_match_from_current_staleness():
    payload = _history(
        started=T0 + timedelta(seconds=46),
        finished=T0 + timedelta(seconds=47),
        target_id="sg",
    )
    result = snapshots.inspect_archived_account_snapshot(
        _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="sg",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(minutes=4)).isoformat(),
        open_store=lambda _project: _Storage([_object(payload)]),
        now_reader=lambda: T0 + timedelta(minutes=20),
        monotonic=lambda: 0.0,
    )
    observation = result["observations"][0]
    assert result["historical_window_match_count"] == 1
    assert result["currently_stale_count"] == 1
    assert observation["historical_window_match"] is True
    assert observation["current_stale"] is True
    assert observation["reason"] == "historical_match_currently_stale"
    assert all(observation[key] is True for key in (
        "source_binding_matches", "scope_matches", "target_matches", "schema_matches",
        "trigger_time_matches", "object_path_matches",
    ))


def test_archive_inspection_accepts_paper_scope_and_target():
    payload = _history(
        started=T0 + timedelta(seconds=1),
        finished=T0 + timedelta(seconds=2),
        target_id="paper",
    )
    result = snapshots.inspect_archived_account_snapshot(
        _env("paper", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="paper",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(seconds=4)).isoformat(),
        open_store=lambda _project: _Storage([_object(payload)]),
        now_reader=lambda: T0 + timedelta(seconds=5),
        monotonic=lambda: 0.0,
    )
    observation = result["observations"][0]
    assert result["target"] == "paper"
    assert observation["historical_window_match"] is True
    assert observation["scope_matches"] is True
    assert observation["target_matches"] is True


def test_archive_inspection_rejects_cli_target_environment_scope_mismatch():
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots.inspect_archived_account_snapshot(
            _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
            target="paper",
            triggered_at=T0.isoformat(),
            completed_at=(T0 + timedelta(seconds=4)).isoformat(),
            open_store=lambda *_args: pytest.fail("target mismatch must stop before storage"),
            now_reader=lambda: T0 + timedelta(seconds=5),
        )
    assert raised.value.category == "inspection_target_invalid"


def test_archive_inspection_cli_rejects_non_whitelisted_target(capsys):
    code = snapshots.main(
        [
            "--inspect-archived-account-snapshot", "--target", "hk",
            "--triggered-at", T0.isoformat(), "--completed-at", (T0 + timedelta(seconds=4)).isoformat(),
        ],
        environ=_env("hk", GITHUB_EVENT_NAME="workflow_dispatch"),
        open_store=lambda *_args: pytest.fail("non-whitelisted target must not read storage"),
        now_reader=lambda: T0 + timedelta(seconds=5),
    )
    assert code == 1
    assert capsys.readouterr().out.strip() == "error: config_invalid"


@pytest.mark.parametrize(
    ("target_id", "event"),
    [("paper", "schedule"), ("sg", "schedule")],
)
def test_archive_inspection_rejects_other_target_or_non_manual_before_storage(target_id, event):
    opened = []
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots.inspect_archived_account_snapshot(
            _env(target_id, GITHUB_EVENT_NAME=event),
            target=target_id,
            triggered_at=T0.isoformat(),
            completed_at=(T0 + timedelta(seconds=4)).isoformat(),
            open_store=lambda *_args: opened.append("opened"),
            now_reader=lambda: T0 + timedelta(seconds=5),
        )
    assert raised.value.category in {"inspection_not_manual", "inspection_target_invalid"}
    assert opened == []


def test_history_validator_accepts_optional_financing_and_rejects_malformed():
    started = T0 + timedelta(seconds=1)
    finished = T0 + timedelta(seconds=2)
    config = snapshots._Config(
        "longbridgequant",
        "asia-east1",
        "service",
        SERVICE_URL,
        PREFIX,
        "qsl-runtime-logs-shared",
        "longbridge/account_snapshots",
        "sg",
        "SG",
        BINDING,
        "job",
        "resource",
    )
    good = _history(started=started, finished=finished, target_id="sg")
    good["financing"] = [{"currency": "USD", "buy_power": "10.00", "risk_level": "1"}]
    candidate = {
        "name": (
            f"longbridge/account_snapshots/sg/{BINDING}/{started.date().isoformat()}/"
            f"{finished.astimezone(timezone.utc).strftime('%H%M%S%fZ.json')}"
        )
    }
    snapshots._validate_history_object(good, config, candidate, T0, T0 + timedelta(seconds=5))

    bad = dict(good)
    bad["financing"] = [{"currency": "USD", "buy_power": "10", "extra": "nope"}]
    with pytest.raises(snapshots._Rejected) as raised:
        snapshots._validate_history_object(bad, config, candidate, T0, T0 + timedelta(seconds=5))
    assert raised.value.category == "gcs_object_invalid"

    result = snapshots.inspect_archived_account_snapshot(
        _env("sg", GITHUB_EVENT_NAME="workflow_dispatch"),
        target="sg",
        triggered_at=T0.isoformat(),
        completed_at=(T0 + timedelta(seconds=4)).isoformat(),
        open_store=lambda _project: _Storage([_object(good)]),
        now_reader=lambda: T0 + timedelta(seconds=5),
        monotonic=lambda: 0.0,
    )
    assert result["observations"][0]["schema_matches"] is True
    assert result["observations"][0]["historical_window_match"] is True


# The independent observer never uses the Scheduler/GCS recorder or broker SDK.
def _independent_env(**overrides):
    value = _env(
        "hk",
        RUNTIME_TARGET_ENABLED="false",
        ACCOUNT_HISTORY_OBSERVATION_MODE="independent_get",
        SNAPSHOT_SERVICE="longbridge-quant-hk-service",
        SNAPSHOT_REGION="asia-east2",
        ACCOUNT_HISTORY_REQUESTED_TARGET="hk",
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REF="refs/heads/main",
        GITHUB_WORKFLOW_REF="QuantStrategyLab/LongBridgePlatform/.github/workflows/execution-report-heartbeat.yml@refs/heads/main",
        ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN="synthetic-cloud-run-id-token",
        ACCOUNT_FACTS_SYNC_ENABLED="true",
        ACCOUNT_FACTS_SYNC_URL=QRS_URL,
        ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
    )
    value.pop("ACCOUNT_HISTORY_GCS_PREFIX")
    value.update(overrides)
    return value


def _service_metadata():
    return {
        "metadata": {
            "name": "longbridge-quant-hk-service",
            "namespace": "252919773759",
            "generation": 7,
            "labels": {"cloud.googleapis.com/location": "asia-east2"},
            "annotations": {"run.googleapis.com/ingress": "all"},
        },
        "spec": {"template": {"metadata": {"name": "longbridge-quant-hk-service-00001"}, "spec": {"containers": [{
            "env": [{"name": "LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED", "value": "true"}],
        }]}}},
        "status": {
            "url": _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"],
            "latestReadyRevisionName": "longbridge-quant-hk-service-00001",
            "latestCreatedRevisionName": "longbridge-quant-hk-service-00001",
            "observedGeneration": 7,
            "conditions": [{"type": "Ready", "status": "True"}],
            "traffic": [{"revisionName": "longbridge-quant-hk-service-00001", "percent": 100}],
        },
    }


def _independent_snapshot(**overrides):
    value = {
        "schema_version": "longbridge_account_snapshot.v1",
        "status": "partial",
        "account_scope": "HK",
        "observed_started_at": (T0 + timedelta(seconds=1)).isoformat(),
        "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat(),
        "snapshot_atomic": False,
        "broker_reported_balances": [{"currency": "USD", "net_assets": "10.25", "total_cash": "-2.5"}],
        "cash": [{"currency": "HKD", "available_cash": "0", "frozen_cash": "0", "settling_cash": "0"}],
        "positions": [{"symbol": "synthetic-private-symbol", "currency": "HKD", "quantity": "3"}],
        "known_non_terminal_orders_7d": [{"synthetic": "private-order"}],
        "known_recent_executions_7d_count": 1,
        "positions_complete": True,
        "cash_complete": True,
        "open_orders_complete": False,
        "recent_executions_complete": False,
        "stable_broker_account_id": None,
        "unique_writer_confirmed": False,
        "execution_fees": None,
        "market_value": None,
        "equity": None,
        "no_order": True,
        "live_authority_granted": False,
        "source_binding": {"kind": "deployment_scope_token_version", "status": "bound", "id": BINDING},
    }
    value.update(overrides)
    return value


class _IndependentResponse:
    def __init__(self, payload, *, status=200, raw=None, redirect=False):
        self.status_code = status
        self.is_redirect = redirect
        self.content = raw if raw is not None else json.dumps(payload).encode()
        self.closed = False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]

    def close(self):
        self.closed = True


def _observe_independent(env=None, metadata=None, snapshot=None, *, get_error=None, response=None, post_response=None, post_error=None):
    calls = {"gets": [], "posts": []}
    response = response or _IndependentResponse(snapshot or _independent_snapshot())

    def get(url, **kwargs):
        calls["gets"].append((url, kwargs))
        if get_error:
            raise get_error
        return response

    def post(url, **kwargs):
        calls["posts"].append((url, kwargs))
        if post_error:
            raise post_error
        payload = json.loads(kwargs["data"])
        return post_response or _IndependentResponse({
            "ok": True, "stored": True, "target_id": payload["target_id"],
            "observation_date": payload["observation_date"],
            "observed_finished_at": payload["observed_finished_at"],
        })

    result = snapshots.observe_independent_hk_account_snapshot(
        _independent_env() if env is None else env,
        service_metadata=_service_metadata() if metadata is None else metadata,
        http_get=get, http_post=post,
        now_reader=_Clock([T0, T0 + timedelta(seconds=3), T0 + timedelta(seconds=4)]),
    )
    return result, calls, response


def test_independent_hk_preserves_native_currency_and_only_publishes_existing_history_contract():
    result, calls, response = _observe_independent()
    assert result.status == "observed" and result.publish_status == "published"
    assert response.closed
    assert len(calls["gets"]) == len(calls["posts"]) == 1
    url, request = calls["gets"][0]
    assert url == _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"] + "/account-snapshot"
    assert request["headers"]["Authorization"] == "Bearer synthetic-cloud-run-id-token"
    assert request["allow_redirects"] is False and request["stream"] is True
    assert 0 < request["timeout"] <= snapshots.HTTP_TIMEOUT_SECONDS
    outgoing = calls["posts"][0][1]["data"]
    history = json.loads(outgoing)
    assert set(history) == set(_history(target_id="hk"))
    assert history["broker_reported_balances"] == _independent_snapshot()["broker_reported_balances"]
    assert history["cash"] == _independent_snapshot()["cash"]
    assert history["target_id"] == "hk" and history["account_scope"] == "HK"
    assert history["observed_started_at"] == _independent_snapshot()["observed_started_at"]
    assert history["observation_date"] == T0.date().isoformat()
    assert b"synthetic-private" not in outgoing and b"private-order" not in outgoing
    assert b"synthetic-cloud-run" not in outgoing


@pytest.mark.parametrize("overrides", [
    {"ACCOUNT_HISTORY_RECORDING_ENABLED": "false"},
    {"ACCOUNT_HISTORY_OBSERVATION_MODE": ""},
    {"ACCOUNT_HISTORY_OBSERVATION_MODE": "scheduler_archive"},
    {"ACCOUNT_HISTORY_OBSERVATION_MODE": "invalid"},
    {"ACCOUNT_HISTORY_TARGET_ID": "sg"},
    {"ACCOUNT_HISTORY_EXPECTED_SCOPE": "SG"},
    {"ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID": ""},
    {"GOOGLE_CLOUD_PROJECT": "other-project"},
    {"RUNTIME_TARGET_ENABLED": "true"},
    {"GITHUB_EVENT_NAME": "schedule"},
    {"ACCOUNT_HISTORY_REQUESTED_TARGET": "all"},
    {"GITHUB_REF": "refs/heads/candidate"},
    {"GITHUB_WORKFLOW_REF": "other/workflow@refs/heads/main"},
    {"ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN": ""},
    {"ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN": "token\n"},
    {"ACCOUNT_HISTORY_SERVICE_URL": "http://untrusted.run.app"},
])
def test_independent_invalid_config_has_no_get_or_publish(overrides):
    result, calls, _ = _observe_independent(_independent_env(**overrides))
    assert result.status in {"disabled", "error"}
    assert calls == {"gets": [], "posts": []}


@pytest.mark.parametrize("mutate", [
    lambda v: v["metadata"].update(name="wrong-service"),
    lambda v: v["metadata"].update(namespace="wrong-project"),
    lambda v: v["metadata"]["labels"].update({"cloud.googleapis.com/location": "asia-east1"}),
    lambda v: v["metadata"]["annotations"].update({"run.googleapis.com/ingress": "internal"}),
    lambda v: v["metadata"]["annotations"].clear(),
    lambda v: v["metadata"]["annotations"].update({"run.googleapis.com/ingress-status": "internal"}),
    lambda v: v["status"].update(url="https://different.run.app"),
    lambda v: v["status"].update(traffic=[{"revisionName": "old-revision", "percent": 100}]),
    lambda v: v["status"].update(traffic=[{"revisionName": "longbridge-quant-hk-service-00001", "percent": 50}]),
    lambda v: v["status"].update(conditions=[{"type": "Ready", "status": "False"}]),
    lambda v: v["spec"]["template"]["spec"]["containers"][0].update(env=[]),
    lambda v: v["spec"]["template"]["spec"]["containers"][0]["env"][0].update(value="false"),
])
def test_independent_service_gate_rejects_before_sending_id_token(mutate):
    metadata = _service_metadata()
    mutate(metadata)
    result, calls, _ = _observe_independent(metadata=metadata)
    assert result.status == "error"
    assert calls == {"gets": [], "posts": []}


@pytest.mark.parametrize("overrides", [
    {"schema_version": "unknown"}, {"status": "ok"}, {"account_scope": "SG"},
    {"positions_complete": False}, {"cash_complete": False},
    {"no_order": False}, {"live_authority_granted": True}, {"snapshot_atomic": True},
    {"source_binding": {"kind": "deployment_scope_token_version", "status": "bound", "id": OTHER_BINDING}},
    {"source_binding": {"kind": "deployment_scope_token_version", "status": "unavailable", "id": None}},
    {"observed_started_at": T0.isoformat().replace("+00:00", "")},
    {"observed_started_at": (T0 - timedelta(seconds=1)).isoformat()},
    {"observed_finished_at": (T0 + timedelta(seconds=4)).isoformat()},
    {"observed_finished_at": T0.isoformat()},
    {"broker_reported_balances": []},
    {"cash": []},
    {"broker_reported_balances": [{"currency": "USD", "net_assets": "0.123456789", "total_cash": "0"}]},
    {"broker_reported_balances": [{"currency": "USD", "net_assets": "1234567890123456", "total_cash": "0"}]},
    {"broker_reported_balances": [{"currency": "USD", "net_assets": "NaN", "total_cash": "0"}]},
    {"cash": [{"currency": "HKD", "available_cash": "0", "frozen_cash": "0", "settling_cash": "0", "extra": "private"}]},
])
def test_independent_invalid_snapshot_never_posts(overrides):
    result, calls, response = _observe_independent(snapshot=_independent_snapshot(**overrides))
    assert result.status == "error"
    assert len(calls["gets"]) == 1 and calls["posts"] == []
    assert response.closed


@pytest.mark.parametrize("response", [
    _IndependentResponse({}, status=403),
    _IndependentResponse({}, status=503),
    _IndependentResponse({}, status=302, redirect=True),
    _IndependentResponse({}, raw=b"x" * (64 * 1024 + 1)),
    _IndependentResponse({}, raw=b"not json"),
])
def test_independent_get_failures_are_closed_without_retry_or_publish(response):
    result, calls, _ = _observe_independent(response=response)
    assert result.status == "error" and len(calls["gets"]) == 1
    assert calls["posts"] == [] and response.closed


def test_independent_get_unknown_is_not_retried():
    result, calls, _ = _observe_independent(get_error=TimeoutError(SECRET))
    assert result.status == "error" and len(calls["gets"]) == 1 and not calls["posts"]


def test_independent_sync_disabled_observes_without_claiming_storage():
    result, calls, _ = _observe_independent(_independent_env(ACCOUNT_FACTS_SYNC_ENABLED="false"))
    assert result.status == "observed" and result.publish_status == "disabled"
    assert len(calls["gets"]) == 1 and calls["posts"] == []


@pytest.mark.parametrize("receipt", [
    {"ok": True}, {"ok": True, "stored": False},
    {"ok": True, "stored": True, "target_id": "sg"},
])
def test_independent_requires_exact_stored_ack(receipt):
    result, calls, _ = _observe_independent(post_response=_IndependentResponse(receipt))
    assert result.status == "observed" and result.publish_status == "unknown"
    assert len(calls["gets"]) == len(calls["posts"]) == 1


def test_independent_unknown_post_does_not_repeat_get_or_post():
    result, calls, _ = _observe_independent(post_error=TimeoutError(QRS_TOKEN))
    assert result.status == "observed" and result.publish_status == "unknown"
    assert len(calls["gets"]) == len(calls["posts"]) == 1


def test_independent_mode_cannot_fall_back_to_scheduler_or_storage():
    result, spies = _record(_independent_env())
    assert result.status == "error"
    assert spies.session.calls == [] and spies.open_calls == [] and spies.posts == []


def test_unknown_observation_mode_cannot_trigger_legacy_recorder():
    result, spies = _record(_env(ACCOUNT_HISTORY_OBSERVATION_MODE="invalid"))
    assert result.status == "error"
    assert spies.session.calls == [] and spies.open_calls == [] and spies.posts == []


def test_independent_hk_workflow_reuses_explicit_identity_and_exact_audience():
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    assert "ACCOUNT_HISTORY_OBSERVATION_MODE:" in workflow
    assert "ACCOUNT_HISTORY_REQUESTED_TARGET: \u0024{{ inputs.target || 'all' }}" in workflow
    assert "ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN:" in workflow
    assert "--independent-hk-account-snapshot" in workflow
    assert "token_format:" in workflow and "id_token_audience:" in workflow
    assert 'gcloud run services describe "\u0024{SNAPSHOT_SERVICE}"' in workflow
    assert '--project "\u0024{GOOGLE_CLOUD_PROJECT}"' in workflow
    assert '--region "\u0024{SNAPSHOT_REGION}"' in workflow
    assert "| uv run --no-sync python scripts/record_daily_account_snapshot.py --independent-hk-account-snapshot" in workflow
    assert "uv run --no-sync python scripts/record_daily_account_snapshot.py\n" in workflow



@pytest.mark.parametrize("overrides", [
    {"SNAPSHOT_SERVICE": "other-service"}, {"SNAPSHOT_REGION": "asia-east1"},
    {"ACCOUNT_FACTS_SYNC_TOKEN": ""},
    {"ACCOUNT_FACTS_SYNC_URL": "https://qrs.example.test/other"},
])
def test_independent_wrong_target_or_sync_configuration_blocks_all_reads(overrides):
    result, calls, _ = _observe_independent(_independent_env(**overrides))
    assert result.status == "error" and calls == {"gets": [], "posts": []}


def test_independent_keeps_hkd_net_assets_without_inventing_usd():
    payload = _independent_snapshot()
    payload["broker_reported_balances"][0]["currency"] = "HKD"
    result, calls, _ = _observe_independent(snapshot=payload)
    assert result.publish_status == "published"
    assert json.loads(calls["posts"][0][1]["data"])["broker_reported_balances"][0]["currency"] == "HKD"


@pytest.mark.parametrize("field", ["cash", "broker_reported_balances"])
def test_independent_duplicate_currency_and_excessive_rows_rejected(field):
    payload = _independent_snapshot()
    payload[field] *= 33
    result, calls, _ = _observe_independent(snapshot=payload)
    assert result.status == "error" and calls["posts"] == []


def test_independent_optional_native_financing_is_preserved():
    financing = [{"currency": "USD", "buy_power": "11.25", "risk_level": "1"}]
    result, calls, _ = _observe_independent(snapshot=_independent_snapshot(financing=financing))
    assert result.publish_status == "published"
    assert json.loads(calls["posts"][0][1]["data"])["financing"] == financing


@pytest.mark.parametrize("financing", [
    [], [{"currency": "HKD", "buy_power": "1"}],
    [{"currency": "USD", "buy_power": "1", "private_extra": "must-not-send"}],
])
def test_independent_invalid_financing_cannot_expand_wire_contract(financing):
    result, calls, _ = _observe_independent(snapshot=_independent_snapshot(financing=financing))
    assert result.status == "error" and calls["posts"] == []


def test_independent_observation_date_is_utc_and_original_instants_are_not_rewritten():
    config = snapshots._independent_hk_config(_independent_env())
    started = "2026-09-29T00:00:01+08:00"
    finished = "2026-09-29T00:00:02+08:00"
    request_started = datetime.fromisoformat(started) - timedelta(seconds=1)
    payload = _independent_snapshot(observed_started_at=started, observed_finished_at=finished)
    history = snapshots._project_independent_history(
        payload, config, request_started, datetime.fromisoformat(finished) + timedelta(seconds=1),
    )
    assert history["observation_date"] == "2026-09-28"
    assert history["observed_started_at"] == started and history["observed_finished_at"] == finished


def test_independent_rechecks_existing_freshness_window_before_publish():
    calls = []
    result = snapshots.observe_independent_hk_account_snapshot(
        _independent_env(), service_metadata=_service_metadata(),
        http_get=lambda *_args, **_kwargs: _IndependentResponse(_independent_snapshot()),
        http_post=lambda *_args, **_kwargs: calls.append("post"),
        now_reader=_Clock([T0, T0 + timedelta(seconds=3), T0 + snapshots.OBSERVATION_WINDOW + timedelta(seconds=2)]),
    )
    assert result.status == "observed" and result.publish_status == "rejected"
    assert result.publish_category == "observation_stale" and calls == []


def test_independent_cli_reports_only_bounded_status(capsys):
    def fail_get(*_args, **_kwargs):
        raise TimeoutError(f"{SECRET} {QRS_TOKEN} synthetic-private-position")

    code = snapshots.main(
        ["--independent-hk-account-snapshot"],
        environ=_independent_env(), service_metadata=_service_metadata(),
        http_get=fail_get, http_post=lambda *_a, **_k: pytest.fail("must not publish"),
        now_reader=lambda: T0,
    )
    assert code == 1
    assert capsys.readouterr().out.strip() == "observation=error:snapshot_request_unknown account_facts_publish=disabled"


def test_independent_configuration_check_never_reads_stdin_or_provider(monkeypatch, capsys):
    monkeypatch.setattr(snapshots, "_read_independent_service_metadata", lambda: pytest.fail("must not read metadata"))
    code = snapshots.main(["--validate-independent-hk-account-snapshot"], environ=_independent_env())
    assert code == 0 and capsys.readouterr().out.strip() == "independent_config=ok"


@pytest.mark.parametrize("raises", [False, True])
def test_independent_transport_disables_retries_and_closes_owned_session(monkeypatch, raises):
    import requests

    events = []
    response = _IndependentResponse(_independent_snapshot())

    class Session:
        def mount(self, prefix, adapter):
            events.append(("mount", prefix, adapter.max_retries.total))

        def get(self, url, **kwargs):
            events.append(("get", url, kwargs))
            if raises:
                raise TimeoutError("synthetic")
            return response

        def close(self):
            events.append(("close",))

    monkeypatch.setattr(requests, "Session", Session)
    if raises:
        with pytest.raises(TimeoutError):
            snapshots._default_http_get("https://synthetic.run.app/account-snapshot", timeout=20, allow_redirects=False)
    else:
        owned = snapshots._default_http_get("https://synthetic.run.app/account-snapshot", timeout=20, allow_redirects=False)
        assert events[-1][0] == "get"
        owned.close()
        assert response.closed
    assert events[0] == ("mount", "https://", 0)
    assert sum(event[0] == "get" for event in events) == 1
    assert events[-1] == ("close",)


def test_independent_workflow_keeps_cloud_resources_secret_and_no_new_auth_identity():
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    assert "SNAPSHOT_SERVICE: \u0024{{ secrets.CLOUD_RUN_SERVICE }}" in workflow
    assert "SNAPSHOT_REGION: \u0024{{ vars.CLOUD_RUN_REGION }}" in workflow
    assert "matrix.target.service" not in workflow
    assert "ACCOUNT_HISTORY_OBSERVATION_MODE: \u0024{{ vars.ACCOUNT_HISTORY_OBSERVATION_MODE || 'scheduler_archive' }}" in workflow
    assert "id_token_include_email: true" in workflow
    assert "id_token_audience: \u0024{{ steps.independent_audience.outputs.service_url || '' }}" in workflow
    assert "service_account: \u0024{{ env.GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT }}" in workflow
    independent = workflow[workflow.index('            independent_get)'):workflow.index('            *)', workflow.index('            independent_get)'))]
    assert "--validate-independent-hk-account-snapshot" in independent
    assert independent.index("--validate-independent") < independent.index("gcloud run services describe")
    assert "/probe" not in independent and "/run" not in independent
    assert "scheduler" not in independent and "tee" not in independent and "upload-artifact" not in independent


@pytest.mark.parametrize("mutate", [
    lambda v: v["metadata"].pop("generation"),
    lambda v: v["metadata"].update(generation=0),
    lambda v: v["metadata"].update(generation=True),
    lambda v: v["metadata"].update(generation="7"),
    lambda v: v["status"].pop("observedGeneration"),
    lambda v: v["status"].update(observedGeneration=6),
    lambda v: v["status"].update(observedGeneration=8),
    lambda v: v["status"].update(observedGeneration="7"),
    lambda v: v["status"].pop("latestCreatedRevisionName"),
    lambda v: v["status"].update(latestCreatedRevisionName="longbridge-quant-hk-service-00002"),
    lambda v: v["spec"]["template"].pop("metadata"),
    lambda v: v["spec"]["template"]["metadata"].update(name="longbridge-quant-hk-service-00002"),
])
def test_independent_revision_and_generation_must_prove_serving_template(mutate):
    metadata = _service_metadata()
    mutate(metadata)
    result, calls, _ = _observe_independent(metadata=metadata)
    assert result.status == "error"
    assert calls == {"gets": [], "posts": []}


def test_independent_unreconciled_new_template_cannot_qualify_old_serving_revision():
    metadata = _service_metadata()
    metadata["metadata"]["generation"] = 2
    metadata["status"]["observedGeneration"] = 1
    metadata["status"]["latestCreatedRevisionName"] = "longbridge-quant-hk-service-00002"
    metadata["spec"]["template"]["metadata"]["name"] = "longbridge-quant-hk-service-00002"
    result, calls, _ = _observe_independent(metadata=metadata)
    assert result.status == "error"
    assert calls == {"gets": [], "posts": []}


@pytest.mark.parametrize("raw_url", [
    _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"] + "/",
    " " + _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"],
    _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"] + " ",
    _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"].upper(),
    _independent_env()["ACCOUNT_HISTORY_SERVICE_URL"] + "\n",
])
def test_independent_noncanonical_raw_audience_rejected_before_get(raw_url):
    result, calls, _ = _observe_independent(_independent_env(ACCOUNT_HISTORY_SERVICE_URL=raw_url))
    assert result.status == "error"
    assert calls == {"gets": [], "posts": []}


def test_independent_pre_auth_audience_output_is_canonical_without_token_or_metadata(monkeypatch, capsys):
    env = _independent_env(ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN="")
    monkeypatch.setattr(snapshots, "_read_independent_service_metadata", lambda: pytest.fail("no service read before auth"))
    assert snapshots.main(["--validate-independent-hk-audience"], environ=env) == 0
    assert capsys.readouterr().out.strip() == "service_url=" + env["ACCOUNT_HISTORY_SERVICE_URL"]


@pytest.mark.parametrize("suffix", ["/", " ", "\n"])
def test_independent_pre_auth_rejects_noncanonical_audience_without_printing_it(suffix, capsys):
    env = _independent_env(ACCOUNT_HISTORY_INDEPENDENT_ID_TOKEN="")
    env["ACCOUNT_HISTORY_SERVICE_URL"] += suffix
    assert snapshots.main(["--validate-independent-hk-audience"], environ=env) == 1
    output = capsys.readouterr().out
    assert output.strip() == "independent_audience=error:independent_config_invalid"
    assert env["ACCOUNT_HISTORY_SERVICE_URL"] not in output


def test_independent_workflow_validates_one_audience_before_auth_and_reuses_it():
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text()
    validation = workflow.index("id: independent_audience")
    assert validation < workflow.index("id: gcp_auth_primary")
    assert '--validate-independent-hk-audience >> "$GITHUB_OUTPUT"' in workflow
    audience = "id_token_audience: \u0024{{ steps.independent_audience.outputs.service_url || '' }}"
    assert workflow.count(audience) == 2
    assert "token_format: \u0024{{ steps.independent_audience.outcome == 'success' && 'id_token' || '' }}" in workflow
    record_step = workflow[workflow.index("      - name: Record and sync daily account snapshot"):workflow.index("      - name: Check recent execution report")]
    assert "ACCOUNT_HISTORY_SERVICE_URL: \u0024{{ env.ACCOUNT_HISTORY_OBSERVATION_MODE != 'independent_get' && vars.ACCOUNT_HISTORY_SERVICE_URL || steps.independent_audience.outputs.service_url }}" in record_step
