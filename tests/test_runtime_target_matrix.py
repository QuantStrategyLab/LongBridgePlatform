from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from application.runtime_target_manifest import (
    RuntimeTargetManifestError,
    build_github_actions_matrix,
    load_runtime_target_manifest,
    validate_runtime_target_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Frozen parity with the pre-manifest hardcoded workflow matrices.
EXPECTED_GUARD = [
    {
        "id": "paper",
        "label": "PAPER",
        "environment": "longbridge-paper",
        "service": "longbridge-quant-paper-service",
        "region": "asia-east1",
        "mode": "paper",
    },
    {
        "id": "hk",
        "label": "HK",
        "environment": "longbridge-hk",
        "service": "longbridge-quant-hk-service",
        "region": "asia-east2",
        "mode": "live",
    },
    {
        "id": "sg",
        "label": "SG",
        "environment": "longbridge-sg",
        "service": "longbridge-quant-sg-service",
        "region": "asia-southeast1",
        "mode": "live",
    },
]

EXPECTED_LIFECYCLE = [
    {
        "id": "paper",
        "label": "PAPER",
        "environment": "longbridge-paper",
        "account_scope": "PAPER",
        "service": "longbridge-quant-paper-service",
        "region": "asia-east1",
        "mode": "paper",
    },
    {
        "id": "hk",
        "label": "HK",
        "environment": "longbridge-hk",
        "account_scope": "HK",
        "service": "longbridge-quant-hk-service",
        "region": "asia-east2",
        "mode": "live",
    },
    {
        "id": "sg",
        "label": "SG",
        "environment": "longbridge-sg",
        "account_scope": "SG",
        "service": "longbridge-quant-sg-service",
        "region": "asia-southeast1",
        "mode": "live",
    },
]

EXPECTED_HEARTBEAT = [
    {
        "id": "paper",
        "label": "PAPER",
        "environment": "longbridge-paper",
        "mode": "paper",
    },
    {
        "id": "hk",
        "label": "HK",
        "environment": "longbridge-hk",
        "mode": "live",
    },
    {
        "id": "sg",
        "label": "SG",
        "environment": "longbridge-sg",
        "mode": "live",
    },
]

EXPECTED_SYNC = [
    {
        "id": "paper",
        "label": "PAPER",
        "environment": "longbridge-paper",
        "default_account_region": "PAPER",
        "mode": "paper",
    },
    {
        "id": "hk",
        "label": "HK",
        "environment": "longbridge-hk",
        "default_account_region": "HK",
        "mode": "live",
    },
    {
        "id": "sg",
        "label": "SG",
        "environment": "longbridge-sg",
        "default_account_region": "SG",
        "mode": "live",
    },
]


def _load_render_script():
    script_path = REPO_ROOT / "scripts" / "render_runtime_target_matrix.py"
    spec = importlib.util.spec_from_file_location("render_runtime_target_matrix", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("guard", EXPECTED_GUARD),
        ("lifecycle", EXPECTED_LIFECYCLE),
        ("heartbeat", EXPECTED_HEARTBEAT),
        ("sync", EXPECTED_SYNC),
    ],
)
def test_checked_in_manifest_matrix_matches_legacy_paper_hk_sg(profile, expected):
    manifest = load_runtime_target_manifest()
    matrix = build_github_actions_matrix(manifest, profile=profile)
    assert matrix == {"target": expected}
    for row in matrix["target"]:
        assert "enabled" not in row


def test_manifest_enabled_true_does_not_filter_or_emit_enablement():
    payload = json.loads(
        (REPO_ROOT / "config" / "runtime_targets.manifest.json").read_text(encoding="utf-8")
    )
    for target in payload["targets"]:
        target["enabled"] = True
    manifest = validate_runtime_target_manifest(payload)
    matrix = build_github_actions_matrix(manifest, profile="guard")
    assert [row["id"] for row in matrix["target"]] == ["paper", "hk", "sg"]
    assert all("enabled" not in row for row in matrix["target"])


