from __future__ import annotations

import pytest

from application.v7_paper_preview import build_v7_paper_preview_mapping
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.common.strategy_contracts import StrategyContext
from strategy_loader import load_strategy_entrypoint_for_profile
from us_equity_strategies.v7_soxl_profile import (
    SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
    V7_CONFIG_SHA256,
)


def _identity(*, account_mode: str = "paper", account_scope: str = "lb-paper-review") -> dict[str, str]:
    return {
        "platform_id": "longbridge",
        "account_mode": account_mode,
        "account_scope": account_scope,
    }


def _available_inputs(*, high_volatility: bool = False) -> dict[str, object]:
    volatility = 0.60 if high_volatility else 0.20
    snapshot = PortfolioSnapshot(
        as_of="2026-08-26",
        total_equity=100_000.0,
        buying_power=100_000.0,
        positions=(),
        metadata={"market_currency_cash": 100_000.0},
    )
    return {
        "derived_indicators": {
            "soxl": {"price": 80.0, "ma_trend": 70.0},
            "soxx": {
                "price": 80.0,
                "ma_trend": 70.0,
                "realized_volatility_10": volatility,
                "realized_volatility_10_dynamic_threshold": 0.50,
                "realized_volatility_10_dynamic_sample_count": 252.0,
            },
        },
        "portfolio_snapshot": snapshot,
    }


def _context() -> StrategyContext:
    inputs = _available_inputs()
    return StrategyContext(
        as_of="2026-08-26",
        market_data={"derived_indicators": inputs["derived_indicators"]},
        portfolio=inputs["portfolio_snapshot"],
    )


def _positions(preview: dict[str, object]) -> dict[str, float]:
    return {
        row["symbol"]: row["target_value"]
        for row in preview["decision"]["positions"]
    }


def test_v7_paper_preview_runs_named_entrypoint_for_full_decision() -> None:
    preview = build_v7_paper_preview_mapping(
        broker_identity=_identity(),
        available_inputs=_available_inputs(),
            as_of="2026-08-26",
            preview_request={
                "platform_id": "longbridge",
                "account_scope": "lb-paper-review",
                "strategy_profile": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "candidate_id": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "config_sha256": V7_CONFIG_SHA256,
            },
    )

    assert _positions(preview) == {
        "BOXX": 38_800.0,
        "SOXL": 33_950.0,
        "SOXX": 24_250.0,
    }
    assert preview["decision"]["diagnostics"]["frozen_research_source"]["ues_revision"].startswith("07b164")
    assert preview["frozen_research_source"]["ues_revision"].startswith("07b164")
    assert preview["signal_effective_after_trading_days"] == 1
    assert preview["preview_only"] is True
    assert preview["no_order"] is True
    assert preview["runtime_enabled"] is False


def test_v7_paper_preview_runs_same_entrypoint_for_high_volatility() -> None:
    preview = build_v7_paper_preview_mapping(
        broker_identity=_identity(),
        context=_context(),
    )
    high_vol_preview = build_v7_paper_preview_mapping(
        broker_identity=_identity(),
        available_inputs=_available_inputs(high_volatility=True),
        as_of="2026-08-26",
    )

    assert _positions(preview) == {
        "BOXX": 38_800.0,
        "SOXL": 33_950.0,
        "SOXX": 24_250.0,
    }
    assert _positions(high_vol_preview) == {
        "BOXX": 72_750.0,
        "SOXL": 0.0,
        "SOXX": 24_250.0,
    }


def test_v7_paper_preview_does_not_treat_dry_run_as_broker_paper_identity() -> None:
    with pytest.raises(ValueError, match="paper broker identity"):
        build_v7_paper_preview_mapping(
            broker_identity={"platform_id": "longbridge", "dry_run_only": True},
            available_inputs=_available_inputs(),
            as_of="2026-08-26",
        )


def test_v7_paper_preview_rejects_cross_account_or_candidate_request() -> None:
    with pytest.raises(ValueError, match="platform identity"):
        build_v7_paper_preview_mapping(
            broker_identity=_identity(),
            available_inputs=_available_inputs(),
            as_of="2026-08-26",
            preview_request={
                "platform_id": "other-platform",
                "account_scope": "lb-paper-review",
                "strategy_profile": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "candidate_id": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "config_sha256": V7_CONFIG_SHA256,
            },
        )

    with pytest.raises(ValueError, match="strategy profile"):
        build_v7_paper_preview_mapping(
            broker_identity=_identity(),
            available_inputs=_available_inputs(),
            as_of="2026-08-26",
            preview_request={
                "platform_id": "longbridge",
                "account_scope": "lb-paper-review",
                "strategy_profile": "other-profile",
                "candidate_id": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "config_sha256": V7_CONFIG_SHA256,
            },
        )

    with pytest.raises(ValueError, match="account mismatch"):
        build_v7_paper_preview_mapping(
            broker_identity=_identity(),
            available_inputs=_available_inputs(),
            as_of="2026-08-26",
            preview_request={
                "platform_id": "longbridge",
                "account_scope": "another-account",
                "strategy_profile": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "candidate_id": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "config_sha256": V7_CONFIG_SHA256,
            },
        )

    with pytest.raises(ValueError, match="candidate identity"):
        build_v7_paper_preview_mapping(
            broker_identity=_identity(),
            available_inputs=_available_inputs(),
            as_of="2026-08-26",
            preview_request={
                "platform_id": "longbridge",
                "account_scope": "lb-paper-review",
                "strategy_profile": SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE,
                "candidate_id": "other-candidate",
                "config_sha256": V7_CONFIG_SHA256,
            },
        )


def test_v7_profile_remains_rejected_by_default_runtime_loader() -> None:
    with pytest.raises(ValueError):
        load_strategy_entrypoint_for_profile(SOXL_SOXX_CORE_ONLY_P2_V7_PROFILE)
