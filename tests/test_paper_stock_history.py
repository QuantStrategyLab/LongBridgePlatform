"""Synthetic coverage for the PAPER native stock-inventory archive port.

Only in-memory broker doubles are used: one request-local native stock read is
reused by the portfolio, the full inventory is archived before strategy
filtering, and the producer accepts only the three public stock facts.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from application.account_snapshot import (
    begin_natural_cycle_history,
    cycle_history_observation,
    end_natural_cycle_history,
    project_cycle_stock_positions,
    read_cycle_account_balance,
    read_cycle_stock_positions,
)
from application.longbridge_portfolio import fetch_strategy_account_state
from scripts.record_daily_account_snapshot import record_projected_daily_account


SOURCE = {"kind": "deployment_scope_token_version", "status": "bound", "id": "a" * 64}
PAPER_ENV = {
    "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
    "ACCOUNT_HISTORY_GCS_PREFIX": "gs://paper-history/longbridge/account_snapshots",
    "ACCOUNT_HISTORY_TARGET_ID": "paper",
    "ACCOUNT_HISTORY_EXPECTED_SCOPE": "PAPER",
    "GOOGLE_CLOUD_PROJECT": "longbridgequant",
}


def _balance(
    currency="USD",
    *,
    net_assets="100",
    total_cash="100",
    available_cash="50",
    financing=None,
):
    return SimpleNamespace(
        currency=currency,
        net_assets=Decimal(net_assets),
        total_cash=Decimal(total_cash),
        cash_infos=[
            SimpleNamespace(
                currency=currency,
                available_cash=Decimal(available_cash),
                frozen_cash=Decimal("0"),
                settling_cash=Decimal("0"),
            )
        ],
        **(financing or {}),
    )


def _position(symbol, currency, quantity, **extra):
    return SimpleNamespace(symbol=symbol, currency=currency, quantity=quantity, **extra)


def _response(*positions):
    return SimpleNamespace(channels=[SimpleNamespace(positions=list(positions))])


class _Store:
    def __init__(self):
        self.calls = []
        self.client = self

    def _parse_uri(self, uri):
        bucket, blob = uri.removeprefix("gs://").split("/", 1)
        return bucket, blob

    def bucket(self, _bucket):
        return self

    def blob(self, _name):
        return self

    def upload_from_string(self, data, **_kwargs):
        self.calls.append(data)


def _record(observation, **overrides):
    store = _Store()
    projection = observation["projection"]
    values = {
        "env": PAPER_ENV,
        "account_scope": "PAPER",
        "source_binding": SOURCE,
        "balances": projection["broker_reported_balances"],
        "cash": projection["cash"],
        "started": observation["started"],
        "finished": observation["finished"],
        "financing": projection.get("financing"),
        "positions": projection.get("broker_reported_positions"),
    }
    values.update(overrides)
    result = record_projected_daily_account(**values, open_store=lambda _project: store)
    record = json.loads(store.calls[0]) if store.calls else None
    return result, record


def _run_probe_with_stock_inventory():
    """Exercise the real probe path with synthetic broker and store doubles."""
    import sys
    from pathlib import Path

    tests_dir = str(Path(__file__).resolve().parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from test_request_handling import load_module

    module = load_module()
    module.ACCOUNT_REGION = "PAPER"
    module._v7_application_candidate_bound = lambda: False

    observed = {"balance": 0, "stocks": 0, "portfolio": 0, "stored": None}
    account = _balance(
        financing={
            "max_finance_amount": Decimal("0.00"),
            "buy_power": Decimal("12.00"),
            "risk_level": 1,
        }
    )
    positions = [
        _position("700.HK", "HKD", Decimal("5"), available_quantity=Decimal("5")),
        _position("AAPL.US", "USD", Decimal("2"), available_quantity=Decimal("2")),
    ]

    class TradeContext:
        def account_balance(self, **_kwargs):
            observed["balance"] += 1
            return [account]

        def stock_positions(self):
            observed["stocks"] += 1
            return _response(*positions)

    trade = TradeContext()

    class QuoteContext:
        def quote(self, _symbols):
            return [
                SimpleNamespace(symbol="AAPL.US", last_done="10.0"),
                SimpleNamespace(symbol="700.HK", last_done="300.0"),
            ]

    quote = QuoteContext()

    class FakePort:
        def get_portfolio_snapshot(self):
            observed["portfolio"] += 1
            state = fetch_strategy_account_state(quote, trade, ["AAPL"])
            return SimpleNamespace(
                buying_power=state["available_cash"],
                total_equity=state["total_strategy_equity"],
                positions=(),
            )

    class FakeRuntime:
        def portfolio_port_factory(self, *_args):
            return FakePort()

    class FakeComposer:
        strategy_adapters = SimpleNamespace(calculate_strategy_indicators=lambda _context: {})

        def build_reporting_adapters(self):
            return SimpleNamespace(
                start_run=lambda: (None, {"status": "pending"}),
                log_event=lambda *_args, **_kwargs: None,
                persist_execution_report=lambda _report: None,
            )

        def build_rebalance_runtime(self, **_kwargs):
            return FakeRuntime()

        def build_account_snapshot_broker_contexts(self):
            return quote, trade, SOURCE

    class Store(_Store):
        def upload_from_string(self, data, **_kwargs):
            observed["stored"] = data
            super().upload_from_string(data, **_kwargs)

    module.build_composer = lambda **_kwargs: FakeComposer()
    module._open_account_history_store = lambda _project: Store()
    submit = Mock()
    module.submit_order = submit

    with patch.dict(os.environ, PAPER_ENV, clear=False):
        result = module.run_probe()
    return result, observed, submit


def test_probe_archives_full_inventory_before_filtering_and_never_trades():
    result, observed, submit = _run_probe_with_stock_inventory()

    assert result == ("Probe OK", 200)
    assert observed["balance"] == 1
    assert observed["stocks"] == 1
    assert observed["portfolio"] == 1
    submit.assert_not_called()

    record = json.loads(observed["stored"])
    assert record["broker_reported_positions"] == [
        {"symbol": "700.HK", "quantity": "5", "currency": "HKD", "market_value": None},
        {"symbol": "AAPL.US", "quantity": "2", "currency": "USD", "market_value": None},
    ]
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True
    assert record["broker_reported_balances"] == [
        {"currency": "USD", "net_assets": "100", "total_cash": "100"}
    ]
    assert record["financing"] == [
        {
            "currency": "USD",
            "max_finance_amount": "0.00",
            "buy_power": "12.00",
            "risk_level": "1",
        }
    ]


def test_portfolio_reuses_one_native_stock_read_and_keeps_unfiltered_inventory():
    stock_calls = []
    t_ctx = SimpleNamespace(
        account_balance=lambda **_: [_balance()],
        stock_positions=lambda: (
            stock_calls.append(1)
            or _response(
                _position("AAPL.US", "USD", "2"),
                _position("700.HK", "HKD", "5"),
            )
        ),
    )
    q_ctx = SimpleNamespace(
        quote=lambda symbols: [
            SimpleNamespace(symbol=symbol, last_done="10.0") for symbol in symbols
        ]
    )
    token = begin_natural_cycle_history()
    try:
        first = fetch_strategy_account_state(q_ctx, t_ctx, ["AAPL"])
        second = fetch_strategy_account_state(q_ctx, t_ctx, ["AAPL"])
        observation = cycle_history_observation()
    finally:
        end_natural_cycle_history(token)

    assert len(stock_calls) == 1
    assert first["quantities"] == {"AAPL": 2.0}
    assert second["quantities"] == first["quantities"]
    assert observation["projection"]["broker_reported_positions"] == [
        {"symbol": "700.HK", "currency": "HKD", "quantity": "5", "market_value": None},
        {"symbol": "AAPL.US", "currency": "USD", "quantity": "2", "market_value": None},
    ]


def test_proven_empty_inventory_is_archived_as_empty_not_unknown():
    token = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(lambda: [_balance()])
        read_cycle_stock_positions(lambda: _response())
        observation = cycle_history_observation()
    finally:
        end_natural_cycle_history(token)

    assert observation["projection"]["broker_reported_positions"] == []
    result, record = _record(observation)
    assert result.status == "recorded"
    assert record["broker_reported_positions"] == []
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True


def test_no_account_channel_is_unknown_not_a_proven_empty_book():
    # Reconciliation also treats zero channels as incomplete position evidence.
    assert project_cycle_stock_positions(SimpleNamespace(channels=[])) is None


def test_stock_read_failure_keeps_balance_and_financing_and_leaves_inventory_unknown():
    calls = []
    financing = {
        "max_finance_amount": Decimal("0.00"),
        "buy_power": Decimal("12.00"),
        "risk_level": 1,
    }

    def failing_reader():
        calls.append(1)
        raise RuntimeError("synthetic stock read failure")

    t_ctx = SimpleNamespace(
        account_balance=lambda: [_balance(financing=financing)],
        stock_positions=failing_reader,
    )
    token = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(t_ctx.account_balance)
        try:
            read_cycle_stock_positions(failing_reader)
        except RuntimeError:
            pass
        observation = cycle_history_observation()
        with pytest.raises(RuntimeError) as raised:
            fetch_strategy_account_state(SimpleNamespace(quote=lambda _symbols: []), t_ctx, ["AAPL"])
    finally:
        end_natural_cycle_history(token)

    assert str(raised.value) == "LongBridge stock positions unavailable"
    assert len(calls) == 1
    projection = observation["projection"]
    assert projection["broker_reported_balances"] == [
        {"currency": "USD", "net_assets": "100", "total_cash": "100"}
    ]
    assert projection["cash"] == [
        {
            "currency": "USD",
            "available_cash": "50",
            "frozen_cash": "0",
            "settling_cash": "0",
        }
    ]
    assert projection["financing"] == [
        {
            "currency": "USD",
            "max_finance_amount": "0.00",
            "buy_power": "12.00",
            "risk_level": "1",
        }
    ]
    assert "broker_reported_positions" not in projection

    result, record = _record(observation)
    assert result.status == "recorded"
    assert record["broker_reported_balances"] == projection["broker_reported_balances"]
    assert record["cash"] == projection["cash"]
    assert record["financing"] == projection["financing"]
    assert "broker_reported_positions" not in record
    assert "broker_reported_positions_scope" not in record
    assert "broker_reported_positions_complete" not in record


def test_multi_currency_inventory_keeps_only_public_facts():
    rows = project_cycle_stock_positions(
        _response(
            _position(
                "700.HK",
                "HKD",
                "3",
                average_cost="private-cost",
                market_value="999",
                account_id="private-account",
            ),
            _position("AAPL.US", "USD", "2"),
        )
    )

    assert rows == [
        {"symbol": "700.HK", "currency": "HKD", "quantity": "3", "market_value": None},
        {"symbol": "AAPL.US", "currency": "USD", "quantity": "2", "market_value": None},
    ]
    assert "private" not in json.dumps(rows)
    assert "999" not in json.dumps(rows)


def test_duplicate_symbol_in_one_cycle_stays_unknown_without_fabricated_rows():
    duplicate = _response(
        _position("700.HK", "HKD", "1"),
        _position("700.HK", "USD", "2"),
    )
    assert project_cycle_stock_positions(duplicate) is None

    token = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(lambda: [_balance()])
        read_cycle_stock_positions(lambda: duplicate)
        observation = cycle_history_observation()
    finally:
        end_natural_cycle_history(token)

    assert "broker_reported_positions" not in observation["projection"]
    result, record = _record(observation)
    assert result.status == "recorded"
    assert "broker_reported_positions" not in record


@pytest.mark.parametrize("cost", ["-12.34000000000000000001", "0.00000000", "123.4500"])
def test_native_cost_is_retained_in_paper_archive_without_average_inference(cost):
    token = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(lambda: [_balance()])
        read_cycle_stock_positions(lambda: _response(
            _position("SYNTH.US", "USD", Decimal("2"), cost_price=Decimal(cost), account_id="private")
        ))
        result, archived = _record(cycle_history_observation())
    finally:
        end_natural_cycle_history(token)
    assert result.status == "recorded"
    row = archived["broker_reported_positions"][0]
    assert row["broker_cost_price"] == cost
    assert row["broker_cost_method"] == "broker_configured"
    assert row["market_value"] is None and "avg_cost" not in row
    assert "private" not in json.dumps(archived)


@pytest.mark.parametrize("cost", [None, True, 1.25, "NaN", "Infinity", "1e3", "1\n", "1" * 31, "0." + "1" * 29])
def test_invalid_optional_cost_keeps_paper_stock_inventory(cost):
    row = project_cycle_stock_positions(_response(_position("SYNTH.US", "USD", "2", cost_price=cost)))[0]
    assert row == {"symbol": "SYNTH.US", "currency": "USD", "quantity": "2", "market_value": None}
