from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.execution_state import ExecutionMarkerStore, claim_account_owner
from application.rebalance_service import _resolve_physical_account_id
from application.runtime_dependencies import LongBridgeRebalanceConfig
from notifications.telegram import build_translator


class AccountOwnerFenceTests(unittest.TestCase):
    def _config(self, **overrides):
        base = dict(
            limit_sell_discount=0.995,
            limit_buy_premium=1.0,
            separator="-",
            translator=build_translator("en"),
            with_prefix=lambda message: message,
            strategy_profile="soxl_soxx_trend_income",
            execution_state_account_scope="PAPER",
            physical_account_id="lb-paper-001",
        )
        base.update(overrides)
        return LongBridgeRebalanceConfig(**base)

    def test_rejects_missing_and_label_ids(self):
        with self.assertRaisesRegex(RuntimeError, "LONGBRIDGE_PHYSICAL_ACCOUNT_ID|physical account id|requires"):
            _resolve_physical_account_id(config=self._config(physical_account_id=""))
        with self.assertRaisesRegex(RuntimeError, "refusing label|physical account"):
            _resolve_physical_account_id(config=self._config(physical_account_id="HK"))
        with self.assertRaisesRegex(RuntimeError, "refusing label|physical account"):
            _resolve_physical_account_id(config=self._config(physical_account_id="PAPER"))
        self.assertEqual(
            _resolve_physical_account_id(config=self._config()),
            "lb-paper-001",
        )

    def test_contested_owner_primitive_blocks_second_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = ExecutionMarkerStore(local_dir=tmpdir, cloud_prefix_uri=None)
            first = claim_account_owner(
                store,
                broker="longbridge",
                account_id="lb-paper-001",
                owner_id="soxl_soxx_trend_income",
            )
            second = claim_account_owner(
                store,
                broker="longbridge",
                account_id="lb-paper-001",
                owner_id="other_profile",
            )
            self.assertTrue(first.allowed)
            self.assertFalse(second.allowed)
            self.assertTrue(second.contested)


if __name__ == "__main__":
    unittest.main()
