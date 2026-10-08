from __future__ import annotations

import datetime as dt
import json
import sys
import socket
from copy import deepcopy
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from google.api_core.exceptions import Forbidden, GoogleAPICallError, PreconditionFailed

from scripts import execution_report_heartbeat as heartbeat
from scripts import publish_daily_runtime_projection as publisher

HK = ZoneInfo("Asia/Hong_Kong")
OBSERVED = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
ROOT = Path(__file__).resolve().parents[1]


class _Blob:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.uploads: list[dict] = []

    def upload_from_string(self, data, *args, **kwargs):
        self.uploads.append({"data": data, "args": args, "kwargs": kwargs})
        if self.fail is not None:
            raise self.fail


class _Bucket:
    def __init__(self, blob: _Blob) -> None:
        self._blob = blob
        self.names: list[str] = []

    def blob(self, name: str) -> _Blob:
        self.names.append(name)
        return self._blob


class _Client:
    def __init__(self, blob: _Blob) -> None:
        self.buckets: list[str] = []
        self._bucket = _Bucket(blob)

    def bucket(self, name: str) -> _Bucket:
        self.buckets.append(name)
        return self._bucket

    def list_blobs(self, bucket_or_name, **kwargs):
        del bucket_or_name, kwargs
        return []


class _SyncResponse:
    def __init__(self, status_code=200, payload=None, *, redirect=False):
        self.status_code = status_code
        self.is_redirect = redirect
        self.content = json.dumps(payload or {}).encode()

    def iter_content(self, *, chunk_size):
        del chunk_size
        yield self.content


def _open_calendar(calendar: str, **kwargs) -> set[dt.date]:
    del calendar
    return {kwargs["start_date"]}


def _closed_calendar(calendar: str, **kwargs) -> set[dt.date]:
    del calendar, kwargs
    return set()


def _target(*, service: str = "lb-paper", scope: str = "PAPER", enabled: bool = True) -> dict:
    return {
        "service": service,
        "strategy_profile": "paper-profile",
        "account_scope": scope,
        "runtime_target_enabled": enabled,
        "scheduler": {"main_time": "5 16 * * 1-5", "timezone": "Asia/Hong_Kong"},
        "market": "HK",
        "market_calendar": "XHKG",
        "market_timezone": "Asia/Hong_Kong",
    }


def _env(**overrides: str) -> dict[str, str]:
    env = {
        "RUNTIME_DAILY_PROJECTION_ENABLED": "true",
        "RUNTIME_DAILY_PROJECTION_GCS_PREFIX": "gs://paper-bucket/runtime_daily",
        "RUNTIME_DAILY_PROJECTION_TARGET_ID": "paper",
        "RUNTIME_HEARTBEAT_ACCOUNT_SCOPE": "PAPER",
        "RUNTIME_TARGET_JSON": json.dumps(_target()),
        "RUNTIME_HEARTBEAT_GCS_GLOBS": "gs://reports/longbridge/**/2026-09/*.json",
        "GCP_PROJECT_ID": "longbridgequant",
    }
    env.update(overrides)
    return env


def _sync_env(**overrides: str) -> dict[str, str]:
    target = _target(service="longbridge-quant-paper-service")
    target.update(
        strategy_profile="russell_top50_leader_rotation",
        market_timezone="America/New_York",
        scheduler={"main_time": "5 16 * * 1-5", "timezone": "America/New_York"},
    )
    env = _env(
        RUNTIME_TARGET_JSON=json.dumps(target),
        RUNTIME_DAILY_SYNC_ENABLED="true",
        RUNTIME_DAILY_SYNC_URL="https://qrs.example.com/api/runtime-daily/sync",
        EXECUTION_EVIDENCE_SYNC_TOKEN="synthetic-test-token",
    )
    env.update(overrides)
    return env


def _sync_describe(service: str, *, project: str | None) -> dict:
    del project
    return _cloud_run(
        {
            "strategy_profile": "russell_top50_leader_rotation",
            "account_scope": "PAPER",
            "service_name": service,
            "runtime_target_enabled": True,
        }
    )


def _report(**changes) -> dict:
    payload = {
        "platform": "longbridge",
        "service_name": "lb-paper",
        "strategy_profile": "paper-profile",
        "account_scope": "PAPER",
        "run_id": "run-1",
        "run_source": "cloud_run",
        "status": "ok",
        "dry_run": False,
        "validation_only": False,
        "started_at": "2026-09-28T16:06:00+08:00",
        "finished_at": "2026-09-28T16:07:00+08:00",
        "runtime_target": {"execution_mode": "paper", "service_name": "lb-paper"},
        "summary": {
            "execution_status": "no_action",
            "broker_submission_done": False,
            "action_done": False,
            "orders_pending_count": 0,
        },
        "errors": [],
    }
    payload.update(changes)
    return payload


def _entry(uri: str, updated: str) -> dict:
    return {"url": uri, "metadata": {"updated": updated}}


def _cloud_run(runtime_target: dict, *, enabled_env: str | None = None) -> dict:
    env = [{"name": "RUNTIME_TARGET_JSON", "value": json.dumps(runtime_target)}]
    if enabled_env is not None:
        env.append({"name": "RUNTIME_TARGET_ENABLED", "value": enabled_env})
    return {"spec": {"template": {"spec": {"containers": [{"env": env}]}}}}


def _describe(service: str, *, project: str | None) -> dict:
    del project
    return _cloud_run(
        {
            "strategy_profile": "paper-profile",
            "account_scope": "PAPER",
            "service_name": service,
            "runtime_target_enabled": True,
        }
    )


def _scheduler_job(
    service: str,
    *,
    schedule: str = "5 16 * * 1-5",
    timezone: str = "Asia/Hong_Kong",
    state: str = "ENABLED",
    path: str = "/",
) -> dict:
    return {
        "state": state,
        "schedule": schedule,
        "timeZone": timezone,
        "httpTarget": {"uri": f"https://{service}-abcd.a.run.app{path}"},
    }


def _jobs_for_env(env: dict[str, str]) -> list[dict]:
    target = json.loads(env["RUNTIME_TARGET_JSON"])
    scheduler = target.get("scheduler") or {}
    return [
        _scheduler_job(
            str(target.get("service") or "lb-paper"),
            schedule=str(scheduler.get("main_time") or "5 16 * * 1-5"),
            timezone=str(scheduler.get("timezone") or "Asia/Hong_Kong"),
        )
    ]


def _publish(
    monkeypatch,
    env,
    reports,
    *,
    calendar=_open_calendar,
    blob=None,
    list_error=False,
    limit=None,
    jobs="default",
    http_post=None,
    describe=_describe,
    private_result_path=None,
):
    blob = blob or _Blob()
    client = _Client(blob)
    calls = {"listed": 0, "read": 0}

    def list_objects(pattern, *, project):
        del pattern, project
        calls["listed"] += 1
        if list_error:
            raise RuntimeError("secret listing detail")
        return reports

    def read_payload(uri: str):
        calls["read"] += 1
        for entry in reports:
            if entry.get("url") == uri:
                return entry.get("payload")
        return None

    if limit is not None:
        env = {**env, "RUNTIME_HEARTBEAT_MAX_REPORTS_TO_READ": str(limit)}
    selected = _jobs_for_env(env) if jobs == "default" else jobs

    def list_jobs(*, project: str | None) -> list:
        del project
        return selected

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", list_jobs)
    publish_kwargs = {}
    if private_result_path is not None:
        publish_kwargs["private_result_path"] = private_result_path
    status, business_date, sync_status = publisher.publish(
        env,
        now=OBSERVED,
        session_dates_loader=calendar,
        client=client,
        list_objects=list_objects,
        read_payload=read_payload,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
        http_post=http_post,
        **publish_kwargs,
    )
    calls["sync_status"] = sync_status
    return status, business_date, blob, client, calls


def _stored(blob: _Blob) -> dict:
    assert len(blob.uploads) == 1
    upload = blob.uploads[0]
    assert upload["kwargs"] == {
        "content_type": "application/json",
        "if_generation_match": 0,
        "timeout": 20,
        "retry": None,
    }
    assert upload["args"] == ()
    return json.loads(upload["data"])


def test_disabled_switch_performs_no_io(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(heartbeat, "_list_gcs_objects", boom)
    monkeypatch.setattr(heartbeat, "_cat_gcs_json", boom)
    monkeypatch.setattr(publisher, "_storage_client", boom)
    status, business_date, sync_status = publisher.publish({"RUNTIME_DAILY_PROJECTION_ENABLED": "false"}, now=OBSERVED)
    assert status == "disabled"
    assert business_date == ""
    assert sync_status == "disabled"


def test_bad_config_and_cross_scope_do_not_write(monkeypatch) -> None:
    blob = _Blob()

    def fail_select(env):
        with pytest.raises(publisher._Rejected):
            publisher.publish(
                env,
                now=OBSERVED,
                client=_Client(blob),
                list_objects=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("listed")),
                report_globs=lambda since, now: ["gs://reports/x"],
            )

    fail_select(_env(RUNTIME_DAILY_PROJECTION_GCS_PREFIX="gs://paper-bucket/other"))
    fail_select(_env(RUNTIME_DAILY_PROJECTION_TARGET_ID="hk"))
    fail_select(_env(RUNTIME_HEARTBEAT_ACCOUNT_SCOPE="HK"))
    fail_select(_env(RUNTIME_TARGET_JSON=json.dumps(_target(scope="US", service="paper-named-service"))))
    fail_select(
        _env(
            RUNTIME_TARGET_JSON="",
            CLOUD_RUN_SERVICE_TARGETS_JSON=json.dumps({"targets": [_target(), _target(service="lb-paper-2")]}),
        )
    )
    assert blob.uploads == []


def test_projections_cover_quiet_closed_missing_and_prior_unknown(monkeypatch) -> None:
    quiet_report = _entry("gs://reports/quiet.json", "2026-09-28T08:07:00Z")
    quiet_report["payload"] = _report()
    status, business_date, blob, client, _calls = _publish(monkeypatch, _env(), [quiet_report])
    stored = _stored(blob)
    assert status == "recorded"
    assert business_date == "2026-09-28"
    assert client.buckets == ["paper-bucket"]
    assert client._bucket.names == ["runtime_daily/longbridge/paper/2026-09-28/20260928T084000000000Z.json"]
    assert stored["records"][0]["status"] == "no_submission"
    assert stored["records"][0]["runs"][0]["run_id"] == "run-1"
    assert "secret" not in json.dumps(stored)

    closed_status, _, closed_blob, _, _ = _publish(monkeypatch, _env(), [], calendar=_closed_calendar)
    assert closed_status == "recorded"
    assert _stored(closed_blob)["records"][0]["status"] == "market_closed"

    missing_status, _, missing_blob, _, _ = _publish(monkeypatch, _env(), [])
    assert missing_status == "recorded"
    assert _stored(missing_blob)["records"][0]["status"] == "missing_report"

    prior = _entry("gs://reports/prior.json", "2026-09-27T08:07:00Z")
    prior["payload"] = _report(
        run_id="prior-unknown",
        started_at="2026-09-27T16:06:00+08:00",
        finished_at="2026-09-27T16:07:00+08:00",
        summary={
            "execution_status": "unknown",
            "broker_submission_done": False,
            "action_done": False,
            "orders_pending_count": 0,
        },
    )
    unknown_status, _, unknown_blob, _, _ = _publish(
        monkeypatch, _env(), [prior], calendar=_closed_calendar
    )
    assert unknown_status == "recorded"
    unknown = _stored(unknown_blob)["records"][0]
    assert unknown["status"] == "unknown"
    assert unknown["schedule"]["state"] == "market_closed"


