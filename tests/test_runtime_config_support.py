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
if (QPK_SRC / "quant_platform_kit" / "common" / "runtime_config.py").exists() and str(QPK_SRC) not in sys.path:
    sys.path.insert(0, str(QPK_SRC))
SCRIPT_PATH = ROOT / "scripts" / "print_strategy_profile_status.py"
SWITCH_PLAN_SCRIPT_PATH = ROOT / "scripts" / "print_strategy_switch_env_plan.py"

from runtime_config_support import (
    DEFAULT_ACCOUNT_REGION,
    DEFAULT_LONGPORT_SECRET_NAME,
    DEFAULT_MARKET,
    DEFAULT_MARKET_CALENDAR,
    DEFAULT_MARKET_TIMEZONE,
    DEFAULT_RESERVED_CASH_FLOOR_USD,
    DEFAULT_RESERVED_CASH_RATIO,
    DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
    DEFAULT_SYMBOL_SUFFIX,
    DEFAULT_TRADING_CURRENCY,
    HK_MARKET,
    HK_MARKET_CALENDAR,
    HK_MARKET_TIMEZONE,
    HK_SYMBOL_SUFFIX,
    HK_TRADING_CURRENCY,
    _resolve_non_negative_float_env,
    _resolve_ratio_env,
    infer_account_region,
    infer_market,
    load_platform_runtime_settings,
)
from strategy_registry import (
    HK_EQUITY_DOMAIN,
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
        "russell_top50_leader_rotation",
        "tqqq_growth_income",
        "soxl_soxx_trend_income",
        "ibit_smart_dca",
    }
)
OPTIONAL_LONGBRIDGE_PROFILES = frozenset({"global_etf_confidence_vol_gate"})
HK_RUNTIME_ENABLED_PROFILES = frozenset(
    {
        "hk_global_etf_tactical_rotation",
        "hk_low_vol_dividend_quality_snapshot",
    }
)
HK_DISABLED_PROFILES = frozenset(
    {
        "hk_blue_chip_leader_rotation",
        "hk_index_mean_reversion",
        "hk_etf_regime_rotation",
    }
)


def expected_longbridge_enabled_profiles(actual_profiles) -> frozenset[str]:
    actual = frozenset(actual_profiles)
    return BASE_LONGBRIDGE_PROFILES | HK_RUNTIME_ENABLED_PROFILES | (OPTIONAL_LONGBRIDGE_PROFILES & actual)


