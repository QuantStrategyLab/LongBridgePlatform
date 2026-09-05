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
    def test_broker_capital_uses_one_balance_read_without_changing_strategy_equity(self):
        from datetime import datetime, timezone

        observed = datetime(2026, 9, 5, tzinfo=timezone.utc)
        balance = types.SimpleNamespace(
            currency="USD", net_assets="2500.50", buy_power="99999",
            max_finance_amount="99999", remaining_finance_amount="99999",
            cash_infos=[types.SimpleNamespace(currency="USD", available_cash=100.0)],
        )
        trade = types.SimpleNamespace(
            account_balance=unittest.mock.Mock(return_value=[balance]),
            stock_positions=lambda: FakePositionsResponse(),
        )
        with patch("application.longbridge_portfolio.datetime", create=True) as clock:
            clock.now.return_value = observed
            state = fetch_strategy_account_state(FakeQuoteContext(), trade, ["SOXL"])
        trade.account_balance.assert_called_once_with()
        self.assertEqual(state["total_strategy_equity"], 250.0)
        self.assertEqual(state["broker_capital"]["net_assets"], 2500.50)
        self.assertEqual(state["broker_capital"]["currency"], "USD")
        self.assertEqual(state["broker_capital"]["observed_at"], observed)
        self.assertEqual(len(state["broker_capital"]["source_digest_sha256"]), 64)

    def test_broker_capital_withholds_missing_ambiguous_or_invalid_denominator(self):
        for balances in (
            [],
            [types.SimpleNamespace(currency="HKD", net_assets=1000)],
            [types.SimpleNamespace(net_assets=1000)],
            [types.SimpleNamespace(currency="USD")],
            [types.SimpleNamespace(currency="USD", net_assets=1000)] * 2,
            *([types.SimpleNamespace(currency="USD", net_assets=value)]
              for value in (None, True, "bad", "NaN", "Infinity", "-Infinity", 0, -1)),
        ):
            with self.subTest(balances=balances):
                trade = types.SimpleNamespace(
                    account_balance=lambda: balances,
                    stock_positions=lambda: types.SimpleNamespace(channels=[]),
                )
                state = fetch_strategy_account_state(FakeQuoteContext(), trade, [])
                self.assertIsNone(state["broker_capital"])

    def test_fetch_strategy_account_state_rejects_account_balance_failure(self):
        class BalanceFailingTradeContext:
            def account_balance(self):
                raise RuntimeError("boom")

            def stock_positions(self):
                return FakePositionsResponse()

        warnings = []
        with self.assertRaisesRegex(RuntimeError, "account balance unavailable"):
            fetch_strategy_account_state(
                FakeQuoteContext(),
                BalanceFailingTradeContext(),
                ["SOXL", "QQQI", "SPYI"],
                warning_log_fn=warnings.append,
            )

        self.assertEqual(
            warnings,
            [
                "[longbridge_account_balance_failed] error_type=RuntimeError",
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

    def test_fetch_strategy_account_state_rejects_missing_quote_for_known_position(self):
        class MissingQuoteContext:
            def quote(self, _symbols):
                return []

        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return FakePositionsResponse()

        with self.assertRaisesRegex(RuntimeError, "position valuation incomplete"):
            fetch_strategy_account_state(MissingQuoteContext(), TradeContext(), ["SOXL", "QQQI"])

    def test_fetch_strategy_account_state_rejects_invalid_quote_for_known_position(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return types.SimpleNamespace(channels=[FakeChannel([FakePosition("SOXL.US", 3)])])

        for last_done in (0, float("nan"), float("inf")):
            with self.subTest(last_done=last_done):
                quote_context = types.SimpleNamespace(
                    quote=lambda _symbols: [
                        types.SimpleNamespace(symbol="SOXL.US", last_done=last_done)
                    ]
                )
                with self.assertRaisesRegex(RuntimeError, "position valuation incomplete"):
                    fetch_strategy_account_state(quote_context, TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_rejects_missing_position_symbol(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return types.SimpleNamespace(channels=[FakeChannel([FakePosition("", 3)])])

        with self.assertRaisesRegex(RuntimeError, "position symbol missing"):
            fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_rejects_position_read_failure(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                raise RuntimeError("unavailable")

        with self.assertRaisesRegex(RuntimeError, "stock positions unavailable"):
            fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_rejects_missing_position_quantity(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return types.SimpleNamespace(
                    channels=[types.SimpleNamespace(positions=[types.SimpleNamespace(
                        symbol="SOXL.US",
                        available_quantity=0,
                    )])]
                )

        with self.assertRaisesRegex(RuntimeError, "position quantity missing"):
            fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_rejects_none_position_quantity(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return types.SimpleNamespace(
                    channels=[FakeChannel([FakePosition("SOXL.US", None, 0)])]
                )

        with self.assertRaisesRegex(RuntimeError, "position quantity missing"):
            fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_rejects_nonfinite_cash(self):
        class TradeContext:
            def account_balance(self):
                return [types.SimpleNamespace(cash_infos=[
                    types.SimpleNamespace(currency="USD", available_cash=float("nan"))
                ])]

            def stock_positions(self):
                return types.SimpleNamespace(channels=[])

        with self.assertRaisesRegex(RuntimeError, "account balance invalid"):
            fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

    def test_fetch_strategy_account_state_allows_zero_position_without_quote(self):
        class TradeContext:
            def account_balance(self):
                return []

            def stock_positions(self):
                return types.SimpleNamespace(channels=[FakeChannel([FakePosition("SOXL.US", 0)])])

        state = fetch_strategy_account_state(
            types.SimpleNamespace(quote=lambda _symbols: []),
            TradeContext(),
            ["SOXL"],
        )

        self.assertEqual(state["quantities"], {"SOXL": 0.0})
        self.assertEqual(state["market_values"], {"SOXL": 0.0})
        self.assertEqual(state["total_strategy_equity"], 0.0)

    def test_fetch_strategy_account_state_allows_zero_cash_and_zero_positions(self):
        class TradeContext:
            def account_balance(self):
                return [types.SimpleNamespace(cash_infos=[
                    types.SimpleNamespace(currency="USD", available_cash=0.0)
                ])]

            def stock_positions(self):
                return types.SimpleNamespace(channels=[])

        state = fetch_strategy_account_state(FakeQuoteContext(), TradeContext(), ["SOXL"])

        self.assertEqual(state["available_cash"], 0.0)
        self.assertEqual(state["cash_by_currency"], {"USD": 0.0})
        self.assertEqual(state["quantities"], {"SOXL": 0.0})

    def test_submit_order_does_not_retry_unknown_internal_error(self):
        attempts = {"count": 0}

        def fake_submit_order(*_args, **_kwargs):
            attempts["count"] += 1
            error = RuntimeError("internal server error")
            error.code = 603203
            raise error

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit_order):
            with self.assertRaisesRegex(RuntimeError, "internal server error"):
                submit_order(
                    object(),
                    "BOXX.US",
                    order_kind="market",
                    side="sell",
                    quantity=4.6177,
                )

        self.assertEqual(attempts["count"], 1)

    def test_submit_order_does_not_retry_timeout(self):
        attempts = {"count": 0}

        def fake_submit_order(*_args, **_kwargs):
            attempts["count"] += 1
            raise TimeoutError("submission timed out")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit_order):
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                submit_order(
                    object(),
                    "BOXX.US",
                    order_kind="market",
                    side="sell",
                    quantity=4.6177,
                )

        self.assertEqual(attempts["count"], 1)

    def test_submit_order_does_not_retry_internal_error_without_code(self):
        attempts = {"count": 0}

        def fake_submit_order(*_args, **_kwargs):
            attempts["count"] += 1
            raise RuntimeError("internal server error")

        with patch("application.longbridge_execution._qpk_submit_order", fake_submit_order):
            with self.assertRaisesRegex(RuntimeError, "internal server error"):
                submit_order(
                    object(),
                    "BOXX.US",
                    order_kind="market",
                    side="sell",
                    quantity=4.6177,
                )

        self.assertEqual(attempts["count"], 1)

    def test_submit_order_preserves_rejected_report(self):
        rejected = ExecutionReport(
            symbol="BOXX",
            side="sell",
            quantity=1.0,
            status="rejected",
            raw_payload={"detail": "rejected by broker"},
        )
        with patch("application.longbridge_execution._qpk_submit_order", return_value=rejected) as submit:
            report = submit_order(
                object(),
                "BOXX.US",
                order_kind="market",
                side="sell",
                quantity=1.0,
            )

        self.assertIs(report, rejected)
        submit.assert_called_once()

    def test_submit_order_preserves_successful_report(self):
        submitted = ExecutionReport(
            symbol="BOXX",
            side="sell",
            quantity=1.0,
            status="submitted",
            broker_order_id="OID-1",
            raw_payload={},
        )
        with patch("application.longbridge_execution._qpk_submit_order", return_value=submitted) as submit:
            report = submit_order(
                object(),
                "BOXX.US",
                order_kind="market",
                side="sell",
                quantity=1.0,
            )

        self.assertIs(report, submitted)
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
