from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if QPK_SRC.exists() and str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))

from application import broker_reconciliation as reconciliation


def _runtime_target():
    return SimpleNamespace(
        platform_id="longbridge",
        strategy_profile="soxl_soxx_trend_income",
        account_scope="PAPER",
        live_continuity=SimpleNamespace(
            state="RECONCILE_ONLY",
            baseline_id="longbridge-paper-baseline",
            baseline_target_sha256="1" * 64,
        ),
    )


def _read_only_trade_context(*, fail_surface: str | None = None):
    calls: list[str] = []

    def read(name, value):
        def fn(*_args, **_kwargs):
            calls.append(name)
            if fail_surface == name:
                raise RuntimeError("provider failure")
            return value

        return fn

    context = SimpleNamespace(
        account_balance=read(
            "account_balance",
            [
                SimpleNamespace(
                    currency="USD",
                    total_cash="10",
                    net_assets="10",
                    buy_power="10",
                    cash_infos=[
                        SimpleNamespace(
                            currency="USD",
                            available_cash="10",
                            frozen_cash="0",
                            settling_cash="0",
                        )
                    ],
                )
            ],
        ),
        stock_positions=read(
            "stock_positions",
            SimpleNamespace(
                channels=[
                    SimpleNamespace(
                        account_channel="Cash",
                        positions=[
                            SimpleNamespace(
                                symbol="SOXL.US",
                                quantity="1",
                                available_quantity="1",
                                currency="USD",
                                cost_price="10",
                            )
                        ],
                    )
                ]
            ),
        ),
        history_orders=read("history_orders", []),
        history_executions=read("history_executions", []),
        submit_order=lambda *_args, **_kwargs: pytest.fail("must not submit orders"),
        cancel_order=lambda *_args, **_kwargs: pytest.fail("must not cancel orders"),
        replace_order=lambda *_args, **_kwargs: pytest.fail("must not replace orders"),
    )
    return context, calls


def test_disabled_reconciliation_does_not_build_contexts():
    payload, status = reconciliation.run_read_only_broker_reconciliation(
        enabled=False,
        account_scope="PAPER",
        runtime_target=_runtime_target(),
        strategy_profile="soxl_soxx_trend_income",
        project_id=None,
        build_read_only_contexts=lambda: pytest.fail("must not build contexts"),
        collect_evidence=reconciliation.collect_read_only_reconciliation_observations,
    )

    assert status == 503
    assert payload["reason"] == "broker_reconciliation_disabled"


def test_missing_collector_and_runtime_target_fail_before_contexts():
    for collector, runtime_target, reason in (
        (None, _runtime_target(), "broker_reconciliation_collector_unavailable"),
        (
            reconciliation.collect_read_only_reconciliation_observations,
            None,
            "broker_reconciliation_runtime_target_unavailable",
        ),
    ):
        payload, status = reconciliation.run_read_only_broker_reconciliation(
            enabled=True,
            account_scope="PAPER",
            runtime_target=runtime_target,
            strategy_profile="soxl_soxx_trend_income",
            project_id=None,
            build_read_only_contexts=lambda: pytest.fail("must not build contexts"),
            collect_evidence=collector,
        )
        assert status == 503
        assert payload["reason"] == reason


def test_provider_read_failure_is_rejected_without_fallback_data():
    context, _calls = _read_only_trade_context(fail_surface="account_balance")

    with pytest.raises(reconciliation.LongBridgeReconciliationReadError):
        reconciliation.collect_read_only_reconciliation_observations(
            object(), context, account_scope="PAPER"
        )


def test_read_only_observations_keep_partial_identity_and_open_order_scope_blocked():
    context, calls = _read_only_trade_context()

    observations = reconciliation.collect_read_only_reconciliation_observations(
        object(), context, account_scope="PAPER", now=datetime(2026, 9, 4, tzinfo=timezone.utc)
    )

    assert calls == [
        "account_balance",
        "stock_positions",
        "history_orders",
        "history_executions",
    ]
    assert observations.account_identity_match is False
    assert observations.open_orders_complete is False


def test_read_only_candidate_without_trusted_baseline_is_blocked(monkeypatch):
    context, _calls = _read_only_trade_context()
    observations = reconciliation.collect_read_only_reconciliation_observations(
        object(), context, account_scope="PAPER", now=datetime(2026, 9, 4, tzinfo=timezone.utc)
    )

    class MarkerStore:
        def calculate_recent_ledger_digest(self, **_kwargs):
            return "2" * 64, 0

    monkeypatch.setattr(
        reconciliation,
        "build_execution_marker_store_from_env",
        lambda **_kwargs: MarkerStore(),
    )
    candidate = reconciliation.build_reconciliation_candidate(
        observations=observations,
        runtime_target=_runtime_target(),
        project_id=None,
        env_reader=lambda *_args: "",
        observed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
    )

    assert candidate.expected_digests_configured is False
    assert candidate.permits_active_lkg is False
    blocker_names = {finding.value for finding in candidate.recovery_blockers}
    assert "broker_reconciliation_account_identity_mismatch" in blocker_names
    assert "broker_reconciliation_positions_mismatch" in blocker_names
