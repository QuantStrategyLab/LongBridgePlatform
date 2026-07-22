from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYNC_PLAN_SCRIPT_PATH = ROOT / "scripts" / "build_cloud_run_env_sync_plan.py"


def runtime_target_json(
    strategy_profile: str,
    *,
    dry_run_only: bool = False,
    platform_id: str = "longbridge",
    deployment_selector: str | None = None,
    account_selector: list[str] | tuple[str, ...] | None = None,
    account_scope: str | None = None,
    service_name: str | None = None,
) -> str:
    payload: dict[str, object] = {
        "platform_id": platform_id,
        "strategy_profile": strategy_profile,
        "dry_run_only": dry_run_only,
    }
    if deployment_selector is not None:
        payload["deployment_selector"] = deployment_selector
    if account_selector is not None:
        payload["account_selector"] = list(account_selector)
    if account_scope is not None:
        payload["account_scope"] = account_scope
    if service_name is not None:
        payload["service_name"] = service_name
    payload["execution_mode"] = "paper" if dry_run_only else "live"
    return json.dumps(payload, separators=(",", ":"))


def test_build_cloud_run_env_sync_plan_legacy_mode_uses_shared_env():
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "longbridge-quant-paper-service",
        "GLOBAL_TELEGRAM_CHAT_ID": "5992562050",
        "NOTIFY_LANG": "zh",
        "ACCOUNT_PREFIX": "PAPER",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "soxl_soxx_trend_income",
            deployment_selector="PAPER",
            account_scope="PAPER",
            service_name="longbridge-quant-paper-service",
        ),
        "LONGBRIDGE_MARKET": "US",
        "LONGBRIDGE_MARKET_TIMEZONE": "America/New_York",
        "EXECUTION_REPORT_GCS_URI": "gs://runtime/execution-reports",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plan = json.loads(result.stdout)
    assert plan["mode"] == "legacy"
    assert len(plan["targets"]) == 1
    target = plan["targets"][0]
    assert target["service_name"] == "longbridge-quant-paper-service"
    assert target["strategy_profile"] == "soxl_soxx_trend_income"
    assert target["env"]["ACCOUNT_PREFIX"] == "PAPER"
    assert target["env"]["GLOBAL_TELEGRAM_CHAT_ID"] == "5992562050"
    assert target["env"]["LONGBRIDGE_MARKET"] == "US"
    assert target["env"]["EXECUTION_REPORT_GCS_URI"] == "gs://runtime/execution-reports"
    assert target["scheduler"] == {
        "timezone": "America/New_York",
        "main_time": "45 15",
        "probe_time": "35 9,15",
        "precheck_time": "45 9",
    }


def test_build_cloud_run_env_sync_plan_rejects_ambiguous_cloud_scheduler_cron():
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE": "longbridge-quant-paper-service",
        "GLOBAL_TELEGRAM_CHAT_ID": "5992562050",
        "NOTIFY_LANG": "zh",
        "ACCOUNT_PREFIX": "PAPER",
        "RUNTIME_TARGET_JSON": runtime_target_json(
            "soxl_soxx_trend_income",
            deployment_selector="PAPER",
            account_scope="PAPER",
            service_name="longbridge-quant-paper-service",
        ),
        "LONGBRIDGE_MARKET": "US",
        "CLOUD_SCHEDULER_PROBE_TIME": "35 9 1-7 * 1-5",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "cannot constrain both day-of-month and day-of-week" in result.stderr


def test_build_cloud_run_env_sync_plan_requires_target_snapshot_in_per_service_mode():
    payload = {
        "defaults": {
            "GLOBAL_TELEGRAM_CHAT_ID": "5992562050",
            "NOTIFY_LANG": "zh",
        },
        "targets": [
            {
                "service": "longbridge-quant-live-mega-service",
                "account_prefix": "SG",
                "runtime_target": json.loads(
                    runtime_target_json(
                        "russell_top50_leader_rotation",
                        deployment_selector="SG",
                        account_scope="SG",
                        service_name="longbridge-quant-live-mega-service",
                    )
                ),
            }
        ],
    }
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(payload),
        "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": "gs://stale-paper/snapshot.csv",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "longbridge-quant-live-mega-service:LONGBRIDGE_FEATURE_SNAPSHOT_PATH" in result.stderr
    assert "gs://stale-paper/snapshot.csv" not in result.stderr


