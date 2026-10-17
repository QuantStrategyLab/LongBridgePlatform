"""Synthetic boundaries around the real composer, service and archive producer."""

import ast
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from application import rebalance_service as service
from application import runtime_composer as composer_module
from application.runtime_composer import LongBridgeRuntimeComposer
from notifications import unified
from notifications.telegram import build_translator

ROOT = Path(__file__).resolve().parents[1]
QRS_SHA = "bd3dc79f10b2c0b8048721695885c3f9333a53c6"
VENDOR = ROOT / "tests/vendor/backport_qrs"


class MemoryStore:
    def __init__(self):
        self.backing = {}

    def read(self, key):
        return copy.deepcopy(self.backing.get(key, (None, None)))

    def compare_and_set(self, key, version, value):
        if self.read(key)[0] != version:
            return False
        self.backing[key] = (str(int(version or 0) + 1), copy.deepcopy(value))
        return True


@pytest.fixture
def bound(monkeypatch):
    target = dict(
        platform_id="longbridge",
        strategy_profile="synthetic_profile",
        service_name="synthetic-service",
        account_scope="SYNTHETIC",
        account_selector=["SYNTHETIC"],
        deployment_selector="SYNTHETIC",
        dry_run_only=False,
    )
    env = {
        unified.FLAG: "true",
        "RUNTIME_TARGET_ENABLED": "true",
        "RUNTIME_TARGET_JSON": json.dumps(target),
        "DIGEST_TARGET_ID": "synthetic",
        "DIGEST_OPAQUE_ACCOUNT_UID": "synthetic-account",
        "DIGEST_STORE_TARGET": "synthetic",
        "DIGEST_CANDIDATES_GCS_URI": "gs://synthetic-candidates/notification",
        "QSL_NOTIFICATION_MARKER_GCS_URI": "gs://synthetic-markers/notification",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    stores = {}
    monkeypatch.setattr(
        unified,
        "GCSNotificationStore",
        lambda uri: stores.setdefault(uri, MemoryStore()),
    )
    return env, target, stores


def function_from(source, name, namespace):
    node = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, ast.FunctionDef) and n.name == name
    )
    scope = dict(namespace)
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), "actual-source:" + name, "exec"
        ),
        scope,
    )
    return scope[name]


def run_cycle(monkeypatch, case, *, report, authorized=False):
    calls = []

    def record(name, value=None):
        calls.append([name, value])

    def forbidden(*a, **kw):
        pytest.fail("unexpected broker/storage boundary")

    execution = {"execution_status": "no_action"}
    action = case in {"order", "pending"}
    if action:
        execution["execution_status"] = "completed"
    if case in {"blocked", "unknown", "error"}:
        execution.update(
            execution_status={
                "blocked": "blocked",
                "unknown": "execution_unknown",
                "error": "failed",
            }[case]
        )
        if case == "blocked":
            execution["blocked_reason"] = "synthetic_rejection"
        if case == "error":
            execution["error"] = "synthetic_failure"
    plan = {"portfolio": {}, "allocation": {"target_mode": "value"}, "execution": {}}
    result = service.ExecutionCycleResult(
        plan=plan,
        portfolio={},
        allocation=plan["allocation"],
        execution=execution,
        logs=(),
        skip_logs=(),
        note_logs=(),
        action_done=action,
        pending_orders=({"order_id": "synthetic-order", "status": "unknown"},)
        if case == "pending"
        else (),
    )

    def bootstrap():
        record("bootstrap")
        return "synthetic-quote", "synthetic-trade", {}

    def snapshot():
        record("snapshot")
        return NS(as_of="2026-10-16", metadata={})

    def resolve(**kw):
        record("plan", sorted(kw))
        return plan

    def execute(**kw):
        record(
            "execute",
            {
                key: value
                for key, value in kw.items()
                if isinstance(value, (bool, int, float, str, type(None)))
            },
        )
        return result

    def sender(**kw):
        return lambda text: record("send", text) or True

    monkeypatch.setattr(composer_module, "build_cycle_sender", sender)
    for name in (
        "build_execution_command_store_from_env",
        "build_strategy_risk_state_store_from_env",
    ):
        monkeypatch.setattr(composer_module, name, lambda **kw: None)
    monkeypatch.setattr(
        composer_module,
        "build_execution_marker_store_from_env",
        lambda **kw: NS(cloud_prefix_uri="gs://synthetic-never-contacted/claims"),
    )
    composer = LongBridgeRuntimeComposer(
        project_id="synthetic-project",
        secret_name="synthetic-unused",
        token_refresh_threshold_days=30,
        account_prefix="SYNTHETIC",
        account_region="SYNTHETIC",
        strategy_profile="synthetic_profile",
        strategy_display_name="Synthetic",
        strategy_display_name_localized="Synthetic",
        strategy_domain="us_equity",
        notify_lang="zh",
        tg_token=None,
        tg_chat_id=None,
        managed_symbols=(),
        benchmark_symbol="QQQ",
        signal_effective_after_trading_days=1,
        separator="-",
        limit_sell_discount=0.99,
        limit_buy_premium=1.01,
        order_poll_interval_sec=1,
        order_poll_max_attempts=1,
        safe_haven_cash_substitute_threshold_usd=0,
        min_order_notional_usd=1,
        dry_run_only=False,
        broker_adapters=NS(
            build_market_data_port=lambda *_: NS(get_quote=forbidden),
            build_portfolio_port=lambda *_: NS(get_portfolio_snapshot=snapshot),
            build_execution_port=lambda *_: NS(submit_order=forbidden),
        ),
        strategy_adapters=NS(
            resolve_rebalance_plan=resolve, calculate_strategy_indicators=forbidden
        ),
        estimate_max_purchase_quantity_fn=forbidden,
        fetch_order_status_fn=forbidden,
        fetch_token_from_secret_fn=forbidden,
        refresh_token_if_needed_fn=forbidden,
        build_contexts_fn=forbidden,
        run_id_builder=lambda: "synthetic-run",
        event_logger=forbidden,
        report_builder=forbidden,
        report_persister=forbidden,
        translator=build_translator("zh"),
        runtime_target=None,
        env_reader=lambda name, default="": default,
        sleeper=forbidden,
        bootstrap_builder=lambda **kw: bootstrap,
    )
    runtime = composer.build_rebalance_runtime()
    config = composer.build_rebalance_config(
        live_execution_session_authorized=authorized
    )
    monkeypatch.setattr(service, "execute_rebalance_cycle", execute)
    monkeypatch.setattr(service, "build_signal_snapshot", lambda **kw: {})
    run = service.run_strategy
    with unified.bind_report(report):
        returned = run(runtime=runtime, config=config)
    assert returned is result
    summarize = function_from(
        (ROOT / "main.py").read_text(),
        "_summarize_cycle_result_for_report",
        {"unified_notifications": unified},
    )
    summary = summarize(result, dry_run=False)
    if report is not None:
        unified.attach_cycle_event(summary, result, report=report, config=config)
        report["summary"] = summary
    return calls, result, summary