def test_list_read_and_truncation_stay_incomplete(monkeypatch, capsys) -> None:
    status, _, blob, _, calls = _publish(monkeypatch, _env(), [], list_error=True, calendar=_closed_calendar)
    stored = _stored(blob)
    assert status == "recorded"
    assert stored["completeness"] == "incomplete"
    assert stored["records"][0]["status"] == "read_incomplete"
    assert stored["records"][0]["schedule"]["state"] == "market_closed"
    assert stored["read_errors"] == ["gs://reports/longbridge/**/2026-09/*.json: list_failed"]
    assert "secret" not in stored["read_errors"][0]
    assert calls["read"] == 0

    unread = _entry("gs://reports/unread.json", "2026-09-28T08:07:00Z")
    read_status, _, read_blob, _, _ = _publish(monkeypatch, _env(), [unread], calendar=_closed_calendar)
    assert read_status == "recorded"
    read_stored = _stored(read_blob)
    assert read_stored["records"][0]["status"] == "read_incomplete"
    assert read_stored["read_errors"] == ["gs://reports/unread.json: unreadable"]
    assert capsys.readouterr().err == ""

    first = _entry("gs://reports/new.json", "2026-09-28T08:07:00Z")
    first["payload"] = _report()
    second = _entry("gs://reports/older.json", "2026-09-28T08:06:00Z")
    second["payload"] = _report(run_id="older")
    truncated_status, _, truncated_blob, _, _ = _publish(
        monkeypatch,
        _env(),
        [first, second],
        calendar=_closed_calendar,
        limit=1,
    )
    assert truncated_status == "recorded"
    truncated = _stored(truncated_blob)
    assert "listing truncated" in truncated["read_errors"]
    assert truncated["records"][0]["status"] == "read_incomplete"
    assert truncated["completeness"] == "incomplete"


def test_observation_paths_are_versioned_by_utc_timestamp() -> None:
    first = dt.datetime(2026, 9, 28, 16, 40, tzinfo=HK)
    next_observation = first + dt.timedelta(minutes=1)

    _, first_name, _ = publisher._object_uri("gs://paper-bucket/runtime_daily", "2026-09-28", first)
    _, next_name, _ = publisher._object_uri("gs://paper-bucket/runtime_daily", "2026-09-28", next_observation)

    assert first_name == "runtime_daily/longbridge/paper/2026-09-28/20260928T084000000000Z.json"
    assert next_name == "runtime_daily/longbridge/paper/2026-09-28/20260928T084100000000Z.json"
    assert first_name != next_name


def test_existing_object_is_not_overwritten_and_unknown_write_is_not_retried(monkeypatch) -> None:
    exists = _Blob(fail=PreconditionFailed("already present"))
    status, business_date, blob, _, _ = _publish(monkeypatch, _env(), [], blob=exists)
    assert status == "already_recorded"
    assert business_date == "2026-09-28"
    assert len(blob.uploads) == 1
    assert blob.uploads[0]["kwargs"]["if_generation_match"] == 0

    unknown = _Blob(fail=RuntimeError("token=secret"))
    with pytest.raises(publisher._KnownFailure) as failure:
        _publish(monkeypatch, _env(), [], blob=unknown)
    assert failure.value.code == "write_unknown"
    assert "token=secret" not in str(failure.value)
    assert len(unknown.uploads) == 1


def test_quiet_read_discards_underlying_stderr(monkeypatch, capsys) -> None:
    def cat(uri: str, *, project: str | None):
        del uri, project
        print("secret payload", file=sys.stderr)
        return None

    monkeypatch.setattr(heartbeat, "_cat_gcs_json", cat)
    assert publisher._quiet_read("gs://reports/unread.json", project="longbridgequant") is None
    assert "secret payload" not in capsys.readouterr().err


def test_main_unknown_write_prints_safe_category_and_does_not_retry(monkeypatch, capsys) -> None:
    blob = _Blob(fail=RuntimeError("token=secret"))
    monkeypatch.setattr(publisher, "_storage_client", lambda: _Client(blob))
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(_env()))
    monkeypatch.setattr(heartbeat, "_list_gcs_objects", lambda *args, **kwargs: [])
    monkeypatch.setattr(heartbeat, "_report_globs", lambda since, now: ["gs://reports/x"])
    for key, value in _env().items():
        monkeypatch.setenv(key, value)
    assert publisher.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "daily runtime projection failed; category=write_unknown"
    assert "token=secret" not in captured.out + captured.err
    assert len(blob.uploads) == 1


def test_forbidden_write_is_classified_once_without_qrs_post(monkeypatch) -> None:
    blob = _Blob(fail=Forbidden("token=secret permission denied"))
    posts = []
    with pytest.raises(publisher._KnownFailure) as failure:
        _publish(
            monkeypatch,
            _sync_env(),
            [],
            blob=blob,
            describe=_sync_describe,
            http_post=lambda *args, **kwargs: posts.append((args, kwargs)),
        )
    assert failure.value.code == "storage_write_permission_denied"
    assert "token=secret" not in str(failure.value)
    assert len(blob.uploads) == 1
    assert posts == []


def test_main_keeps_unclassified_exceptions_generic_and_redacted(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        publisher,
        "publish",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("token=secret detail")),
    )
    assert publisher.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "daily runtime projection failed"
    assert "token=secret" not in captured.out + captured.err


def test_main_displays_only_safe_schedule_failure_category(monkeypatch, capsys) -> None:
    for key, value in _env().items():
        monkeypatch.setenv(key, value)

    def fail_describe(*_args, **_kwargs):
        raise RuntimeError("token=secret detail")

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", fail_describe)
    assert publisher.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out.strip() == "daily runtime projection failed; category=schedule_unavailable"
    assert "token=secret" not in captured.out + captured.err


def test_main_hides_errors_and_keeps_heartbeat_uninvoked(monkeypatch, capsys) -> None:
    assert "heartbeat.main" not in (ROOT / "scripts/publish_daily_runtime_projection.py").read_text(encoding="utf-8")
    monkeypatch.delenv("RUNTIME_DAILY_PROJECTION_ENABLED", raising=False)
    assert publisher.main([]) == 0
    assert capsys.readouterr().out.strip() == "daily runtime projection disabled"

    monkeypatch.setenv("RUNTIME_DAILY_PROJECTION_ENABLED", "true")
    monkeypatch.setenv("RUNTIME_DAILY_PROJECTION_TARGET_ID", "paper")
    monkeypatch.setenv("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE", "SG")
    monkeypatch.setenv("RUNTIME_DAILY_PROJECTION_GCS_PREFIX", "gs://paper-bucket/runtime_daily")
    assert publisher.main([]) == 2
    assert capsys.readouterr().out.strip() == "daily runtime projection rejected"


@pytest.mark.parametrize("status", ["recorded", "already_recorded"])
@pytest.mark.parametrize("sync_enabled", ["false", "true"])
@pytest.mark.parametrize(
    ("sync_status", "enabled_exit"),
    [("recorded", 0), ("rejected", 2), ("unknown", 1),
     ("skipped_existing", 1), ("disabled", 2), ("unexpected", 1)],
)
def test_main_requires_sync_ack_only_when_explicitly_enabled(
    monkeypatch, capsys, status, sync_enabled, sync_status, enabled_exit,
) -> None:
    monkeypatch.setenv("RUNTIME_DAILY_SYNC_ENABLED", sync_enabled)
    monkeypatch.setattr(publisher, "publish", lambda *_args, **_kwargs: (status, "2026-09-28", sync_status))
    assert publisher.main([]) == (enabled_exit if sync_enabled == "true" else 0)
    assert capsys.readouterr().out.strip().endswith(f"qrs_sync={sync_status}")


@pytest.mark.parametrize("sync_enabled", [None, "", "false", "True", "TRUE", "1", " true"])
def test_main_sync_opt_in_is_exact_and_disabled_projection_stays_normal(
    monkeypatch, capsys, sync_enabled,
) -> None:
    if sync_enabled is None:
        monkeypatch.delenv("RUNTIME_DAILY_SYNC_ENABLED", raising=False)
    else:
        monkeypatch.setenv("RUNTIME_DAILY_SYNC_ENABLED", sync_enabled)
    monkeypatch.setattr(publisher, "publish", lambda *_args, **_kwargs: ("recorded", "2026-09-28", "disabled"))
    assert publisher.main([]) == 0
    assert capsys.readouterr().out.strip().endswith("qrs_sync=disabled")
    monkeypatch.setenv("RUNTIME_DAILY_SYNC_ENABLED", "true")
    monkeypatch.setattr(publisher, "publish", lambda *_args, **_kwargs: ("disabled", "", "disabled"))
    assert publisher.main([]) == 0
    assert capsys.readouterr().out.strip() == "daily runtime projection disabled"


@pytest.mark.parametrize("sync_enabled", ["false", "true"])
def test_main_unrecognized_local_status_stays_failed(monkeypatch, capsys, sync_enabled) -> None:
    monkeypatch.setenv("RUNTIME_DAILY_SYNC_ENABLED", sync_enabled)
    monkeypatch.setattr(publisher, "publish", lambda *_args, **_kwargs: ("unexpected", "", "recorded"))
    assert publisher.main([]) == 1
    assert capsys.readouterr().out.strip() == "daily runtime projection failed"


