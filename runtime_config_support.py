from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from quant_platform_kit.common.runtime_config import (
    resolve_bool_value,
    resolve_cash_only_execution_env,
    resolve_dry_run_env,
    resolve_optional_bool_env,
    resolve_optional_dca_mode_env,
    resolve_optional_float_env,
    resolve_optional_ibit_zscore_exit_mode_env,
    resolve_optional_positive_float_env,
    resolve_optional_ratio_env,
    resolve_optional_symbol_env,
    resolve_split_env_list,
    resolve_strategy_runtime_path_settings,
)
from quant_platform_kit.common.runtime_target import (
    RuntimeTarget,
    resolve_runtime_target_from_env,
)
try:
    from quant_platform_kit.common.broker_costs import (
        BrokerCostProfile,
        minimum_economic_order_notional_usd,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    @dataclass(frozen=True)
    class BrokerCostProfile:
        fixed_order_fee_usd: float = 0.0
        minimum_order_fee_usd: float = 0.0
        max_fixed_fee_bps: float = 100.0
        explicit_min_order_notional_usd: float = 0.0

    def minimum_economic_order_notional_usd(profile: BrokerCostProfile | None) -> float:
        if profile is None:
            return 0.0
        explicit_floor = max(0.0, float(profile.explicit_min_order_notional_usd or 0.0))
        fee_floor = max(
            max(0.0, float(profile.fixed_order_fee_usd or 0.0)),
            max(0.0, float(profile.minimum_order_fee_usd or 0.0)),
        )
        max_fee_bps = max(0.0, float(profile.max_fixed_fee_bps or 0.0))
        if fee_floor <= 0.0 or max_fee_bps <= 0.0:
            return explicit_floor
        return max(explicit_floor, fee_floor / (max_fee_bps / 10_000.0))
from strategy_registry import (
    LONGBRIDGE_PLATFORM,
    STRATEGY_CATALOG,
    resolve_strategy_definition,
    resolve_strategy_metadata,
)

if TYPE_CHECKING:
    pass

DEFAULT_ACCOUNT_REGION = "DEFAULT"
DEFAULT_LONGPORT_SECRET_NAME = "longport_token_hk"
DEFAULT_MARKET = "US"
DEFAULT_MARKET_CALENDAR = "NYSE"
DEFAULT_MARKET_TIMEZONE = "America/New_York"
DEFAULT_SYMBOL_SUFFIX = ".US"
DEFAULT_TRADING_CURRENCY = "USD"
HK_MARKET = "HK"
HK_MARKET_CALENDAR = "XHKG"
HK_MARKET_TIMEZONE = "Asia/Hong_Kong"
HK_SYMBOL_SUFFIX = ".HK"
HK_TRADING_CURRENCY = "HKD"
DEFAULT_RESERVED_CASH_FLOOR_USD = 0.0
DEFAULT_RESERVED_CASH_RATIO = 0.0
DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0
DEFAULT_LONGBRIDGE_FIXED_ORDER_FEE_USD = 0.99
DEFAULT_LONGBRIDGE_MAX_FIXED_FEE_BPS = 100.0
DEFAULT_LONGBRIDGE_MIN_ORDER_NOTIONAL_USD = minimum_economic_order_notional_usd(
    BrokerCostProfile(
        fixed_order_fee_usd=DEFAULT_LONGBRIDGE_FIXED_ORDER_FEE_USD,
        max_fixed_fee_bps=DEFAULT_LONGBRIDGE_MAX_FIXED_FEE_BPS,
        explicit_min_order_notional_usd=100.0,
    )
)


@dataclass(frozen=True)
class PlatformRuntimeSettings:
    project_id: str | None
    secret_name: str
    account_prefix: str
    strategy_profile: str
    strategy_display_name: str
    strategy_domain: str
    account_region: str
    notify_lang: str
    tg_token: str | None
    tg_chat_id: str | None
    dry_run_only: bool
    notification_channel: str = "telegram"
    wecom_webhook_url: str | None = None
    dingtalk_webhook_url: str | None = None
    feishu_webhook_url: str | None = None
    serverchan_webhook_url: str | None = None
    runtime_target_enabled: bool = True
    market: str = DEFAULT_MARKET
    market_calendar: str = DEFAULT_MARKET_CALENDAR
    market_timezone: str = DEFAULT_MARKET_TIMEZONE
    symbol_suffix: str = DEFAULT_SYMBOL_SUFFIX
    trading_currency: str = DEFAULT_TRADING_CURRENCY
    reserved_cash_floor_usd: float = DEFAULT_RESERVED_CASH_FLOOR_USD
    reserved_cash_ratio: float = DEFAULT_RESERVED_CASH_RATIO
    min_order_notional_usd: float = DEFAULT_LONGBRIDGE_MIN_ORDER_NOTIONAL_USD
    safe_haven_cash_substitute_threshold_usd: float = DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD
    cash_only_execution: bool = True
    debug_position_snapshot: bool = False
    income_threshold_usd: float | None = None
    qqqi_income_ratio: float | None = None
    income_layer_enabled: bool | None = None
    income_layer_start_usd: float | None = None
    income_layer_max_ratio: float | None = None
    dca_mode: str | None = None
    dca_base_investment_usd: float | None = None
    ibit_zscore_exit_enabled: bool | None = None
    ibit_zscore_exit_mode: str | None = None
    ibit_zscore_exit_parking_symbol: str | None = None
    ibit_zscore_exit_risk_reduced_exposure: float | None = None
    ibit_zscore_exit_risk_off_exposure: float | None = None
    ibit_zscore_exit_allow_outside_execution_window: bool | None = None
    runtime_execution_window_trading_days: int | None = None
    market_signal_handoff_index_uri: str | None = None
    market_signal_handoff_manifest_uri: str | None = None
    market_signal_consumption_audit_uri: str | None = None
    market_signal_cache_dir: str | None = None
    market_signal_required: bool = False
    market_signal_fallback_mode: str | None = None
    market_signal_max_stale_days: int | None = None
    feature_snapshot_path: str | None = None
    feature_snapshot_manifest_path: str | None = None
    feature_snapshot_fallback_mode: str | None = None
    feature_snapshot_fallback_cache_dir: str | None = None
    feature_snapshot_fallback_max_stale_days: int | None = None
    strategy_config_path: str | None = None
    strategy_config_source: str | None = None
    strategy_plugin_mounts_json: str | None = None
    strategy_plugin_alert_channels: tuple[str, ...] = ()
    strategy_plugin_alert_email_recipients: tuple[str, ...] = ()
    strategy_plugin_alert_email_sender_email: str | None = None
    strategy_plugin_alert_email_sender_password: str | None = None
    strategy_plugin_alert_email_smtp_host: str | None = None
    strategy_plugin_alert_email_smtp_port: str | None = None
    strategy_plugin_alert_email_smtp_security: str | None = None
    strategy_plugin_alert_sms_recipients: tuple[str, ...] = ()
    strategy_plugin_alert_sms_provider: str | None = None
    strategy_plugin_alert_sms_account_id: str | None = None
    strategy_plugin_alert_sms_auth_token: str | None = None
    strategy_plugin_alert_sms_sender: str | None = None
    strategy_plugin_alert_sms_messaging_service_id: str | None = None
    strategy_plugin_alert_sms_api_base_url: str | None = None
    strategy_plugin_alert_sms_body_max_chars: str | None = None
    strategy_plugin_alert_push_recipients: tuple[str, ...] = ()
    strategy_plugin_alert_push_provider: str | None = None
    strategy_plugin_alert_push_app_token: str | None = None
    strategy_plugin_alert_push_access_token: str | None = None
    strategy_plugin_alert_push_api_base_url: str | None = None
    strategy_plugin_alert_push_device: str | None = None
    strategy_plugin_alert_push_priority: str | None = None
    strategy_plugin_alert_push_tags: str | None = None
    strategy_plugin_alert_push_body_max_chars: str | None = None
    strategy_plugin_alert_telegram_chat_ids: tuple[str, ...] = ()
    strategy_plugin_alert_telegram_bot_token: str | None = None
    strategy_plugin_alert_telegram_api_base_url: str | None = None
    strategy_plugin_alert_telegram_parse_mode: str | None = None
    strategy_plugin_alert_telegram_disable_web_page_preview: str | None = None
    strategy_plugin_alert_telegram_body_max_chars: str | None = None
    runtime_target: RuntimeTarget | None = None
    strategy_metadata: Any = None


def resolve_strategy_profile(raw_value: str | None) -> str:
    return resolve_strategy_definition(
        raw_value,
        platform_id=LONGBRIDGE_PLATFORM,
    ).profile


def infer_account_region(
    raw_value: str | None,
    *,
    account_prefix: str,
) -> str:
    for candidate in (
        raw_value,
        account_prefix,
    ):
        normalized = _normalize_region(candidate)
        if normalized is not None:
            return normalized
    return DEFAULT_ACCOUNT_REGION


def infer_market(raw_value: str | None, *, account_region: str) -> str:
    for candidate in (raw_value, account_region):
        value = str(candidate or "").strip().upper()
        if not value:
            continue
        if value in {HK_MARKET, "HONG_KONG", "HONGKONG"}:
            return HK_MARKET
        if value in {DEFAULT_MARKET, "USA", "NYSE", "NASDAQ"}:
            return DEFAULT_MARKET
    return DEFAULT_MARKET


def _normalize_symbol_suffix(raw_value: str | None, *, default: str) -> str:
    value = str(raw_value if raw_value is not None else default).strip().upper()
    if not value:
        return ""
    return value if value.startswith(".") else f".{value}"


def _market_defaults(market: str) -> dict[str, str]:
    if market == HK_MARKET:
        return {
            "market_calendar": HK_MARKET_CALENDAR,
            "market_timezone": HK_MARKET_TIMEZONE,
            "symbol_suffix": HK_SYMBOL_SUFFIX,
            "trading_currency": HK_TRADING_CURRENCY,
        }
    return {
        "market_calendar": DEFAULT_MARKET_CALENDAR,
        "market_timezone": DEFAULT_MARKET_TIMEZONE,
        "symbol_suffix": DEFAULT_SYMBOL_SUFFIX,
        "trading_currency": DEFAULT_TRADING_CURRENCY,
    }


def load_platform_runtime_settings(
    *,
    project_id_resolver: Callable[[], str | None],
) -> PlatformRuntimeSettings:
    account_prefix = os.getenv("ACCOUNT_PREFIX", "DEFAULT")
    safe_haven_cash_substitute_threshold_usd = resolve_optional_float_env(
        os.environ,
        "LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD",
    )
    runtime_target = resolve_runtime_target_from_env(
        env=os.environ,
        expected_platform_id=LONGBRIDGE_PLATFORM,
    )
    strategy_definition = resolve_strategy_definition(
        runtime_target.strategy_profile,
        platform_id=LONGBRIDGE_PLATFORM,
    )
    strategy_metadata = resolve_strategy_metadata(
        strategy_definition.profile,
        platform_id=LONGBRIDGE_PLATFORM,
    )
    runtime_paths = resolve_strategy_runtime_path_settings(
        strategy_catalog=STRATEGY_CATALOG,
        strategy_definition=strategy_definition,
        strategy_metadata=strategy_metadata,
        platform_env_prefix="LONGBRIDGE",
        env=os.environ,
        repo_root=Path(__file__).resolve().parent,
    )
    account_region = infer_account_region(
        os.getenv("ACCOUNT_REGION"),
        account_prefix=account_prefix,
    )
    market = infer_market(os.getenv("LONGBRIDGE_MARKET"), account_region=account_region)
    market_defaults = _market_defaults(market)
    return PlatformRuntimeSettings(
        project_id=project_id_resolver(),
        secret_name=os.getenv("LONGPORT_SECRET_NAME", DEFAULT_LONGPORT_SECRET_NAME),
        account_prefix=account_prefix,
        strategy_profile=runtime_paths.strategy_profile,
        strategy_display_name=runtime_paths.strategy_display_name,
        strategy_domain=runtime_paths.strategy_domain,
        account_region=account_region,
        market=market,
        market_calendar=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_CALENDAR"),
            market_defaults["market_calendar"],
        )
        or DEFAULT_MARKET_CALENDAR,
        market_timezone=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_TIMEZONE"),
            market_defaults["market_timezone"],
        )
        or DEFAULT_MARKET_TIMEZONE,
        symbol_suffix=_normalize_symbol_suffix(
            os.getenv("LONGBRIDGE_SYMBOL_SUFFIX"),
            default=market_defaults["symbol_suffix"],
        ),
        trading_currency=(
            _first_non_empty(
                os.getenv("LONGBRIDGE_TRADING_CURRENCY"),
                market_defaults["trading_currency"],
            )
            or DEFAULT_TRADING_CURRENCY
        ).upper(),
        notify_lang=os.getenv("NOTIFY_LANG", "en"),
        tg_token=os.getenv("TELEGRAM_TOKEN"),
        tg_chat_id=os.getenv("GLOBAL_TELEGRAM_CHAT_ID"),
        notification_channel=os.getenv("NOTIFICATION_CHANNEL", "telegram"),
        wecom_webhook_url=os.getenv("NOTIFICATION_WECOM_WEBHOOK_URL"),
        dingtalk_webhook_url=os.getenv("NOTIFICATION_DINGTALK_WEBHOOK_URL"),
        feishu_webhook_url=os.getenv("NOTIFICATION_FEISHU_WEBHOOK_URL"),
        serverchan_webhook_url=os.getenv("NOTIFICATION_SERVERCHAN_WEBHOOK_URL"),
        dry_run_only=resolve_dry_run_env(os.environ, "LONGBRIDGE_DRY_RUN_ONLY"),
        runtime_target_enabled=_runtime_target_enabled_env(),
        reserved_cash_floor_usd=_resolve_non_negative_float_env(
            "LONGBRIDGE_MIN_RESERVED_CASH_USD",
            default=DEFAULT_RESERVED_CASH_FLOOR_USD,
        ),
        reserved_cash_ratio=_resolve_ratio_env(
            "LONGBRIDGE_RESERVED_CASH_RATIO",
            default=DEFAULT_RESERVED_CASH_RATIO,
        ),
        min_order_notional_usd=_resolve_non_negative_float_env(
            "LONGBRIDGE_MIN_ORDER_NOTIONAL_USD",
            default=DEFAULT_LONGBRIDGE_MIN_ORDER_NOTIONAL_USD,
        ),
        safe_haven_cash_substitute_threshold_usd=(
            max(0.0, safe_haven_cash_substitute_threshold_usd)
            if safe_haven_cash_substitute_threshold_usd is not None
            else DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD
        ),
        cash_only_execution=resolve_cash_only_execution_env(
            os.environ,
            platform_env_prefix="LONGBRIDGE",
        ),
        debug_position_snapshot=resolve_bool_value(os.getenv("LONGBRIDGE_DEBUG_POSITION_SNAPSHOT")),
        income_threshold_usd=resolve_optional_float_env(os.environ, "INCOME_THRESHOLD_USD"),
        qqqi_income_ratio=_qqqi_income_ratio_env(),
        income_layer_enabled=resolve_optional_bool_env("INCOME_LAYER_ENABLED"),
        income_layer_start_usd=_optional_non_negative_float_env("INCOME_LAYER_START_USD"),
        income_layer_max_ratio=resolve_optional_ratio_env("INCOME_LAYER_MAX_RATIO"),
        dca_mode=resolve_optional_dca_mode_env("DCA_MODE"),
        dca_base_investment_usd=resolve_optional_positive_float_env("DCA_BASE_INVESTMENT_USD"),
        ibit_zscore_exit_enabled=resolve_optional_bool_env("IBIT_ZSCORE_EXIT_ENABLED"),
        ibit_zscore_exit_mode=resolve_optional_ibit_zscore_exit_mode_env("IBIT_ZSCORE_EXIT_MODE"),
        ibit_zscore_exit_parking_symbol=resolve_optional_symbol_env("IBIT_ZSCORE_EXIT_PARKING_SYMBOL"),
        ibit_zscore_exit_risk_reduced_exposure=resolve_optional_ratio_env(
            "IBIT_ZSCORE_EXIT_RISK_REDUCED_EXPOSURE"
        ),
        ibit_zscore_exit_risk_off_exposure=resolve_optional_ratio_env("IBIT_ZSCORE_EXIT_RISK_OFF_EXPOSURE"),
        ibit_zscore_exit_allow_outside_execution_window=resolve_optional_bool_env(
            "IBIT_ZSCORE_EXIT_ALLOW_OUTSIDE_EXECUTION_WINDOW"
        ),
        runtime_execution_window_trading_days=_runtime_execution_window_trading_days_env(
            strategy_definition.profile
        ),
        market_signal_handoff_index_uri=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_SIGNAL_HANDOFF_INDEX_URI"),
            os.getenv("MARKET_SIGNAL_HANDOFF_INDEX_URI"),
        ),
        market_signal_handoff_manifest_uri=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_SIGNAL_HANDOFF_MANIFEST_URI"),
            os.getenv("MARKET_SIGNAL_HANDOFF_MANIFEST_URI"),
        ),
        market_signal_consumption_audit_uri=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_SIGNAL_CONSUMPTION_AUDIT_URI"),
            os.getenv("MARKET_SIGNAL_CONSUMPTION_AUDIT_URI"),
        ),
        market_signal_cache_dir=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_SIGNAL_CACHE_DIR"),
            os.getenv("MARKET_SIGNAL_CACHE_DIR"),
        ),
        market_signal_required=resolve_bool_value(
            _first_non_empty(
                os.getenv("LONGBRIDGE_MARKET_SIGNAL_REQUIRED"),
                os.getenv("MARKET_SIGNAL_REQUIRED"),
                "false",
            )
        ),
        market_signal_fallback_mode=_first_non_empty(
            os.getenv("LONGBRIDGE_MARKET_SIGNAL_FALLBACK_MODE"),
            os.getenv("MARKET_SIGNAL_FALLBACK_MODE"),
        ),
        market_signal_max_stale_days=_optional_int(
            _first_non_empty(
                os.getenv("LONGBRIDGE_MARKET_SIGNAL_MAX_STALE_DAYS"),
                os.getenv("LONGBRIDGE_MARKET_SIGNAL_FALLBACK_MAX_STALE_DAYS"),
                os.getenv("MARKET_SIGNAL_MAX_STALE_DAYS"),
                os.getenv("MARKET_SIGNAL_FALLBACK_MAX_STALE_DAYS"),
            )
        ),
        feature_snapshot_path=runtime_paths.feature_snapshot_path,
        feature_snapshot_manifest_path=runtime_paths.feature_snapshot_manifest_path,
        feature_snapshot_fallback_mode=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_MODE"),
            os.getenv("FEATURE_SNAPSHOT_FALLBACK_MODE"),
        ),
        feature_snapshot_fallback_cache_dir=_first_non_empty(
            os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR"),
            os.getenv("FEATURE_SNAPSHOT_FALLBACK_CACHE_DIR"),
        ),
        feature_snapshot_fallback_max_stale_days=_optional_int(
            _first_non_empty(
                os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_MAX_STALE_DAYS"),
                os.getenv("LONGBRIDGE_FEATURE_SNAPSHOT_FALLBACK_MAX_STALE_DAYS"),
                os.getenv("FEATURE_SNAPSHOT_MAX_STALE_DAYS"),
                os.getenv("FEATURE_SNAPSHOT_FALLBACK_MAX_STALE_DAYS"),
            )
        ),
        strategy_config_path=runtime_paths.strategy_config_path,
        strategy_config_source=runtime_paths.strategy_config_source,
        strategy_plugin_mounts_json=(
            os.getenv("LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON")
            or os.getenv("STRATEGY_PLUGIN_MOUNTS_JSON")
        ),
        strategy_plugin_alert_channels=resolve_split_env_list("STRATEGY_PLUGIN_ALERT_CHANNELS"),
        strategy_plugin_alert_email_recipients=resolve_split_env_list("STRATEGY_PLUGIN_ALERT_EMAIL_RECIPIENTS"),
        strategy_plugin_alert_email_sender_email=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_EMAIL")),
        strategy_plugin_alert_email_sender_password=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_EMAIL_SENDER_PASSWORD")
        ),
        strategy_plugin_alert_email_smtp_host=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_HOST")),
        strategy_plugin_alert_email_smtp_port=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_PORT")),
        strategy_plugin_alert_email_smtp_security=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_EMAIL_SMTP_SECURITY")
        ),
        strategy_plugin_alert_sms_recipients=resolve_split_env_list("STRATEGY_PLUGIN_ALERT_SMS_RECIPIENTS"),
        strategy_plugin_alert_sms_provider=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_SMS_PROVIDER")),
        strategy_plugin_alert_sms_account_id=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_SMS_ACCOUNT_ID")),
        strategy_plugin_alert_sms_auth_token=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_SMS_AUTH_TOKEN")),
        strategy_plugin_alert_sms_sender=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_SMS_SENDER")),
        strategy_plugin_alert_sms_messaging_service_id=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_SMS_MESSAGING_SERVICE_ID")
        ),
        strategy_plugin_alert_sms_api_base_url=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_SMS_API_BASE_URL")),
        strategy_plugin_alert_sms_body_max_chars=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_SMS_BODY_MAX_CHARS")
        ),
        strategy_plugin_alert_push_recipients=resolve_split_env_list("STRATEGY_PLUGIN_ALERT_PUSH_RECIPIENTS"),
        strategy_plugin_alert_push_provider=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_PROVIDER")),
        strategy_plugin_alert_push_app_token=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_APP_TOKEN")),
        strategy_plugin_alert_push_access_token=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_ACCESS_TOKEN")),
        strategy_plugin_alert_push_api_base_url=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_API_BASE_URL")),
        strategy_plugin_alert_push_device=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_DEVICE")),
        strategy_plugin_alert_push_priority=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_PRIORITY")),
        strategy_plugin_alert_push_tags=_first_non_empty(os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_TAGS")),
        strategy_plugin_alert_push_body_max_chars=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_PUSH_BODY_MAX_CHARS")
        ),
        strategy_plugin_alert_telegram_chat_ids=resolve_split_env_list(
            "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS"
        ),
        strategy_plugin_alert_telegram_bot_token=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN")
        ),
        strategy_plugin_alert_telegram_api_base_url=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_API_BASE_URL")
        ),
        strategy_plugin_alert_telegram_parse_mode=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_PARSE_MODE")
        ),
        strategy_plugin_alert_telegram_disable_web_page_preview=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_DISABLE_WEB_PAGE_PREVIEW")
        ),
        strategy_plugin_alert_telegram_body_max_chars=_first_non_empty(
            os.getenv("STRATEGY_PLUGIN_ALERT_TELEGRAM_BODY_MAX_CHARS")
        ),
        runtime_target=runtime_target,
        strategy_metadata=strategy_metadata,
    )


