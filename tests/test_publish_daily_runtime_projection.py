from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
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
    status, business_date, sync_status = publisher.publish(
        env,
        now=OBSERVED,
        session_dates_loader=calendar,
        client=client,
        list_objects=list_objects,
        read_payload=read_payload,
        report_globs=lambda since, now: ["gs://reports/longbridge/**/2026-09/*.json"],
        http_post=http_post,
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