@pytest.mark.parametrize(
    ("outcome", "sync_enabled", "expected_exit", "expected_sync", "expected_posts"),
    [
        ("ack", "true", 0, "recorded", 1),
        ("ack", "false", 0, "disabled", 0),
        ("ack", "True", 0, "disabled", 0),
        ("unauthorized", "true", 2, "rejected", 1),
        ("redirect", "true", 2, "rejected", 1),
        ("negative_ack", "true", 2, "rejected", 1),
        ("missing_token", "true", 2, "rejected", 0),
        ("timeout", "true", 1, "unknown", 1),
        ("server_error", "true", 1, "unknown", 1),
        ("mismatched_ack", "true", 1, "unknown", 1),
        ("invalid_json", "true", 1, "unknown", 1),
        ("oversized_response", "true", 1, "unknown", 1),
        ("existing", "true", 1, "skipped_existing", 0),
        ("existing", "false", 0, "skipped_existing", 0),
    ],
)
def test_main_real_publish_keeps_archive_and_does_not_retry_sync(
    monkeypatch, capsys, outcome, sync_enabled, expected_exit, expected_sync, expected_posts,
) -> None:
    env = _sync_env(RUNTIME_DAILY_SYNC_ENABLED=sync_enabled)
    if outcome == "missing_token":
        env["EXECUTION_EVIDENCE_SYNC_TOKEN"] = ""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    blob = _Blob(fail=PreconditionFailed("already exists") if outcome == "existing" else None)
    requests = []
    results = []
    real_publish = publisher.publish

    def post(url, **kwargs):
        requests.append((url, kwargs))
        if outcome == "timeout":
            raise TimeoutError("token=secret transport detail")
        if outcome == "unauthorized":
            return _SyncResponse(status_code=401)
        if outcome == "redirect":
            return _SyncResponse(status_code=302, redirect=True)
        if outcome == "server_error":
            return _SyncResponse(status_code=503)
        payload = json.loads(kwargs["data"])
        response = _SyncResponse(payload={
            "ok": outcome != "negative_ack",
            "stored": True,
            "business_date": "wrong" if outcome == "mismatched_ack" else payload["records"][0]["business_date"],
            "target_key": publisher._RUNTIME_DAILY_SYNC_TARGET_KEY,
            "account_key": "secret-account-key",
        })
        if outcome == "invalid_json":
            response.content = b"secret-invalid-json"
        if outcome == "oversized_response":
            response.content = b"x" * (publisher._RUNTIME_DAILY_SYNC_MAX_RESPONSE_BYTES + 1)
        return response

    def publish_offline(supplied_env, *, now):
        del now
        result = real_publish(
            supplied_env, now=OBSERVED, session_dates_loader=_open_calendar,
            client=_Client(blob), list_objects=lambda *_args, **_kwargs: [],
            read_payload=lambda _uri: None,
            report_globs=lambda _since, _now: ["gs://reports/longbridge/**/2026-09/*.json"],
            http_post=post,
        )
        results.append(result)
        return result

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _sync_describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(env))
    monkeypatch.setattr(publisher, "publish", publish_offline)
    assert publisher.main([]) == expected_exit
    captured = capsys.readouterr()
    expected_status = "already_recorded" if outcome == "existing" else "recorded"
    assert results == [(expected_status, "2026-09-28", expected_sync)]
    assert captured.out.strip().endswith(f"qrs_sync={expected_sync}")
    assert "secret" not in captured.out + captured.err
    assert len(blob.uploads) == 1
    assert blob.uploads[0]["kwargs"] == {
        "content_type": "application/json", "if_generation_match": 0, "timeout": 20, "retry": None,
    }
    assert len(requests) == expected_posts
    if requests:
        assert requests[0][1]["data"] == blob.uploads[0]["data"].encode("utf-8")
        assert requests[0][1]["allow_redirects"] is False


def test_missing_schedule_does_not_publish_a_normal_day(monkeypatch) -> None:
    target = _target()
    target["scheduler"] = {}
    env = _env(RUNTIME_TARGET_JSON=json.dumps(target))
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_describe_scheduler_job", lambda *args, **kwargs: None)
    blob = _Blob()
    with pytest.raises(RuntimeError, match="schedule_unavailable"):
        publisher.publish(
            env,
            now=OBSERVED,
            client=_Client(blob),
            list_objects=lambda *args, **kwargs: [],
            report_globs=lambda since, now: ["gs://reports/x"],
        )
    assert blob.uploads == []


def test_deployed_mismatch_or_disabled_does_not_publish_paper_closed_day(monkeypatch) -> None:
    candidate = _env(RUNTIME_TARGET_JSON=json.dumps(_target(service="svc-paper", scope="PAPER")))
    deployed = [
        {
            "strategy_profile": "paper-profile",
            "account_scope": "SG",
            "service_name": "svc-sg",
            "runtime_target_enabled": True,
        },
        {
            "strategy_profile": "paper-profile",
            "account_scope": "PAPER",
            "service_name": "svc-paper",
            "runtime_target_enabled": False,
        },
        {
            "strategy_profile": "paper-profile",
            "account_scope": "PAPER",
            "service_name": "svc-paper",
        },
    ]
    for runtime_target in deployed:
        describes = {"count": 0}

        def describe(service: str, *, project: str | None, runtime_target=runtime_target) -> dict:
            del project
            describes["count"] += 1
            assert service == "svc-paper"
            return _cloud_run(runtime_target)

        monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", describe)
        blob = _Blob()
        with pytest.raises(publisher._Rejected):
            publisher.publish(
                candidate,
                now=OBSERVED,
                session_dates_loader=_closed_calendar,
                client=_Client(blob),
                list_objects=lambda *args, **kwargs: [],
                report_globs=lambda since, now: ["gs://reports/x"],
            )
        assert blob.uploads == []
        assert describes["count"] == 1


def test_actual_scheduler_overrides_template_cron(monkeypatch) -> None:
    target = _target()
    target["scheduler"] = {"main_time": "5 16 1-7 * *", "timezone": "Asia/Hong_Kong"}
    env = _env(RUNTIME_TARGET_JSON=json.dumps(target))
    status, _, blob, _, _ = _publish(
        monkeypatch,
        env,
        [],
        jobs=[_scheduler_job("lb-paper", schedule="5 16 * * 1-5", state="ENABLED")],
    )
    stored = _stored(blob)
    record = stored["records"][0]
    assert status == "recorded"
    assert record["status"] == "missing_report"
    assert record["schedule"]["state"] == "due"
    assert record["completeness"] == "incomplete"
    assert stored["completeness"] == "incomplete"


def test_paused_missing_or_conflicting_scheduler_is_not_a_normal_day(monkeypatch) -> None:
    target = _target()
    target["scheduler"] = {"main_time": "5 16 1-7 * *", "timezone": "Asia/Hong_Kong"}
    env = _env(RUNTIME_TARGET_JSON=json.dumps(target))
    cases = [
        [_scheduler_job("lb-paper", state="PAUSED")],
        [],
        [
            _scheduler_job("lb-paper", schedule="5 16 * * 1-5"),
            _scheduler_job("lb-paper", schedule="5 16 1-7 * *"),
        ],
    ]
    for jobs in cases:
        status, _, blob, _, _ = _publish(monkeypatch, env, [], jobs=jobs)
        stored = _stored(blob)
        record = stored["records"][0]
        assert status == "recorded"
        assert stored["completeness"] == "incomplete"
        assert record["completeness"] == "incomplete"
        assert record["status"] != "not_due"
        assert set(stored["read_errors"]) <= {
            "scheduler_paused",
            "scheduler_missing",
            "scheduler_conflict",
        }


def test_scheduler_read_failure_does_not_publish_or_leak(monkeypatch) -> None:
    def list_jobs(*, project: str | None) -> list:
        del project
        raise RuntimeError("token=secret")

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", list_jobs)
    blob = _Blob()
    with pytest.raises(RuntimeError, match="schedule_unavailable") as raised:
        publisher.publish(
            _env(),
            now=OBSERVED,
            session_dates_loader=_open_calendar,
            client=_Client(blob),
            list_objects=lambda *args, **kwargs: [],
            report_globs=lambda since, now: ["gs://reports/x"],
        )
    assert blob.uploads == []
    assert "token=secret" not in str(raised.value)


def test_report_listing_and_reads_stop_at_the_bound(monkeypatch) -> None:
    class _Report:
        def __init__(self, name: str, *, size: int, body: bytes, fail: BaseException | None = None) -> None:
            self.name = name
            self.updated = OBSERVED
            self.size = size
            self.body = body
            self.fail = fail
            self.downloads: list[dict] = []

        def download_as_bytes(self, **kwargs):
            self.downloads.append(kwargs)
            if self.fail is not None:
                raise self.fail
            return self.body

    class _Scanner:
        def __init__(self, reports: list[_Report]) -> None:
            self.reports = reports
            self.pulled = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.pulled >= len(self.reports):
                raise StopIteration
            report = self.reports[self.pulled]
            self.pulled += 1
            return report

    reports = [
        _Report(f"longbridge/2026-09/report-{index:04d}.json", size=20, body=b"{}")
        for index in range(21)
    ]

    def bind(items: list[_Report], upload: _Blob):
        by_name = {item.name: item for item in items}
        scanner = _Scanner(items)
        seen: dict[str, list] = {}

        class _BoundClient(_Client):
            def list_blobs(self, bucket_or_name, **kwargs):
                del bucket_or_name
                seen["kwargs"] = [kwargs]
                return scanner

            def bucket(self, name: str):
                parent = super().bucket(name)

                class _BoundBucket:
                    def blob(self, object_name: str):
                        report = by_name.get(object_name)
                        return report if report is not None else parent.blob(object_name)

                return _BoundBucket()

        return _BoundClient(upload), scanner, seen

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(_env()))
    upload = _Blob()
    client, scanner, listed = bind(reports, upload)
    status, _, _ = publisher.publish(
        _env(),
        now=OBSERVED,
        session_dates_loader=_closed_calendar,
        client=client,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
    )
    stored = _stored(upload)
    assert status == "recorded"
    assert scanner.pulled == 21
    assert scanner.pulled < publisher._MAX_LIST_SCAN
    assert listed["kwargs"][0]["max_results"] == publisher._MAX_LIST_SCAN + 1
    assert listed["kwargs"][0]["page_size"] == publisher._MAX_LIST_SCAN + 1
    assert listed["kwargs"][0]["timeout"] == 20
    assert listed["kwargs"][0]["retry"] is None
    assert sum(len(report.downloads) for report in reports) == 20
    assert reports[0].downloads[0]["timeout"] == 20
    assert reports[0].downloads[0]["retry"] is None
    assert reports[0].downloads[0]["end"] == publisher._MAX_REPORT_BYTES - 1
    assert "listing truncated" in stored["read_errors"]
    assert stored["completeness"] == "incomplete"
    assert stored["records"][0]["completeness"] == "incomplete"

    huge = _Report("longbridge/2026-09/huge.json", size=publisher._MAX_REPORT_BYTES + 1, body=b"secret-bytes")
    huge_upload = _Blob()
    huge_client, _, _ = bind([huge], huge_upload)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(_env()))
    huge_status, _, _ = publisher.publish(
        _env(),
        now=OBSERVED,
        session_dates_loader=_closed_calendar,
        client=huge_client,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
    )
    huge_stored = _stored(huge_upload)
    assert huge_status == "recorded"
    assert huge.downloads == []
    assert huge_stored["read_errors"] == ["read_truncated"]
    assert "secret-bytes" not in huge_stored["read_errors"]
    assert huge_stored["completeness"] == "incomplete"

    broken = _Report(
        "longbridge/2026-09/broken.json",
        size=20,
        body=b"{}",
        fail=RuntimeError("token=secret"),
    )
    broken_upload = _Blob()
    broken_client, _, _ = bind([broken], broken_upload)
    broken_status, _, _ = publisher.publish(
        _env(),
        now=OBSERVED,
        session_dates_loader=_closed_calendar,
        client=broken_client,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
    )
    broken_stored = _stored(broken_upload)
    assert broken_status == "recorded"
    assert broken_stored["read_errors"] == ["read_failed"]
    assert "token=secret" not in json.dumps(broken_stored)
    assert broken_stored["records"][0]["status"] == "read_incomplete"


