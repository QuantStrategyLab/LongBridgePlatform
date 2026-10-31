"""Synthetic contracts for the pinned QPK/UES broker-to-risk path."""

from datetime import datetime, timezone
import unittest
from types import SimpleNamespace

from application.longbridge_portfolio import fetch_strategy_account_state

from application.runtime_broker_adapters import LongBridgeBrokerAdapters
from quant_platform_kit.common.models import PortfolioSnapshot, Position
from quant_platform_kit.risk.portfolio_diagnostics import extract_portfolio_risk_diagnostics


class CandidateDependencyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LongBridgeBrokerAdapters(
            strategy_symbols=('AAA', 'BBB'), account_hash='synthetic',
            fetch_last_price_fn=lambda *_: None,
            fetch_strategy_account_state_fn=lambda *_: None,
            submit_order_fn=lambda *_args, **_kwargs: None,
            cash_only_execution=True,
        )

    def round_trip(self, positions):
        snapshot = PortfolioSnapshot(
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc), total_equity=100,
            cash_balance=100, positions=tuple(positions),
        )
        state = self.adapter.build_account_state_from_snapshot(snapshot)
        return self.adapter.build_portfolio_snapshot_from_account_state(state)

    def test_long_and_short_keep_cost_and_signed_pnl(self):
        for quantity, value, expected in ((2, 24, 4 / 124), (-2, -24, -4 / 76)):
            with self.subTest(quantity=quantity):
                rebuilt = self.round_trip((Position('AAA', quantity, value, 10),))
                self.assertEqual(len(rebuilt.positions), 1)
                self.assertEqual(rebuilt.positions[0].quantity, quantity)
                self.assertEqual(rebuilt.positions[0].average_cost, 10)
                diagnostics = extract_portfolio_risk_diagnostics(rebuilt)
                self.assertEqual(diagnostics['unrealized_pnl_coverage'], 1.0)
                self.assertAlmostEqual(diagnostics['unrealized_pnl_pct'], expected)

    def test_partial_or_invalid_cost_cannot_be_full_portfolio_pnl(self):
        for cost in (None, float('nan'), float('inf')):
            with self.subTest(cost=cost):
                rebuilt = self.round_trip((Position('AAA', 2, 24, 10), Position('BBB', 1, 15, cost)))
                diagnostics = extract_portfolio_risk_diagnostics(rebuilt)
                self.assertEqual(diagnostics['unrealized_pnl_coverage'], 0.5)
                self.assertNotIn('unrealized_pnl_pct', diagnostics)

    def test_synthetic_snapshot_duplicate_metadata_cannot_be_rebuilt(self):
        with self.assertRaisesRegex(ValueError, 'duplicate symbol metadata'):
            self.round_trip((Position('AAA', 1, 12, 10), Position('AAA', 2, 24, 11)))

    def test_broker_account_state_without_cost_has_unknown_pnl(self):
        state = {
            'available_cash': 100, 'total_strategy_equity': 124,
            'market_values': {'AAA': 24}, 'quantities': {'AAA': 2},
            'sellable_quantities': {'AAA': 2},
        }
        rebuilt = self.adapter.build_portfolio_snapshot_from_account_state(state)
        diagnostics = extract_portfolio_risk_diagnostics(rebuilt)
        self.assertIsNone(rebuilt.positions[0].average_cost)
        self.assertEqual(diagnostics['unrealized_pnl_coverage'], 0.0)
        self.assertNotIn('unrealized_pnl_pct', diagnostics)


class BrokerInputCompatibilityTests(unittest.TestCase):
    """Exercise synthetic SDK-shaped rows through the actual broker entry."""

    def build_snapshot(self, channels):
        cash = SimpleNamespace(currency='USD', available_cash=100)
        balance = SimpleNamespace(currency='USD', net_assets=100, cash_infos=[cash])
        trade = SimpleNamespace(
            account_balance=lambda: [balance],
            stock_positions=lambda: SimpleNamespace(channels=[
                SimpleNamespace(positions=positions) for positions in channels
            ]),
        )
        quote = SimpleNamespace(quote=lambda symbols: [
            SimpleNamespace(symbol=symbol, last_done=12) for symbol in symbols
        ])
        captured_states = []

        def fetch(quote_context, trade_context):
            state = fetch_strategy_account_state(quote_context, trade_context, ('AAA',))
            captured_states.append(state)
            return state

        adapter = LongBridgeBrokerAdapters(
            strategy_symbols=('AAA',), account_hash='synthetic',
            fetch_last_price_fn=lambda *_: None, fetch_strategy_account_state_fn=fetch,
            submit_order_fn=lambda *_args, **_kwargs: None,
        )
        snapshot = adapter.build_managed_portfolio_snapshot(quote, trade)
        return captured_states[0], snapshot

    @staticmethod
    def position(quantity, **kwargs):
        return SimpleNamespace(symbol='AAA.US', quantity=quantity,
                               available_quantity=quantity, currency='USD', **kwargs)

    def test_negative_broker_position_remains_signed_and_pnl_unknown(self):
        state, snapshot = self.build_snapshot([[self.position(-2)]])
        self.assertEqual(state['quantities']['AAA'], -2)
        self.assertEqual(state['market_values']['AAA'], -24)
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].quantity, -2)
        self.assertEqual(snapshot.positions[0].market_value, -24)
        self.assertNotIn('unrealized_pnl_pct', extract_portfolio_risk_diagnostics(snapshot))

    def test_same_symbol_in_separate_channels_is_aggregated(self):
        state, snapshot = self.build_snapshot([[self.position(1)], [self.position(2)]])
        self.assertEqual(state['quantities']['AAA'], 3)
        self.assertEqual(state['market_values']['AAA'], 36)
        self.assertNotIn('ambiguous_position_symbols', state)
        self.assertEqual(len(snapshot.positions), 1)
        self.assertEqual(snapshot.positions[0].quantity, 3)
        self.assertEqual(snapshot.positions[0].market_value, 36)

    def test_broker_cost_is_not_carried_into_managed_snapshot(self):
        state, snapshot = self.build_snapshot([[self.position(2, cost_price=10, account_id='synthetic')]])
        self.assertNotIn('position_details', state)
        self.assertIsNone(snapshot.positions[0].average_cost)
        self.assertIsNone(snapshot.positions[0].account_id)
        diagnostics = extract_portfolio_risk_diagnostics(snapshot)
        self.assertEqual(diagnostics['unrealized_pnl_coverage'], 0.0)
        self.assertNotIn('unrealized_pnl_pct', diagnostics)


if __name__ == '__main__':
    unittest.main()
