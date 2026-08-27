from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.durable_execution_commands import (  # noqa: E402
    build_paper_execution_decision_digest,
    build_paper_execution_command,
)
from application.paper_execution_command_consumer import (  # noqa: E402
    consume_due_paper_execution_commands,
)
from quant_platform_kit.common.execution_commands import (  # noqa: E402
    ExecutionCommandState,
    ExecutionCommandStore,
)
from quant_platform_kit.common.models import (  # noqa: E402
    PortfolioSnapshot,
    Position,
    QuoteSnapshot,
)
from quant_platform_kit.common.paper_execution_admission import build_paper_risk_admission_receipt  # noqa: E402
from quant_platform_kit.common.strategy_release import build_runtime_loaded_receipt  # noqa: E402


def _release_identity() -> dict[str, str]:
    return {
        "release_id": "soxl-p2-v3.20260824",
        "manifest_sha256": "a" * 64,
        "strategy_revision": "soxl-p2-v3",
        "config_sha256": "b" * 64,
        "risk_policy_sha256": "c" * 64,
        "evidence_sha256": "d" * 64,
        "plugin_bundle_sha256": "e" * 64,
        "effective_session": "2026-08-25",
    }


def _allocation() -> dict[str, object]:
    return {
        "target_mode": "value",
        "targets": {"SOXL": 100.0, "BOXX": 100.0},
        "strategy_symbols": ("SOXL", "BOXX"),
        "risk_symbols": ("SOXL",),
        "safe_haven_symbols": ("BOXX",),
    }


def _command(*, bind_release: bool = True, receipt_decision_digest: str | None = None):
    allocation = _allocation()
    release = _release_identity()
    command_release = release if bind_release else None
    decision_digest = receipt_decision_digest or build_paper_execution_decision_digest(
        allocation=allocation,
        strategy_release=command_release,
    )
    return build_paper_execution_command(
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution={
            "signal_date": "2026-08-24",
            "effective_date": "2026-08-25",
            "execution_timing_contract": "next_trading_day",
        },
        allocation=allocation,
        strategy_release=command_release,
        paper_risk_admission_receipt=build_paper_risk_admission_receipt(
            strategy_profile="soxl_soxx_trend_income",
            release_id=release["release_id"],
            risk_policy_sha256=release["risk_policy_sha256"],
            decision_digest=decision_digest,
            effective_session="2026-08-25",
            disposition="allow_new_risk",
            reason_codes=(),
        ).to_dict(),
    )


class _MarketDataPort:
    def get_quote(self, symbol: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=f"{symbol}.US",
            as_of=datetime(2026, 8, 25, tzinfo=timezone.utc),
            last_price=10.0,
        )


def _portfolio(*, include_unmanaged: bool = False) -> PortfolioSnapshot:
    positions = [Position(symbol="SOXL", quantity=20.0, market_value=200.0)]
    if include_unmanaged:
        positions.append(Position(symbol="AAPL", quantity=1.0, market_value=100.0))
    return PortfolioSnapshot(
        as_of=datetime(2026, 8, 25, tzinfo=timezone.utc),
        total_equity=1000.0 + (100.0 if include_unmanaged else 0.0),
        cash_balance=800.0,
        positions=tuple(positions),
    )


def test_paper_consumer_simulates_reconciled_orders_and_never_calls_an_execution_port(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command()
    assert store.enqueue(command)
    release = _release_identity()

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="paper-command-verify",
        portfolio=_portfolio(),
        market_data_port=_MarketDataPort(),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=release),
        expected_strategy_release=release,
    )

    assert result["status"] == "ok"
    assert result["commands"] == [
        {
            "command_id": command.command_id,
            "status": "filled",
            "proposals_count": 2,
            "would_block": False,
        }
    ]
    assert store.current_state(command) is ExecutionCommandState.FILLED
    events = store.events(command)
    assert [event.state for event in events] == [
        ExecutionCommandState.CLAIMED,
        ExecutionCommandState.SUBMITTED,
        ExecutionCommandState.ACCEPTED,
        ExecutionCommandState.FILLED,
    ]
    proposals = events[1].details["proposals"]
    assert [proposal["exposure_effect"] for proposal in proposals] == ["increases", "reduces"]
    assert proposals[0]["details"] == {
        "side": "buy",
        "quantity": 10.0,
        "reference_price": 10.0,
        "current_value": 0.0,
        "target_value": 100.0,
        "target_notional_delta": 100.0,
        "current_quantity": 0.0,
    }
    receipts = events[1].details["runtime_command_gate_receipts"]
    assert {receipt["enforcement"] for receipt in receipts} == {"enforce"}
    assert all(receipt["broker_write_allowed"] is True for receipt in receipts)
    admission = events[1].details["paper_execution_admission"]
    assert admission["disposition"] == "allow_new_risk"
    assert isinstance(admission["receipt_sha256"], str)


def test_paper_consumer_requires_runtime_release_before_claiming(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command()
    assert store.enqueue(command)

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="paper-command-verify",
        portfolio=_portfolio(),
        market_data_port=_MarketDataPort(),
        runtime_release_receipt=None,
        expected_strategy_release=_release_identity(),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "release_receipt_missing"
    assert store.current_state(command) is ExecutionCommandState.QUEUED


def test_paper_consumer_rejects_unbound_or_unreconciled_commands(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command(bind_release=False)
    assert store.enqueue(command)
    release = _release_identity()

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="paper-command-verify",
        portfolio=_portfolio(include_unmanaged=True),
        market_data_port=_MarketDataPort(),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=release),
        expected_strategy_release=release,
    )

    assert result["commands"][0]["status"] == "rejected"
    assert result["commands"][0]["would_block"] is True
    assert store.current_state(command) is ExecutionCommandState.REJECTED
    receipt = store.events(command)[-1].details["runtime_command_gate_receipts"][0]
    assert receipt["enforcement"] == "enforce"
    assert receipt["broker_write_allowed"] is False
    assert receipt["mode"] == "halted"
    assert "release_identity_mismatch" in receipt["reasons"]
    assert "position_reconciliation_mismatch" in receipt["reasons"]


def test_paper_consumer_rejects_risk_receipt_bound_to_another_decision(tmp_path: Path) -> None:
    store = ExecutionCommandStore(local_dir=tmp_path)
    command = _command(receipt_decision_digest="f" * 64)
    assert store.enqueue(command)
    release = _release_identity()

    result = consume_due_paper_execution_commands(
        store=store,
        as_of_session="2026-08-25",
        claimant="paper-command-verify",
        portfolio=_portfolio(),
        market_data_port=_MarketDataPort(),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=release),
        expected_strategy_release=release,
    )

    assert result["commands"][0]["status"] == "rejected"
    assert store.current_state(command) is ExecutionCommandState.REJECTED
    receipt = store.events(command)[-1].details["runtime_command_gate_receipts"][0]
    assert receipt["broker_write_allowed"] is False
    assert "paper_risk_admission_command_mismatch" in receipt["reasons"]
