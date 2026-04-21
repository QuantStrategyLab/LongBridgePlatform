import sys
import types
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLATFORM_KIT_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(PLATFORM_KIT_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_KIT_SRC))

from application.runtime_broker_adapters import build_runtime_broker_adapters
from quant_platform_kit.common.models import ExecutionReport, OrderIntent


def test_build_market_data_port_normalizes_symbols_and_caches_quotes():
    observed = {"quotes": [], "history": []}
    openapi_module = types.ModuleType("longport.openapi")
    openapi_module.Period = types.SimpleNamespace(Day="day")
    openapi_module.AdjustType = types.SimpleNamespace(ForwardAdjust="forward")

    class QuoteContext:
        def candlesticks(self, symbol, period, lookback, adjust_type):
            observed["history"].append((symbol, period, lookback, adjust_type))
            return [
                types.SimpleNamespace(close=123.45, high=124.0, low=122.0, timestamp="2026-04-18T00:00:00Z"),
                types.SimpleNamespace(close=125.67, high=126.0, low=124.0, timestamp="2026-04-21T00:00:00Z"),
            ]

    adapters = build_runtime_broker_adapters(
        strategy_symbols=("SOXL",),
        account_hash="HK",
        fetch_last_price_fn=lambda _quote_context, symbol: (
            observed["quotes"].append(symbol),
            125.67,
        )[-1],
        fetch_strategy_account_state_fn=lambda *_args, **_kwargs: {},
        submit_order_fn=lambda *_args, **_kwargs: None,
        clock=lambda: datetime(2026, 4, 21, tzinfo=timezone.utc),
    )

    original_openapi_module = sys.modules.get("longport.openapi")
    sys.modules["longport.openapi"] = openapi_module
    try:
        market_data_port = adapters.build_market_data_port(QuoteContext())
        quote_a = market_data_port.get_quote("soxl")
        quote_b = market_data_port.get_quote("SOXL.US")
        series = market_data_port.get_price_series("soxl")
    finally:
        if original_openapi_module is None:
            sys.modules.pop("longport.openapi", None)
        else:
            sys.modules["longport.openapi"] = original_openapi_module

    assert quote_a.symbol == "SOXL.US"
    assert quote_a.last_price == 125.67
    assert quote_a == quote_b
    assert observed["quotes"] == ["SOXL.US"]
    assert observed["history"] == [("SOXL.US", "day", 260, "forward")]
    assert series.symbol == "SOXL.US"
    assert [point.close for point in series.points] == [123.45, 125.67]


def test_build_portfolio_and_execution_ports_adapt_runtime_calls():
    observed = {"account_reads": [], "orders": []}
    adapters = build_runtime_broker_adapters(
        strategy_symbols=("SOXL", "BOXX"),
        account_hash="HK-001",
        fetch_last_price_fn=lambda *_args, **_kwargs: 0.0,
        fetch_strategy_account_state_fn=lambda quote_context, trade_context: (
            observed["account_reads"].append((quote_context, trade_context)),
            {
                "market_values": {"SOXL": 1200.0, "BOXX": 800.0},
                "quantities": {"SOXL": 10, "BOXX": 7},
                "available_cash": 500.0,
                "total_strategy_equity": 2500.0,
                "cash_by_currency": {"USD": 500.0},
                "sellable_quantities": {"SOXL": 10, "BOXX": 7},
            },
        )[-1],
        submit_order_fn=lambda trade_context, symbol, **kwargs: (
            observed["orders"].append((trade_context, symbol, kwargs)),
            ExecutionReport(
                symbol=symbol,
                side=kwargs["side"],
                quantity=kwargs["quantity"],
                status="submitted",
                broker_order_id="order-1",
            ),
        )[-1],
    )

    portfolio_port = adapters.build_portfolio_port("quote-context", "trade-context")
    snapshot = portfolio_port.get_portfolio_snapshot()
    execution_port = adapters.build_execution_port("trade-context")
    report = execution_port.submit_order(
        OrderIntent(
            symbol="SOXL.US",
            side="buy",
            quantity=3,
            order_type="limit",
            limit_price=126.5,
        )
    )

    assert observed["account_reads"] == [("quote-context", "trade-context")]
    assert snapshot.total_equity == 2500.0
    assert snapshot.buying_power == 500.0
    assert snapshot.metadata["account_hash"] == "HK-001"
    assert snapshot.metadata["cash_by_currency"] == {"USD": 500.0}
    assert observed["orders"] == [
        (
            "trade-context",
            "SOXL.US",
            {
                "order_kind": "limit",
                "side": "buy",
                "quantity": 3,
                "submitted_price": 126.5,
            },
        )
    ]
    assert report.broker_order_id == "order-1"
