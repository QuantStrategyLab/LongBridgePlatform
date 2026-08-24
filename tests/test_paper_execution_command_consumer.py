from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.durable_execution_commands import build_paper_execution_command  # noqa: E402
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


def _command(*, bind_release: bool = True):
    return build_paper_execution_command(
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution={
            "signal_date": "2026-08-24",
            "effective_date": "2026-08-25",
            "execution_timing_contract": "next_trading_day",
        },
        allocation={
            "target_mode": "value",
            "targets": {"SOXL": 100.0, "BOXX": 100.0},
            "strategy_symbols": ("SOXL", "BOXX"),
            "risk_symbols": ("SOXL",),
            "safe_haven_symbols": ("BOXX",),
        },
        strategy_release=_release_identity() if bind_release else None,
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
    assert receipt["mode"] == "halted"
    assert "release_identity_mismatch" in receipt["reasons"]
    assert "position_reconciliation_mismatch" in receipt["reasons"]
