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


def _balance(currency):
    return SimpleNamespace(
        currency=currency,
        net_assets=Decimal("1234.50"),
        total_cash=Decimal("234.50"),
        cash_infos=[SimpleNamespace(
            currency=currency,
            available_cash=Decimal("200.25"),
            frozen_cash=Decimal("20"),
            settling_cash=Decimal("14.25"),
        )],
    )


def test_portfolio_reuses_one_complete_raw_balance_read_and_cleans_capture():
    balances = [_balance("HKD"), _balance("USD")]
    calls = []
    trade = SimpleNamespace(
        account_balance=lambda: calls.append("balance") or balances,
        stock_positions=lambda: SimpleNamespace(channels=[]),
    )
    token = begin_natural_cycle_history()
    try:
        state = fetch_strategy_account_state(SimpleNamespace(), trade, [])
        observation = cycle_history_observation()
    finally:
        end_natural_cycle_history(token)

    assert calls == ["balance"]
    assert state["total_strategy_equity"] == 200.25
    assert observation["projection"]["broker_reported_balances"] == [
        {"currency": "HKD", "net_assets": "1234.5", "total_cash": "234.5"},
        {"currency": "USD", "net_assets": "1234.5", "total_cash": "234.5"},
    ]
    assert observation["finished"].tzinfo == timezone.utc
    assert cycle_history_observation() is None
