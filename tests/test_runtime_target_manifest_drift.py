import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_runtime_target_manifest_drift",
    ROOT / "scripts" / "check_runtime_target_manifest_drift.py",
)
assert _spec is not None and _spec.loader is not None
drift = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = drift
_spec.loader.exec_module(drift)

from application.runtime_target_manifest import (  # noqa: E402
    load_runtime_target_manifest,
    validate_runtime_target_manifest,
)


def _manifest():
    return validate_runtime_target_manifest(
        {
            "schema_version": 1,
            "platform_id": "longbridge",
            "targets": [
                {
                    "id": "hk",
                    "label": "HK",
                    "account_scope": "HK",
                    "mode": "live",
                    "environment": "longbridge-hk",
                    "service": "longbridge-quant-hk-service",
                    "region": "asia-east2",
                    "strategy_profile": "tqqq_growth_income",
                    "secret_ref": {
                        "longport_token": "longport_token_hk",
                        "longport_app_key": "longport-app-key-hk",
                        "longport_app_secret": "longport-app-secret-hk",
                    },
                }
            ],
        }
    )


def _target(**overrides):
    payload = {
        "platform_id": "longbridge",
        "strategy_profile": "tqqq_growth_income",
        "account_scope": "HK",
        "service_name": "longbridge-quant-hk-service",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_matching_target_has_no_drift():
    assert drift.evaluate_target_drift(_manifest(), target_id="hk", runtime_target_json=_target()) == []


def test_unknown_target_is_reported():
    findings = drift.evaluate_target_drift(_manifest(), target_id="sg", runtime_target_json=_target())
    assert [finding.code for finding in findings] == ["manifest_target_missing"]


def test_missing_and_invalid_runtime_target_are_reported():
    codes = [
        [finding.code for finding in drift.evaluate_target_drift(_manifest(), target_id="hk", runtime_target_json=raw)]
        for raw in ("", None, "{not-json", "[]")
    ]
    assert codes == [["runtime_target_missing"], ["runtime_target_missing"], ["runtime_target_invalid"], ["runtime_target_invalid"]]


def test_strategy_profile_drift_is_reported():
    findings = drift.evaluate_target_drift(
        _manifest(),
        target_id="hk",
        runtime_target_json=_target(strategy_profile="hk_global_etf_tactical_rotation"),
    )
    assert [finding.code for finding in findings] == ["strategy_profile_mismatch"]


def test_strategy_profile_env_conflict_is_reported():
    findings = drift.evaluate_target_drift(
        _manifest(),
        target_id="hk",
        runtime_target_json=_target(),
        strategy_profile_env="hk_global_etf_tactical_rotation",
    )
    assert [finding.code for finding in findings] == ["strategy_profile_conflict"]


def test_incomplete_target_and_identity_drift_are_reported():
    findings = drift.evaluate_target_drift(
        _manifest(),
        target_id="hk",
        runtime_target_json=json.dumps(
            {
                "platform_id": "other",
                "account_scope": "SG",
                "service_name": "longbridge-quant-sg-service",
            }
        ),
    )
    assert [finding.code for finding in findings] == [
        "platform_id_mismatch",
        "runtime_target_incomplete",
        "account_scope_mismatch",
        "service_name_mismatch",
    ]


def test_checked_in_manifest_runs_clean_with_environment(monkeypatch, capsys):
    manifest = load_runtime_target_manifest()
    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target())
    monkeypatch.delenv("STRATEGY_PROFILE", raising=False)
    assert drift.main(["--target-id", "hk"]) == 0
    assert "manifest matches" in capsys.readouterr().out

    monkeypatch.setenv("RUNTIME_TARGET_JSON", _target(strategy_profile="hk_global_etf_tactical_rotation"))
    assert drift.main(["--target-id", "hk"]) == 1
    assert "strategy_profile_mismatch" in capsys.readouterr().err