def test_twenty_one_reports_mark_projection_incomplete(monkeypatch) -> None:
    class _Report:
        def __init__(self, name: str, updated: dt.datetime, body: bytes = b"{}") -> None:
            self.name = name
            self.updated = updated
            self.size = len(body)
            self.body = body
            self.downloads: list[dict] = []

        def download_as_bytes(self, **kwargs):
            self.downloads.append(kwargs)
            return self.body

    class _Scanner:
        def __init__(self, reports: list[_Report]) -> None:
            self.reports = reports
            self.pulled = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.pulled >= len(self.reports):
                raise StopIteration
            report = self.reports[self.pulled]
            self.pulled += 1
            return report

    pending = json.dumps(
        _report(
            run_id="pending-21",
            summary={
                "execution_status": "pending_reconciliation",
                "broker_submission_done": False,
                "action_done": False,
                "orders_pending_count": 1,
            },
        )
    ).encode()
    reports = [
        _Report(
            f"longbridge/2026-09/{index:04d}.json",
            OBSERVED - dt.timedelta(minutes=index),
            pending if index == 20 else b"{}",
        )
        for index in range(21)
    ]
    scanner = _Scanner(reports)
    by_name = {item.name: item for item in reports}

    class _BoundClient(_Client):
        def list_blobs(self, bucket_or_name, **kwargs):
            del bucket_or_name, kwargs
            return scanner

        def bucket(self, name: str):
            parent = super().bucket(name)

            class _BoundBucket:
                def blob(self, object_name: str):
                    report = by_name.get(object_name)
                    return report if report is not None else parent.blob(object_name)

            return _BoundBucket()

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(_env()))
    upload = _Blob()
    publisher.publish(
        _env(),
        now=OBSERVED,
        session_dates_loader=_closed_calendar,
        client=_BoundClient(upload),
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
    )
    stored = _stored(upload)
    record = stored["records"][0]
    assert scanner.pulled == 21
    assert scanner.pulled < publisher._MAX_LIST_SCAN
    assert sum(len(report.downloads) for report in reports) == 20
    assert reports[-1].downloads == []
    assert b"pending_reconciliation" in reports[-1].body
    assert "pending-21" not in json.dumps(stored)
    assert stored["read_errors"] == ["listing truncated"]
    assert stored["completeness"] == "incomplete"
    assert record["completeness"] == "incomplete"
    assert record["schedule"]["state"] == "market_closed"
    assert record["status"] != "market_closed"


def test_name_sorted_scan_keeps_newer_objects_and_marks_truncation(monkeypatch) -> None:
    class _Report:
        def __init__(self, name: str, updated: dt.datetime) -> None:
            self.name = name
            self.updated = updated
            self.size = 2
            self.downloads: list[dict] = []

        def download_as_bytes(self, **kwargs):
            self.downloads.append(kwargs)
            return b"{}"

    class _Scanner:
        def __init__(self, reports: list[_Report]) -> None:
            self.reports = reports
            self.pulled = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.pulled >= len(self.reports):
                raise StopIteration
            report = self.reports[self.pulled]
            self.pulled += 1
            return report

    total = publisher._MAX_LIST_SCAN + 10
    reports = []
    for index in range(total):
        if 94 <= index <= 113:
            updated = OBSERVED - dt.timedelta(seconds=113 - index)
        else:
            updated = OBSERVED - dt.timedelta(hours=20, seconds=index)
        reports.append(_Report(f"longbridge/2026-09/{index:04d}.json", updated))
    scanner = _Scanner(reports)
    by_name = {item.name: item for item in reports}

    class _BoundClient(_Client):
        def list_blobs(self, bucket_or_name, **kwargs):
            del bucket_or_name, kwargs
            return scanner

        def bucket(self, name: str):
            parent = super().bucket(name)

            class _BoundBucket:
                def blob(self, object_name: str):
                    report = by_name.get(object_name)
                    return report if report is not None else parent.blob(object_name)

            return _BoundBucket()

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", _describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda *, project: _jobs_for_env(_env()))
    upload = _Blob()
    publisher.publish(
        _env(),
        now=OBSERVED,
        session_dates_loader=_closed_calendar,
        client=_BoundClient(upload),
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
    )
    stored = _stored(upload)
    downloaded = {report.name for report in reports if report.downloads}
    expected = {f"longbridge/2026-09/{index:04d}.json" for index in range(94, 114)}
    assert len(reports) >= 114
    assert scanner.pulled == publisher._MAX_LIST_SCAN + 1
    assert downloaded == expected
    assert "longbridge/2026-09/0000.json" not in downloaded
    assert f"longbridge/2026-09/{total - 1:04d}.json" not in downloaded
    assert "listing truncated" in stored["read_errors"]
    assert stored["completeness"] == "incomplete"
    assert stored["records"][0]["completeness"] == "incomplete"


def test_scheduler_identity_uses_helper_uri_not_run_path(monkeypatch) -> None:
    target = _target()
    target["scheduler"] = {"main_time": "5 16 * * 1-5", "timezone": "Asia/Hong_Kong"}
    env = _env(RUNTIME_TARGET_JSON=json.dumps(target))
    status, _, blob, _, _ = _publish(
        monkeypatch,
        env,
        [],
        jobs=[_scheduler_job("lb-paper", schedule="5 16 1-7 * *", path="/run")],
    )
    stored = _stored(blob)
    record = stored["records"][0]
    assert status == "recorded"
    assert stored["read_errors"] == ["scheduler_missing"]
    assert record["status"] != "not_due"
    assert record["completeness"] == "incomplete"
    assert stored["completeness"] == "incomplete"

    selected, _, selected_blob, _, _ = _publish(
        monkeypatch,
        env,
        [],
        jobs=[
            _scheduler_job("lb-paper", schedule="5 16 1-7 * *", path="/run"),
            _scheduler_job("lb-paper", schedule="5 16 * * 1-5", path="/"),
            _scheduler_job("lb-paper", schedule="15 9 * * 1-5", path="/probe"),
        ],
    )
    selected_record = _stored(selected_blob)["records"][0]
    assert selected == "recorded"
    assert selected_record["status"] == "missing_report"
    assert selected_record["schedule"]["state"] == "due"


def test_disabled_target_is_still_projected(monkeypatch) -> None:
    env = _env(RUNTIME_TARGET_JSON=json.dumps(_target(enabled=False)))
    status, _, blob, _, _ = _publish(monkeypatch, env, [])
    assert status == "recorded"
    assert _stored(blob)["records"][0]["status"] == "missing_report"


def test_runtime_daily_sync_posts_only_new_projection_and_checks_qrs_contract(monkeypatch) -> None:
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        payload = json.loads(kwargs["data"])
        record = payload["records"][0]
        return _SyncResponse(payload={
            "ok": True,
            "stored": True,
            "business_date": record["business_date"],
            "target_key": "longbridge-quant-paper-service|russell_top50_leader_rotation|paper",
            "account_key": "synthetic-account-key",
        })

    status, business_date, blob, _, calls = _publish(
        monkeypatch,
        _sync_env(),
        [],
        http_post=post,
        describe=_sync_describe,
    )
    assert status == "recorded"
    assert business_date == "2026-09-28"
    assert calls["sync_status"] == "recorded"
    assert len(requests) == 1
    stored_projection = json.loads(blob.uploads[0]["data"])
    assert stored_projection["records"][0]["target"]["account_scope"] == "paper"
    url, kwargs = requests[0]
    assert url == "https://qrs.example.com/api/runtime-daily/sync"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == publisher._RUNTIME_DAILY_SYNC_TIMEOUT_SECONDS
    assert kwargs["stream"] is True
    assert kwargs["data"] == blob.uploads[0]["data"].encode("utf-8")
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-test-token"


@pytest.mark.parametrize(
    "overrides",
    [
        {"EXECUTION_EVIDENCE_SYNC_TOKEN": ""},
        {"RUNTIME_DAILY_SYNC_URL": ""},
        {"RUNTIME_DAILY_SYNC_URL": "https://qrs.example.com/wrong"},
    ],
)
def test_runtime_daily_sync_missing_or_invalid_auth_config_does_not_post(monkeypatch, overrides) -> None:
    requests = []
    status, _, blob, _, calls = _publish(
        monkeypatch,
        _sync_env(**overrides),
        [],
        http_post=lambda *args, **kwargs: requests.append((args, kwargs)),
        describe=_sync_describe,
    )
    assert status == "recorded"
    assert calls["sync_status"] == "rejected"
    assert blob.uploads
    assert requests == []


def test_runtime_daily_sync_skips_existing_object_and_unknown_store(monkeypatch) -> None:
    requests = []
    existing = _Blob(fail=PreconditionFailed("already exists"))
    status, _, _, _, calls = _publish(
        monkeypatch,
        _sync_env(),
        [],
        blob=existing,
        http_post=lambda *args, **kwargs: requests.append((args, kwargs)),
        describe=_sync_describe,
    )
    assert status == "already_recorded"
    assert calls["sync_status"] == "skipped_existing"
    assert requests == []

    unknown = _Blob(fail=GoogleAPICallError("synthetic write failure"))
    with pytest.raises(RuntimeError, match="write_unknown"):
        _publish(
            monkeypatch,
            _sync_env(),
            [],
            blob=unknown,
            http_post=lambda *args, **kwargs: requests.append((args, kwargs)),
            describe=_sync_describe,
        )
    assert requests == []


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (TimeoutError("synthetic timeout"), "unknown"),
        (_SyncResponse(status_code=302, redirect=True), "rejected"),
        (_SyncResponse(payload={"ok": True, "stored": True, "business_date": "wrong", "target_key": "wrong", "account_key": "synthetic"}), "unknown"),
        (_SyncResponse(status_code=401, payload={"error": "unauthorized"}), "rejected"),
    ],
)
def test_runtime_daily_sync_failure_is_separate_and_never_retried(monkeypatch, response, expected) -> None:
    requests = []

    def post(*args, **kwargs):
        requests.append((args, kwargs))
        if isinstance(response, BaseException):
            raise response
        return response

    status, _, blob, _, calls = _publish(
        monkeypatch,
        _sync_env(),
        [],
        http_post=post,
        describe=_sync_describe,
    )
    assert status == "recorded"
    assert calls["sync_status"] == expected
    assert blob.uploads
    assert len(requests) == 1


