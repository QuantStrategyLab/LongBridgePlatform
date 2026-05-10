import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
SCRIPT_PATH = ROOT / "scripts" / "print_strategy_profile_status.py"
SWITCH_PLAN_SCRIPT_PATH = ROOT / "scripts" / "print_strategy_switch_env_plan.py"

from runtime_config_support import (
    DEFAULT_ACCOUNT_REGION,
    DEFAULT_LONGPORT_SECRET_NAME,
    infer_account_region,
    load_platform_runtime_settings,
)
from strategy_registry import (
    LONGBRIDGE_PLATFORM,
    US_EQUITY_DOMAIN,
    get_eligible_profiles_for_platform,
    get_platform_profile_matrix,
    get_platform_profile_status_matrix,
    get_supported_profiles_for_platform,
)


SAMPLE_STRATEGY_PROFILE = "soxl_soxx_trend_income"
BASE_LONGBRIDGE_PROFILES = frozenset(
    {
        "global_etf_rotation",
        "mega_cap_leader_rotation_top50_balanced",
        "russell_1000_multi_factor_defensive",
        "tqqq_growth_income",
        "soxl_soxx_trend_income",
        "tech_communication_pullback_enhancement",
    }
)
OPTIONAL_LONGBRIDGE_PROFILES = frozenset({"global_etf_confidence_vol_gate"})


def expected_longbridge_profiles(actual_profiles) -> frozenset[str]:
    actual = frozenset(actual_profiles)
    return BASE_LONGBRIDGE_PROFILES | (OPTIONAL_LONGBRIDGE_PROFILES & actual)


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


