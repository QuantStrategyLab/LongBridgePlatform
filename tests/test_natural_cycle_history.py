import importlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from google.api_core.exceptions import PreconditionFailed
from quant_platform_kit.cloud.gcp_provider import GcpObjectStore


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.account_snapshot import (
    begin_natural_cycle_history,
    build_account_snapshot_source_binding,
    current_cycle_token_version,
    cycle_history_observation,
    end_natural_cycle_history,
    note_cycle_history_observation,
    note_cycle_token_version,
    project_cycle_history_balances,
    read_cycle_token,
)
from application.longbridge_portfolio import fetch_strategy_account_state
from application.runtime_bootstrap_adapters import build_runtime_bootstrap
from scripts.record_daily_account_snapshot import (
    HISTORY_WRITE_TIMEOUT_SECONDS,
    record_projected_daily_account,
)


STARTED = datetime(2026, 9, 28, 1, 2, tzinfo=timezone.utc)
FINISHED = datetime(2026, 9, 28, 1, 3, tzinfo=timezone.utc)
ENV = {
    "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
    "ACCOUNT_HISTORY_GCS_PREFIX": "gs://lb-paper-bucket/account_snapshots",
    "ACCOUNT_HISTORY_TARGET_ID": "paper",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
    "GOOGLE_CLOUD_PROJECT": "qsl-paper-project",
}


class Metadata:
    def __init__(self, value, version_name):
        self.value = value
        self.version_name = version_name


class _Blob:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def upload_from_string(
        self,
        data,
        content_type=None,
        if_generation_match=None,
        timeout=None,
        retry=None,
    ):
        self.client.uploads.append(
            {
                "name": self.name,
                "data": data,
                "content_type": content_type,
                "if_generation_match": if_generation_match,
                "timeout": timeout,
                "retry": retry,
            }
        )
        if self.client.error is not None:
            raise self.client.error
        if self.name in self.client.objects:
            raise PreconditionFailed("exists")
        self.client.objects[self.name] = data


class _Bucket:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def blob(self, name):
        return _Blob(self.client, f"{self.name}/{name}")


class _Client:
    def __init__(self, error=None):
        self.error = error
        self.uploads = []
        self.objects = {}

    def bucket(self, name):
        return _Bucket(self, name)


def _gcp_store(error=None):
    store = GcpObjectStore(project_id="qsl-paper-project")
    store._client = _Client(error=error)
    return store


class Store:
    def __init__(self):
        self.calls = []

    def create_text(self, uri, data, content_type="text/plain"):
        self.calls.append((uri, data, content_type))
        return True


def _cash(currency, available="10", frozen="1", settling="2"):
    return types.SimpleNamespace(
        currency=currency,
        available_cash=available,
        frozen_cash=frozen,
        settling_cash=settling,
    )


def _balance(currency, net_assets, total_cash, cash_infos):
    return types.SimpleNamespace(
        currency=currency,
        net_assets=net_assets,
        total_cash=total_cash,
        cash_infos=cash_infos,
    )


def _binding():
    return build_account_snapshot_source_binding(
        version_name="projects/p/secrets/s/versions/3",
        project_id="qsl-paper-project",
        service="longbridge-quant-paper-service",
        revision="paper-00001",
        account_scope="PAPER",
        region="asia-east1",
    )


def _projection():
    return project_cycle_history_balances(
        [
            _balance("USD", "2500.50", "100.00", [_cash("USD")]),
            _balance("HKD", "-12.50", "3", [_cash("HKD", available="8")]),
        ]
    )


def _record(store, **overrides):
    projection = _projection()
    payload = {
        "account_scope": "PAPER",
        "source_binding": _binding(),
        "balances": projection["broker_reported_balances"],
        "cash": projection["cash"],
        "started": STARTED,
        "finished": FINISHED,
        "open_store": lambda _project_id: store,
    }
    payload.update(overrides)
    return record_projected_daily_account(ENV, **payload)


