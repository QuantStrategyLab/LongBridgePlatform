from __future__ import annotations

from types import SimpleNamespace
import unittest

from application.execution_receipt_adapter import attach_cycle_execution_receipt


REVISION = "a" * 40


def _report() -> dict[str, object]:
    return {
        "platform": "longbridge",
        "strategy_profile": "sg_us_equity_rotation",
        "dry_run": False,
        "runtime_target": {"execution_mode": "paper"},
        "runtime_release_receipt": {
            "attestation_state": "self_attested",
            "strategy_release": {"strategy_revision": REVISION},
        },
    }


class ExecutionReceiptAdapterTest(unittest.TestCase):
    def test_submission_does_not_claim_broker_acknowledgement(self) -> None:
        report = _report()

        attach_cycle_execution_receipt(report, SimpleNamespace(action_done=True, pending_orders=()))

        self.assertEqual(report["execution_receipt"]["outcome"], "submitted")
        self.assertEqual(report["execution_receipt"]["broker_confirmation"], "not_observed")

    def test_pending_orders_require_reconciliation(self) -> None:
        report = _report()

        attach_cycle_execution_receipt(
            report,
            SimpleNamespace(action_done=True, pending_orders=({"symbol": "QQQ"},)),
        )

        self.assertEqual(report["execution_receipt"]["outcome"], "reconciliation_required")

    def test_dry_run_never_claims_submission(self) -> None:
        report = _report()
        report["dry_run"] = True

        attach_cycle_execution_receipt(report, SimpleNamespace(action_done=True, pending_orders=()))

        self.assertEqual(report["execution_receipt"]["outcome"], "no_action")