def test_runtime_daily_sync_stays_off_by_default(monkeypatch) -> None:
    requests = []
    status, _, blob, _, calls = _publish(
        monkeypatch,
        _env(),
        [],
        http_post=lambda *args, **kwargs: requests.append((args, kwargs)),
    )
    assert status == "recorded"
    assert calls["sync_status"] == "disabled"
    assert blob.uploads
    assert requests == []


def test_runtime_daily_sync_rejects_noncontract_paper_target_locally(monkeypatch) -> None:
    requests = []
    target = _target(service="other-paper-service")
    target.update(strategy_profile="russell_top50_leader_rotation", market_timezone="America/New_York")
    status, _, blob, _, calls = _publish(
        monkeypatch,
        _sync_env(RUNTIME_TARGET_JSON=json.dumps(target)),
        [],
        http_post=lambda *args, **kwargs: requests.append((args, kwargs)),
        describe=_sync_describe,
    )
    assert status == "recorded"
    assert calls["sync_status"] == "rejected"
    assert blob.uploads
    assert requests == []


def test_workflow_adds_a_paper_projection_after_auth_with_existing_sync_token() -> None:
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text(encoding="utf-8")
    heartbeat_step = workflow.index("name: Check recent execution report")
    projection = workflow.index("name: Publish daily runtime projection")
    assert heartbeat_step < projection
    assert "uv run --no-sync python scripts/execution_report_heartbeat.py" in workflow
    assert "uv run --no-sync python scripts/publish_daily_runtime_projection.py" in workflow
    step = workflow.split("name: Publish daily runtime projection", 1)[1].split("\n      - ", 1)[0]
    assert "matrix.target.label == 'PAPER'" in step
    assert "vars.RUNTIME_DAILY_PROJECTION_ENABLED == 'true'" in step
    assert "!cancelled()" in step
    assert "steps.gcp_auth_primary.outcome == 'success'" in step
    assert "steps.gcp_auth_retry.outcome == 'success'" in step
    assert "RUNTIME_DAILY_PROJECTION_ENABLED: ${{ vars.RUNTIME_DAILY_PROJECTION_ENABLED }}" in step
    assert "RUNTIME_DAILY_SYNC_ENABLED: ${{ vars.RUNTIME_DAILY_SYNC_ENABLED }}" in step
    assert "RUNTIME_DAILY_SYNC_URL: ${{ vars.RUNTIME_DAILY_SYNC_URL }}" in step
    assert "EXECUTION_EVIDENCE_SYNC_TOKEN: ${{ vars.RUNTIME_DAILY_SYNC_ENABLED == 'true' && secrets.EXECUTION_EVIDENCE_SYNC_TOKEN || '' }}" in step



# Explicitly opted-in verified-path fixtures and regressions.


@pytest.fixture(autouse=True)
def _deny_external_io(monkeypatch):
    """Every daily-caller test is offline, including accidental fallback paths."""
    def denied(*args, **kwargs):
        raise AssertionError("external I/O forbidden in synthetic daily-caller tests")

    monkeypatch.setattr(heartbeat, "_run_gcloud", denied)
    monkeypatch.setattr(publisher, "_storage_client", denied)
    monkeypatch.setattr(requests.sessions.Session, "request", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)


def _verified_cloud_run(runtime_target: dict, *, enabled_env: str | None = None, service: str | None = None) -> dict:
    env = [{"name": "RUNTIME_TARGET_JSON", "value": json.dumps(runtime_target)}]
    if enabled_env is not None:
        env.append({"name": "RUNTIME_TARGET_ENABLED", "value": enabled_env})
    service = service or runtime_target["service_name"]
    return {
        "metadata": {"name": service},
        "status": {
            "url": f"https://{service}-abcd.a.run.app",
            "traffic": [{"revisionName": f"{service}-r1", "percent": 100}],
        },
        "spec": {"template": {"spec": {"containers": [{"env": env}]}}},
    }


def _verified_describe(service: str, *, project: str | None) -> dict:
    del project
    return _verified_cloud_run(
        {
            "strategy_profile": "paper-profile",
            "account_scope": "PAPER",
            "service_name": service,
            "runtime_target_enabled": True,
        }
    )


def _verified_sync_describe(service: str, *, project: str | None) -> dict:
    del project
    return _verified_cloud_run(
        {
            "strategy_profile": "russell_top50_leader_rotation",
            "account_scope": "PAPER",
            "service_name": service,
            "runtime_target_enabled": True,
        }
    )


def _serving_context(service="lb-paper", *, service_payload=None, runtime_target=None, path="/run"):
    runtime_target = runtime_target or {
        "strategy_profile": "paper-profile", "account_scope": "PAPER",
        "service_name": service, "runtime_target_enabled": True,
    }
    payload = deepcopy(service_payload) if service_payload is not None else _verified_cloud_run(runtime_target)
    payload["metadata"] = {"name": service}
    payload["status"] = {
        "url": f"https://{service}-abcd.a.run.app",
        "traffic": [{"revisionName": f"{service}-r1", "percent": 100}],
    }
    return {
        "service": payload,
        "revision": {
            "metadata": {"name": f"{service}-r1", "labels": {"commit-sha": "a" * 40}},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            "spec": deepcopy(payload["spec"]["template"]["spec"]),
        },
        "route_contract": {
            "service": service, "source_commit": "a" * 40,
            "path": path, "http_method": "POST",
        },
    }


def _context_for_env(env, *, describe=_verified_describe):
    service = json.loads(env["RUNTIME_TARGET_JSON"])["service"]
    return _serving_context(service, service_payload=describe(service, project="synthetic"))


def _verified_scheduler_job(
    service: str,
    *,
    schedule: str = "5 16 * * 1-5",
    timezone: str = "Asia/Hong_Kong",
    state: str = "ENABLED",
    path: str = "/run",
) -> dict:
    return {
        "name": f"projects/synthetic/locations/test/jobs/{service}-scheduler",
        "state": state,
        "schedule": schedule,
        "timeZone": timezone,
        "httpTarget": {"uri": f"https://{service}-abcd.a.run.app{path}", "httpMethod": "POST"},
    }


def _verified_jobs_for_env(env: dict[str, str]) -> list[dict]:
    target = json.loads(env["RUNTIME_TARGET_JSON"])
    scheduler = target.get("scheduler") or {}
    return [
        _verified_scheduler_job(
            str(target.get("service") or "lb-paper"),
            schedule=str(scheduler.get("main_time") or "5 16 * * 1-5"),
            timezone=str(scheduler.get("timezone") or "Asia/Hong_Kong"),
        )
    ]


def _publish_verified(
    monkeypatch,
    env,
    reports,
    *,
    calendar=_open_calendar,
    blob=None,
    list_error=False,
    limit=None,
    jobs="default",
    http_post=None,
    describe=_verified_describe,
    serving_context="fixture",
):
    blob = blob or _Blob()
    client = _Client(blob)
    calls = {"listed": 0, "read": 0}

    def list_objects(pattern, *, project):
        del pattern, project
        calls["listed"] += 1
        if list_error:
            raise RuntimeError("secret listing detail")
        return reports

    def read_payload(uri: str):
        calls["read"] += 1
        for entry in reports:
            if entry.get("url") == uri:
                return entry.get("payload")
        return None

    if limit is not None:
        env = {**env, "RUNTIME_HEARTBEAT_MAX_REPORTS_TO_READ": str(limit)}
    selected = _verified_jobs_for_env(env) if jobs == "default" else jobs
    if serving_context == "fixture":
        serving_context = _context_for_env(env, describe=describe)

    def list_jobs(*, project: str | None) -> list:
        del project
        return selected

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", describe)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", list_jobs)
    status, business_date, sync_status = publisher.publish_verified(
        env,
        now=OBSERVED,
        session_dates_loader=calendar,
        client=client,
        list_objects=list_objects,
        read_payload=read_payload,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
        http_post=http_post,
        serving_context=serving_context,
    )
    calls["sync_status"] = sync_status
    return status, business_date, blob, client, calls


def test_projections_cover_quiet_closed_missing_and_prior_unknown_verified(monkeypatch) -> None:
    quiet_report = _entry("gs://reports/quiet.json", "2026-09-28T08:07:00Z")
    quiet_report["payload"] = _report()
    status, business_date, blob, client, _calls = _publish_verified(monkeypatch, _env(), [quiet_report])
    stored = _stored(blob)
    assert status == "recorded"
    assert business_date == "2026-09-28"
    assert client.buckets == ["paper-bucket"]
    assert client._bucket.names == ["runtime_daily/longbridge/paper/2026-09-28/20260928T084000000000Z.json"]
    assert stored["records"][0]["status"] == "no_submission"
    assert stored["records"][0]["runs"][0]["run_id"] == "run-1"
    assert "secret" not in json.dumps(stored)

    closed_status, _, closed_blob, _, _ = _publish_verified(monkeypatch, _env(), [], calendar=_closed_calendar)
    assert closed_status == "recorded"
    assert _stored(closed_blob)["records"][0]["status"] == "market_closed"

    missing_status, _, missing_blob, _, _ = _publish_verified(monkeypatch, _env(), [])
    assert missing_status == "recorded"
    assert _stored(missing_blob)["records"][0]["status"] == "missing_report"

    prior = _entry("gs://reports/prior.json", "2026-09-27T08:07:00Z")
    prior["payload"] = _report(
        run_id="prior-unknown",
        started_at="2026-09-27T16:06:00+08:00",
        finished_at="2026-09-27T16:07:00+08:00",
        summary={
            "execution_status": "unknown",
            "broker_submission_done": False,
            "action_done": False,
            "orders_pending_count": 0,
        },
    )
    unknown_status, _, unknown_blob, _, _ = _publish_verified(
        monkeypatch, _env(), [prior], calendar=_closed_calendar
    )
    assert unknown_status == "recorded"
    unknown = _stored(unknown_blob)["records"][0]
    assert unknown["status"] == "unknown"
    assert unknown["schedule"]["state"] == "market_closed"


