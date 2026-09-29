from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from types import SimpleNamespace

from application.account_snapshot import (
    begin_natural_cycle_history,
    cycle_history_observation,
    end_natural_cycle_history,
    read_cycle_account_balance,
)
from application.longbridge_portfolio import fetch_strategy_account_state


def _balance(currency: str, *, cash_currency: str | None = None):
    return SimpleNamespace(
        currency=currency,
        net_assets=Decimal("1234.50"),
        total_cash=Decimal("234.50"),
        cash_infos=[
            SimpleNamespace(
                currency=cash_currency or currency,
                available_cash=Decimal("200.25"),
                frozen_cash=Decimal("20"),
                settling_cash=Decimal("14.25"),
            )
        ],
    )


def test_portfolio_capture_uses_one_complete_raw_balance_read_and_cleans_context():
    balances = [_balance("USD"), _balance("HKD")]
    trade = SimpleNamespace(
        account_balance=lambda: balances,
        stock_positions=lambda: SimpleNamespace(channels=[]),
    )
    capture = begin_natural_cycle_history()
    try:
        state = fetch_strategy_account_state(SimpleNamespace(), trade, [])
        observation = cycle_history_observation()
    finally:
        end_natural_cycle_history(capture)

    assert state["total_strategy_equity"] == 200.25
    assert observation["projection"] == {
            "broker_reported_balances": [
                {"currency": "HKD", "net_assets": "1234.5", "total_cash": "234.5"},
                {"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"},
            ],
            "cash": [
                {
                    "currency": "HKD",
                    "available_cash": "200.25",
                    "frozen_cash": "20",
                    "settling_cash": "14.25",
                },
                {
                    "currency": "USD",
                    "available_cash": "200.25",
                    "frozen_cash": "20",
                    "settling_cash": "14.25",
                },
            ],
        }
    assert observation["started"].tzinfo == timezone.utc
    assert observation["finished"].tzinfo == timezone.utc
    assert cycle_history_observation() is None


def test_incomplete_or_duplicate_balance_projection_is_not_captured():
    capture = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(lambda: [_balance("USD"), _balance("USD")])
        assert cycle_history_observation() is None
    finally:
        end_natural_cycle_history(capture)
    assert cycle_history_observation() is None


def test_missing_cash_fact_does_not_create_a_history_projection():
    account = _balance("USD")
    account.cash_infos[0].settling_cash = None
    capture = begin_natural_cycle_history()
    try:
        read_cycle_account_balance(lambda: [account])
        assert cycle_history_observation() is None
    finally:
        end_natural_cycle_history(capture)


def test_balance_capture_is_inert_without_request_context():
    read_cycle_account_balance(lambda: [])
    assert cycle_history_observation() is None
