from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from application.account_financing import (
    FinancingPayloadError,
    project_native_financing,
    validate_financing_payload,
)
from application.account_snapshot import (
    begin_natural_cycle_history,
    cycle_history_observation,
    end_natural_cycle_history,
    read_cycle_account_balance,
)
from scripts import record_daily_account_snapshot as history


def _account(
    currency="USD",
    *,
    net_assets="100",
    total_cash="80",
    cash=None,
    **financing,
):
    cash = cash or [("USD", "50")]
    return SimpleNamespace(
        currency=currency,
        net_assets=Decimal(net_assets),
        total_cash=Decimal(total_cash),
        cash_infos=[
            SimpleNamespace(
                currency=code,
                available_cash=Decimal(available),
                frozen_cash=Decimal("0"),
                settling_cash=Decimal("0"),
            )
            for code, available in cash
        ],
        **financing,
    )


def test_projection_omits_missing_cells_and_preserves_zero_negative_precision():
    balances = [
        _account(
            "USD",
            max_finance_amount=Decimal("0.00"),
            remaining_finance_amount=Decimal("-1.2500"),
            buy_power=Decimal("10.10"),
            init_margin=None,
            risk_level=2,
        )
    ]
    rows = project_native_financing(balances, balance_currencies={"USD"})
    assert rows == [
        {
            "currency": "USD",
            "max_finance_amount": "0.00",
            "remaining_finance_amount": "-1.2500",
            "buy_power": "10.10",
            "risk_level": "2",
        }
    ]


def test_projection_sorts_multicurrency_and_accepts_risk_wire_strings():
    balances = [
        _account("USD", buy_power="1", risk_level="3"),
        _account("HKD", cash=[("HKD", "1")], margin_call=Decimal("2"), risk_level=0),
    ]
    rows = project_native_financing(balances, balance_currencies={"USD", "HKD"})
    assert [row["currency"] for row in rows] == ["HKD", "USD"]
    assert rows[0]["risk_level"] == "0"
    assert rows[1]["risk_level"] == "3"


@pytest.mark.parametrize(
    "bad",
    [
        True,
        False,
        1.5,
        "1e3",
        "NaN",
        "Infinity",
        "+1",
        "01",
        "1.",
        ".1",
    ],
)
def test_projection_omits_invalid_money_and_bool_risk(bad):
    balances = [
        _account(
            "USD",
            max_finance_amount=bad,
            risk_level=True if bad is not False else False,
            buy_power=Decimal("9"),
        )
    ]
    rows = project_native_financing(balances, balance_currencies={"USD"})
    assert rows == [{"currency": "USD", "buy_power": "9"}]


def test_projection_omits_unknown_currency_only_and_duplicate_rows():
    first = _account("USD", buy_power="1")
    duplicate = _account("USD", buy_power="2")
    outside = _account("SGD", cash=[("SGD", "1")], buy_power="3")
    empty = _account("HKD", cash=[("HKD", "1")])
    rows = project_native_financing(
        [first, duplicate, outside, empty],
        balance_currencies={"USD", "HKD"},
    )
    assert rows == [{"currency": "USD", "buy_power": "1"}]


def test_validation_rejects_malformed_optional_payloads():
    currencies = {"USD", "HKD"}
    valid = [{"currency": "HKD", "buy_power": "1"}, {"currency": "USD", "risk_level": "1"}]
    assert validate_financing_payload(valid, balance_currencies=currencies) == valid
    for bad in (
        [],
        [{"currency": "USD"}],
        [{"currency": "USD", "buy_power": "1", "extra": "x"}],
        [{"currency": "USD", "buy_power": 1}],
        [{"currency": "USD", "risk_level": 1}],
        [{"currency": "USD", "risk_level": True}],
        [{"currency": "SGD", "buy_power": "1"}],
        [{"currency": "USD", "buy_power": "1"}, {"currency": "USD", "buy_power": "2"}],
        [{"currency": "USD", "buy_power": "1"}, {"currency": "HKD", "buy_power": "1"}],
        [{"currency": "USD", "buy_power": "1e2"}],
    ):
        with pytest.raises(FinancingPayloadError):
            validate_financing_payload(bad, balance_currencies=currencies)


def test_capture_keeps_one_query_same_object_and_optional_financing():
    token = begin_natural_cycle_history()
    calls = []
    response = [
        _account("USD", net_assets="100.10", buy_power=Decimal("12.00"), risk_level=1),
        _account(
            "HKD",
            net_assets="600",
            cash=[("HKD", "3"), ("SGD", "4")],
            max_finance_amount="0",
        ),
    ]
    try:
        first = read_cycle_account_balance(lambda: calls.append(1) or response)
        second = read_cycle_account_balance(lambda: calls.append(2) or [])
        observed = cycle_history_observation()
        assert first is response and second is response
        assert calls == [1]
        assert observed["projection"]["broker_reported_balances"] == [
            {"currency": "HKD", "net_assets": "600", "total_cash": "80"},
            {"currency": "USD", "net_assets": "100.1", "total_cash": "80"},
        ]
        assert observed["projection"]["financing"] == [
            {"currency": "HKD", "max_finance_amount": "0"},
            {"currency": "USD", "buy_power": "12.00", "risk_level": "1"},
        ]
    finally:
        end_natural_cycle_history(token)


def test_producer_rejects_invalid_financing_with_zero_writes():
    writes = []
    env = {
        "ACCOUNT_HISTORY_RECORDING_ENABLED": "true",
        "ACCOUNT_HISTORY_GCS_PREFIX": "gs://qsl-runtime-logs-shared/longbridge/account_snapshots",
        "ACCOUNT_HISTORY_EXPECTED_SCOPE": "SG",
        "ACCOUNT_HISTORY_TARGET_ID": "sg",
        "GOOGLE_CLOUD_PROJECT": "longbridge-prod",
    }
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    result = history.record_projected_daily_account(
        env,
        account_scope="SG",
        source_binding={"kind": history.SOURCE_KIND, "status": "bound", "id": "a" * 64},
        balances=[{"currency": "USD", "net_assets": "1", "total_cash": "1"}],
        cash=[{"currency": "USD", "available_cash": "1", "frozen_cash": "0", "settling_cash": "0"}],
        started=now,
        finished=now,
        open_store=lambda _project: writes.append(1),
        financing=[{"currency": "USD", "buy_power": "1", "unknown": "x"}],
    )
    assert result.status == "skipped"
    assert result.category == "projection_invalid"
    assert writes == []