def test_existing_object_is_not_overwritten_and_unknown_write_is_not_retried_verified(monkeypatch) -> None:
    exists = _Blob(fail=PreconditionFailed("already present"))
    status, business_date, blob, _, _ = _publish_verified(monkeypatch, _env(), [], blob=exists)
    assert status == "already_recorded"
    assert business_date == "2026-09-28"
    assert len(blob.uploads) == 1
    assert blob.uploads[0]["kwargs"]["if_generation_match"] == 0

    unknown = _Blob(fail=RuntimeError("token=secret"))
    with pytest.raises(publisher._KnownFailure) as failure:
        _publish_verified(monkeypatch, _env(), [], blob=unknown)
    assert failure.value.code == "write_unknown"
    assert "token=secret" not in str(failure.value)
    assert len(unknown.uploads) == 1


def test_runtime_daily_sync_posts_only_new_projection_and_checks_qrs_contract_verified(monkeypatch) -> None:
    requests = []

    def post(url, **kwargs):
        requests.append((url, kwargs))
        payload = json.loads(kwargs["data"])
        record = payload["records"][0]
        return _SyncResponse(payload={
            "ok": True,
            "stored": True,
            "business_date": record["business_date"],
            "target_key": "longbridge-quant-paper-service|russell_top50_leader_rotation|paper",
            "account_key": "synthetic-account-key",
        })

    status, business_date, blob, _, calls = _publish_verified(
        monkeypatch,
        _sync_env(),
        [],
        http_post=post,
        describe=_verified_sync_describe,
    )
    assert status == "recorded"
    assert business_date == "2026-09-28"
    assert calls["sync_status"] == "recorded"
    assert len(requests) == 1
    stored_projection = json.loads(blob.uploads[0]["data"])
    assert stored_projection["records"][0]["target"]["account_scope"] == "paper"
    url, kwargs = requests[0]
    assert url == "https://qrs.example.com/api/runtime-daily/sync"
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == publisher._RUNTIME_DAILY_SYNC_TIMEOUT_SECONDS
    assert kwargs["stream"] is True
    assert kwargs["data"] == blob.uploads[0]["data"].encode("utf-8")
    assert kwargs["headers"]["Authorization"] == "Bearer synthetic-test-token"


@pytest.mark.parametrize("jobs", [
    [_verified_scheduler_job("lb-paper", state="PAUSED")],
    [],
    [_verified_scheduler_job("lb-paper"), _verified_scheduler_job("lb-paper", schedule="5 16 1-7 * *")],
])
def test_paused_missing_or_conflicting_scheduler_fails_before_report_reads_verified(monkeypatch, jobs) -> None:
    context = _serving_context()
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", lambda *args, **kwargs: context["service"])
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda **kwargs: jobs)

    def boom(*args, **kwargs):
        raise AssertionError("report, archive or POST must not run")

    blob = _Blob()
    client = _Client(blob)
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_env(), now=OBSERVED, serving_context=context, client=client,
                          list_objects=boom, read_payload=boom, report_globs=boom, http_post=boom)
    assert blob.uploads == []
    assert client.buckets == []


@pytest.mark.parametrize("declared_path,other_path", [("/run", "/"), ("/", "/run")])
def test_scheduler_identity_uses_only_independently_declared_route_verified(monkeypatch, declared_path, other_path) -> None:
    context = _serving_context(path=declared_path)
    status, _, blob, _, _ = _publish_verified(
        monkeypatch, _env(), [], serving_context=context,
        jobs=[
            _verified_scheduler_job("lb-paper", schedule="5 16 1-7 * *", path=other_path),
            _verified_scheduler_job("lb-paper", schedule="5 16 * * 1-5", path=declared_path),
            _verified_scheduler_job("lb-paper", path="/probe"),
        ],
    )
    assert status == "recorded"
    assert _stored(blob)["records"][0]["schedule"]["state"] == "due"
    rejected_blob = _Blob()
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        _publish_verified(monkeypatch, _env(), [], blob=rejected_blob, serving_context=context,
                 jobs=[_verified_scheduler_job("lb-paper", path=other_path)])
    assert rejected_blob.uploads == []


def test_missing_serving_context_fails_before_any_io_verified(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", boom)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", boom)
    monkeypatch.setattr(publisher, "_storage_client", boom)
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_sync_env(), now=OBSERVED, list_objects=boom, read_payload=boom,
                          report_globs=boom, http_post=boom)


@pytest.mark.parametrize("drift", ["origin", "service", "revision", "traffic_split"])
def test_changed_serving_readback_fails_before_report_storage_or_post_verified(monkeypatch, drift) -> None:
    context = _serving_context()
    readback = deepcopy(context["service"])
    if drift == "origin":
        readback["status"]["url"] = "https://other-service.a.run.app"
    elif drift == "service":
        readback["metadata"]["name"] = "other-service"
    elif drift == "revision":
        readback["status"]["traffic"][0]["revisionName"] = "lb-paper-r2"
    else:
        readback["status"]["traffic"] = [
            {"revisionName": "lb-paper-r1", "percent": 50},
            {"revisionName": "lb-paper-r2", "percent": 50},
        ]
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", lambda *args, **kwargs: readback)

    def boom(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", boom)
    monkeypatch.setattr(publisher, "_storage_client", boom)
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_env(), now=OBSERVED, serving_context=context,
                          list_objects=boom, read_payload=boom, report_globs=boom, http_post=boom)


@pytest.mark.parametrize("problem", [
    "origin_missing", "route_missing", "wrong_route_commit", "wrong_revision",
    "unready_revision", "missing_revision_spec",
])
def test_unproven_serving_context_fails_before_any_io_verified(monkeypatch, problem) -> None:
    context = _serving_context()
    if problem == "origin_missing":
        del context["service"]["status"]["url"]
    elif problem == "route_missing":
        del context["route_contract"]
    elif problem == "wrong_route_commit":
        context["route_contract"]["source_commit"] = "b" * 40
    elif problem == "wrong_revision":
        context["revision"]["metadata"]["name"] = "lb-paper-r2"
    elif problem == "unready_revision":
        context["revision"]["status"]["conditions"][0]["status"] = "False"
    else:
        del context["revision"]["spec"]

    def boom(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", boom)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", boom)
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_env(), now=OBSERVED, serving_context=context,
                          list_objects=boom, read_payload=boom, report_globs=boom, http_post=boom)


def test_confirmed_scheduler_without_context_erases_template_without_lookup_verified(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("scheduler lookup must not run")

    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", boom)
    target = _target()
    selected, error = publisher._confirmed_scheduler(target, project="synthetic")
    assert error == "scheduler_context_unevaluable"
    assert selected["scheduler"] == {}
    assert target["scheduler"]["main_time"] == "5 16 * * 1-5"


@pytest.mark.parametrize("problem", ["wrong_route", "wrong_origin", "wrong_method"])
def test_unmatched_scheduler_cannot_archive_even_a_quiet_report_verified(monkeypatch, problem) -> None:
    context = _context_for_env(_sync_env(), describe=_verified_sync_describe)
    service = context["service"]["metadata"]["name"]
    job = _verified_scheduler_job(service)
    if problem == "wrong_route":
        job["httpTarget"]["uri"] = context["service"]["status"]["url"] + "/"
    elif problem == "wrong_origin":
        job["httpTarget"]["uri"] = context["service"]["status"]["url"] + ".other.test/run"
    else:
        job["httpTarget"]["httpMethod"] = "GET"
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", lambda *args, **kwargs: context["service"])
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", lambda **kwargs: [job])
    report = _entry("gs://reports/quiet.json", "2026-09-28T08:07:00Z")
    report["payload"] = _report()
    calls = {"listed": 0, "read": 0, "post": 0}

    def listing(*args, **kwargs):
        calls["listed"] += 1
        return [report]

    def read(*args, **kwargs):
        calls["read"] += 1
        return report["payload"]

    def post(*args, **kwargs):
        calls["post"] += 1
        return _SyncResponse()

    blob = _Blob()
    client = _Client(blob)
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_sync_env(), now=OBSERVED, serving_context=context, client=client,
                          list_objects=listing, read_payload=read, http_post=post)
    assert calls == {"listed": 0, "read": 0, "post": 0}
    assert blob.uploads == []
    assert client.buckets == []


def test_serving_revision_config_wins_over_a_staged_service_template_verified(monkeypatch) -> None:
    context = _serving_context()
    readback = deepcopy(context["service"])
    staged = {
        "strategy_profile": "staged-profile", "account_scope": "SG",
        "service_name": "staged-service", "runtime_target_enabled": False,
    }
    readback["spec"] = _verified_cloud_run(staged)["spec"]
    calls = []

    def describe(service, *, project):
        calls.append((service, project))
        return readback

    status, _, blob, _, _ = _publish_verified(monkeypatch, _env(), [], describe=describe, serving_context=context)
    assert status == "recorded"
    assert len(calls) == 1
    assert _stored(blob)["records"][0]["target"] == {
        "service": "lb-paper", "strategy_profile": "paper-profile", "account_scope": "PAPER",
    }


def test_staged_enabled_template_cannot_override_disabled_serving_revision_verified(monkeypatch) -> None:
    context = _serving_context(runtime_target={
        "strategy_profile": "paper-profile", "account_scope": "PAPER",
        "service_name": "lb-paper", "runtime_target_enabled": False,
    })
    # The fresh service template is enabled, but it is not the serving config.
    blob = _Blob()
    with pytest.raises(publisher._Rejected, match="deployed_target_rejected"):
        _publish_verified(monkeypatch, _env(), [], serving_context=context, describe=_verified_describe, blob=blob)
    assert blob.uploads == []


def test_transport_cannot_mutate_the_supplied_revision_observation_verified(monkeypatch) -> None:
    context = _serving_context()
    original = deepcopy(context)

    def describe(service, *, project):
        context["revision"]["spec"]["containers"][0]["env"][0]["value"] = json.dumps({
            "strategy_profile": "changed-profile", "account_scope": "SG",
            "service_name": service, "runtime_target_enabled": False,
        })
        return original["service"]

    status, _, blob, _, _ = _publish_verified(monkeypatch, _env(), [], describe=describe, serving_context=context)
    assert status == "recorded"
    assert _stored(blob)["records"][0]["target"]["strategy_profile"] == "paper-profile"


def test_legacy_default_does_not_silently_opt_in_or_emit_verified_health(monkeypatch) -> None:
    def strict_path_must_not_run(*args, **kwargs):
        raise AssertionError("default natural publication must remain unadopted")

    monkeypatch.setattr(publisher, "publish_verified", strict_path_must_not_run)
    monkeypatch.setattr(publisher, "_confirmed_scheduler", strict_path_must_not_run)
    status, business_date, blob, _, calls = _publish(monkeypatch, _env(), [])
    assert (status, business_date, calls["sync_status"]) == ("recorded", "2026-09-28", "disabled")
    stored = _stored(blob)
    assert stored["read_errors"] == []
    assert stored["records"][0]["status"] == "missing_report"
    serialized = json.dumps(stored)
    assert "cycle_health" not in serialized
    assert "route_contract" not in serialized
    assert "verified_route" not in serialized