class RuntimeConfigSupportTests(unittest.TestCase):
    def test_load_platform_runtime_settings_uses_defaults_with_explicit_strategy_profile(self):
        with patch.dict(
            os.environ,
            {"RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE)},
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.project_id, "project-1")
        self.assertEqual(settings.secret_name, DEFAULT_LONGPORT_SECRET_NAME)
        self.assertEqual(settings.account_prefix, "DEFAULT")
        self.assertEqual(settings.strategy_profile, SAMPLE_STRATEGY_PROFILE)
        self.assertEqual(settings.strategy_display_name, "SOXL/SOXX Semiconductor Trend Income")
        self.assertEqual(settings.strategy_domain, US_EQUITY_DOMAIN)
        self.assertEqual(settings.account_region, DEFAULT_ACCOUNT_REGION)
        self.assertEqual(settings.notify_lang, "en")
        self.assertIsNone(settings.tg_token)
        self.assertIsNone(settings.tg_chat_id)
        self.assertFalse(settings.dry_run_only)
        self.assertFalse(settings.fractional_limit_buy_fallback_to_market)
        self.assertFalse(settings.debug_position_snapshot)
        self.assertIsNotNone(settings.runtime_target)
        self.assertEqual(settings.runtime_target.platform_id, "longbridge")
        self.assertEqual(settings.runtime_target.execution_mode, "live")
        self.assertIsNone(settings.income_threshold_usd)
        self.assertIsNone(settings.qqqi_income_ratio)
        self.assertIsNone(settings.feature_snapshot_path)
        self.assertIsNone(settings.strategy_config_path)
        self.assertIsNone(settings.strategy_plugin_mounts_json)

    def test_load_platform_runtime_settings_prefers_runtime_target_json(self):
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_REGION": "hk",
                "RUNTIME_TARGET_JSON": (
                    '{"platform_id":"longbridge","strategy_profile":"global_etf_rotation",'
                    '"dry_run_only":true,"deployment_selector":"SG","account_selector":["SG"],'
                    '"account_scope":"SG","service_name":"longbridge-quant-sg-service",'
                    '"execution_mode":"paper"}'
                ),
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "global_etf_rotation")
        self.assertEqual(settings.runtime_target.strategy_profile, "global_etf_rotation")
        self.assertEqual(settings.runtime_target.platform_id, "longbridge")
        self.assertTrue(settings.runtime_target.dry_run_only)
        self.assertEqual(settings.runtime_target.execution_mode, "paper")
        self.assertEqual(settings.runtime_target.deployment_selector, "SG")
        self.assertEqual(settings.runtime_target.account_selector, ("SG",))
        self.assertEqual(settings.runtime_target.service_name, "longbridge-quant-sg-service")

    def test_load_platform_runtime_settings_requires_strategy_profile(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(EnvironmentError, "RUNTIME_TARGET_JSON is required"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_platform_supported_profiles_are_filtered_by_registry(self):
        profiles = get_supported_profiles_for_platform(LONGBRIDGE_PLATFORM)
        self.assertEqual(profiles, expected_longbridge_profiles(profiles))

    def test_platform_eligible_profiles_are_exposed_by_capability_matrix(self):
        profiles = get_eligible_profiles_for_platform(LONGBRIDGE_PLATFORM)
        self.assertEqual(profiles, expected_longbridge_profiles(profiles))

    def test_dry_run_only_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(
                    SAMPLE_STRATEGY_PROFILE,
                    dry_run_only=True,
                ),
                "LONGBRIDGE_DRY_RUN_ONLY": "true",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertTrue(settings.dry_run_only)

    def test_fractional_limit_buy_fallback_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_FRACTIONAL_LIMIT_BUY_FALLBACK_TO_MARKET": "true",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertTrue(settings.fractional_limit_buy_fallback_to_market)

    def test_debug_position_snapshot_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_DEBUG_POSITION_SNAPSHOT": "true",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertTrue(settings.debug_position_snapshot)

    def test_strategy_plugin_mounts_are_loaded_from_env(self):
        mount_config = '{"strategy_plugins":[{"strategy":"soxl_soxx_trend_income","plugin":"crisis_response_shadow","signal_path":"gs://bucket/latest_signal.json"}]}'
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "STRATEGY_PLUGIN_MOUNTS_JSON": '{"strategy_plugins":[{"plugin":"global"}]}',
                "LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON": mount_config,
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_plugin_mounts_json, mount_config)

    def test_income_layer_overrides_are_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json("tqqq_growth_income"),
                "INCOME_THRESHOLD_USD": "100000",
                "QQQI_INCOME_RATIO": "0.5",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "tqqq_growth_income")
        self.assertEqual(settings.income_threshold_usd, 100000.0)
        self.assertEqual(settings.qqqi_income_ratio, 0.5)

    def test_tech_runtime_execution_window_override_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(
                    "tech_communication_pullback_enhancement"
                ),
                "LONGBRIDGE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS": "31",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "tech_communication_pullback_enhancement")
        self.assertEqual(settings.runtime_execution_window_trading_days, 31)

    def test_rejects_invalid_qqqi_income_ratio(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json("tqqq_growth_income"),
                "QQQI_INCOME_RATIO": "1.5",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "QQQI_INCOME_RATIO"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_rejects_human_readable_alias(self):
        with patch.dict(
            os.environ,
            {"RUNTIME_TARGET_JSON": runtime_target_json("semiconductor_trend_income")},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_account_region_prefers_explicit_env(self):
        region = infer_account_region(
            "sg",
            account_prefix="HK",
        )
        self.assertEqual(region, "SG")

    def test_account_region_falls_back_to_account_prefix(self):
        region = infer_account_region(
            None,
            account_prefix="hk",
        )
        self.assertEqual(region, "HK")

    def test_account_region_defaults_when_prefix_missing(self):
        region = infer_account_region(
            None,
            account_prefix="",
        )
        self.assertEqual(region, DEFAULT_ACCOUNT_REGION)

    def test_unsupported_strategy_profile_fails_fast(self):
        with patch.dict(
            os.environ,
            {"RUNTIME_TARGET_JSON": runtime_target_json("balanced_income")},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_platform_profile_matrix_exposes_profiles_without_selection_roles(self):
        rows = get_platform_profile_matrix()
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile[SAMPLE_STRATEGY_PROFILE]["display_name"], "SOXL/SOXX Semiconductor Trend Income")
        self.assertNotIn("is_default", by_profile[SAMPLE_STRATEGY_PROFILE])
        self.assertNotIn("is_rollback", by_profile[SAMPLE_STRATEGY_PROFILE])

    def test_platform_profile_status_matrix_matches_current_longbridge_rollout(self):
        rows = get_platform_profile_status_matrix()
        by_profile = {row["canonical_profile"]: row for row in rows}

        self.assertEqual(set(by_profile), expected_longbridge_profiles(by_profile))
        self.assertEqual(
            by_profile["soxl_soxx_trend_income"],
            {
                "canonical_profile": "soxl_soxx_trend_income",
                "display_name": "SOXL/SOXX Semiconductor Trend Income",
                "domain": "us_equity",
                "eligible": True,
                "enabled": True,
                "platform": "longbridge",
            },
        )
        self.assertEqual(by_profile["tqqq_growth_income"]["display_name"], "TQQQ Growth Income")
        self.assertEqual(
            by_profile["russell_1000_multi_factor_defensive"]["display_name"],
            "Russell 1000 Multi-Factor",
        )
        self.assertTrue(by_profile["russell_1000_multi_factor_defensive"]["eligible"])
        self.assertTrue(by_profile["russell_1000_multi_factor_defensive"]["enabled"])
        self.assertEqual(by_profile["global_etf_rotation"]["display_name"], "Global ETF Rotation")
        self.assertTrue(by_profile["global_etf_rotation"]["eligible"])
        self.assertTrue(by_profile["global_etf_rotation"]["enabled"])
        if "global_etf_confidence_vol_gate" in by_profile:
            self.assertEqual(
                by_profile["global_etf_confidence_vol_gate"]["display_name"],
                "Global ETF Confidence Vol Gate",
            )
            self.assertTrue(by_profile["global_etf_confidence_vol_gate"]["eligible"])
            self.assertTrue(by_profile["global_etf_confidence_vol_gate"]["enabled"])
        self.assertTrue(by_profile["tech_communication_pullback_enhancement"]["eligible"])
        self.assertTrue(by_profile["tech_communication_pullback_enhancement"]["enabled"])
        self.assertEqual(by_profile["tech_communication_pullback_enhancement"]["display_name"], "Tech/Communication Pullback Enhancement")
        self.assertTrue(by_profile["mega_cap_leader_rotation_top50_balanced"]["eligible"])
        self.assertTrue(by_profile["mega_cap_leader_rotation_top50_balanced"]["enabled"])
        self.assertEqual(
            by_profile["mega_cap_leader_rotation_top50_balanced"]["display_name"],
            "Mega Cap Leader Rotation Top50 Balanced",
        )

    def test_loads_feature_snapshot_env_for_tech_profile(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(
                    "tech_communication_pullback_enhancement"
                ),
                "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": "gs://bucket/tech.csv",
                "LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH": "gs://bucket/tech.csv.manifest.json",
                "LONGBRIDGE_STRATEGY_CONFIG_PATH": "/workspace/configs/tech.json",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "tech_communication_pullback_enhancement")
        self.assertEqual(settings.feature_snapshot_path, "gs://bucket/tech.csv")
        self.assertEqual(settings.feature_snapshot_manifest_path, "gs://bucket/tech.csv.manifest.json")
        self.assertEqual(settings.strategy_config_path, "/workspace/configs/tech.json")
        self.assertEqual(settings.strategy_config_source, "env")

    def test_derives_feature_snapshot_paths_from_artifact_root(self):
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_TARGET_JSON": runtime_target_json(
                        "mega_cap_leader_rotation_top50_balanced"
                    ),
                    "STRATEGY_ARTIFACT_ROOT": tmp_dir,
                },
                clear=True,
            ):
                settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        expected_dir = Path(tmp_dir) / "mega_cap_leader_rotation_top50_balanced"
        self.assertEqual(
            settings.feature_snapshot_path,
            str(expected_dir / "mega_cap_leader_rotation_top50_balanced_feature_snapshot_latest.csv"),
        )
        self.assertEqual(
            settings.feature_snapshot_manifest_path,
            str(expected_dir / "mega_cap_leader_rotation_top50_balanced_feature_snapshot_latest.csv.manifest.json"),
        )

    def test_print_strategy_profile_status_json_matches_registry(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--json"],
            check=True,
            capture_output=True,
            text=True,
        )

        rows = json.loads(result.stdout)
        self.assertEqual(
            [
                {
                    key: row[key]
                    for key in (
                        "canonical_profile",
                        "display_name",
                        "domain",
                        "eligible",
                        "enabled",
                        "platform",
                    )
                }
                for row in rows
            ],
            get_platform_profile_status_matrix(),
        )
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile["global_etf_rotation"]["profile_group"], "direct_runtime_inputs")
        self.assertEqual(by_profile["global_etf_rotation"]["input_mode"], "market_history")
        self.assertFalse(by_profile["global_etf_rotation"]["requires_snapshot_artifacts"])
        self.assertFalse(by_profile["global_etf_rotation"]["requires_strategy_config_path"])
        self.assertEqual(by_profile["tech_communication_pullback_enhancement"]["profile_group"], "snapshot_backed")
        self.assertEqual(by_profile["tech_communication_pullback_enhancement"]["input_mode"], "feature_snapshot")
        self.assertTrue(by_profile["tech_communication_pullback_enhancement"]["requires_snapshot_artifacts"])
        self.assertTrue(by_profile["tech_communication_pullback_enhancement"]["requires_strategy_config_path"])
        self.assertEqual(by_profile["mega_cap_leader_rotation_top50_balanced"]["profile_group"], "snapshot_backed")
        self.assertEqual(by_profile["mega_cap_leader_rotation_top50_balanced"]["input_mode"], "feature_snapshot")
        self.assertTrue(by_profile["mega_cap_leader_rotation_top50_balanced"]["requires_snapshot_artifacts"])
        self.assertFalse(by_profile["mega_cap_leader_rotation_top50_balanced"]["requires_strategy_config_path"])
        self.assertFalse(
            by_profile["russell_1000_multi_factor_defensive"]["requires_strategy_config_path"]
        )

    def test_print_strategy_profile_status_table_contains_expected_headers(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("canonical_profile", result.stdout)
        self.assertIn("display_name", result.stdout)
        self.assertIn("profile_group", result.stdout)
        self.assertIn("input_mode", result.stdout)
        self.assertIn("requires_snapshot_artifacts", result.stdout)
        self.assertIn("soxl_soxx_trend_income", result.stdout)
        self.assertIn("global_etf_rotation", result.stdout)
        self.assertIn("russell_1000_multi_factor_defensive", result.stdout)
        self.assertIn("Global ETF Rotation", result.stdout)
        self.assertIn("Russell 1000 Multi-Factor", result.stdout)
        self.assertIn("Tech/Communication Pullback Enhancement", result.stdout)

    def test_print_strategy_switch_env_plan_for_global_etf_rotation(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "global_etf_rotation",
                "--account-region",
                "sg",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["platform"], "longbridge")
        self.assertEqual(plan["canonical_profile"], "global_etf_rotation")
        self.assertEqual(plan["set_env"]["ACCOUNT_REGION"], "SG")
        self.assertEqual(plan["set_env"]["ACCOUNT_PREFIX"], "SG")
        self.assertEqual(plan["runtime_target"]["platform_id"], "longbridge")
        self.assertEqual(plan["runtime_target"]["strategy_profile"], "global_etf_rotation")
        self.assertEqual(plan["runtime_target"]["deployment_selector"], "SG")
        self.assertEqual(plan["runtime_target"]["account_scope"], "SG")
        self.assertEqual(plan["runtime_target"]["service_name"], "longbridge-quant-sg-service")
        self.assertEqual(plan["runtime_target"]["execution_mode"], "live")
        self.assertEqual(plan["profile_group"], "direct_runtime_inputs")
        self.assertEqual(plan["input_mode"], "market_history")
        self.assertFalse(plan["requires_snapshot_artifacts"])
        self.assertFalse(plan["requires_strategy_config_path"])
        self.assertIn("LONGBRIDGE_FEATURE_SNAPSHOT_PATH", plan["remove_if_present"])

    def test_print_strategy_switch_env_plan_for_russell(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "russell_1000_multi_factor_defensive",
                "--account-region",
                "hk",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["canonical_profile"], "russell_1000_multi_factor_defensive")
        self.assertEqual(plan["set_env"]["ACCOUNT_REGION"], "HK")
        self.assertEqual(plan["set_env"]["ACCOUNT_PREFIX"], "HK")
        self.assertEqual(plan["profile_group"], "snapshot_backed")
        self.assertEqual(plan["input_mode"], "feature_snapshot")
        self.assertTrue(plan["requires_snapshot_artifacts"])
        self.assertFalse(plan["requires_snapshot_manifest_path"])
        self.assertFalse(plan["requires_strategy_config_path"])
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_PATH"], "<required>")
        self.assertNotIn("LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH", plan["set_env"])
        self.assertIn(
            "LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH",
            plan["remove_if_present"],
        )
        self.assertIn("LONGBRIDGE_STRATEGY_CONFIG_PATH", plan["remove_if_present"])


    def test_print_strategy_switch_env_plan_rejects_archived_mega_cap_dynamic_top20(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "mega_cap_leader_rotation_dynamic_top20",
                "--account-region",
                "hk",
                "--json",
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported STRATEGY_PROFILE", result.stderr)

    def test_print_strategy_switch_env_plan_for_mega_cap_top50_balanced(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "mega_cap_leader_rotation_top50_balanced",
                "--account-region",
                "hk",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["canonical_profile"], "mega_cap_leader_rotation_top50_balanced")
        self.assertEqual(plan["set_env"]["ACCOUNT_REGION"], "HK")
        self.assertEqual(plan["set_env"]["ACCOUNT_PREFIX"], "HK")
        self.assertEqual(plan["profile_group"], "snapshot_backed")
        self.assertEqual(plan["input_mode"], "feature_snapshot")
        self.assertTrue(plan["requires_snapshot_artifacts"])
        self.assertFalse(plan["requires_strategy_config_path"])
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_PATH"], "<required>")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"], "<required>")
        self.assertEqual(
            plan["hints"]["feature_snapshot_filename"],
            "mega_cap_leader_rotation_top50_balanced_feature_snapshot_latest.csv",
        )

    def test_print_strategy_switch_env_plan_rejects_archived_dynamic_mega_leveraged_pullback_sg(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "dynamic_mega_leveraged_pullback",
                "--account-region",
                "sg",
                "--json",
            ],
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported STRATEGY_PROFILE", result.stderr)

    def test_print_strategy_switch_env_plan_for_tech_uses_bundled_config_by_default(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "tech_communication_pullback_enhancement",
                "--account-region",
                "hk",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["canonical_profile"], "tech_communication_pullback_enhancement")
        self.assertEqual(plan["config_source_policy"], "bundled_or_env")
        self.assertTrue(plan["requires_strategy_config_path"])
        self.assertEqual(plan["runtime_execution_window_trading_days"], 1)
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_PATH"], "<required>")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"], "<required>")
        self.assertNotIn("LONGBRIDGE_STRATEGY_CONFIG_PATH", plan["set_env"])
        self.assertIn("LONGBRIDGE_STRATEGY_CONFIG_PATH", plan["remove_if_present"])


if __name__ == "__main__":
    unittest.main()
