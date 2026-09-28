from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.record_daily_account_snapshot import (  # noqa: E402
    record_daily_account_snapshot,
    main,
)


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
BINDING_A = "a" * 64
BINDING_B = "b" * 64
SERVICE = "https://longbridge-quant-paper-service-ab12.asia-east1.run.app"
PREFIX = "gs://acct-history/account_snapshots"
QRS_SYNC_URL = "https://qrs.example.test/api/account-facts/sync"
SECRET_TOKEN = "synthetic-oidc-token"
QRS_TOKEN = "synthetic-qrs-token"
LEAK = "raw-secret-value"


def _env(**overrides):
    env = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_SERVICE_URL": SERVICE,
        "ACCOUNT_HISTORY_GCS_PREFIX": PREFIX,
        "ACCOUNT_HISTORY_TARGET_ID": "paper",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
        "GOOGLE_CLOUD_PROJECT": "longbridgequant",
    }
    env.update(overrides)
    return env


def _payload(**overrides):
    started = NOW - timedelta(minutes=2)
    finished = NOW - timedelta(minutes=1)
    payload = {
        "schema_version": "longbridge_account_snapshot.v1",
        "status": "partial",
        "account_scope": "PAPER",
        "positions_complete": True,
        "cash_complete": True,
        "no_order": True,
        "live_authority_granted": False,
        "snapshot_atomic": False,
        "source_binding": {
            "kind": "deployment_scope_token_version",
            "status": "bound",
            "id": BINDING_A,
        },
        "observed_started_at": started.isoformat(),
        "observed_finished_at": finished.isoformat(),
        "broker_reported_balances": [
            {"currency": "USD", "net_assets": "10", "total_cash": "-2.5"},
            {"currency": "HKD", "net_assets": "3", "total_cash": "1"},
        ],
        "cash": [
            {
                "currency": "USD",
                "available_cash": "-2.5",
                "frozen_cash": "0",
                "settling_cash": "0",
            },
            {
                "currency": "HKD",
                "available_cash": "1",
                "frozen_cash": "0",
                "settling_cash": "0",
            },
        ],
        "positions": [{"symbol": "SOXL.US", "quantity": "1"}],
        "token": LEAK,
    }
    payload.update(overrides)
    return payload


class _Response:
    def __init__(self, status_code, payload=None, content=None):
        self.status_code = status_code
        self.is_redirect = 300 <= status_code < 400
        if content is None:
            content = json.dumps(payload if payload is not None else _payload()).encode()
        self.content = content

    def iter_content(self, chunk_size=8192):
        yield self.content

    def close(self):
        pass


