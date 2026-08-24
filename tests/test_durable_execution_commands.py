from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.durable_execution_commands import (  # noqa: E402
    build_paper_execution_command,
    enqueue_paper_execution_command,
    resolve_paper_execution_command_consumer_enabled,
    resolve_paper_execution_command_producer_enabled,
)
from quant_platform_kit.common.strategy_release import build_runtime_loaded_receipt


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


def test_paper_producer_persists_observation_gate_receipt_without_authorizing_a_consumer() -> None:
    class Store:
        cloud_prefix_uri = "gs://paper/commands"
        local_dir = None

        def enqueue(self, _command):
            return True

    release = _release_identity()
    result = enqueue_paper_execution_command(
        enabled=True,
        dry_run_only=True,
        store=Store(),
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution=_execution(),
        allocation=_allocation(),
        runtime_release_receipt=build_runtime_loaded_receipt(strategy_release=release),
        expected_strategy_release=release,
    )

    assert result is not None
    gate = result["runtime_command_gate"]
    assert isinstance(gate, dict)
    assert gate["enforcement"] == "observe"
    assert gate["mode"] == "active"
    assert gate["policy_allows"] is False
    assert gate["broker_write_allowed"] is True
    assert "exposure_effect_unknown" in gate["reasons"]
    assert result["consumer_authorized"] is False


def test_paper_command_binds_complete_strategy_release_when_available() -> None:
    command = build_paper_execution_command(
        platform="longbridge",
        account_scope="PAPER",
        strategy_profile="soxl_soxx_trend_income",
        execution=_execution(),
        allocation=_allocation(),
        strategy_release=_release_identity(),
    )

    assert command.intent["strategy_release"] == _release_identity()


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


def test_paper_consumer_rejects_live_enablement() -> None:
    assert resolve_paper_execution_command_consumer_enabled(
        env_reader=lambda _name, _default="": "true",
        dry_run_only=True,
    )
    try:
        resolve_paper_execution_command_consumer_enabled(
            env_reader=lambda _name, _default="": "true",
            dry_run_only=False,
        )
    except RuntimeError as exc:
        assert "paper-only" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("live enablement must fail closed")