def test_recording_disabled_does_not_open_a_store():
    calls = []
    result = record_projected_daily_account(
        {},
        account_scope="PAPER",
        source_binding=_binding(),
        balances=[{"currency": "USD", "net_assets": "1", "total_cash": "1"}],
        cash=[
            {
                "currency": "USD",
                "available_cash": "1",
                "frozen_cash": "0",
                "settling_cash": "0",
            }
        ],
        started=STARTED,
        finished=FINISHED,
        open_store=lambda _project_id: calls.append("open"),
    )
    assert result.status == "disabled"
    assert calls == []


def test_metadata_failure_does_not_read_the_secret_again():
    secret_reads = []

    class SecretStore:
        def get_secret_with_metadata(self, secret_name, project_id=None):
            raise RuntimeError("metadata unavailable")

        def get_secret(self, secret_name, project_id=None):
            secret_reads.append((project_id, secret_name))
            return "raw-token\n"

    with pytest.raises(RuntimeError, match="metadata unavailable"):
        read_cycle_token(
            "qsl-paper-project",
            "secret-1",
            store=SecretStore(),
            fetch_token=lambda project_id, secret_name: secret_reads.append("fetch"),
        )
    assert secret_reads == []


def test_plain_token_read_still_uses_one_existing_fetch():
    class SecretStore:
        def get_secret(self, secret_name, project_id=None):
            return "raw-token\n"

    loaded = read_cycle_token(
        "qsl-paper-project",
        "secret-1",
        store=SecretStore(),
        fetch_token=lambda project_id, secret_name: SecretStore().get_secret(
            secret_name, project_id=project_id
        ).strip(),
    )
    assert loaded == "raw-token"


def test_refresh_change_does_not_keep_the_old_version():
    capture = begin_natural_cycle_history()
    try:
        observed = {}
        bootstrap = build_runtime_bootstrap(
            project_id="qsl-paper-project",
            secret_name="secret-1",
            token_refresh_threshold_days=30,
            fetch_token_from_secret_fn=lambda *_args: Metadata(" token-a\n", "versions/3"),
            refresh_token_if_needed_fn=lambda token, **_kwargs: (
                observed.setdefault("token", token),
                "token-b",
            )[-1],
            build_contexts_fn=lambda *_args: ("quote", "trade"),
            calculate_strategy_indicators_fn=lambda _quote: {},
            env_reader=lambda name, default="": {
                "LONGPORT_APP_KEY": "app-key",
                "LONGPORT_APP_SECRET": "app-secret",
            }.get(name, default),
        )
        assert bootstrap()[:2] == ("quote", "trade")
        assert observed["token"] == "token-a"
        assert current_cycle_token_version() is None
        store = Store()
        result = _record(store, source_binding=build_account_snapshot_source_binding(
            version_name=current_cycle_token_version(),
            project_id="qsl-paper-project",
            service="longbridge-quant-paper-service",
            revision="paper-00001",
            account_scope="PAPER",
        ))
        assert result.status == "skipped"
        assert store.calls == []
    finally:
        end_natural_cycle_history(capture)


def test_missing_projection_skips_history_without_changing_strategy_state():
    class Quote:
        def quote(self, _symbols):
            return [types.SimpleNamespace(symbol="SOXL.US", last_done=25)]

    class Trade:
        def __init__(self):
            self.calls = 0

        def account_balance(self):
            self.calls += 1
            return [
                types.SimpleNamespace(
                    currency="USD",
                    net_assets="2500.50",
                    cash_infos=[types.SimpleNamespace(currency="USD", available_cash=100.0)],
                )
            ]

        def stock_positions(self):
            return types.SimpleNamespace(channels=[])

    capture = begin_natural_cycle_history()
    try:
        trade = Trade()
        state = fetch_strategy_account_state(Quote(), trade, ["SOXL"])
        assert trade.calls == 1
        assert state["available_cash"] == 100.0
        assert state["total_strategy_equity"] == 100.0
        assert cycle_history_observation() is None
    finally:
        end_natural_cycle_history(capture)


