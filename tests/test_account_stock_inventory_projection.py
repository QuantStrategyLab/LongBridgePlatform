from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import record_daily_account_snapshot as snapshots  # noqa: E402


T0 = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
BINDING = "a" * 64
CONFIG = snapshots._IndependentConfig(
    project_id="example-project",
    region="asia-east2",
    service="example-hk-reader",
    service_url="https://example-hk-reader-abc123-uc.a.run.app",
    source_binding_id=BINDING,
    target_id="hk",
    account_scope="HK",
)


def _payload(**overrides):
    value = {
        "schema_version": "longbridge_account_snapshot.v1",
        "status": "partial",
        "account_scope": "HK",
        "observed_started_at": (T0 + timedelta(seconds=1)).isoformat(),
        "observed_finished_at": (T0 + timedelta(seconds=2)).isoformat(),
        "snapshot_atomic": False,
        "broker_reported_balances": [{"currency": "USD", "net_assets": "10.25", "total_cash": "-2.5"}],
        "cash": [{"currency": "HKD", "available_cash": "0", "frozen_cash": "0", "settling_cash": "0"}],
        "positions": [{"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "3"}],
        "positions_complete": True,
        "cash_complete": True,
        "no_order": True,
        "live_authority_granted": False,
        "source_binding": {"kind": "deployment_scope_token_version", "status": "bound", "id": BINDING},
    }
    value.update(overrides)
    return value


def _project(payload):
    return snapshots._project_independent_history(
        payload, CONFIG, T0, T0 + timedelta(seconds=5),
    )


def _rejects(payload):
    with pytest.raises(snapshots._Rejected) as raised:
        _project(payload)
    assert raised.value.category == "snapshot_invalid"


def test_non_empty_stock_inventory_projects_quantity_without_price():
    record = _project(_payload(positions=[
        {"symbol": "700.HK", "currency": "HKD", "quantity": "100"},
        {"symbol": "AAPL.US", "currency": "USD", "quantity": "-0.5"},
    ]))
    assert record["broker_reported_positions"] == [
        {"symbol": "700.HK", "quantity": "100", "currency": "HKD", "market_value": None},
        {"symbol": "AAPL.US", "quantity": "-0.5", "currency": "USD", "market_value": None},
    ]
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True


def test_empty_positions_list_is_proven_empty_stock_inventory():
    record = _project(_payload(positions=[]))
    assert record["broker_reported_positions"] == []
    assert record["broker_reported_positions_scope"] == "stocks_only"
    assert record["broker_reported_positions_complete"] is True


_MISSING = object()


@pytest.mark.parametrize("positions", [_MISSING, None, {}, "[]", [None], ["SYNTH.HK"], 0])
def test_missing_null_object_or_non_list_positions_are_rejected(positions):
    payload = _payload()
    if positions is _MISSING:
        payload.pop("positions")
    else:
        payload["positions"] = positions
    _rejects(payload)


@pytest.mark.parametrize("complete", [False, None, "true", 1, 0])
def test_positions_complete_must_be_exactly_true(complete):
    _rejects(_payload(positions_complete=complete))


def test_missing_positions_complete_is_rejected():
    payload = _payload()
    payload.pop("positions_complete")
    _rejects(payload)


@pytest.mark.parametrize(
    "quantity",
    ["0", "-0", "-1.25", "0.00000001", "123456789012345.12345678", "-0.5"],
)
def test_fractional_short_and_zero_quantities_are_preserved(quantity):
    record = _project(_payload(positions=[
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": quantity},
    ]))
    assert record["broker_reported_positions"][0]["quantity"] == quantity


@pytest.mark.parametrize(
    "rows",
    [
        [
            {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "1"},
            {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "2"},
        ],
        [
            {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "1"},
            {"symbol": "SYNTH.HK", "currency": "USD", "quantity": "2"},
        ],
    ],
)
def test_duplicate_symbols_are_rejected_without_aggregation(rows):
    _rejects(_payload(positions=rows))


@pytest.mark.parametrize(
    "row",
    [
        {"symbol": "synth.hk", "currency": "HKD", "quantity": "1"},
        {"symbol": " SYNTH.HK", "currency": "HKD", "quantity": "1"},
        {"symbol": "SYNTH.HK", "currency": "hkd", "quantity": "1"},
        {"symbol": "SYNTH.HK", "currency": "HK", "quantity": "1"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": True},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": 1.5},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "NaN"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "Infinity"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "1e3"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "00"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "1."},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": ".5"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "0.123456789"},
        {"symbol": "SYNTH.HK", "currency": "HKD", "quantity": "1234567890123456"},
        {"symbol": "SYNTH.HK", "currency": "HKD"},
        {"symbol": "SYNTH.HK", "quantity": "1"},
        {"currency": "HKD", "quantity": "1"},
    ],
)
def test_invalid_symbol_currency_or_quantity_is_rejected(row):
    _rejects(_payload(positions=[row]))


def _rows(count):
    return [
        {"symbol": f"SYNTH{index:02d}.HK", "currency": "HKD", "quantity": "1"}
        for index in range(count)
    ]


def test_more_than_sixty_four_rows_are_rejected():
    _rejects(_payload(positions=_rows(65)))


def test_sixty_four_rows_are_accepted():
    record = _project(_payload(positions=_rows(64)))
    assert len(record["broker_reported_positions"]) == 64


def test_private_position_fields_and_orders_are_not_projected():
    payload = _payload(
        positions=[{
            "symbol": "SYNTH.HK", "currency": "HKD", "quantity": "3",
            "average_cost": "private-cost", "market_value": "999",
            "account_id": "private-account",
        }],
        known_non_terminal_orders_7d=[{"synthetic": "private-order"}],
        known_recent_executions_7d_count=1,
    )
    record = _project(payload)
    assert record["broker_reported_positions"] == [
        {"symbol": "SYNTH.HK", "quantity": "3", "currency": "HKD", "market_value": None},
    ]
    serialized = json.dumps(record)
    assert "private" not in serialized and "999" not in serialized


def test_positions_are_sorted_by_currency_then_symbol_without_mutating_input():
    payload = _payload(positions=[
        {"symbol": "ZZZ.US", "currency": "USD", "quantity": "1"},
        {"symbol": "700.HK", "currency": "HKD", "quantity": "2"},
        {"symbol": "AAA.US", "currency": "USD", "quantity": "3"},
        {"symbol": "000.HK", "currency": "HKD", "quantity": "4"},
    ])
    before = json.loads(json.dumps(payload))
    record = _project(payload)
    assert [row["symbol"] for row in record["broker_reported_positions"]] == [
        "000.HK", "700.HK", "AAA.US", "ZZZ.US",
    ]
    assert payload == before


def test_rejection_category_is_fixed_and_does_not_include_input():
    secret = "private-symbol-value"
    with pytest.raises(snapshots._Rejected) as raised:
        _project(_payload(positions=[{"symbol": secret, "currency": "HKD", "quantity": "1"}]))
    assert raised.value.category == "snapshot_invalid"
    assert secret not in raised.value.category
