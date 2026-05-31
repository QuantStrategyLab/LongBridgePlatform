import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))

from application.longbridge_execution import submit_order
from application.longbridge_portfolio import fetch_strategy_account_state
from quant_platform_kit.common.models import ExecutionReport


class FakeQuoteContext:
    def __init__(self):
        self.quote_calls = []

    def quote(self, symbols):
        self.quote_calls.append(tuple(symbols))
        prices = {"SOXL.US": 50.0, "QQQI.US": 20.0, "00700.HK": 320.0}
        return [
            type("Quote", (), {"symbol": symbol, "last_done": prices[symbol]})()
            for symbol in symbols
        ]


class FakePosition:
    def __init__(self, symbol, quantity, available_quantity=None):
        self.symbol = symbol
        self.quantity = quantity
        self.available_quantity = available_quantity if available_quantity is not None else quantity


class FakeChannel:
    def __init__(self, positions):
        self.positions = positions


class FakePositionsResponse:
    def __init__(self):
        self.channels = [FakeChannel([FakePosition("SOXL.US", 3), FakePosition("QQQI.US", 2, 1)])]


class LongBridgeLocalHelpersTests(unittest.TestCase):
    def test_fetch_strategy_account_state_falls_back_when_account_balance_fails(self):
        class BalanceFailingTradeContext:
            def account_balance(self):
                raise RuntimeError("boom")

            def stock_positions(self):
                return FakePositionsResponse()

        warnings = []
        state = fetch_strategy_account_state(
            FakeQuoteContext(),
            BalanceFailingTradeContext(),
            ["SOXL", "QQQI", "SPYI"],
            warning_log_fn=warnings.append,
        )

        self.assertEqual(state["available_cash"], 0.0)
        self.assertEqual(state["cash_by_currency"], {})
        self.assertEqual(state["market_values"]["SOXL"], 150.0)
        self.assertEqual(state["quantities"]["QQQI"], 2)
        self.assertEqual(state["sellable_quantities"]["QQQI"], 1)
        self.assertEqual(state["total_strategy_equity"], 190.0)
        self.assertEqual(
            warnings,
            [
                "[longbridge_account_balance_failed] error_type=RuntimeError error=boom",
            ],
        )

    def test_fetch_strategy_account_state_uses_configured_cash_currency(self):
        class TradeContext:
            def account_balance(self):
                usd = types.SimpleNamespace(currency="USD", available_cash=100.0)
                hkd = types.SimpleNamespace(currency="HKD", available_cash=8000.0)
                return [types.SimpleNamespace(cash_infos=[usd, hkd])]

            def stock_positions(self):
                return types.SimpleNamespace(
                    channels=[FakeChannel([FakePosition("00700.HK", 2)])]
                )

        state = fetch_strategy_account_state(
            FakeQuoteContext(),
            TradeContext(),
            ["00700"],
            cash_currency="HKD",
        )

        self.assertEqual(state["available_cash"], 8000.0)
        self.assertEqual(state["cash_by_currency"], {"USD": 100.0, "HKD": 8000.0})
        self.assertEqual(state["market_values"]["00700"], 640.0)
        self.assertEqual(state["trading_currency"], "HKD")
        self.assertEqual(state["total_strategy_equity"], 8640.0)

    def test_submit_order_retries_once_on_internal_server_error(self):
        longport_module = types.ModuleType("longport")
        openapi_module = types.ModuleType("longport.openapi")
        openapi_module.OrderSide = types.SimpleNamespace(Buy="Buy", Sell="Sell")
        openapi_module.OrderType = types.SimpleNamespace(LO="LO", MO="MO")
        openapi_module.TimeInForceType = types.SimpleNamespace(Day="Day")

        attempts = {"count": 0}

        def fake_submit_order(*_args, **_kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                exc = RuntimeError("internal server error")
                exc.code = 603203
                raise exc
            return ExecutionReport(
                symbol="BOXX",
                side="sell",
                quantity=1.0,
                status="submitted",
                broker_order_id="OID-1",
                raw_payload={},
            )

        ctx = types.SimpleNamespace(submit_order=fake_submit_order)
        with patch.dict(
            sys.modules,
            {"longport": longport_module, "longport.openapi": openapi_module},
        ):
            with patch("application.longbridge_execution.time.sleep", lambda _seconds: None):
                with patch(
                    "application.longbridge_execution._qpk_submit_order",
                    fake_submit_order,
                ):
                    report = submit_order(
                        ctx,
                        "BOXX.US",
                        order_kind="market",
                        side="sell",
                        quantity=4.6177,
                    )

        self.assertEqual(report.status, "submitted")
        self.assertEqual(report.broker_order_id, "OID-1")
        self.assertEqual(attempts["count"], 2)


if __name__ == "__main__":
    unittest.main()
