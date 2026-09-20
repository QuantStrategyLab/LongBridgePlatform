from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from application.runtime_target_manifest import (
    RuntimeTargetManifestError,
    default_manifest_path,
    iter_enabled_targets,
    load_runtime_target_manifest,
    validate_runtime_target_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "config" / "runtime_targets.manifest.json"


def _valid_payload() -> dict:
    return {
        "schema_version": 1,
        "platform_id": "longbridge",
        "targets": [
            {
                "id": "paper",
                "label": "PAPER",
                "account_scope": "PAPER",
                "mode": "paper",
                "enabled": False,
                "environment": "longbridge-paper",
                "service": "longbridge-quant-paper-service",
                "region": "asia-east1",
                "strategy_profile": "russell_top50_leader_rotation",
                "secret_ref": {
                    "longport_token": "longport_token_paper",
                    "longport_app_key": "longport-app-key-paper",
                    "longport_app_secret": "longport-app-secret-paper",
                },
            }
        ],
    }


def test_default_manifest_path_points_at_checked_in_file():
    assert default_manifest_path() == MANIFEST_PATH
    assert MANIFEST_PATH.is_file()


def test_checked_in_manifest_loads_existing_paper_hk_sg_targets():
    manifest = load_runtime_target_manifest()
    assert manifest.platform_id == "longbridge"
    assert manifest.schema_version == 1
    by_id = {target.id: target for target in manifest.targets}
    assert set(by_id) == {"paper", "hk", "sg"}

    paper = by_id["paper"]
    assert paper.mode == "paper"
    assert paper.environment == "longbridge-paper"
    assert paper.service == "longbridge-quant-paper-service"
    assert paper.region == "asia-east1"
    assert paper.strategy_profile == "russell_top50_leader_rotation"
    assert paper.secret_ref.longport_token == "longport_token_paper"
    assert paper.enabled is False

    hk = by_id["hk"]
    assert hk.mode == "live"
    assert hk.environment == "longbridge-hk"
    assert hk.service == "longbridge-quant-hk-service"
    assert hk.region == "asia-east2"
    assert hk.strategy_profile == "hk_global_etf_tactical_rotation"

    sg = by_id["sg"]
    assert sg.mode == "live"
    assert sg.environment == "longbridge-sg"
    assert sg.service == "longbridge-quant-sg-service"
    assert sg.region == "asia-southeast1"
    assert sg.strategy_profile == "soxl_soxx_trend_income"

    assert list(iter_enabled_targets(manifest)) == []


def test_missing_enabled_defaults_to_disabled():
    payload = _valid_payload()
    del payload["targets"][0]["enabled"]
    manifest = validate_runtime_target_manifest(payload)
    assert manifest.targets[0].enabled is False


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda p: p.update({"schema_version": 2}), "Unsupported schema_version"),
        (lambda p: p.update({"platform_id": "ibkr"}), "platform_id must be"),
        (lambda p: p.update({"targets": []}), "at least one entry"),
        (
            lambda p: p["targets"][0].update({"mode": "dry-run"}),
            "mode must be one of",
        ),
        (
            lambda p: p["targets"][0].pop("region"),
            "missing required fields: region",
        ),
        (
            lambda p: p["targets"][0].pop("strategy_profile"),
            "missing required fields: strategy_profile",
        ),
        (
            lambda p: p["targets"][0].pop("secret_ref"),
            "missing required fields: secret_ref",
        ),
        (
            lambda p: p["targets"][0]["secret_ref"].pop("longport_token"),
            "missing required secret name fields",
        ),
        (
            lambda p: p["targets"].append(copy.deepcopy(p["targets"][0])),
            "Duplicate target id",
        ),
        (
            lambda p: (
                p["targets"].append(
                    {
                        **copy.deepcopy(p["targets"][0]),
                        "id": "other",
                        "environment": "longbridge-other",
                    }
                )
            ),
            "Duplicate Cloud Run service",
        ),
        (
            lambda p: (
                p["targets"].append(
                    {
                        **copy.deepcopy(p["targets"][0]),
                        "id": "other",
                        "service": "longbridge-quant-other-service",
                    }
                )
            ),
            "Duplicate GitHub Environment",
        ),
        (
            lambda p: p["targets"][0].update({"token": "abc"}),
            "must not contain secret-value field",
        ),
        (
            lambda p: p["targets"][0]["secret_ref"].update(
                {"longport_token": "a" * 80}
            ),
            "secret value",
        ),
        (
            lambda p: p["targets"][0].update(
                {"description": "Bearer " + ("x" * 40)}
            ),
            "secret value",
        ),
        (
            lambda p: p["targets"][0].update({"enabled": "false"}),
            "enabled must be a boolean",
        ),
    ],
)
def test_manifest_rejects_invalid_payloads(mutator, message):
    payload = _valid_payload()
    mutator(payload)
    with pytest.raises(RuntimeTargetManifestError, match=message):
        validate_runtime_target_manifest(payload)


def test_manifest_accepts_live_paper_and_shadow_modes():
    payload = _valid_payload()
    base = payload["targets"][0]
    payload["targets"] = [
        {**copy.deepcopy(base), "id": "a", "mode": "live", "service": "svc-a", "environment": "env-a"},
        {**copy.deepcopy(base), "id": "b", "mode": "paper", "service": "svc-b", "environment": "env-b"},
        {**copy.deepcopy(base), "id": "c", "mode": "shadow", "service": "svc-c", "environment": "env-c"},
    ]
    manifest = validate_runtime_target_manifest(payload)
    assert [target.mode for target in manifest.targets] == ["live", "paper", "shadow"]


def test_validate_script_accepts_checked_in_manifest(tmp_path, monkeypatch, capsys):
    import importlib.util

    script_path = REPO_ROOT / "scripts" / "validate_runtime_target_manifest.py"
    spec = importlib.util.spec_from_file_location("validate_runtime_target_manifest", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main([]) == 0
    out = capsys.readouterr().out
    assert "targets=3" in out
    assert module.main(["--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["target_count"] == 3

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": 1, "platform_id": "longbridge", "targets": []}), encoding="utf-8")
    assert module.main(["--path", str(bad)]) == 1