def test_verified_and_legacy_daily_records_match_when_each_schedule_is_established(monkeypatch) -> None:
    report = _entry("gs://reports/quiet.json", "2026-09-28T08:07:00Z")
    report["payload"] = _report()
    legacy_status, legacy_date, legacy_blob, _, legacy_calls = _publish(monkeypatch, _env(), [report])
    verified_status, verified_date, verified_blob, _, verified_calls = _publish_verified(
        monkeypatch, _env(), [report],
    )
    assert (legacy_status, legacy_date, legacy_calls["sync_status"]) == (
        verified_status, verified_date, verified_calls["sync_status"],
    )
    assert legacy_blob.uploads[0]["data"] == verified_blob.uploads[0]["data"]
    assert _stored(legacy_blob)["records"][0]["status"] == "no_submission"
    with pytest.raises(publisher._KnownFailure, match="schedule_unevaluable"):
        publisher.publish_verified(_env(), now=OBSERVED)


def _one_shot_env(**overrides):
    env = _sync_env(
        RUNTIME_TARGET_ENABLED="false",
        RUNTIME_HEARTBEAT_GCS_URIS="gs://synthetic-existing/reports",
        CLOUD_RUN_SERVICE="longbridge-quant-paper-service",
        CLOUD_RUN_REGION="synthetic-region",
        GOOGLE_APPLICATION_CREDENTIALS="/synthetic/wif.json",
        CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE="/synthetic/wif.json",
    )
    env.update(overrides)
    return env