def test_matrix_includes_disabled_new_target_without_treating_enabled_as_authority():
    payload = json.loads(
        (REPO_ROOT / "config" / "runtime_targets.manifest.json").read_text(encoding="utf-8")
    )
    payload["targets"].append(
        {
            "id": "shadow-a",
            "label": "SHADOW-A",
            "account_scope": "SHADOW-A",
            "mode": "shadow",
            "enabled": False,
            "environment": "longbridge-shadow-a",
            "service": "longbridge-quant-shadow-a-service",
            "region": "asia-east1",
            "strategy_profile": "russell_top50_leader_rotation",
            "secret_ref": {
                "longport_token": "longport_token_shadow_a",
                "longport_app_key": "longport-app-key-shadow-a",
                "longport_app_secret": "longport-app-secret-shadow-a",
            },
        }
    )
    manifest = validate_runtime_target_manifest(payload)
    matrix = build_github_actions_matrix(manifest, profile="lifecycle")
    assert [row["id"] for row in matrix["target"]] == ["paper", "hk", "sg", "shadow-a"]
    shadow = matrix["target"][-1]
    assert shadow["mode"] == "shadow"
    assert shadow["account_scope"] == "SHADOW-A"
    assert "enabled" not in shadow


def test_unknown_profile_and_empty_targets_fail_closed():
    manifest = load_runtime_target_manifest()
    with pytest.raises(RuntimeTargetManifestError, match="Unknown matrix profile"):
        build_github_actions_matrix(manifest, profile="deploy")

    payload = {
        "schema_version": 1,
        "platform_id": "longbridge",
        "targets": [
            {
                "id": "paper",
                "label": "PAPER",
                "mode": "paper",
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
    # Bypass public validator emptiness rule by mutating a validated object.
    validated = validate_runtime_target_manifest(copy.deepcopy(payload))
    empty = type(validated)(
        schema_version=validated.schema_version,
        platform_id=validated.platform_id,
        targets=(),
        source_path=validated.source_path,
    )
    with pytest.raises(RuntimeTargetManifestError, match="no targets"):
        build_github_actions_matrix(empty, profile="guard")


def test_render_script_writes_github_output_and_rejects_bad_manifest(
    tmp_path, monkeypatch, capsys
):
    module = _load_render_script()
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))

    assert module.main(["--profile", "guard", "--github-output"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"target": EXPECTED_GUARD}
    written = output_file.read_text(encoding="utf-8").strip()
    assert written.startswith("matrix=")
    assert json.loads(written.removeprefix("matrix=")) == {"target": EXPECTED_GUARD}

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"schema_version": 1, "platform_id": "longbridge", "targets": []}),
        encoding="utf-8",
    )
    assert module.main(["--profile", "sync", "--path", str(bad)]) == 1

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert module.main(["--profile", "heartbeat", "--github-output"]) == 1


def test_monitor_and_deploy_workflows_consume_validated_matrix_output():
    workflows = {
        "runtime-guard.yml": "guard",
        "runtime-target-lifecycle.yml": "lifecycle",
        "execution-report-heartbeat.yml": "heartbeat",
        "sync-cloud-run-env.yml": "sync",
    }
    for name, profile in workflows.items():
        text = (REPO_ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "jobs:" in text
        assert "resolve-matrix:" in text
        assert f"render_runtime_target_matrix.py --profile {profile} --github-output" in text
        assert "needs: resolve-matrix" in text
        assert "matrix: ${{ fromJSON(needs.resolve-matrix.outputs.matrix) }}" in text
        assert "vars.RUNTIME_TARGET_ENABLED" in text
        # Hardcoded PAPER/HK/SG matrix blocks must not remain in dynamic consumers.
        assert "- label: PAPER\n            environment: longbridge-paper" not in text


def test_render_script_is_stdlib_only():
    script = (REPO_ROOT / "scripts" / "render_runtime_target_matrix.py").read_text(
        encoding="utf-8"
    )
    assert "import uv" not in script
    assert "yaml" not in script
    assert "requests" not in script
