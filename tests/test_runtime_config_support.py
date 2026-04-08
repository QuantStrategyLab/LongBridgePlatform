import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
QPK_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))

from runtime_config_support import (
    DEFAULT_ACCOUNT_REGION,
    DEFAULT_LONGPORT_SECRET_NAME,
    DEFAULT_STRATEGY_PROFILE,
    infer_account_region,
    load_platform_runtime_settings,
)
from strategy_registry import (
    LONGBRIDGE_PLATFORM,
    US_EQUITY_DOMAIN,
    get_eligible_profiles_for_platform,
    get_platform_profile_matrix,
    get_supported_profiles_for_platform,
)


class RuntimeConfigSupportTests(unittest.TestCase):
    def test_load_platform_runtime_settings_uses_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.project_id, "project-1")
        self.assertEqual(settings.secret_name, DEFAULT_LONGPORT_SECRET_NAME)
        self.assertEqual(settings.account_prefix, "DEFAULT")
        self.assertEqual(settings.strategy_profile, DEFAULT_STRATEGY_PROFILE)
        self.assertEqual(settings.strategy_display_name, "SOXL/SOXX Semiconductor Trend Income")
        self.assertEqual(settings.strategy_domain, US_EQUITY_DOMAIN)
        self.assertEqual(settings.account_region, DEFAULT_ACCOUNT_REGION)
        self.assertEqual(settings.notify_lang, "en")
        self.assertIsNone(settings.tg_token)
        self.assertIsNone(settings.tg_chat_id)
        self.assertFalse(settings.dry_run_only)
        self.assertIsNone(settings.feature_snapshot_path)
        self.assertIsNone(settings.strategy_config_path)

    def test_platform_supported_profiles_are_filtered_by_registry(self):
        self.assertEqual(
            get_supported_profiles_for_platform(LONGBRIDGE_PLATFORM),
            frozenset({"hybrid_growth_income", "semiconductor_rotation_income", "tech_pullback_cash_buffer"}),
        )

    def test_platform_eligible_profiles_are_exposed_by_capability_matrix(self):
        self.assertEqual(
            get_eligible_profiles_for_platform(LONGBRIDGE_PLATFORM),
            frozenset({"hybrid_growth_income", "semiconductor_rotation_income", "tech_pullback_cash_buffer"}),
        )

    def test_dry_run_only_is_loaded_from_env(self):
        with patch.dict(os.environ, {"LONGBRIDGE_DRY_RUN_ONLY": "true"}, clear=True):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertTrue(settings.dry_run_only)

    def test_accepts_human_readable_alias(self):
        with patch.dict(os.environ, {"STRATEGY_PROFILE": "semiconductor_trend_income"}, clear=True):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, DEFAULT_STRATEGY_PROFILE)

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
        with patch.dict(os.environ, {"STRATEGY_PROFILE": "balanced_income"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_platform_profile_matrix_marks_default(self):
        rows = get_platform_profile_matrix()
        by_profile = {row["canonical_profile"]: row for row in rows}
        self.assertEqual(by_profile[DEFAULT_STRATEGY_PROFILE]["display_name"], "SOXL/SOXX Semiconductor Trend Income")
        self.assertTrue(by_profile[DEFAULT_STRATEGY_PROFILE]["is_default"])

    def test_loads_feature_snapshot_env_for_tech_profile(self):
        with patch.dict(
            os.environ,
            {
                "STRATEGY_PROFILE": "tech_pullback_cash_buffer",
                "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": "gs://bucket/tech.csv",
                "LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH": "gs://bucket/tech.csv.manifest.json",
                "LONGBRIDGE_STRATEGY_CONFIG_PATH": "/workspace/configs/tech.json",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "tech_pullback_cash_buffer")
        self.assertEqual(settings.feature_snapshot_path, "gs://bucket/tech.csv")
        self.assertEqual(settings.feature_snapshot_manifest_path, "gs://bucket/tech.csv.manifest.json")
        self.assertEqual(settings.strategy_config_path, "/workspace/configs/tech.json")
        self.assertEqual(settings.strategy_config_source, "env")


if __name__ == "__main__":
    unittest.main()
