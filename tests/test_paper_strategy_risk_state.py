from __future__ import annotations

import pytest

from application.paper_strategy_risk_state import (
    PAPER_STRATEGY_RISK_STATE_OBSERVATION_SCHEMA_VERSION,
    record_paper_strategy_risk_state_transition,
    resolve_paper_strategy_risk_state_enabled,
)
from quant_platform_kit.common.strategy_risk_state import (
    StrategyRiskStateIdentity,
    StrategyRiskStateStore,
    build_strategy_risk_state_transition,
)


def _transition():
    return build_strategy_risk_state_transition(
        identity=StrategyRiskStateIdentity(
            strategy_profile="soxl_soxx_trend_income",
            account_scope="sg",
            candidate_id="soxl_soxx_core_only_p2_v3",
            config_sha256="a" * 64,
        ),
        effective_session="2026-08-24",
        input_sha256="b" * 64,
        state={"cooldown_remaining_sessions": 2, "reentry_allowed": False},
    )


def test_disabled_adapter_is_a_noop_without_storage() -> None:
    assert (
        record_paper_strategy_risk_state_transition(
            enabled=False,
            dry_run_only=True,
            store=None,
            transition_payload=None,
            expected_strategy_profile="soxl_soxx_trend_income",
            expected_account_scope="sg",
        )
        is None
    )


def test_enabled_paper_adapter_records_and_deduplicates_transition(tmp_path) -> None:
    transition = _transition()
    store = StrategyRiskStateStore(local_dir=tmp_path)
    kwargs = {
        "enabled": True,
        "dry_run_only": True,
        "store": store,
        "transition_payload": transition.to_dict(),
        "expected_strategy_profile": "soxl_soxx_trend_income",
        "expected_account_scope": "sg",
    }

    created = record_paper_strategy_risk_state_transition(**kwargs)
    duplicate = record_paper_strategy_risk_state_transition(**kwargs)

    assert created == {
        "schema_version": PAPER_STRATEGY_RISK_STATE_OBSERVATION_SCHEMA_VERSION,
        "status": "created",
        "transition_sha256": transition.transition_sha256,
        "effective_session": "2026-08-24",
        "chain_length": 1,
        "consumer_authorized": False,
    }
    assert duplicate["status"] == "already_appended"
    assert store.load_chain(transition.identity) == (transition,)


def test_enabled_adapter_fails_closed_for_live_missing_or_mismatched_input(tmp_path) -> None:
    transition = _transition()
    store = StrategyRiskStateStore(local_dir=tmp_path)
    base = {
        "enabled": True,
        "store": store,
        "transition_payload": transition.to_dict(),
        "expected_strategy_profile": "soxl_soxx_trend_income",
        "expected_account_scope": "sg",
    }

    with pytest.raises(RuntimeError, match="paper-only"):
        record_paper_strategy_risk_state_transition(dry_run_only=False, **base)
    with pytest.raises(RuntimeError, match="account scope"):
        record_paper_strategy_risk_state_transition(
            dry_run_only=True,
            expected_account_scope="hk",
            **{key: value for key, value in base.items() if key != "expected_account_scope"},
        )
    with pytest.raises(RuntimeError, match="required"):
        record_paper_strategy_risk_state_transition(
            enabled=True,
            dry_run_only=True,
            store=None,
            transition_payload=None,
            expected_strategy_profile="soxl_soxx_trend_income",
            expected_account_scope="sg",
        )


def test_opt_in_rejects_a_live_runtime() -> None:
    with pytest.raises(RuntimeError, match="paper-only"):
        resolve_paper_strategy_risk_state_enabled(
            env_reader=lambda _key, _default="": "true",
            dry_run_only=False,
        )