def _one_shot_publish(
    monkeypatch, *, enabled=False, report=None, blob=None, http_post=None, changes=None,
    private_result_path=None, without_workflow_report_root=False, workflow_runtime_enabled="false",
):
    env = _one_shot_env(
        RUNTIME_TARGET_ENABLED=workflow_runtime_enabled,
        RUNTIME_HEARTBEAT_GCS_URIS="" if without_workflow_report_root else "gs://synthetic-existing/reports",
        EXECUTION_REPORT_GCS_URI="",
    )
    deployed = {
        "strategy_profile": "russell_top50_leader_rotation", "account_scope": "PAPER",
        "service_name": env["CLOUD_RUN_SERVICE"], "market_timezone": "America/New_York",
    }
    if enabled is not None:
        deployed["runtime_target_enabled"] = enabled
    deployed.update(changes or {})
    observed = {"described": 0, "post": 0}
    blob = blob or _Blob()
    def describe(service, *, project):
        observed["described"] += 1
        assert service == env["CLOUD_RUN_SERVICE"]
        result = _cloud_run(deployed)
        result["status"] = {"traffic": [{"revisionName": "synthetic-paper-001", "percent": 100}]}
        return result
    def read_revision(command):
        assert command[:5] == ["gcloud", "run", "revisions", "describe", "synthetic-paper-001"]
        from types import SimpleNamespace
        revision = {
            "metadata": {"name": "synthetic-paper-001", "labels": {"serving.knative.dev/service": env["CLOUD_RUN_SERVICE"]}},
            "spec": _cloud_run(deployed)["spec"]["template"]["spec"],
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        revision["spec"]["containers"][0].setdefault("env", []).append(
            {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-existing/reports"}
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(revision))
    monkeypatch.setattr(heartbeat, "_run_gcloud", read_revision)
    def prohibited(*_a, **_kw):
        pytest.fail("single disabled observation must not invoke Scheduler/heartbeat")
    monkeypatch.setattr(heartbeat, "_describe_cloud_run_service", describe)
    monkeypatch.setattr(heartbeat, "_hydrate_runtime_target_schedules", prohibited)
    monkeypatch.setattr(heartbeat, "_list_scheduler_jobs", prohibited)
    monkeypatch.setattr(heartbeat, "_describe_scheduler_job", prohibited)
    monkeypatch.setattr(heartbeat, "main", prohibited)
    monkeypatch.setattr(publisher, "_storage_client", prohibited)
    monkeypatch.setattr(publisher, "_legacy_confirmed_scheduler", prohibited)
    objects = [] if report is None else [{"url": "gs://synthetic-existing/reports/run.json", "metadata": {"updated": OBSERVED.isoformat()}}]
    def post(url, **kwargs):
        observed["post"] += 1
        assert url == publisher._ONE_SHOT_SYNC_URL
        assert kwargs["allow_redirects"] is False
        assert kwargs["data"].decode() == blob.uploads[0]["data"]
        if http_post is not None:
            return http_post(url, **kwargs)
        body = json.loads(kwargs["data"])
        return _SyncResponse(payload={
            "ok": True, "stored": True, "business_date": body["records"][0]["business_date"],
            "target_key": publisher._RUNTIME_DAILY_SYNC_TARGET_KEY, "account_key": "synthetic-private-account",
        })
    result = publisher.publish(
        env, now=OBSERVED, one_shot_only=True, client=_Client(blob),
        list_objects=lambda *_a, **_kw: objects,
        read_payload=lambda _: report,
        report_globs=lambda *_: ["gs://synthetic-existing/reports/*.json"],
        http_post=post,
        private_result_path=private_result_path,
    )
    return result, blob, observed


@pytest.mark.parametrize("enabled,reason", [
    (False, "runtime_target_disabled"), (True, "runtime_target_enablement_conflict"),
    (None, "runtime_target_enablement_unknown"), ("invalid", "runtime_target_enablement_unknown"),
])
def test_one_shot_stopped_target_is_incomplete_without_scheduler(monkeypatch, enabled, reason):
    result, blob, observed = _one_shot_publish(monkeypatch, enabled=enabled)
    assert result == ("recorded", "2026-09-28", "recorded")
    body = _stored(blob)
    record = body["records"][0]
    assert body["completeness"] == record["completeness"] == "incomplete"
    assert record["status"] == "read_incomplete"
    assert record["schedule"]["state"] == "unevaluable"
    assert record["schedule"]["reason"] == reason
    assert body["read_errors"] == [reason]
    assert all(record["schedule"][key] is None for key in ("latest_due_at", "next_due_at", "grace_ends_at", "publication_grace_ended"))
    assert record["runs"] == [] and record["fills"]["count"] is None
    assert observed == {"described": 1, "post": 1}
    assert "synthetic-private-account" not in blob.uploads[0]["data"]


def test_one_shot_active_paper_records_only_incomplete_schedule_observation(monkeypatch):
    result, blob, observed = _one_shot_publish(
        monkeypatch, enabled=True, workflow_runtime_enabled="true"
    )
    record = _stored(blob)["records"][0]
    assert result == ("recorded", "2026-09-28", "recorded")
    assert record["completeness"] == "incomplete"
    assert record["status"] == "read_incomplete"
    assert record["schedule"]["reason"] == "runtime_target_enabled_schedule_unverified"
    assert record["runs"] == [] and record["fills"]["count"] is None
    # The fixture prohibits heartbeat, Scheduler and broker/run paths; only the
    # one readback and existing daily QRS ACK occur.
    assert observed == {"described": 1, "post": 1}


def test_one_shot_uses_verified_serving_report_root_when_workflow_root_is_absent(monkeypatch):
    result, _blob, observed = _one_shot_publish(monkeypatch, without_workflow_report_root=True)
    assert result == ("recorded", "2026-09-28", "recorded")
    assert observed == {"described": 1, "post": 1}


@pytest.mark.parametrize("status", ["unknown", "reconciliation_required"])
def test_disabled_observation_does_not_erase_historical_unresolved_fact(monkeypatch, status):
    report = _report(
        service_name="longbridge-quant-paper-service", strategy_profile="russell_top50_leader_rotation",
        started_at="2026-09-27T12:00:00Z", finished_at="2026-09-27T12:01:00Z",
        summary={"execution_status": status, "broker_submission_done": False, "action_done": False, "orders_pending_count": 1 if status == "reconciliation_required" else 0},
    )
    _result, blob, _observed = _one_shot_publish(monkeypatch, report=report)
    record = _stored(blob)["records"][0]
    assert record["status"] == status and record["completeness"] == "incomplete"
    assert len(record["runs"]) == 1 and record["runs"][0]["activity"] == status


def test_single_observation_derives_same_bucket_child_and_does_not_enable_runtime():
    original = _one_shot_env(
        RUNTIME_DAILY_PROJECTION_GCS_PREFIX="gs://unapproved-bucket/runtime_daily",
        RUNTIME_DAILY_SYNC_URL="https://unapproved.example.com/api/runtime-daily/sync",
    )
    scoped = publisher._one_shot_environment(original)
    assert scoped["RUNTIME_DAILY_PROJECTION_GCS_PREFIX"] == "gs://synthetic-existing/reports/runtime_daily"
    assert scoped["RUNTIME_DAILY_SYNC_URL"] == publisher._ONE_SHOT_SYNC_URL
    assert scoped["RUNTIME_TARGET_ENABLED"] == original["RUNTIME_TARGET_ENABLED"] == "false"
    assert original["RUNTIME_DAILY_PROJECTION_GCS_PREFIX"].startswith("gs://unapproved-bucket")


@pytest.mark.parametrize("value", [
    "", "gs://synthetic-existing/one,gs://synthetic-existing/two", "https://synthetic.example.com/reports",
    "gs://synthetic-existing/reports/**", "gs://synthetic-existing/reports/../other", "gs://synthetic-existing//reports",
    "gs://synthetic-existing/reports?query=x", "gs://synthetic-existing/reports%2Fother",
])
def test_one_shot_report_base_must_be_unique_canonical_gs_uri(value):
    with pytest.raises(publisher._Rejected):
        publisher._one_shot_environment(_one_shot_env(RUNTIME_HEARTBEAT_GCS_URIS=value, EXECUTION_REPORT_GCS_URI=""))


@pytest.mark.parametrize("field,value", [
    ("RUNTIME_TARGET_ENABLED", ""), ("RUNTIME_TARGET_ENABLED", "maybe"),
    ("RUNTIME_HEARTBEAT_ACCOUNT_SCOPE", "SG"), ("CLOUD_RUN_SERVICE", "synthetic-other-service"),
    ("GOOGLE_APPLICATION_CREDENTIALS", ""), ("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", ""),
    ("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", "/synthetic/other.json"), ("CLOUD_RUN_REGION", ""),
])
def test_one_shot_selection_and_explicit_identity_fail_before_io(field, value):
    with pytest.raises(publisher._Rejected):
        publisher._one_shot_environment(_one_shot_env(**{field: value}))


@pytest.mark.parametrize("changes", [
    {"account_scope": "SG"}, {"service_name": "synthetic-other-service"},
    {"strategy_profile": "synthetic-other-profile"}, {"market_timezone": "Asia/Hong_Kong"}, {"market_timezone": ""},
])
def test_one_shot_deployment_mismatch_cannot_write_or_sync(monkeypatch, changes):
    with pytest.raises(publisher._Rejected):
        _one_shot_publish(monkeypatch, changes=changes)


def test_one_shot_create_only_conflict_never_posts(monkeypatch):
    result, blob, observed = _one_shot_publish(monkeypatch, blob=_Blob(PreconditionFailed("synthetic")))
    assert result == ("already_recorded", "2026-09-28", "skipped_existing")
    assert observed["post"] == 0 and len(blob.uploads) == 1


def test_private_result_matches_new_incomplete_projection_without_claiming_health(monkeypatch, tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    destination = private_dir / "runtime-daily.json"
    result, blob, _observed = _one_shot_publish(
        monkeypatch, private_result_path=destination,
    )

    assert result == ("recorded", "2026-09-28", "recorded")
    handoff = json.loads(destination.read_text(encoding="utf-8"))
    stored = _stored(blob)
    assert handoff == {
        "business_date": "2026-09-28",
        "projection_completeness": "incomplete",
        "projection_uri": "gs://synthetic-existing/reports/runtime_daily/longbridge/paper/2026-09-28/20260928T084000000000Z.json",
        "target_keys": [publisher._RUNTIME_DAILY_SYNC_TARGET_KEY],
    }
    assert handoff["projection_uri"].endswith(
        "/" + publisher._object_uri(
            "gs://synthetic-existing/reports/runtime_daily",
            "2026-09-28",
            OBSERVED,
        )[1]
    )
    assert handoff["projection_completeness"] == stored["completeness"] == "incomplete"
    assert handoff["target_keys"] == [stored["records"][0]["target_key"]]
    assert handoff["projection_uri"] not in json.dumps(stored)
    assert private_dir.stat().st_mode & 0o777 == 0o700
    assert destination.stat().st_mode & 0o777 == 0o600


def test_natural_publish_can_return_its_new_object_through_private_file(monkeypatch, tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    destination = private_dir / "runtime-daily.json"
    status, business_date, blob, client, _calls = _publish(
        monkeypatch, _env(), [], private_result_path=destination,
    )

    handoff = json.loads(destination.read_text(encoding="utf-8"))
    stored = _stored(blob)
    assert status == "recorded"
    assert handoff["business_date"] == business_date == stored["records"][0]["business_date"]
    assert handoff["projection_uri"] == f"gs://paper-bucket/{client._bucket.names[0]}"
    assert handoff["target_keys"] == [stored["records"][0]["target_key"]]
    assert handoff["projection_completeness"] == stored["completeness"]


def test_private_result_is_not_written_for_create_only_conflict_or_unknown_upload(monkeypatch, tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    conflict_path = private_dir / "conflict.json"
    result, _blob, _observed = _one_shot_publish(
        monkeypatch,
        blob=_Blob(PreconditionFailed("synthetic")),
        private_result_path=conflict_path,
    )
    assert result == ("already_recorded", "2026-09-28", "skipped_existing")
    assert not conflict_path.exists()

    unknown_path = private_dir / "unknown.json"
    with pytest.raises(publisher._KnownFailure, match="write_unknown"):
        _one_shot_publish(
            monkeypatch,
            blob=_Blob(RuntimeError("synthetic-private-upload-error")),
            private_result_path=unknown_path,
        )
    assert not unknown_path.exists()
    assert "synthetic-private-upload-error" not in str(unknown_path)


def test_private_result_requires_owner_only_directory_before_publication(monkeypatch, tmp_path):
    public_dir = tmp_path / "public"
    public_dir.mkdir(mode=0o755)
    public_dir.chmod(0o755)
    blob = _Blob()
    with pytest.raises(publisher._Rejected, match="private_result_path_invalid"):
        _one_shot_publish(
            monkeypatch,
            blob=blob,
            private_result_path=public_dir / "runtime-daily.json",
        )
    assert blob.uploads == []


def test_one_shot_unknown_post_does_not_retry_or_discard_created_object(monkeypatch):
    def post(*_a, **_kw):
        raise RuntimeError("synthetic-private-transport-error")
    result, blob, observed = _one_shot_publish(monkeypatch, http_post=post)
    assert result == ("recorded", "2026-09-28", "unknown")
    assert len(blob.uploads) == 1 and observed["post"] == 1


def test_one_shot_main_only_emits_fixed_storage_and_ack_status(monkeypatch, capsys):
    observed = []
    def publish(env, *, now, one_shot_only):
        assert one_shot_only is True
        observed.append(1)
        return "recorded", "2026-09-28", "unknown"
    monkeypatch.setattr(publisher, "publish", publish)
    assert publisher.main(["--one-shot-paper"]) == 1
    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "recorded", "projection_created": True, "qrs_ack_valid": False}
    assert "projection_uri" not in output and "target_key" not in output
    assert observed == [1]


def test_main_private_result_file_keeps_projection_uri_off_stdout(monkeypatch, capsys, tmp_path):
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700)
    destination = private_dir / "runtime-daily.json"

    def publish(_env, *, now, private_result_path):
        assert now.tzinfo is not None
        publisher._write_private_result(private_result_path, {
            "projection_uri": "gs://synthetic-private/object.json",
            "business_date": "2026-09-28",
            "target_keys": ["synthetic-target"],
            "projection_completeness": "incomplete",
        })
        return "recorded", "2026-09-28", "disabled"

    monkeypatch.setattr(publisher, "publish", publish)
    assert publisher.main(["--private-result-file", str(destination)]) == 0
    output = capsys.readouterr().out
    assert "gs://synthetic-private/object.json" not in output
    assert json.loads(destination.read_text(encoding="utf-8"))["projection_uri"] == "gs://synthetic-private/object.json"


@pytest.mark.parametrize("traffic", [
    [], [{"latestRevision": True, "percent": 100}],
    [{"revisionName": "synthetic-paper-001", "percent": 99}],
    [{"revisionName": "synthetic-paper-001", "percent": 50}, {"revisionName": "synthetic-paper-002", "percent": 50}],
    [{"revisionName": "synthetic-paper-001;unsafe", "percent": 100}],
])
def test_single_observation_requires_unique_named_full_traffic_revision(monkeypatch, traffic):
    monkeypatch.setattr(heartbeat, "_run_gcloud", lambda *_: pytest.fail("ambiguous routing must reject before read"))
    with pytest.raises(publisher._Rejected, match="one_shot_serving_unavailable"):
        publisher._one_shot_serving_deployment({"status": {"traffic": traffic}}, service="synthetic-paper", project="synthetic-project", region="synthetic-region")


@pytest.mark.parametrize("change", ["name", "service", "not_ready", "duplicate_ready", "multi_container"])
def test_single_observation_rejects_revision_identity_or_readiness_mismatch(monkeypatch, change):
    from types import SimpleNamespace
    revision = {
        "metadata": {"name": "synthetic-paper-001", "labels": {"serving.knative.dev/service": "synthetic-paper"}},
        "spec": {"containers": [{"env": [{"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-existing/reports"}]}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    if change == "name":
        revision["metadata"]["name"] = "synthetic-paper-002"
    elif change == "service":
        revision["metadata"]["labels"]["serving.knative.dev/service"] = "synthetic-other"
    elif change == "not_ready":
        revision["status"]["conditions"][0]["status"] = "False"
    elif change == "duplicate_ready":
        revision["status"]["conditions"] *= 2
    else:
        revision["spec"]["containers"] *= 2
    monkeypatch.setattr(heartbeat, "_run_gcloud", lambda *_: SimpleNamespace(returncode=0, stdout=json.dumps(revision)))
    with pytest.raises(publisher._Rejected, match="one_shot_serving_unavailable"):
        publisher._one_shot_serving_deployment({"status": {"traffic": [{"revisionName": "synthetic-paper-001", "percent": 100}]}}, service="synthetic-paper", project="synthetic-project", region="synthetic-region")


def test_single_observation_uses_served_enabled_config_over_staged_disabled_template(monkeypatch):
    from types import SimpleNamespace
    staged = _cloud_run({"runtime_target_enabled": False})
    staged["status"] = {"traffic": [{"revisionName": "synthetic-paper-001", "percent": 100}]}
    served = _cloud_run({"runtime_target_enabled": True})
    revision = {
        "metadata": {"name": "synthetic-paper-001", "labels": {"serving.knative.dev/service": "synthetic-paper"}},
        "spec": served["spec"]["template"]["spec"],
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    revision["spec"]["containers"][0].setdefault("env", []).append(
        {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-existing/reports"}
    )
    monkeypatch.setattr(heartbeat, "_run_gcloud", lambda *_: SimpleNamespace(returncode=0, stdout=json.dumps(revision)))
    effective = publisher._one_shot_serving_deployment(staged, service="synthetic-paper", project="synthetic-project", region="synthetic-region")
    assert publisher._one_shot_disabled_reason(
        effective, heartbeat._deployed_runtime_target(effective), declared_runtime_enabled="true"
    ) == "runtime_target_enabled_schedule_unverified"


@pytest.mark.parametrize("rows", [
    [],
    [{"name": "EXECUTION_REPORT_GCS_URI", "valueSource": {"secretKeyRef": {"secret": "synthetic"}}}],
    [{"name": "EXECUTION_REPORT_GCS_URI", "value": ""}],
    [
        {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-existing/reports"},
        {"name": "EXECUTION_REPORT_GCS_URI", "value": "gs://synthetic-other/reports"},
    ],
])
def test_single_observation_requires_unique_plain_serving_report_root(monkeypatch, rows):
    from types import SimpleNamespace
    revision = {
        "metadata": {"name": "synthetic-paper-001", "labels": {"serving.knative.dev/service": "synthetic-paper"}},
        "spec": {"containers": [{"env": rows}]},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }
    monkeypatch.setattr(heartbeat, "_run_gcloud", lambda *_: SimpleNamespace(returncode=0, stdout=json.dumps(revision)))
    with pytest.raises(publisher._Rejected, match="one_shot_report_prefix_unavailable"):
        publisher._one_shot_serving_deployment(
            {"status": {"traffic": [{"revisionName": "synthetic-paper-001", "percent": 100}]}},
            service="synthetic-paper", project="synthetic-project", region="synthetic-region",
        )


def test_one_shot_rejects_explicit_workflow_root_conflicting_with_serving_root():
    with pytest.raises(publisher._Rejected, match="one_shot_report_prefix_conflict"):
        publisher._one_shot_environment(
            _one_shot_env(RUNTIME_HEARTBEAT_GCS_URIS="gs://synthetic-other/reports"),
            report_root_uri="gs://synthetic-existing/reports",
        )
