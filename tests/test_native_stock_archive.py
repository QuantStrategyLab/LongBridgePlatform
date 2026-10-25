"""Synthetic coverage for the audited native stock-inventory archive port.

These tests exercise only in-memory broker doubles: one request-local stock
read reused by the portfolio, the unfiltered archive projection, and the
producer's optional three-field stock block.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from application import account_snapshot
from application.longbridge_portfolio import fetch_strategy_account_state
from scripts.record_daily_account_snapshot import record_projected_daily_account

SOURCE = {"kind": "deployment_scope_token_version", "status": "bound", "id": "a" * 64}


def _balance(currency="USD", net_assets="100", total_cash="100", available_cash="50"):
    return SimpleNamespace(
        currency=currency,
        net_assets=net_assets,
        total_cash=total_cash,
        cash_infos=[
            SimpleNamespace(
                currency=currency,
                available_cash=available_cash,
                frozen_cash="0",
                settling_cash="0",
            )
        ],
    )


def _position(symbol, currency, quantity, **extra):
    return SimpleNamespace(symbol=symbol, currency=currency, quantity=quantity, **extra)


def _response(*positions):
    return SimpleNamespace(channels=[SimpleNamespace(positions=list(positions))])


def _quotes(symbols):
    return [SimpleNamespace(symbol=symbol, last_done="10.0") for symbol in symbols]


class _Store:
    def __init__(self):
        self.calls = []
        self.client = self

    def _parse_uri(self, uri):
        bucket, blob = uri.removeprefix("gs://").split("/", 1)
        return bucket, blob

    def bucket(self, _bucket):
        return self

    def blob(self, name):
        self.name = name
        return self

    def upload_from_string(self, data, *, content_type, if_generation_match, timeout, retry):
        self.calls.append((data, content_type, if_generation_match, timeout, retry))


def _producer_inputs(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "env": {
            "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
            "ACCOUNT_HISTORY_GCS_PREFIX": "gs://history/longbridge/account_snapshots",
            "ACCOUNT_HISTORY_TARGET_ID": "hk",
            "ACCOUNT_HISTORY_EXPECTED_SCOPE": "HK",
            "GOOGLE_CLOUD_PROJECT": "generic-project",
        },
        "account_scope": "HK",
        "source_binding": SOURCE,
        "balances": [{"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"}],
        "cash": [
            {
                "currency": "HKD",
                "available_cash": "200.25",
                "frozen_cash": "20",
                "settling_cash": "14.25",
            }
        ],
        "started": now - timedelta(seconds=2),
        "finished": now - timedelta(seconds=1),
    }
    values.update(overrides)
    return values


def test_cycle_stock_read_is_performed_once_and_reused():
    token = account_snapshot.begin_natural_cycle_history()
    calls = []
    response = _response(_position("700.HK", "HKD", "100"))

    def reader():
        calls.append(1)
        return response

    try:
        assert account_snapshot.read_cycle_stock_positions(reader) is response
        assert account_snapshot.read_cycle_stock_positions(reader) is response
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert len(calls) == 1


def test_cycle_stock_failure_is_reused_without_a_retry():
    token = account_snapshot.begin_natural_cycle_history()
    calls = []
    error = RuntimeError("synthetic stock read failure")

    def reader():
        calls.append(1)
        raise error

    try:
        with pytest.raises(RuntimeError) as first:
            account_snapshot.read_cycle_stock_positions(reader)
        with pytest.raises(RuntimeError) as second:
            account_snapshot.read_cycle_stock_positions(reader)
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert first.value is error
    assert second.value is error
    assert len(calls) == 1


def test_cycle_projection_keeps_full_inventory_outside_strategy_assets():
    token = account_snapshot.begin_natural_cycle_history()
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
    q_ctx = SimpleNamespace(quote=_quotes)
    try:
        state = fetch_strategy_account_state(q_ctx, t_ctx, ["AAPL"])
        second_state = fetch_strategy_account_state(q_ctx, t_ctx, ["AAPL"])
        observation = account_snapshot.cycle_history_observation()
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert len(stock_calls) == 1
    assert observation["projection"]["broker_reported_positions"] == [
        {"symbol": "700.HK", "currency": "HKD", "quantity": "5", "market_value": None},
        {"symbol": "AAPL.US", "currency": "USD", "quantity": "2", "market_value": None},
    ]
    assert state["quantities"] == {"AAPL": 2.0}
    assert state["market_values"] == {"AAPL": 20.0}
    assert second_state["quantities"] == state["quantities"]


def test_unknown_stock_response_is_not_archived_as_empty():
    token = account_snapshot.begin_natural_cycle_history()
    try:
        account_snapshot.read_cycle_account_balance(lambda: [_balance()])
        account_snapshot.read_cycle_stock_positions(
            lambda: SimpleNamespace(channels=None)
        )
        observation = account_snapshot.cycle_history_observation()
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert "broker_reported_positions" not in observation["projection"]


def test_proven_empty_stock_inventory_is_archived_as_empty():
    token = account_snapshot.begin_natural_cycle_history()
    try:
        account_snapshot.read_cycle_account_balance(lambda: [_balance()])
        account_snapshot.read_cycle_stock_positions(lambda: _response())
        observation = account_snapshot.cycle_history_observation()
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert observation["projection"]["broker_reported_positions"] == []


def test_projection_keeps_only_public_stock_facts():
    rows = account_snapshot.project_cycle_stock_positions(
        _response(
            _position(
                "700.HK",
                "HKD",
                "3",
                average_cost="private-cost",
                market_value="999",
                account_id="private-account",
            )
        )
    )

    assert rows == [
        {"symbol": "700.HK", "currency": "HKD", "quantity": "3", "market_value": None}
    ]
    assert "private" not in json.dumps(rows)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(channels=None),
        SimpleNamespace(),
        SimpleNamespace(channels=[SimpleNamespace(positions=None)]),
        _response(_position("700.HK", "HKD", "1"), _position("700.HK", "HKD", "2")),
        _response(_position("700.HK", "HKD", "NaN")),
        _response(_position("700.HK", "HKD", "1234567890123456")),
        _response(_position("700.HK", "HKD", "0.123456789")),
        _response(*[_position(f"SYNTH{index:02d}.HK", "HKD", "1") for index in range(65)]),
    ],
)
def test_unknown_or_unbounded_stock_projection_stays_unknown(response):
    assert account_snapshot.project_cycle_stock_positions(response) is None


def test_portfolio_reuses_the_probe_failure_without_reading_again():
    token = account_snapshot.begin_natural_cycle_history()
    calls = []

    def stock_reader():
        calls.append(1)
        raise RuntimeError("synthetic stock read failure")

    t_ctx = SimpleNamespace(
        account_balance=lambda **_: [_balance()],
        stock_positions=stock_reader,
    )
    try:
        account_snapshot.read_cycle_account_balance(t_ctx.account_balance)
        with pytest.raises(RuntimeError):
            account_snapshot.read_cycle_stock_positions(stock_reader)
        with pytest.raises(RuntimeError) as raised:
            fetch_strategy_account_state(SimpleNamespace(), t_ctx, ["AAPL"])
    finally:
        account_snapshot.end_natural_cycle_history(token)

    assert str(raised.value) == "LongBridge stock positions unavailable"
    assert len(calls) == 1


def test_producer_without_inventory_keeps_the_original_object():
    store = _Store()
    result = record_projected_daily_account(
        **_producer_inputs(), open_store=lambda _project: store
    )

    assert result.status == "recorded"
    record = json.loads(store.calls[0][0])
    assert "broker_reported_positions" not in record
    assert "broker_reported_positions_scope" not in record
    assert "broker_reported_positions_complete" not in record
    assert record["broker_reported_balances"] == [
        {"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"}
    ]
    assert record["cash"] == [
        {
            "currency": "HKD",
            "available_cash": "200.25",
            "frozen_cash": "20",
            "settling_cash": "14.25",
        }
    ]


def test_producer_explicit_none_inventory_keeps_the_original_object():
    store = _Store()
    result = record_projected_daily_account(
        **_producer_inputs(positions=None), open_store=lambda _project: store
    )

    assert result.status == "recorded"
    assert "broker_reported_positions" not in json.loads(store.calls[0][0])


def test_producer_proven_empty_inventory_carries_all_three_fields():
    store = _Store()
    result = record_projected_daily_account(
        **_producer_inputs(positions=[]), open_store=lambda _project: store
    )

    assert result.status == "recorded"
    record = json.loads(store.calls[0][0])
    assert record["broker_reported_positions"] == []
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True


def test_producer_projects_whitelisted_rows_without_private_fields():
    store = _Store()
    result = record_projected_daily_account(
        **_producer_inputs(
            positions=[
                {
                    "symbol": "ZZZ.US",
                    "currency": "USD",
                    "quantity": "1",
                    "average_cost": "private-cost",
                    "market_value": "999",
                },
                {"symbol": "700.HK", "currency": "HKD", "quantity": "-0.5"},
            ]
        ),
        open_store=lambda _project: store,
    )

    assert result.status == "recorded"
    body = store.calls[0][0]
    record = json.loads(body)
    assert record["broker_reported_positions"] == [
        {"symbol": "700.HK", "quantity": "-0.5", "currency": "HKD", "market_value": None},
        {"symbol": "ZZZ.US", "quantity": "1", "currency": "USD", "market_value": None},
    ]
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True
    assert "private-cost" not in body
    assert "999" not in body


@pytest.mark.parametrize(
    "positions",
    [
        "not-a-list",
        {},
        [None],
        ["700.HK"],
        [{"symbol": "700.HK", "currency": "HKD"}],
        [{"symbol": "700.HK", "currency": "hkd", "quantity": "1"}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": True}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": 1.5}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": "NaN"}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": "1e3"}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": "1234567890123456"}],
        [{"symbol": "700.HK", "currency": "HKD", "quantity": "0.123456789"}],
        [
            {"symbol": "700.HK", "currency": "HKD", "quantity": "1"},
            {"symbol": "700.HK", "currency": "HKD", "quantity": "2"},
        ],
        [{"symbol": f"SYNTH{index:02d}.HK", "currency": "HKD", "quantity": "1"} for index in range(65)],
    ],
)
def test_producer_rejects_invalid_or_unbounded_inventory(positions):
    opened = []

    result = record_projected_daily_account(
        **_producer_inputs(positions=positions),
        open_store=lambda project: opened.append(project),
    )

    assert result.status == "skipped"
    assert result.category == "projection_invalid"
    assert opened == []
