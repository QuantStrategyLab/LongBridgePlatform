"""Offline caller regressions; all target identities are synthetic."""
from __future__ import annotations
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pytest
from scripts import build_cloud_run_env_sync_plan as planner
from scripts import run_hk_notification_canary as canary


def _plan(service, runtime_service=None):
    return planner.build_sync_plan({
        "PLATFORM_CONFIG_JSON": "{}",
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps({
            "defaults": {"GLOBAL_TELEGRAM_CHAT_ID": "fake-chat", "NOTIFY_LANG": "en"},
            "targets": [{"service": service, "account_prefix": "PAPER", "runtime_target": {
                "platform_id": "longbridge", "strategy_profile": "tqqq_growth_income",
                "deployment_selector": "PAPER", "account_scope": "PAPER",
                "account_selector": ["PAPER"], "execution_mode": "paper", "dry_run_only": True,
                "service_name": service if runtime_service is None else runtime_service,
            }}],
        }),
    })["targets"][0]


@pytest.mark.parametrize("service", ["invalid/name", " x", "UPPER", "x" * 50])
def test_plan_rejects_exact_invalid_service_without_echo(service):
    with pytest.raises(ValueError) as error:
        _plan(service)
    assert service not in str(error.value)


def test_plan_rejects_runtime_service_whitespace_instead_of_renaming():
    with pytest.raises(ValueError):
        _plan("fake-service", " fake-service ")


@pytest.mark.parametrize("length", [20, 34, 45, 46, 48])
def test_plan_reports_budget_without_granting_tag_or_traffic_authority(length):
    target = _plan("s" * length)
    preflight = target["tag_preflight"]
    assert preflight["headroom"] == 46 - length
    assert preflight["tag_status"] == ("AVAILABLE" if length < 46 else "TAG_IMPOSSIBLE")
    assert preflight["traffic_authority"] is False
    assert target["service_name"] == "s" * length


@pytest.mark.parametrize("room", range(1, 13))
def test_canary_allocates_short_tag_from_exact_candidate_and_operation(room, tmp_path):
    service = "s" * (46 - room)
    options = dict(operation_id="fake-run", project="fake-project", region="fake-region", candidate_id="fake-candidate", state_path=tmp_path / "tags.json")
    tag = canary._canary_tag(service, existing_tags={}, **options)
    assert 1 <= len(tag) <= room
    assert tag[0].islower()
    assert tag == canary._canary_tag(service, existing_tags={}, **options)
    with pytest.raises(canary.CanaryError, match="tag_allocation_refused"):
        canary._canary_tag(service, existing_tags={tag: None}, **options)


def test_canary_impossible_budget_refuses_before_opening_cloud_session():
    opened = []
    with pytest.raises(canary.CanaryError, match="tag_allocation_refused"):
        canary.run_hk_notification_canary(env={
            "WORKFLOW_TARGET": "HK", "GITHUB_REF": "refs/heads/main",
            "GITHUB_REPOSITORY": "QuantStrategyLab/LongBridgePlatform",
            "GOOGLE_CLOUD_PROJECT": "fake-project", "CLOUD_RUN_REGION": "asia-east2",
            "CLOUD_RUN_SERVICE": "s" * 48, "HK_CANDIDATE_REVISION": "fake-candidate",
            "HK_NOTIFICATION_CANARY_BACKUP_PATH": str(canary.BACKUP_PATH),
            "WARMUP_TRAFFIC_APPROVED": "true",
        }, session_factory=lambda: opened.append(True))
    assert opened == []


def _claim_worker(root):
    canary.BACKUP_ROOT = Path(root)
    canary.BACKUP_PATH = Path(root) / canary.BACKUP_RELATIVE_PATH
    try:
        canary._create_operation_claim(b'{"operation_id":"fake-run"}')
    except canary.CanaryError:
        return False
    return True


def test_operation_claim_is_exclusive_across_processes_and_restart(tmp_path):
    with ProcessPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(_claim_worker, [str(tmp_path)] * 2)) == [False, True]
    assert _claim_worker(str(tmp_path)) is False


def test_operation_claim_rejects_changed_or_missing_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(canary, "BACKUP_PATH", tmp_path / canary.BACKUP_RELATIVE_PATH)
    body = b'{"operation_id":"fake-run"}'
    identity = canary._create_operation_claim(body)
    canary._require_operation_claim(body, identity)
    claim = canary.BACKUP_PATH.with_suffix(".operation.json")
    claim.write_bytes(b'{"operation_id":"fake-foreign-run"}')
    with pytest.raises(canary.CanaryError, match="operation_ownership_lost"):
        canary._require_operation_claim(body, identity)
    claim.unlink()
    with pytest.raises(canary.CanaryError, match="operation_ownership_lost"):
        canary._require_operation_claim(body, identity)


def test_operation_claim_replacement_with_identical_bytes_loses_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(canary, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(canary, "BACKUP_PATH", tmp_path / canary.BACKUP_RELATIVE_PATH)
    body = b'{"operation_id":"fake-run"}'
    identity = canary._create_operation_claim(body)
    path = canary.BACKUP_PATH.with_suffix(".operation.json")
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(body)
    replacement.chmod(0o600)
    replacement.replace(path)
    with pytest.raises(canary.CanaryError, match="operation_ownership_lost"):
        canary._require_operation_claim(body, identity)
