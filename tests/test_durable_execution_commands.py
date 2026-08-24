from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.durable_execution_commands import (  # noqa: E402
    build_paper_execution_command,
    enqueue_paper_execution_command,
    resolve_paper_execution_command_producer_enabled,
)


def _execution() -> dict[str, object]:
    return {
        "signal_date": "2026-07-17",
        "effective_date": "2026-07-20",
        "execution_timing_contract": "next_trading_day",
    }


def _allocation() -> dict[str, object]:
    return {
        "target_mode": "value",
        "targets": {"SOXL": 350.0, "BOXX": 150.0},
        "strategy_symbols": ("SOXL", "BOXX"),
        "risk_symbols": ("SOXL",),
        "safe_haven_symbols": ("BOXX",),
    }


def test_paper_command_is_content_addressed_and_excludes_broker_authority() -> None:
    first = build_paper_execution_command(
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution=_execution(),
        allocation=_allocation(),
    )
    second = build_paper_execution_command(
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution=_execution(),
        allocation=_allocation(),
    )

    assert first.command_id == second.command_id
    assert first.execution_mode == "paper"
    assert first.effective_date == "2026-07-20"
    assert first.intent == {
        "schema_version": "longbridge.paper-execution-intent.v1",
        "target_mode": "value",
        "targets": {"BOXX": 150.0, "SOXL": 350.0},
        "strategy_symbols": ["BOXX", "SOXL"],
        "risk_symbols": ["SOXL"],
        "safe_haven_symbols": ["BOXX"],
    }


def test_paper_producer_enqueues_once_and_never_authorizes_consumer() -> None:
    observed = []

    class Store:
        cloud_prefix_uri = "gs://paper/commands"
        local_dir = None

        def enqueue(self, command):
            observed.append(command)
            return len(observed) == 1

    kwargs = {
        "enabled": True,
        "dry_run_only": True,
        "store": Store(),
        "platform": "longbridge",
        "account_scope": "PAPER",
        "strategy_profile": "soxl_soxx_trend_income",
        "execution": _execution(),
        "allocation": _allocation(),
    }
    first = enqueue_paper_execution_command(**kwargs)
    second = enqueue_paper_execution_command(**kwargs)

    assert first and first["status"] == "QUEUED"
    assert second and second["status"] == "ALREADY_QUEUED"
    assert first["consumer_authorized"] is False
    assert len(observed) == 2


def test_paper_producer_rejects_live_enablement() -> None:
    assert resolve_paper_execution_command_producer_enabled(
        env_reader=lambda _name, _default="": "true",
        dry_run_only=True,
    )
    try:
        resolve_paper_execution_command_producer_enabled(
            env_reader=lambda _name, _default="": "true",
            dry_run_only=False,
        )
    except RuntimeError as exc:
        assert "paper-only" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("live enablement must fail closed")