def _normalize_region(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    return value.upper()


def _qqqi_income_ratio_env() -> float | None:
    value = resolve_optional_float_env(os.environ, "QQQI_INCOME_RATIO")
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"QQQI_INCOME_RATIO must be in [0,1], got {value}")
    return value


def _runtime_target_enabled_env() -> bool:
    value = resolve_optional_bool_env("RUNTIME_TARGET_ENABLED")
    return True if value is None else value


def _optional_non_negative_float_env(name: str) -> float | None:
    value = resolve_optional_float_env(os.environ, name)
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return float(value)



def _resolve_non_negative_float_env(name: str, *, default: float) -> float:
    value = resolve_optional_float_env(os.environ, name)
    if value is None:
        return float(default)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")
    return float(value)


def _resolve_ratio_env(name: str, *, default: float) -> float:
    value = _resolve_non_negative_float_env(name, default=default)
    if value > 1.0:
        raise ValueError(f"{name} must be in [0,1], got {value}")
    return value


def _first_non_empty(*raw_values: str | None) -> str | None:
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if value:
            return value
    return None


def _optional_int(raw_value: str | None) -> int | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    return int(value)



def _runtime_execution_window_trading_days_env(strategy_profile: str) -> int | None:
    if strategy_profile != "tech_communication_pullback_enhancement":
        return None
    raw_value = os.getenv("LONGBRIDGE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS")
    if raw_value is None or not str(raw_value).strip():
        return None
    try:
        value = int(str(raw_value).strip())
    except ValueError as exc:
        raise ValueError(
            "LONGBRIDGE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS must be a positive integer"
        ) from exc
    if value <= 0:
        raise ValueError(
            "LONGBRIDGE_TECH_RUNTIME_EXECUTION_WINDOW_TRADING_DAYS must be a positive integer"
        )
    return value
