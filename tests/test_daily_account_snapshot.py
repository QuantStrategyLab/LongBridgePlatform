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
    def __init__(self, objects=()):
        self.client = self
        self.objects = list(objects)
        self.list_calls = []
        self.blob_calls = []

    def list_blobs(self, bucket, **kwargs):
        self.list_calls.append((bucket, kwargs))
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
        self.calls = []
        self.read_count = 0
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        self.read_count += 1
        if self.read_count == self.read_error_number or self.read_count in self.read_error_numbers:
            raise TimeoutError("synthetic scheduler read timeout")
        return _Response(payload=self.job)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if url.endswith(":resume"):
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
            if self.pause_applies:
                self.job["state"] = "PAUSED"
            if self.pause_error:
                raise self.pause_error
            return self.pause_response
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
    def __init__(self, *, objects=(), job=None, run_error=None, post_error=None, post_response=None, **session_options):
        self.storage = _Storage(objects)
        self.session = _Session(job=job, run_error=run_error, **session_options)
        self.open_calls = []
        self.open_state_at_open = []
        self.scheduler_call_count_at_open = []
        self.posts = []
        self.post_error = post_error
        self.post_response = post_response

    def open_store(self, project):
        self.open_calls.append(project)
        self.open_state_at_open.append(self.session.job.get("state"))
        self.scheduler_call_count_at_open.append(len(self.session.calls))
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


@pytest.mark.parametrize("target_id", ["paper", "hk"])
def test_paused_scheduler_is_rejected_before_run_or_gcs_for_every_target(target_id):
    result, spies = _record(
        _env(target_id),
        _Spies(job=_job(target_id, state="PAUSED")),
    )

    assert result.category == "scheduler_job_mismatch"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


def test_paused_sg_resumes_runs_once_restores_full_job_then_reads_and_publishes():
    payload = _history(T0 + timedelta(seconds=6), T0 + timedelta(seconds=7), target_id="sg")
    raw = json.dumps(payload, indent=2).encode()
    job = _job("sg", state="PAUSED", lastAttemptTime=(T0 - timedelta(days=2)).isoformat())
    original = json.loads(json.dumps(job))
    spies = _Spies(objects=[_object(payload, raw=raw)], job=job)
    result, spies = _record(
        _env(
            "sg",
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
    assert spies.open_state_at_open == ["PAUSED"]
    assert spies.scheduler_call_count_at_open == [6]
    assert len(spies.posts) == 1 and spies.posts[0][1]["data"] == raw


@pytest.mark.parametrize("runtime_flag", ["true", "False", " false", ""])
def test_paused_sg_requires_exact_false_runtime_flag(runtime_flag):
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED=runtime_flag),
        _Spies(job=_job("sg", state="PAUSED")),
    )
    assert result.category == "runtime_target_not_disabled"
    assert [call[0] for call in spies.session.calls] == ["get"]
    assert spies.open_calls == []


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
def test_paused_sg_mismatched_job_or_schedule_fails_before_mutation(mutate, expected):
    job = _job("sg", state="PAUSED")
    mutate(job)
    result, spies = _record(
        _env("sg", RUNTIME_TARGET_ENABLED="false"),
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
def test_paused_sg_pause_failure_never_reads_or_publishes(pause_response, pause_applies, expected):
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
    assert spies.open_calls == [] and spies.posts == []


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
