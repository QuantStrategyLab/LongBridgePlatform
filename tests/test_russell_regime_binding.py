"""Exercise the pinned UES entrypoint with synthetic platform configuration."""
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest
from quant_platform_kit.common.models import PortfolioSnapshot
from quant_platform_kit.risk.contracts import RuntimeRiskLimits

import strategy_runtime as runtime_module

PROFILE = "russell_top50_leader_rotation"
DAY = "2026-11-03"


def regime_config():
    payload = {
        "plugin": "market_regime_control",
        "schema_version": "market_regime_control.v1",
        "as_of": DAY,
        "canonical_route": "risk_on",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {
        "market_regime_control": payload,
        "market_regime_control_expected": {
            "plugin": "market_regime_control",
            "schema_version": "market_regime_control.v1",
            "as_of": DAY,
            "valid_until": DAY,
            "payload_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        },
    }


@pytest.fixture
def platform(tmp_path):
    path = tmp_path / "strategy.json"
    path.write_text(json.dumps(regime_config()))
    settings = replace(runtime_module._default_runtime_settings(PROFILE, "Synthetic Russell"),
                       strategy_config_path=str(path), dry_run_only=True,
                       feature_snapshot_path="synthetic.csv",
                       feature_snapshot_manifest_path="synthetic-manifest.json")
    runtime = runtime_module.load_strategy_runtime(
        PROFILE, runtime_settings=settings, logger=lambda _message: None,
        execution_materials={"runtime_risk_limits": RuntimeRiskLimits(
            allowed_symbols=("FAKEA", "FAKEB", "FAKEC", "FAKED", "BOXX"),
            product_leverage_factors={symbol: 1 for symbol in ("FAKEA", "FAKEB", "FAKEC", "FAKED", "BOXX")},
            nominal_caps={symbol: 1.0 for symbol in ("FAKEA", "FAKEB", "FAKEC", "FAKED", "BOXX")},
            total_nominal_exposure_cap=1.0, total_effective_exposure_cap=1.0, max_positions=5,
        )},
    )
    # Real ranking/weight construction; only the external snapshot read is replaced.
    rows = [dict(symbol=symbol, sector="Synthetic", close=100.0, adv20_usd=100_000_000.0,
                 history_days=300, mom_3m=0.2, mom_6m=0.3, mom_12_1=0.4,
                 rel_mom_6m_vs_benchmark=0.1, rel_mom_6m_vs_broad_benchmark=0.1,
                 high_252_gap=-0.01, sma200_gap=0.1, vol_63=0.15, maxdd_126=-0.1)
            for symbol in ("QQQ", "SPY", "FAKEA", "FAKEB", "FAKEC", "FAKED")]
    guard = SimpleNamespace(frame=pd.DataFrame(rows), metadata={"snapshot_guard_decision": "proceed"})

    def evaluate():
        with patch.object(runtime_module, "load_feature_snapshot_guarded", return_value=guard), \
                patch("quant_platform_kit.common.feature_snapshot_runtime.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 3, tzinfo=timezone.utc)
            return runtime.evaluate(
                translator=lambda key, **_kwargs: key,
                portfolio_snapshot=PortfolioSnapshot(
                    as_of=datetime(2026, 11, 3, tzinfo=timezone.utc),
                    total_equity=100_000.0, buying_power=100_000.0, positions=(),
                ),
            ).decision
    return path, evaluate


def test_ordinary_russell_decision_keeps_target_weights(platform):
    _, evaluate = platform
    decision = evaluate()
    assert "no_execute" not in decision.risk_flags
    assert decision.diagnostics["actionable"] is True
    assert any(position.symbol.startswith("FAKE") for position in decision.positions)


@pytest.mark.parametrize("defect", ["missing", "invalid", "digest", "expired", "missing_signal"])
def test_russell_binding_gate_stays_closed(platform, defect):
    path, evaluate = platform
    config = regime_config()
    if defect == "missing":
        del config["market_regime_control_expected"]
    elif defect == "invalid":
        config["market_regime_control_expected"] = "invalid"
    elif defect == "digest":
        config["market_regime_control_expected"]["payload_sha256"] = "0" * 64
    elif defect == "expired":
        config["market_regime_control_expected"]["valid_until"] = "2026-11-02"
    else:
        del config["market_regime_control"]
    path.write_text(json.dumps(config))
    decision = evaluate()
    assert "no_execute" in decision.risk_flags
    assert not decision.positions
    assert decision.diagnostics["market_regime_control_binding_valid"] is False


def test_next_evaluation_rereads_binding_after_revocation(platform):
    path, evaluate = platform
    assert "no_execute" not in evaluate().risk_flags
    path.write_text(json.dumps({"market_regime_control": regime_config()["market_regime_control"]}))
    assert "no_execute" in evaluate().risk_flags


@pytest.mark.parametrize("content", ["{", "[]"])
def test_unreadable_configuration_blocks_without_raw_diagnostics(platform, content):
    path, evaluate = platform
    path.write_text(content)
    with pytest.raises(ValueError, match="^Invalid Russell regime configuration$"):
        evaluate()


def test_read_failure_blocks_without_private_exception_text(platform):
    _, evaluate = platform
    with patch("strategy_runtime.Path.read_text", side_effect=OSError("synthetic-private-detail")):
        with pytest.raises(ValueError, match="^Invalid Russell regime configuration$") as error:
            evaluate()
    assert error.value.__suppress_context__ is True
    assert "synthetic-private-detail" not in str(error.value)
