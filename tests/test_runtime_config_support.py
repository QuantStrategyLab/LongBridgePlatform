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
from strategy_registry import LONGBRIDGE_PLATFORM, US_EQUITY_DOMAIN, get_supported_profiles_for_platform


class RuntimeConfigSupportTests(unittest.TestCase):
    def test_load_platform_runtime_settings_uses_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.project_id, "project-1")
        self.assertEqual(settings.secret_name, DEFAULT_LONGPORT_SECRET_NAME)
        self.assertEqual(settings.account_prefix, "DEFAULT")
        self.assertEqual(settings.service_name, "longbridge-quant-semiconductor-rotation-income")
        self.assertEqual(settings.strategy_profile, DEFAULT_STRATEGY_PROFILE)
        self.assertEqual(settings.strategy_domain, US_EQUITY_DOMAIN)
        self.assertEqual(settings.account_region, DEFAULT_ACCOUNT_REGION)
        self.assertEqual(settings.notify_lang, "en")
        self.assertIsNone(settings.tg_token)
        self.assertIsNone(settings.tg_chat_id)

    def test_platform_supported_profiles_are_filtered_by_registry(self):
        self.assertEqual(
            get_supported_profiles_for_platform(LONGBRIDGE_PLATFORM),
            frozenset({DEFAULT_STRATEGY_PROFILE}),
        )

    def test_account_region_prefers_explicit_env(self):
        region = infer_account_region(
            "sg",
            account_prefix="HK",
            service_name="longbridge-quant-semiconductor-rotation-income-hk",
        )
        self.assertEqual(region, "SG")

    def test_account_region_falls_back_to_account_prefix(self):
        region = infer_account_region(
            None,
            account_prefix="hk",
            service_name="longbridge-quant",
        )
        self.assertEqual(region, "HK")

    def test_account_region_falls_back_to_service_name_suffix(self):
        region = infer_account_region(
            None,
            account_prefix="",
            service_name="longbridge-quant-semiconductor-rotation-income-sg",
        )
        self.assertEqual(region, "SG")

    def test_unsupported_strategy_profile_fails_fast(self):
        with patch.dict(os.environ, {"STRATEGY_PROFILE": "balanced_income"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")


if __name__ == "__main__":
    unittest.main()