def export(env, reports, store=None):
    store = store or MemoryStore()
    by_uri = {
        f"gs://synthetic-archive/archive/longbridge/core/2026-10/{i}.json": r
        for i, r in enumerate(reports)
    }

    def read(args):
        return (
            [
                {"url": uri, "metadata": {"updated": "2026-10-16T20:01:00Z"}}
                for uri in by_uri
            ]
            if args[0] == "ls"
            else by_uri[args[1]]
        )

    result = unified.emit_archived(
        {**env, "EXECUTION_REPORT_GCS_URI": "gs://synthetic-archive/archive"},
        now=unified._stamp("2026-10-17T00:00:00Z"),
        read=read,
        store=store,
    )
    return result, next(
        (
            v
            for k, (_, v) in store.backing.items()
            if not k.startswith("candidate_state/")
        ),
        None,
    )


@pytest.fixture
def qrs(monkeypatch):
    names = (
        "merge_digest_candidates",
        "notification_candidate_adapter",
        "daily_digest_notify",
        "display_names",
        "notification_mode",
        "notification_snapshot",
        "unified_daily_digest",
        "notification_approved_gates",
        "daily_digest_aggregator",
        "daily_digest_delivery_axes",
    )
    previous = {
        n: m
        for n, m in sys.modules.items()
        if any(n == p or n.startswith(p + ".") for p in names)
    }
    for name in previous:
        del sys.modules[name]
    monkeypatch.syspath_prepend(str(VENDOR))
    monkeypatch.setenv("QRS_UNIFIED_NOTIFICATION_ENABLED", "true")
    try:
        yield NS(
            merge=importlib.import_module(names[0]),
            intake=importlib.import_module(names[1]),
            digest=importlib.import_module(names[2]),
            renderer=importlib.import_module("unified_daily_digest"),
            aggregator=importlib.import_module("daily_digest_aggregator"),
        )
    finally:
        for name in list(sys.modules):
            if any(name == p or name.startswith(p + ".") for p in names):
                del sys.modules[name]
        sys.modules.update(previous)


def receive(qrs, tmp_path, env, payload, duplicate=False):
    directory = tmp_path / "candidates"
    directory.mkdir()
    (directory / "producer.json").write_text(json.dumps(payload))
    if duplicate:
        (directory / "duplicate.json").write_text(json.dumps(payload))
    merged, stats = qrs.merge.merge(None, directory)
    expected = [unified.target_binding(env)[0]]
    qrs.intake.validate_expected(expected)
    raw = qrs.merge._rows(merged)
    for row in raw:
        qrs.intake.validate_run_row(row)
    current, stale = qrs.intake.partition(raw, payload["runs"][0]["business_day"])
    assert not stale
    rows, summary = qrs.intake.aggregate(current, expected)
    text = qrs.renderer.render(
        qrs.digest.DailyDigestInput(
            business_day=payload["runs"][0]["business_day"],
            locale="zh",
            runs=qrs.digest.filter_runs_for_digest(rows),
            notification_summary=summary,
            evidence_coverage=qrs.digest.EvidenceCoverage(
                candidates_configured=True,
                candidates_loaded=True,
                candidates_count=len(rows),
            ),
        )
    )
    (tmp_path / "receiver.json").write_text(
        json.dumps(
            {"qrs_sha": QRS_SHA, "summary": summary, "rows": rows},
            default=str,
            indent=2,
        )
    )
    return rows, summary, text, stats