class _Spies:
    def __init__(self, response=None, created=True, explode_store=False, post_response=None):
        self.calls = []
        self.response = response or _Response(200)
        self.created = created
        self.explode_store = explode_store
        self.stored = []
        self.post_response = post_response or _Response(
            200,
            {
                "ok": True,
                "stored": True,
                "target_id": "paper",
                "observation_date": "2026-09-28",
                "observed_finished_at": (NOW - timedelta(minutes=1)).isoformat(),
            },
        )

    def fetch_id_token(self, audience):
        self.calls.append(("token", audience))
        return SECRET_TOKEN

    def http_get(self, url, *, headers, timeout):
        self.calls.append(("http", url, headers, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def http_post(self, url, *, headers, data, timeout, allow_redirects, stream):
        self.calls.append(("post", url, headers, data, timeout, allow_redirects, stream))
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response

    def open_store(self, project_id):
        self.calls.append(("store", project_id))
        if self.explode_store:
            raise RuntimeError(f"gs://secret {LEAK}")
        spies = self

        class _Store:
            def create_text(self, uri, data, content_type="text/plain"):
                spies.calls.append(("create", uri, data, content_type))
                spies.stored.append((uri, data, content_type))
                if isinstance(spies.created, Exception):
                    raise spies.created
                return spies.created

        return _Store()


def _record(env=None, spies=None, now=NOW, now_reader=None):
    spies = spies or _Spies()
    result = record_daily_account_snapshot(
        env if env is not None else _env(),
        fetch_id_token=spies.fetch_id_token,
        http_get=spies.http_get,
        http_post=spies.http_post,
        open_store=spies.open_store,
        now_reader=now_reader or (lambda: now),
    )
    return result, spies


def test_disabled_switch_does_not_touch_token_http_or_store():
    result, spies = _record(_env(ACCOUNT_HISTORY_RECORDING_ENABLED="false"))

    assert result.status == "disabled"
    assert spies.calls == []


@pytest.mark.parametrize(
    "bad_env",
    [
        {"ACCOUNT_HISTORY_SERVICE_URL": "http://svc.a.run.app"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://user:pass@svc.a.run.app"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app/account-snapshot"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app?q=1"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app#frag"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://example.com"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app:99999"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app:abc"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://[svc.a.run.app]"},
        {"ACCOUNT_HISTORY_SERVICE_URL": ""},
        {"ACCOUNT_HISTORY_GCS_PREFIX": "gs://[acct-history]/account_snapshots"},
        {"ACCOUNT_HISTORY_GCS_PREFIX": "gs://acct-history/execution-reports"},
        {"ACCOUNT_HISTORY_GCS_PREFIX": "gs://acct-history/execution_reports/account_snapshots"},
        {"ACCOUNT_HISTORY_GCS_PREFIX": "https://acct-history/account_snapshots"},
        {"ACCOUNT_HISTORY_GCS_PREFIX": ""},
        {"ACCOUNT_HISTORY_TARGET_ID": ""},
        {"ACCOUNT_HISTORY_TARGET_ID": "../paper"},
        {"ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK"},
        {"ACCOUNT_HISTORY_EXPECTED_SCOPE": ""},
        {"GOOGLE_CLOUD_PROJECT": ""},
    ],
)
def test_invalid_config_matrix(bad_env):
    result, spies = _record(_env(**bad_env))

    assert result.status == "error"
    assert result.category == "config_invalid"
    assert spies.calls == []
    assert LEAK not in result.category
    assert "run.app" not in result.category


@pytest.mark.parametrize("status", [302, 503, 500])
def test_non_200_and_redirect_matrix(status):
    spies = _Spies(_Response(status, _payload()))
    result, spies = _record(spies=spies)

    assert result.status == "error"
    assert result.category == "http_failed"
    assert [name for name, *_rest in spies.calls] == ["token", "http"]
    assert spies.stored == []


def test_timeout_does_not_write():
    spies = _Spies(TimeoutError("https://svc.a.run.app timed out"))
    result, spies = _record(spies=spies)

    assert result.status == "error"
    assert result.category == "http_failed"
    assert spies.stored == []
    assert "run.app" not in result.category


@pytest.mark.parametrize(
    "payload",
    [
        _payload(account_scope="SG"),
        _payload(status="blocked"),
        _payload(source_binding={"kind": "deployment_scope_token_version", "status": "unavailable", "id": None}),
        _payload(source_binding={"kind": "deployment_scope_token_version", "status": "bound", "id": "LATEST"}),
        _payload(snapshot_atomic=True),
        _payload(no_order=False),
        _payload(live_authority_granted=True),
        _payload(positions_complete=False),
        _payload(cash_complete=False),
        _payload(observed_finished_at=(NOW - timedelta(minutes=3)).isoformat()),
        _payload(observed_started_at=(NOW + timedelta(minutes=1)).isoformat(), observed_finished_at=(NOW + timedelta(minutes=2)).isoformat()),
        _payload(observed_started_at=(NOW - timedelta(minutes=16)).isoformat()),
        _payload(observed_started_at="2026-09-28T11:58:00"),
        _payload(broker_reported_balances=[{"currency": "USD", "net_assets": 10, "total_cash": "1"}]),
        _payload(broker_reported_balances=[{"currency": "USD", "net_assets": True, "total_cash": "1"}]),
        _payload(cash=[{"currency": "USD", "available_cash": "NaN", "frozen_cash": "0", "settling_cash": "0"}]),
        _payload(cash=[]),
        _payload(
            cash=[
                {"currency": "USD", "available_cash": "1", "frozen_cash": "0", "settling_cash": "0"},
                {"currency": "USD", "available_cash": "2", "frozen_cash": "0", "settling_cash": "0"},
            ]
        ),
    ],
)
def test_invalid_snapshot_matrix(payload):
    spies = _Spies(_Response(200, payload))
    result, spies = _record(spies=spies)

    assert result.status == "error"
    assert result.category == "response_invalid"
    assert spies.stored == []
    assert LEAK not in result.category
    assert "-2.5" not in result.category


def test_record_stores_only_whitelisted_multi_currency_fields():
    result, spies = _record()

    assert result.status == "recorded"
    assert [name for name, *_rest in spies.calls] == ["token", "http", "store", "create"]
    audience = spies.calls[0][1]
    url = spies.calls[1][1]
    headers = spies.calls[1][2]
    assert audience == SERVICE
    assert url == f"{SERVICE}/account-snapshot"
    assert headers["Authorization"] == f"Bearer {SECRET_TOKEN}"
    assert spies.calls[1][3] > 0
    uri, data, content_type = spies.stored[0]
    assert uri == f"{PREFIX}/paper/{BINDING_A}/2026-09-28.json"
    assert content_type == "application/json"
    saved = json.loads(data)
    assert saved["schema_version"] == "longbridge_account_snapshot_history.v1"
    assert saved["snapshot_schema_version"] == "longbridge_account_snapshot.v1"
    assert saved["account_scope"] == "PAPER"
    assert saved["target_id"] == "paper"
    assert saved["source_binding"] == {
        "kind": "deployment_scope_token_version",
        "status": "bound",
        "id": BINDING_A,
    }
    assert saved["snapshot_atomic"] is False
    assert saved["observation_date"] == "2026-09-28"
    assert saved["broker_reported_balances"] == [
        {"currency": "USD", "net_assets": "10", "total_cash": "-2.5"},
        {"currency": "HKD", "net_assets": "3", "total_cash": "1"},
    ]
    assert saved["cash"][0]["available_cash"] == "-2.5"
    assert saved["cash"][1]["currency"] == "HKD"
    assert "positions" not in saved
    assert "token" not in saved
    assert "equity" not in saved
    assert LEAK not in data
    assert "net_assets_total" not in saved


def test_publishes_exact_created_history_body_once_to_qrs():
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        )
    )

    assert result.status == "recorded"
    assert result.publish_status == "published"
    assert [name for name, *_ in spies.calls].count("http") == 1
    posts = [call for call in spies.calls if call[0] == "post"]
    assert len(posts) == 1
    _, url, headers, body, timeout, allow_redirects, stream = posts[0]
    assert url == QRS_SYNC_URL
    assert headers == {
        "Authorization": f"Bearer {QRS_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    assert body.decode("utf-8") == spies.stored[0][1]
    assert timeout > 0
    assert allow_redirects is False
    assert stream is True
    posted_body = body.decode("utf-8")
    assert SECRET_TOKEN not in posted_body
    assert QRS_TOKEN not in posted_body
    assert LEAK not in posted_body


def test_qrs_publish_is_off_by_default():
    result, spies = _record()

    assert result.status == "recorded"
    assert result.publish_status == "disabled"
    assert [name for name, *_ in spies.calls].count("post") == 0


def test_store_unknown_never_posts_to_qrs():
    spies = _Spies(created=None)
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.category == "store_unknown"
    assert [name for name, *_ in spies.calls].count("post") == 0


def test_already_recorded_does_not_publish_newly_fetched_body():
    spies = _Spies(created=False)
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "already_recorded"
    assert result.publish_status == "skipped_already_recorded"
    assert [name for name, *_ in spies.calls].count("post") == 0


def test_qrs_redirect_is_not_followed_or_treated_as_published():
    spies = _Spies(post_response=_Response(302, {"Location": "https://other.example.test"}))
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "rejected"
    assert [name for name, *_ in spies.calls].count("post") == 1
    assert spies.calls[-1][5] is False


def test_qrs_timeout_is_unknown_and_never_retried():
    spies = _Spies(post_response=TimeoutError(f"{QRS_TOKEN} {LEAK}"))
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "unknown"
    assert [name for name, *_ in spies.calls].count("post") == 1
    assert QRS_TOKEN not in result.publish_category
    assert LEAK not in result.publish_category


def test_qrs_error_response_is_sanitized_and_record_stays_recorded():
    spies = _Spies(post_response=_Response(401, {"error": f"bad token {QRS_TOKEN} {LEAK}"}))
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "rejected"
    assert result.publish_category == "qrs_http_rejected"
    assert QRS_TOKEN not in result.publish_category
    assert LEAK not in result.publish_category


def test_qrs_oversized_response_is_unknown_without_retry():
    spies = _Spies(post_response=_Response(200, content=b"x" * (64 * 1024 + 1)))
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "unknown"
    assert result.publish_category == "qrs_response_too_large"
    assert [name for name, *_ in spies.calls].count("post") == 1


def test_qrs_oversized_payload_is_not_sent():
    large_payload = _payload(
        cash=[
            {
                "currency": "USD",
                "available_cash": "1" * (64 * 1024),
                "frozen_cash": "0",
                "settling_cash": "0",
            }
        ]
    )
    spies = _Spies(response=_Response(200, large_payload))
    result, spies = _record(
        env=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        spies=spies,
    )

    assert result.status == "recorded"
    assert result.publish_status == "rejected"
    assert result.publish_category == "qrs_payload_too_large"
    assert [name for name, *_ in spies.calls].count("post") == 0


def test_cli_reports_qrs_failure_without_erasing_record_success(capsys):
    spies = _Spies(post_response=TimeoutError(f"{QRS_TOKEN} {LEAK}"))
    code = main(
        [],
        environ=_env(
            ACCOUNT_FACTS_SYNC_ENABLED="true",
            ACCOUNT_FACTS_SYNC_URL=QRS_SYNC_URL,
            ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
        ),
        fetch_id_token=spies.fetch_id_token,
        http_get=spies.http_get,
        http_post=spies.http_post,
        open_store=spies.open_store,
        now_reader=lambda: NOW,
    )

    output = capsys.readouterr().out
    assert code == 1
    assert output.strip() == "record=recorded account_facts_publish=unknown"
    assert QRS_TOKEN not in output
    assert LEAK not in output


def test_qrs_sync_url_must_be_exact_https_endpoint():
    for bad_url in (
        "http://qrs.example.test/api/account-facts/sync",
        "https://qrs.example.test/api/account-facts/sync/",
        "https://qrs.example.test/api/account-facts/sync?next=https://evil.test",
        "https://user:pass@qrs.example.test/api/account-facts/sync",
        "https://qrs.example.test:443/api/account-facts/sync",
        "https://qrs.example.test/other",
    ):
        spies = _Spies()
        result, spies = _record(
            env=_env(
                ACCOUNT_FACTS_SYNC_ENABLED="true",
                ACCOUNT_FACTS_SYNC_URL=bad_url,
                ACCOUNT_FACTS_SYNC_TOKEN=QRS_TOKEN,
            ),
            spies=spies,
        )
        assert result.status == "recorded"
        assert result.publish_status == "rejected"
        assert [name for name, *_ in spies.calls].count("post") == 0


def test_new_source_or_utc_day_uses_a_new_object_segment():
    late = datetime(2026, 9, 28, 0, 5, tzinfo=timezone.utc)
    previous_start = datetime(2026, 9, 27, 23, 55, tzinfo=timezone.utc)
    spies = _Spies(
        _Response(
            200,
            _payload(
                source_binding={
                    "kind": "deployment_scope_token_version",
                    "status": "bound",
                    "id": BINDING_B,
                    "token": LEAK,
                },
                observed_started_at=previous_start.isoformat(),
                observed_finished_at=late.isoformat(),
            ),
        )
    )
    result, spies = _record(spies=spies, now=late)

    assert result.status == "recorded"
    assert spies.stored[0][0] == f"{PREFIX}/paper/{BINDING_B}/2026-09-27.json"
    assert LEAK not in spies.stored[0][1]


def test_existing_object_is_already_recorded_without_another_fetch():
    spies = _Spies(created=False)
    result, spies = _record(spies=spies)

    assert result.status == "already_recorded"
    assert [name for name, *_rest in spies.calls] == ["token", "http", "store", "create"]


def test_unknown_store_result_is_not_retried():
    spies = _Spies(created=None)
    result, spies = _record(spies=spies)

    assert result.status == "error"
    assert result.category == "store_unknown"
    assert [name for name, *_rest in spies.calls].count("create") == 1
    assert [name for name, *_rest in spies.calls].count("http") == 1
    assert LEAK not in result.category

    spies = _Spies(explode_store=True)
    result, spies = _record(spies=spies)
    assert result.status == "error"
    assert result.category == "store_unknown"
    assert "secret" not in result.category
    assert [name for name, *_rest in spies.calls].count("http") == 1


def test_cli_disabled_and_success_use_injected_dependencies(capsys):
    disabled = main(
        [],
        environ=_env(ACCOUNT_HISTORY_RECORDING_ENABLED=""),
        fetch_id_token=lambda *_args, **_kwargs: pytest.fail("token"),
        http_get=lambda *_args, **_kwargs: pytest.fail("http"),
        open_store=lambda *_args, **_kwargs: pytest.fail("store"),
        now_reader=lambda: NOW,
    )
    assert disabled == 0
    assert capsys.readouterr().out.strip() == "record=disabled account_facts_publish=disabled"

    spies = _Spies()
    code = main(
        [],
        environ=_env(),
        fetch_id_token=spies.fetch_id_token,
        http_get=spies.http_get,
        open_store=spies.open_store,
        now_reader=lambda: NOW,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "record=recorded account_facts_publish=disabled"
    assert SECRET_TOKEN not in captured.out


def test_cli_error_is_a_short_category(capsys):
    code = main(
        [],
        environ=_env(ACCOUNT_HISTORY_SERVICE_URL="https://example.com"),
        fetch_id_token=lambda *_args, **_kwargs: pytest.fail("token"),
        http_get=lambda *_args, **_kwargs: pytest.fail("http"),
        open_store=lambda *_args, **_kwargs: pytest.fail("store"),
        now_reader=lambda: NOW,
    )
    assert code == 1
    assert capsys.readouterr().out.strip() == "record=error:config_invalid account_facts_publish=disabled"


def test_gcloud_token_wrapper_success_failure_and_timeout(monkeypatch, capsys):
    import subprocess

    from scripts.record_daily_account_snapshot import _Rejected, _fetch_id_token

    calls = []

    def succeed(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=b"synthetic-oidc-token\n",
            stderr=b"debug " + LEAK.encode(),
        )

    monkeypatch.setattr(subprocess, "run", succeed)
    assert _fetch_id_token(SERVICE) == "synthetic-oidc-token"
    args, kwargs = calls[0]
    assert args == [
        "gcloud",
        "auth",
        "print-identity-token",
        f"--audiences={SERVICE}",
        "--quiet",
    ]
    assert kwargs["capture_output"] is True
    assert kwargs["shell"] is False
    assert kwargs["timeout"] > 0
    assert "synthetic-oidc-token" not in " ".join(args)
    assert capsys.readouterr().out == ""

    def fail(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=LEAK.encode())

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(_Rejected) as raised:
        _fetch_id_token(SERVICE)
    assert raised.value.category == "token_unavailable"
    assert LEAK not in str(raised.value)
    assert len(calls) == 2

    def time_out(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(
            args,
            kwargs["timeout"],
            output=b"synthetic-oidc-token",
            stderr=LEAK.encode(),
        )

    monkeypatch.setattr(subprocess, "run", time_out)
    with pytest.raises(_Rejected) as raised:
        _fetch_id_token(SERVICE)
    assert raised.value.category == "token_unavailable"
    assert "synthetic-oidc-token" not in str(raised.value)
    assert LEAK not in str(raised.value)
    assert len(calls) == 3
    assert capsys.readouterr().out == ""


def test_cli_uses_the_gcloud_wrapper_and_keeps_stderr_private(monkeypatch, capsys):
    import subprocess

    def succeed(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=b"synthetic-oidc-token\n",
            stderr=LEAK.encode(),
        )

    monkeypatch.setattr(subprocess, "run", succeed)
    spies = _Spies()
    code = main(
        [],
        environ=_env(),
        http_get=spies.http_get,
        open_store=spies.open_store,
        now_reader=lambda: NOW,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "record=recorded account_facts_publish=disabled"
    assert LEAK not in captured.out
    assert LEAK not in captured.err
    assert spies.calls[0][2]["Authorization"] == "Bearer synthetic-oidc-token"


def test_observation_finished_during_the_request_is_recorded():
    request_started = NOW
    clock = {"now": request_started}
    reads = []

    def fetch_id_token(_audience):
        clock["now"] += timedelta(seconds=4)
        return SECRET_TOKEN

    def http_get(_url, *, headers, timeout):
        assert headers["Authorization"] == f"Bearer {SECRET_TOKEN}"
        assert timeout > 0
        finish = clock["now"] + timedelta(seconds=3)
        start = finish - timedelta(seconds=30)
        clock["finish"] = finish
        clock["now"] = finish + timedelta(milliseconds=200)
        return _Response(
            200,
            _payload(
                observed_started_at=start.isoformat(),
                observed_finished_at=finish.isoformat(),
            ),
        )

    def now_reader():
        reads.append(clock["now"])
        return clock["now"]

    spies = _Spies()
    result = record_daily_account_snapshot(
        _env(),
        fetch_id_token=fetch_id_token,
        http_get=http_get,
        open_store=spies.open_store,
        now_reader=now_reader,
    )

    assert result.status == "recorded"
    assert clock["finish"] > request_started
    assert reads[-1] > clock["finish"]
    assert spies.stored[0][0].endswith("/2026-09-28.json")


def test_finish_after_the_received_clock_is_still_rejected():
    def http_get(_url, *, headers, timeout):
        return _Response(
            200,
            _payload(
                observed_started_at=(NOW - timedelta(minutes=1)).isoformat(),
                observed_finished_at=(NOW + timedelta(minutes=1)).isoformat(),
            ),
        )

    spies = _Spies()
    result = record_daily_account_snapshot(
        _env(),
        fetch_id_token=spies.fetch_id_token,
        http_get=http_get,
        open_store=spies.open_store,
        now_reader=lambda: NOW,
    )

    assert result.status == "error"
    assert result.category == "response_invalid"
    assert spies.stored == []


def test_malformed_url_cli_stays_a_short_category(capsys):
    cases = (
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://svc.a.run.app:99999"},
        {"ACCOUNT_HISTORY_SERVICE_URL": "https://[svc.a.run.app]"},
        {"ACCOUNT_HISTORY_GCS_PREFIX": "gs://[acct-history]/account_snapshots"},
    )
    for bad in cases:
        code = main(
            [],
            environ=_env(**bad),
            fetch_id_token=lambda *_args, **_kwargs: pytest.fail("token"),
            http_get=lambda *_args, **_kwargs: pytest.fail("http"),
            open_store=lambda *_args, **_kwargs: pytest.fail("store"),
            now_reader=lambda: NOW,
        )
        captured = capsys.readouterr()
        assert code == 1
        assert captured.out.strip() == "record=error:config_invalid account_facts_publish=disabled"
        assert "Traceback" not in captured.err
        assert "Port out of range" not in captured.err
        assert "IPv6" not in captured.err


def test_heartbeat_workflow_adds_an_optional_paper_step_without_a_new_scheduler():
    workflow = (ROOT / ".github/workflows/execution-report-heartbeat.yml").read_text(encoding="utf-8")
    heartbeat = workflow.index("Check recent execution report")
    record = workflow.index("Record daily paper account snapshot")
    assert heartbeat < record
    assert 'cron: "20 22 * * *"' in workflow
    assert workflow.count("schedule:") == 1
    assert "gcloud scheduler" not in workflow
    step = workflow[record:]
    assert "success()" in step
    assert "matrix.target.label == 'PAPER'" in step
    assert "vars.ACCOUNT_HISTORY_RECORDING_ENABLED == 'true'" in step
    assert "ACCOUNT_HISTORY_TARGET_ID: ${{ matrix.target.id }}" in step
    assert "ACCOUNT_HISTORY_EXPECTED_SCOPE: ${{ matrix.target.label }}" in step
    assert "GOOGLE_CLOUD_PROJECT: ${{ env.GCP_PROJECT_ID }}" in step
    assert "uv run --no-sync python scripts/record_daily_account_snapshot.py" in step
    assert "ACCOUNT_HISTORY_RECORDING_ENABLED: true" not in workflow