def test_total_asset_currency_can_differ_from_cash_currencies():
    projection = project_cycle_history_balances(
        [
            _balance(
                "USD",
                "1000.00",
                "80",
                [_cash("USD", available="30"), _cash("HKD", available="500")],
            )
        ]
    )
    assert projection["broker_reported_balances"] == [
        {"currency": "USD", "net_assets": "1000", "total_cash": "80"}
    ]
    assert [row["currency"] for row in projection["cash"]] == ["HKD", "USD"]
    assert project_cycle_history_balances(
        [_balance("USD", "1", "1", [_cash("USD"), _cash("USD")])]
    ) is None
    assert project_cycle_history_balances(
        [_balance("USD", None, "1", [_cash("USD")])]
    ) is None


def test_negative_and_multi_currency_projection_is_stored_once():
    projection = _projection()
    assert [row["currency"] for row in projection["broker_reported_balances"]] == ["HKD", "USD"]
    assert projection["broker_reported_balances"][0]["net_assets"] == "-12.5"
    store = _gcp_store()
    result = _record(store)
    assert result.status == "recorded"
    call = store.client.uploads[0]
    assert call["if_generation_match"] == 0
    assert call["timeout"] == HISTORY_WRITE_TIMEOUT_SECONDS
    assert call["retry"] is None
    assert call["content_type"] == "application/json"
    assert "no_order" not in call["data"]
    assert "versions/3" not in call["data"]
    assert "positions" not in call["data"]
    assert call["name"].endswith("/paper/" + _binding()["id"] + "/2026-09-28.json")
    assert "-12.5" in call["data"]
    assert "HKD" in call["data"] and "USD" in call["data"]


def test_duplicate_day_and_unknown_write_do_not_retry_or_overwrite(capsys):
    store = _gcp_store()
    assert _record(store).status == "recorded"
    original = next(iter(store.client.objects.values()))
    assert _record(store).status == "already_recorded"
    assert len(store.client.uploads) == 2
    assert next(iter(store.client.objects.values())) == original

    unknown = _gcp_store(error=RuntimeError("slow secret token"))
    report = {"orders": 2, "status": "ok"}
    outcome = _record(unknown)
    assert outcome.status == "error"
    assert outcome.category == "store_unknown"
    assert len(unknown.client.uploads) == 1
    assert unknown.client.uploads[0]["retry"] is None
    assert unknown.client.uploads[0]["timeout"] == HISTORY_WRITE_TIMEOUT_SECONDS
    assert report == {"orders": 2, "status": "ok"}
    logged = capsys.readouterr().out
    assert "account_history status=error category=store_unknown" in logged
    assert "slow secret token" not in logged


def test_create_text_without_bounds_is_skipped():
    store = Store()
    assert _record(store).status == "skipped"
    assert _record(store).category == "store_unbounded"
    assert store.calls == []


def test_missing_source_writes_nothing():
    store = Store()
    result = _record(
        store,
        source_binding={"kind": "deployment_scope_token_version", "status": "unavailable", "id": None},
    )
    assert result.status == "skipped"
    assert result.category == "source_unbound"
    assert store.calls == []


def _runtime_target(dry_run: str) -> str:
    dry = dry_run == "true"
    return json.dumps(
        {
            "platform_id": "longbridge",
            "strategy_profile": "soxl_soxx_trend_income",
            "dry_run_only": dry,
            "execution_mode": "paper" if dry else "live",
            "account_scope": "PAPER",
        },
        separators=(",", ":"),
    )


def _load_main(monkeypatch, *, dry_run, history_enabled):
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _runtime_target(dry_run))
    monkeypatch.setenv("RUNTIME_TARGET_ENABLED", "true")
    monkeypatch.setenv("ACCOUNT_REGION", "PAPER")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "qsl-paper-project")
    monkeypatch.setenv("LONGBRIDGE_DRY_RUN_ONLY", dry_run)
    monkeypatch.setenv("LONGBRIDGE_EXECUTION_STATE_CLOUD_URI", "gs://unit-test-execution/claims")
    monkeypatch.setenv("K_SERVICE", "longbridge-quant-paper-service")
    monkeypatch.setenv("K_REVISION", "paper-00001")
    monkeypatch.setenv("CLOUD_RUN_REGION", "asia-east1")
    monkeypatch.setenv("GLOBAL_TELEGRAM_CHAT_ID", "shared-chat-id")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    if history_enabled:
        for key, value in ENV.items():
            monkeypatch.setenv(key, value)
    else:
        monkeypatch.delenv("ACCOUNT_HISTORY_RECORDING_ENABLED", raising=False)
    sys.modules.pop("main", None)
    return importlib.import_module("main")