def test_build_cloud_run_env_sync_plan_supports_per_service_targets():
    payload = {
        "defaults": {
            "GLOBAL_TELEGRAM_CHAT_ID": "5992562050",
            "NOTIFY_LANG": "zh",
            "LONGBRIDGE_MARKET": "HK",
            "LONGBRIDGE_MARKET_CALENDAR": "XHKG",
            "LONGBRIDGE_MARKET_TIMEZONE": "Asia/Hong_Kong",
            "LONGBRIDGE_SYMBOL_SUFFIX": ".HK",
            "LONGBRIDGE_TRADING_CURRENCY": "HKD",
            "cloud_scheduler_probe_time": "40 9,15",
            "EXECUTION_REPORT_GCS_URI": "gs://runtime/execution-reports",
        },
        "targets": [
            {
                "service": "longbridge-quant-hk-verify-service",
                "account_prefix": "HK",
                "cloud_scheduler_main_time": "10 16",
                "runtime_target": json.loads(
                    runtime_target_json(
                        "tqqq_growth_income",
                        deployment_selector="HK",
                        account_scope="HK",
                        service_name="longbridge-quant-hk-verify-service",
                    )
                ),
            },
            {
                "service": "longbridge-quant-live-mega-service",
                "account_prefix": "SG",
                "runtime_target": {
                    **json.loads(
                        runtime_target_json(
                            "russell_top50_leader_rotation",
                            deployment_selector="SG",
                            account_scope="SG",
                            service_name="longbridge-quant-live-mega-service",
                        )
                    ),
                    "scheduler": {
                        "timezone": "America/New_York",
                        "main_time": "45 15 1-7 * *",
                        "probe_time": "35 9,15 1-7 * *",
                        "precheck_time": "45 9 1-7 * *",
                    },
                },
                "longbridge_feature_snapshot_path": "gs://runtime/mega/snapshot.csv",
                "longbridge_feature_snapshot_manifest_path": "gs://runtime/mega/snapshot.csv.manifest.json",
            },
        ],
    }
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(payload),
        "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": "gs://stale-paper/snapshot.csv",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plan = json.loads(result.stdout)
    assert plan["mode"] == "per_service"
    by_service = {target["service_name"]: target for target in plan["targets"]}
    hk_verify = by_service["longbridge-quant-hk-verify-service"]
    live_mega = by_service["longbridge-quant-live-mega-service"]

    assert hk_verify["env"]["ACCOUNT_PREFIX"] == "HK"
    assert hk_verify["env"]["STRATEGY_PROFILE"] == "tqqq_growth_income"
    assert hk_verify["env"]["LONGBRIDGE_MARKET"] == "HK"
    assert hk_verify["env"]["LONGBRIDGE_TRADING_CURRENCY"] == "HKD"
    assert hk_verify["scheduler"] == {
        "timezone": "Asia/Hong_Kong",
        "main_time": "10 16",
        "probe_time": "40 9,15",
        "precheck_time": "45 9",
    }
    assert "LONGBRIDGE_FEATURE_SNAPSHOT_PATH" not in hk_verify["env"]
    assert "LONGBRIDGE_FEATURE_SNAPSHOT_PATH" in hk_verify["remove_env_vars"]
    assert "gs://stale-paper/snapshot.csv" not in json.dumps(hk_verify)

    assert live_mega["env"]["ACCOUNT_PREFIX"] == "SG"
    assert live_mega["env"]["STRATEGY_PROFILE"] == "russell_top50_leader_rotation"
    assert live_mega["scheduler"] == {
        "timezone": "America/New_York",
        "main_time": "45 15 1-7 * *",
        "probe_time": "35 9,15 1-7 * *",
        "precheck_time": "45 9 1-7 * *",
    }
    assert live_mega["env"]["LONGBRIDGE_FEATURE_SNAPSHOT_PATH"] == "gs://runtime/mega/snapshot.csv"
    assert (
        live_mega["env"]["LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"]
        == "gs://runtime/mega/snapshot.csv.manifest.json"
    )


def test_current_service_uses_environment_runtime_enabled_state():
    payload = {
        "defaults": {
            "GLOBAL_TELEGRAM_CHAT_ID": "5992562050",
            "NOTIFY_LANG": "zh",
            "LONGBRIDGE_MARKET": "US",
        },
        "targets": [
            {
                "service": service,
                "account_prefix": scope,
                "runtime_target": json.loads(
                    runtime_target_json(
                        "tqqq_growth_income",
                        deployment_selector=scope,
                        account_scope=scope,
                        service_name=service,
                    )
                ),
            }
            for service, scope in (
                ("longbridge-quant-paper-service", "PAPER"),
                ("longbridge-quant-sg-service", "SG"),
            )
        ],
    }
    env = {
        **os.environ,
        "CLOUD_RUN_SERVICE_TARGETS_JSON": json.dumps(payload),
        "CLOUD_RUN_SERVICE": "longbridge-quant-sg-service",
        "RUNTIME_TARGET_ENABLED": "false",
    }

    result = subprocess.run(
        [sys.executable, str(SYNC_PLAN_SCRIPT_PATH), "--json"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    plan = json.loads(result.stdout)
    by_service = {target["service_name"]: target for target in plan["targets"]}
    assert by_service["longbridge-quant-sg-service"]["env"]["RUNTIME_TARGET_ENABLED"] == "false"
    assert by_service["longbridge-quant-paper-service"]["env"]["RUNTIME_TARGET_ENABLED"] == "true"