def expected_longbridge_profiles(actual_profiles) -> frozenset[str]:
    return expected_longbridge_enabled_profiles(actual_profiles)


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
        self.assertEqual(settings.market, DEFAULT_MARKET)
        self.assertEqual(settings.market_calendar, DEFAULT_MARKET_CALENDAR)
        self.assertEqual(settings.market_timezone, DEFAULT_MARKET_TIMEZONE)
        self.assertEqual(settings.symbol_suffix, DEFAULT_SYMBOL_SUFFIX)
        self.assertEqual(settings.trading_currency, DEFAULT_TRADING_CURRENCY)
        self.assertEqual(settings.notify_lang, "en")
        self.assertIsNone(settings.tg_token)
        self.assertIsNone(settings.tg_chat_id)
        self.assertFalse(settings.dry_run_only)
        self.assertEqual(
            settings.safe_haven_cash_substitute_threshold_usd,
            DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
        )
        self.assertEqual(settings.min_order_notional_usd, 100.0)
        self.assertEqual(settings.reserved_cash_floor_usd, DEFAULT_RESERVED_CASH_FLOOR_USD)
        self.assertEqual(settings.reserved_cash_ratio, DEFAULT_RESERVED_CASH_RATIO)
        self.assertFalse(settings.debug_position_snapshot)
        self.assertIsNotNone(settings.runtime_target)
        self.assertEqual(settings.runtime_target.platform_id, "longbridge")
        self.assertEqual(settings.runtime_target.execution_mode, "live")
        self.assertTrue(settings.runtime_target_enabled)
        self.assertIsNone(settings.income_threshold_usd)
        self.assertIsNone(settings.qqqi_income_ratio)
        self.assertIsNone(settings.feature_snapshot_path)
        self.assertIsNone(settings.strategy_config_path)
        self.assertIsNone(settings.strategy_plugin_mounts_json)
        self.assertEqual(settings.strategy_plugin_alert_channels, ())
        self.assertEqual(settings.strategy_plugin_alert_email_recipients, ())
        self.assertIsNone(settings.strategy_plugin_alert_email_sender_email)
        self.assertIsNone(settings.strategy_plugin_alert_email_sender_password)
        self.assertIsNone(settings.strategy_plugin_alert_email_smtp_host)
        self.assertIsNone(settings.strategy_plugin_alert_email_smtp_port)
        self.assertIsNone(settings.strategy_plugin_alert_email_smtp_security)
        self.assertEqual(settings.strategy_plugin_alert_sms_recipients, ())
        self.assertIsNone(settings.strategy_plugin_alert_sms_provider)
        self.assertIsNone(settings.strategy_plugin_alert_sms_account_id)
        self.assertIsNone(settings.strategy_plugin_alert_sms_auth_token)
        self.assertIsNone(settings.strategy_plugin_alert_sms_sender)
        self.assertIsNone(settings.strategy_plugin_alert_sms_messaging_service_id)
        self.assertIsNone(settings.strategy_plugin_alert_sms_api_base_url)
        self.assertIsNone(settings.strategy_plugin_alert_sms_body_max_chars)
        self.assertEqual(settings.strategy_plugin_alert_push_recipients, ())
        self.assertIsNone(settings.strategy_plugin_alert_push_provider)
        self.assertIsNone(settings.strategy_plugin_alert_push_app_token)
        self.assertIsNone(settings.strategy_plugin_alert_push_access_token)
        self.assertIsNone(settings.strategy_plugin_alert_push_api_base_url)
        self.assertIsNone(settings.strategy_plugin_alert_push_device)
        self.assertIsNone(settings.strategy_plugin_alert_push_priority)
        self.assertIsNone(settings.strategy_plugin_alert_push_tags)
        self.assertIsNone(settings.strategy_plugin_alert_push_body_max_chars)
        self.assertEqual(settings.strategy_plugin_alert_telegram_chat_ids, ())
        self.assertIsNone(settings.strategy_plugin_alert_telegram_bot_token)
        self.assertIsNone(settings.strategy_plugin_alert_telegram_api_base_url)
        self.assertIsNone(settings.strategy_plugin_alert_telegram_parse_mode)
        self.assertIsNone(settings.strategy_plugin_alert_telegram_disable_web_page_preview)
        self.assertIsNone(settings.strategy_plugin_alert_telegram_body_max_chars)

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
        self.assertEqual(profiles, expected_longbridge_enabled_profiles(profiles))
        for profile in HK_DISABLED_PROFILES:
            self.assertNotIn(profile, profiles)

    def test_platform_policy_accepts_future_hk_equity_domain(self):
        from strategy_registry import PLATFORM_SUPPORTED_DOMAINS

        self.assertIn(HK_EQUITY_DOMAIN, PLATFORM_SUPPORTED_DOMAINS[LONGBRIDGE_PLATFORM])
        self.assertIn(US_EQUITY_DOMAIN, PLATFORM_SUPPORTED_DOMAINS[LONGBRIDGE_PLATFORM])

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

    def test_runtime_target_enabled_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "RUNTIME_TARGET_ENABLED": "false",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertFalse(settings.runtime_target_enabled)

    def test_invalid_runtime_target_enabled_is_rejected(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "RUNTIME_TARGET_ENABLED": "maybe",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "RUNTIME_TARGET_ENABLED"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

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

    def test_safe_haven_cash_substitute_threshold_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD": "750",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.safe_haven_cash_substitute_threshold_usd, 750.0)

    def test_min_order_notional_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_MIN_ORDER_NOTIONAL_USD": "150",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.min_order_notional_usd, 150.0)

    def test_reserved_cash_policy_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_MIN_RESERVED_CASH_USD": "250",
                "LONGBRIDGE_RESERVED_CASH_RATIO": "0.025",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.reserved_cash_floor_usd, 250.0)
        self.assertEqual(settings.reserved_cash_ratio, 0.025)

    def test_reserved_cash_ratio_rejects_invalid_env(self):
        with patch.dict(os.environ, {"LONGBRIDGE_RESERVED_CASH_RATIO": "1.25"}, clear=True):
            with self.assertRaisesRegex(ValueError, "LONGBRIDGE_RESERVED_CASH_RATIO"):
                _resolve_ratio_env("LONGBRIDGE_RESERVED_CASH_RATIO", default=0.0)

    def test_reserved_cash_floor_rejects_non_finite_env(self):
        for raw_value in ("nan", "inf", "-inf"):
            with self.subTest(raw_value=raw_value):
                with patch.dict(
                    os.environ,
                    {"LONGBRIDGE_MIN_RESERVED_CASH_USD": raw_value},
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "LONGBRIDGE_MIN_RESERVED_CASH_USD must be finite",
                    ):
                        _resolve_non_negative_float_env(
                            "LONGBRIDGE_MIN_RESERVED_CASH_USD",
                            default=0.0,
                        )

    def test_reserved_cash_ratio_rejects_non_finite_env(self):
        for raw_value in ("nan", "inf", "-inf"):
            with self.subTest(raw_value=raw_value):
                with patch.dict(
                    os.environ,
                    {"LONGBRIDGE_RESERVED_CASH_RATIO": raw_value},
                    clear=True,
                ):
                    with self.assertRaisesRegex(
                        ValueError,
                        "LONGBRIDGE_RESERVED_CASH_RATIO must be finite",
                    ):
                        _resolve_ratio_env("LONGBRIDGE_RESERVED_CASH_RATIO", default=0.0)

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

    def test_strategy_plugin_alert_email_config_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS": "alerts@example.com; voice@example.com",
                "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL": "sender@example.com",
                "STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD": "secret",
                "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST": "smtp.example.com",
                "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT": "587",
                "STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY": "starttls",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_plugin_alert_email_recipients, ("alerts@example.com", "voice@example.com"))
        self.assertEqual(settings.strategy_plugin_alert_email_sender_email, "sender@example.com")
        self.assertEqual(settings.strategy_plugin_alert_email_sender_password, "secret")
        self.assertEqual(settings.strategy_plugin_alert_email_smtp_host, "smtp.example.com")
        self.assertEqual(settings.strategy_plugin_alert_email_smtp_port, "587")
        self.assertEqual(settings.strategy_plugin_alert_email_smtp_security, "starttls")

    def test_strategy_plugin_alert_sms_config_is_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS": "+15165480265;(516) 548-0265",
                "STRATEGY_PLUGIN_ALERT_SMS_PROVIDER": "twilio",
                "STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID": "AC123",
                "STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN": "secret",
                "STRATEGY_PLUGIN_ALERT_SMS_SENDER": "+15551234567",
                "STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID": "MG123",
                "STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL": "https://twilio.example.test",
                "STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS": "160",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_plugin_alert_sms_recipients, ("+15165480265", "(516) 548-0265"))
        self.assertEqual(settings.strategy_plugin_alert_sms_provider, "twilio")
        self.assertEqual(settings.strategy_plugin_alert_sms_account_id, "AC123")
        self.assertEqual(settings.strategy_plugin_alert_sms_auth_token, "secret")
        self.assertEqual(settings.strategy_plugin_alert_sms_sender, "+15551234567")
        self.assertEqual(settings.strategy_plugin_alert_sms_messaging_service_id, "MG123")
        self.assertEqual(settings.strategy_plugin_alert_sms_api_base_url, "https://twilio.example.test")
        self.assertEqual(settings.strategy_plugin_alert_sms_body_max_chars, "160")

    def test_strategy_plugin_alert_channels_and_push_config_are_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "STRATEGY_PLUGIN_ALERT_CHANNELS": "email;push;telegram",
                "STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS": "risk-topic; backup-topic",
                "STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER": "ntfy",
                "STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN": "app-token",
                "STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN": "access-token",
                "STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL": "https://ntfy.example.test",
                "STRATEGY_PLUGIN_ALERT_PUSH_DEVICE": "iphone",
                "STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY": "5",
                "STRATEGY_PLUGIN_ALERT_PUSH_TAGS": "warning",
                "STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS": "300",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS": "12345; @risk_channel",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN": "telegram-token",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_API_BASE_URL": "https://telegram.example.test",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_PARSE_MODE": "HTML",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW": "false",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS": "900",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_plugin_alert_channels, ("email", "push", "telegram"))
        self.assertEqual(settings.strategy_plugin_alert_push_recipients, ("risk-topic", "backup-topic"))
        self.assertEqual(settings.strategy_plugin_alert_push_provider, "ntfy")
        self.assertEqual(settings.strategy_plugin_alert_push_app_token, "app-token")
        self.assertEqual(settings.strategy_plugin_alert_push_access_token, "access-token")
        self.assertEqual(settings.strategy_plugin_alert_push_api_base_url, "https://ntfy.example.test")
        self.assertEqual(settings.strategy_plugin_alert_push_device, "iphone")
        self.assertEqual(settings.strategy_plugin_alert_push_priority, "5")
        self.assertEqual(settings.strategy_plugin_alert_push_tags, "warning")
        self.assertEqual(settings.strategy_plugin_alert_push_body_max_chars, "300")
        self.assertEqual(settings.strategy_plugin_alert_telegram_chat_ids, ("12345", "@risk_channel"))
        self.assertEqual(settings.strategy_plugin_alert_telegram_bot_token, "telegram-token")
        self.assertEqual(
            settings.strategy_plugin_alert_telegram_api_base_url,
            "https://telegram.example.test",
        )
        self.assertEqual(settings.strategy_plugin_alert_telegram_parse_mode, "HTML")
        self.assertEqual(settings.strategy_plugin_alert_telegram_disable_web_page_preview, "false")
        self.assertEqual(settings.strategy_plugin_alert_telegram_body_max_chars, "900")

    def test_income_layer_overrides_are_loaded_from_env(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json("tqqq_growth_income"),
                "INCOME_THRESHOLD_USD": "100000",
                "QQQI_INCOME_RATIO": "0.5",
                "INCOME_LAYER_ENABLED": "false",
                "INCOME_LAYER_START_USD": "250000",
                "INCOME_LAYER_MAX_RATIO": "0.25",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.strategy_profile, "tqqq_growth_income")
        self.assertEqual(settings.income_threshold_usd, 100000.0)
        self.assertEqual(settings.qqqi_income_ratio, 0.5)
        self.assertFalse(settings.income_layer_enabled)
        self.assertEqual(settings.income_layer_start_usd, 250000.0)
        self.assertEqual(settings.income_layer_max_ratio, 0.25)

    def test_tech_runtime_execution_window_override_rejects_research_only_profile(self):
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
            with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

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

    def test_rejects_invalid_income_layer_max_ratio(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json("tqqq_growth_income"),
                "INCOME_LAYER_MAX_RATIO": "1.5",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "INCOME_LAYER_MAX_RATIO"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_rejects_invalid_income_layer_start_usd(self):
        with patch.dict(
            os.environ,
            {
                "RUNTIME_TARGET_JSON": runtime_target_json("tqqq_growth_income"),
                "INCOME_LAYER_START_USD": "-1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "INCOME_LAYER_START_USD"):
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

    def test_market_defaults_to_hk_for_hk_account_region(self):
        market = infer_market(None, account_region="hk")
        self.assertEqual(market, HK_MARKET)

        with patch.dict(
            os.environ,
            {
                "ACCOUNT_REGION": "hk",
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.market, HK_MARKET)
        self.assertEqual(settings.market_calendar, HK_MARKET_CALENDAR)
        self.assertEqual(settings.market_timezone, HK_MARKET_TIMEZONE)
        self.assertEqual(settings.symbol_suffix, HK_SYMBOL_SUFFIX)
        self.assertEqual(settings.trading_currency, HK_TRADING_CURRENCY)

    def test_market_env_overrides_region_defaults(self):
        with patch.dict(
            os.environ,
            {
                "ACCOUNT_REGION": "hk",
                "RUNTIME_TARGET_JSON": runtime_target_json(SAMPLE_STRATEGY_PROFILE),
                "LONGBRIDGE_MARKET": "US",
                "LONGBRIDGE_MARKET_CALENDAR": "XNYS",
                "LONGBRIDGE_MARKET_TIMEZONE": "Etc/UTC",
                "LONGBRIDGE_SYMBOL_SUFFIX": "US",
                "LONGBRIDGE_TRADING_CURRENCY": "usd",
            },
            clear=True,
        ):
            settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        self.assertEqual(settings.market, DEFAULT_MARKET)
        self.assertEqual(settings.market_calendar, "XNYS")
        self.assertEqual(settings.market_timezone, "Etc/UTC")
        self.assertEqual(settings.symbol_suffix, DEFAULT_SYMBOL_SUFFIX)
        self.assertEqual(settings.trading_currency, DEFAULT_TRADING_CURRENCY)

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
        self.assertNotIn("tech_communication_pullback_enhancement", by_profile)
        self.assertTrue(by_profile["russell_top50_leader_rotation"]["eligible"])
        self.assertTrue(by_profile["russell_top50_leader_rotation"]["enabled"])
        self.assertEqual(
            by_profile["russell_top50_leader_rotation"]["display_name"],
            "Russell Top50 Leader Rotation",
        )
        self.assertEqual(
            by_profile["hk_global_etf_tactical_rotation"],
            {
                "canonical_profile": "hk_global_etf_tactical_rotation",
                "display_name": "HK Global ETF Tactical Rotation",
                "domain": "hk_equity",
                "eligible": True,
                "enabled": True,
                "platform": "longbridge",
            },
        )
        for profile in HK_DISABLED_PROFILES:
            self.assertNotIn(profile, by_profile)

    def test_loads_feature_snapshot_env_rejects_research_only_tech_profile(self):
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
            with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_rejects_disabled_hk_profiles(self):
        for profile in sorted(HK_DISABLED_PROFILES):
            with self.subTest(profile=profile):
                with patch.dict(
                    os.environ,
                    {
                        "RUNTIME_TARGET_JSON": runtime_target_json(profile),
                        "ACCOUNT_REGION": "HK",
                        "LONGBRIDGE_FEATURE_SNAPSHOT_PATH": "gs://bucket/hk.csv",
                        "LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH": "gs://bucket/hk.csv.manifest.json",
                    },
                    clear=True,
                ):
                    with self.assertRaisesRegex(ValueError, "Unsupported STRATEGY_PROFILE"):
                        load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

    def test_accepts_runtime_enabled_hk_profiles(self):
        expected_names = {
            "hk_global_etf_tactical_rotation": "HK Global ETF Tactical Rotation",
        }
        for profile, display_name in expected_names.items():
            with self.subTest(profile=profile):
                with patch.dict(
                    os.environ,
                    {
                        "RUNTIME_TARGET_JSON": runtime_target_json(profile),
                        "ACCOUNT_REGION": "HK",
                    },
                    clear=True,
                ):
                    settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

                self.assertEqual(settings.strategy_profile, profile)
                self.assertEqual(settings.strategy_display_name, display_name)
                self.assertEqual(settings.strategy_domain, "hk_equity")
                self.assertEqual(settings.market, HK_MARKET)
                self.assertEqual(settings.market_calendar, HK_MARKET_CALENDAR)
                self.assertEqual(settings.market_timezone, HK_MARKET_TIMEZONE)
                self.assertEqual(settings.symbol_suffix, HK_SYMBOL_SUFFIX)
                self.assertEqual(settings.trading_currency, HK_TRADING_CURRENCY)

    def test_derives_feature_snapshot_paths_from_artifact_root(self):
        with TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "RUNTIME_TARGET_JSON": runtime_target_json(
                        "russell_top50_leader_rotation"
                    ),
                    "STRATEGY_ARTIFACT_ROOT": tmp_dir,
                },
                clear=True,
            ):
                settings = load_platform_runtime_settings(project_id_resolver=lambda: "project-1")

        expected_dir = Path(tmp_dir) / "russell_top50_leader_rotation"
        self.assertEqual(
            settings.feature_snapshot_path,
            str(expected_dir / "russell_top50_leader_rotation_feature_snapshot_latest.csv"),
        )
        self.assertEqual(
            settings.feature_snapshot_manifest_path,
            str(expected_dir / "russell_top50_leader_rotation_feature_snapshot_latest.csv.manifest.json"),
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
                        "display_name_zh",
                        "domain",
                        "eligible",
                        "enabled",
                        "platform",
                    )
                    if key in row
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
        self.assertNotIn("tech_communication_pullback_enhancement", by_profile)
        self.assertEqual(by_profile["russell_top50_leader_rotation"]["profile_group"], "snapshot_backed")
        self.assertEqual(
            by_profile["russell_top50_leader_rotation"]["display_name_zh"],
            "罗素 Top50 领涨轮动",
        )
        self.assertEqual(by_profile["russell_top50_leader_rotation"]["input_mode"], "feature_snapshot")
        self.assertTrue(by_profile["russell_top50_leader_rotation"]["requires_snapshot_artifacts"])
        self.assertFalse(by_profile["russell_top50_leader_rotation"]["requires_strategy_config_path"])
        for profile in (
            "hk_index_mean_reversion",
            "hk_etf_regime_rotation",
            "hk_blue_chip_leader_rotation",
            "hk_dividend_gold_defensive_rotation",
        ):
            self.assertNotIn(profile, by_profile)
        for profile in HK_RUNTIME_ENABLED_PROFILES - {"hk_low_vol_dividend_quality_snapshot"}:
            self.assertEqual(by_profile[profile]["profile_group"], "direct_runtime_inputs")
            self.assertEqual(by_profile[profile]["input_mode"], "market_history")
            self.assertFalse(by_profile[profile]["requires_snapshot_artifacts"])
            self.assertFalse(by_profile[profile]["requires_snapshot_manifest_path"])
            self.assertFalse(by_profile[profile]["requires_strategy_config_path"])
        self.assertEqual(by_profile["hk_low_vol_dividend_quality_snapshot"]["profile_group"], "snapshot_backed")
        self.assertEqual(by_profile["hk_low_vol_dividend_quality_snapshot"]["input_mode"], "feature_snapshot")
        self.assertTrue(by_profile["hk_low_vol_dividend_quality_snapshot"]["requires_snapshot_artifacts"])
        self.assertTrue(by_profile["hk_low_vol_dividend_quality_snapshot"]["requires_snapshot_manifest_path"])
        self.assertFalse(by_profile["hk_low_vol_dividend_quality_snapshot"]["requires_strategy_config_path"])

    def test_print_strategy_profile_status_table_contains_expected_headers(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH)],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("canonical_profile", result.stdout)
        self.assertIn("display_name", result.stdout)
        self.assertIn("display_name_zh", result.stdout)
        self.assertIn("profile_group", result.stdout)
        self.assertIn("input_mode", result.stdout)
        self.assertIn("requires_snapshot_artifacts", result.stdout)
        self.assertIn("soxl_soxx_trend_income", result.stdout)
        self.assertIn("global_etf_rotation", result.stdout)
        self.assertIn("hk_global_etf_tactical_rotation", result.stdout)
        self.assertNotIn("hk_dividend_gold_defensive_rotation", result.stdout)
        self.assertIn("Global ETF Rotation", result.stdout)
        self.assertIn("HK Global ETF Tactical Rotation", result.stdout)
        self.assertNotIn("HK Dividend-Gold Defensive Rotation", result.stdout)
        self.assertIn("Russell Top50 Leader Rotation", result.stdout)
        self.assertIn("罗素 Top50 领涨轮动", result.stdout)
        self.assertNotIn("Tech/Communication Pullback Enhancement", result.stdout)
        self.assertNotIn("hk_blue_chip_leader_rotation", result.stdout)
        self.assertNotIn("hk_index_mean_reversion", result.stdout)
        self.assertNotIn("hk_etf_regime_rotation", result.stdout)

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
        self.assertIn("LONGBRIDGE_MIN_RESERVED_CASH_USD", plan["optional_env"])
        self.assertIn("LONGBRIDGE_RESERVED_CASH_RATIO", plan["optional_env"])
        self.assertIn("LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_SIGNAL_HANDOFF_INDEX_URI", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_SIGNAL_CACHE_DIR", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_SIGNAL_REQUIRED", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_CALENDAR", plan["optional_env"])
        self.assertIn("LONGBRIDGE_MARKET_TIMEZONE", plan["optional_env"])
        self.assertIn("LONGBRIDGE_SYMBOL_SUFFIX", plan["optional_env"])
        self.assertIn("LONGBRIDGE_TRADING_CURRENCY", plan["optional_env"])
        self.assertIn("LONGBRIDGE_FEATURE_SNAPSHOT_PATH", plan["remove_if_present"])

    def test_print_strategy_switch_env_plan_for_hk_global_etf_dry_run(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "hk_global_etf_tactical_rotation",
                "--account-region",
                "hk",
                "--dry-run-only",
                "--deployment-selector",
                "hk-verify",
                "--account-scope",
                "hk-verify",
                "--service-name",
                "longbridge-quant-hk-verify-service",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["platform"], "longbridge")
        self.assertEqual(plan["canonical_profile"], "hk_global_etf_tactical_rotation")
        self.assertEqual(plan["domain"], HK_EQUITY_DOMAIN)
        self.assertEqual(plan["set_env"]["ACCOUNT_REGION"], "HK")
        self.assertEqual(plan["set_env"]["ACCOUNT_PREFIX"], "HK")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_DRY_RUN_ONLY"], "true")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_MARKET"], HK_MARKET)
        self.assertEqual(plan["set_env"]["LONGBRIDGE_MARKET_CALENDAR"], HK_MARKET_CALENDAR)
        self.assertEqual(plan["set_env"]["LONGBRIDGE_MARKET_TIMEZONE"], HK_MARKET_TIMEZONE)
        self.assertEqual(plan["set_env"]["LONGBRIDGE_SYMBOL_SUFFIX"], HK_SYMBOL_SUFFIX)
        self.assertEqual(plan["set_env"]["LONGBRIDGE_TRADING_CURRENCY"], HK_TRADING_CURRENCY)
        self.assertTrue(plan["runtime_target"]["dry_run_only"])
        self.assertEqual(plan["runtime_target"]["execution_mode"], "paper")
        self.assertEqual(plan["runtime_target"]["deployment_selector"], "hk-verify")
        self.assertEqual(plan["runtime_target"]["account_scope"], "hk-verify")
        self.assertEqual(plan["runtime_target"]["service_name"], "longbridge-quant-hk-verify-service")
        runtime_target_env = json.loads(plan["set_env"]["RUNTIME_TARGET_JSON"])
        self.assertTrue(runtime_target_env["dry_run_only"])
        self.assertEqual(runtime_target_env["execution_mode"], "paper")
        self.assertEqual(plan["profile_group"], "direct_runtime_inputs")
        self.assertEqual(plan["input_mode"], "market_history")
        self.assertFalse(plan["requires_snapshot_artifacts"])
        self.assertIn("LONGBRIDGE_FEATURE_SNAPSHOT_PATH", plan["remove_if_present"])
        self.assertTrue(plan["dry_run_plan"]["dry_run_only"])
        self.assertTrue(plan["dry_run_plan"]["verify_only"])
        self.assertEqual(
            plan["dry_run_plan"]["workflow_dispatch"],
            {
                "workflow": "sync-cloud-run-env.yml",
                "target": "hk-verify",
                "cloud_run_service": "longbridge-quant-hk-verify-service",
                "deploy_image": True,
                "sync_env": True,
            },
        )
        self.assertTrue(any("lot-size" in check for check in plan["dry_run_plan"]["checks"]))
        self.assertFalse(
            any(
                "Cloud Run" in action and "deploy" in action
                for action in plan["dry_run_plan"]["blocked_actions"]
            )
        )
        self.assertTrue(
            any(
                "live" in action and "orders" in action
                for action in plan["dry_run_plan"]["blocked_actions"]
            )
        )

    def test_print_strategy_switch_env_plan_rejects_retired_russell_defensive(self):
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
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported STRATEGY_PROFILE", result.stderr)


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
                "russell_top50_leader_rotation",
                "--account-region",
                "hk",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["canonical_profile"], "russell_top50_leader_rotation")
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
            "russell_top50_leader_rotation_feature_snapshot_latest.csv",
        )

    def test_print_strategy_switch_env_plan_for_hk_low_vol_dividend_quality_snapshot(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SWITCH_PLAN_SCRIPT_PATH),
                "--profile",
                "hk_low_vol_dividend_quality_snapshot",
                "--account-region",
                "hk",
                "--dry-run-only",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        plan = json.loads(result.stdout)
        self.assertEqual(plan["canonical_profile"], "hk_low_vol_dividend_quality_snapshot")
        self.assertTrue(plan["enabled"])
        self.assertEqual(plan["profile_group"], "snapshot_backed")
        self.assertEqual(plan["input_mode"], "feature_snapshot")
        self.assertEqual(plan["snapshot_contract_version"], "hk_low_vol_dividend_quality_snapshot.factor_snapshot.v1")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_DRY_RUN_ONLY"], "true")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_PATH"], "<required>")
        self.assertEqual(plan["set_env"]["LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH"], "<required>")
        self.assertEqual(
            plan["hints"]["feature_snapshot_filename"],
            "hk_low_vol_dividend_quality_snapshot_factor_snapshot_latest.csv",
        )
        self.assertEqual(
            plan["hints"]["feature_snapshot_manifest_filename"],
            "hk_low_vol_dividend_quality_snapshot_factor_snapshot_latest.csv.manifest.json",
        )

    def test_print_strategy_switch_env_plan_rejects_hk_disabled_profiles(self):
        for profile in sorted(HK_DISABLED_PROFILES):
            with self.subTest(profile=profile):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SWITCH_PLAN_SCRIPT_PATH),
                        "--profile",
                        profile,
                        "--account-region",
                        "hk",
                        "--json",
                    ],
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsupported STRATEGY_PROFILE", result.stderr)

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

    def test_print_strategy_switch_env_plan_rejects_research_only_tech_profile(self):
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
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported STRATEGY_PROFILE", result.stderr)


if __name__ == "__main__":
    unittest.main()