def _arm_cycle(module, events, *, market_open=True):
    module.is_market_open_now = lambda **_kwargs: market_open

    def cycle(**_kwargs):
        note_cycle_token_version("projects/p/secrets/s/versions/3")
        note_cycle_history_observation(_projection(), STARTED, FINISHED)
        return None

    module.run_rebalance_cycle = cycle

    def persist(report, **_kwargs):
        events.append("report")
        events.append(report)
        return types.SimpleNamespace(local_path="/tmp/runtime-report.json", gcs_uri=None)

    module.persist_runtime_report = persist


def test_run_strategy_gates_the_daily_sample(monkeypatch, capsys):
    def forbid_store():
        raise AssertionError("history store opened")

    blocked = (
        {"dry_run": "false", "history": False, "call": {}},
        {"dry_run": "false", "history": True, "call": {"validation_only": True, "force_run": True}},
        {"dry_run": "false", "history": True, "call": {"force_run": True}, "market_open": False},
        {"dry_run": "true", "history": True, "call": {}},
    )
    for case in blocked:
        module = _load_main(
            monkeypatch,
            dry_run=case["dry_run"],
            history_enabled=case["history"],
        )
        events = []
        _arm_cycle(module, events, market_open=case.get("market_open", True))
        monkeypatch.setattr("quant_platform_kit.cloud.get_object_store", forbid_store)
        assert module.run_strategy(**case["call"]) is True
        assert events[0] == "report"
        assert events[1]["status"] == "ok"
        assert "account_history status=recorded" not in capsys.readouterr().out

    module = _load_main(monkeypatch, dry_run="false", history_enabled=True)
    events = []
    store = _gcp_store()
    _arm_cycle(module, events)
    monkeypatch.setattr(
        "quant_platform_kit.cloud.get_object_store",
        lambda: events.append("history") or store,
    )
    assert module.run_strategy() is True
    assert events[0] == "report"
    assert events[2] == "history"
    report = events[1]
    summary = dict(report.get("summary") or {})
    upload = store.client.uploads[0]
    assert upload["if_generation_match"] == 0
    assert upload["timeout"] == HISTORY_WRITE_TIMEOUT_SECONDS
    assert upload["retry"] is None
    assert len(store.client.uploads) == 1
    assert report["status"] == "ok"
    assert dict(report.get("summary") or {}) == summary

    failed = _gcp_store(error=TimeoutError("slow"))
    module = _load_main(monkeypatch, dry_run="false", history_enabled=True)
    events = []
    _arm_cycle(module, events)
    monkeypatch.setattr("quant_platform_kit.cloud.get_object_store", lambda: failed)
    assert module.run_strategy() is True
    assert events[0] == "report"
    assert events[1]["status"] == "ok"
    assert len(failed.client.uploads) == 1
    assert "account_history status=error category=store_unknown" in capsys.readouterr().out
    assert "slow" not in capsys.readouterr().out


def test_non_paper_scope_and_target_label_do_not_form_a_sample():
    store = Store()
    result = _record(store, account_scope="LIVE")
    assert result.status == "skipped"
    assert store.calls == []
    unbound = build_account_snapshot_source_binding(
        version_name=None,
        project_id="qsl-paper-project",
        service="longbridge-quant-paper-service",
        revision="paper-00001",
        account_scope="PAPER",
    )
    assert unbound["status"] == "unavailable"
    assert _record(store, source_binding=unbound).status == "skipped"
    assert store.calls == []
