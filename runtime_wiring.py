"""Runtime configuration, adapters, and composer wiring."""

from __future__ import annotations


import sys

_FALLBACK_MODULE_NAME = __name__


class _MainModuleProxy:
    """Prefer ``main.<name>`` (facade-aware); fall back during module import."""

    def __getattr__(self, name):
        for key in ("main", "__main__"):
            main_mod = sys.modules.get(key)
            if main_mod is None:
                continue
            if getattr(main_mod, "__name__", None) in {
                "http_routes",
                "runtime_wiring",
                "notifications_runtime",
                "strategy_plugins_runtime",
            }:
                continue
            try:
                # Use getattr so ModuleType facades can resolve mutable globals.
                return getattr(main_mod, name)
            except AttributeError:
                continue
        return getattr(sys.modules[_FALLBACK_MODULE_NAME], name)

main = _MainModuleProxy()

import dataclasses
import json
import importlib
import os
import time
from datetime import datetime, timezone

from flask import Flask, request

import google.auth
import requests
from application.monitor_dispatcher import dispatch_due_monitors, load_monitor_targets
from application.startup_config import (
    StartupConfigReceipt,
    normalize_monitor_dispatch_targets,
    normalize_plugin_mounts_strategy,
    overlay_env,
)
from application.broker_reconciliation import (
    account_snapshot_enabled,
    collect_read_only_reconciliation_observations,
    reconciliation_enabled,
    run_read_only_account_snapshot,
    run_read_only_broker_reconciliation,
)
from application.execution_receipt_adapter import (
    attach_cycle_execution_receipt,
    attach_terminal_fallback_execution_receipt,
)
from application.runtime_broker_adapters import build_runtime_broker_adapters
from application.runtime_composer import build_runtime_composer
from application.rebalance_service import run_strategy as run_rebalance_cycle
from application.durable_execution_commands import (
    resolve_paper_execution_command_consumer_enabled,
)
from application.paper_execution_command_consumer import (
    consume_due_paper_execution_commands,
)
from application.runtime_strategy_adapters import build_runtime_strategy_adapters
from application.longbridge_execution import submit_order
from application import validation_cycle_observation
from runtime_execution_policy import fractional_buy_execution_enabled, FRACTIONAL_BUY_QUANTITY_STEP
from application.longbridge_portfolio import fetch_strategy_account_state
from application.account_snapshot import (
    begin_natural_cycle_history, cycle_history_observation, end_natural_cycle_history,
    read_cycle_account_balance, read_cycle_stock_positions,
)
from scripts.record_daily_account_snapshot import record_projected_daily_account
from entrypoints.cloud_run import is_market_open_now
from runtime_execution_policy import dca_execution_unsupported_reason
from runtime_config_support import load_platform_runtime_settings
from strategy_registry import V7_LONGTERM_COMPOUNDING_PROFILE
from notifications.telegram import build_signal_text, build_strategy_display_name, build_translator
from notifications import unified as unified_notifications
from quant_platform_kit.common.runtime_reports import (
    append_runtime_report_error,
    build_runtime_report_base,
    finalize_runtime_report,
    persist_runtime_report,
)
from quant_platform_kit.common.strategy_plugins import (
    build_strategy_plugin_report_payload,
    load_configured_strategy_plugin_signals,
    parse_strategy_plugin_mounts,
)
from quant_platform_kit.notifications.strategy_plugin_alerts import (
    StrategyPluginAlertStateSettings,
    build_strategy_plugin_alert_context_label as build_alert_context_label,
    publish_strategy_plugin_alerts as dispatch_strategy_plugin_alerts,
)
from quant_platform_kit.common.runtime_inputs import build_strategy_evaluation_inputs
from runtime_logging import build_run_id, emit_runtime_log
from quant_platform_kit.longbridge import (
    build_contexts,
    calculate_rotation_indicators,
    estimate_max_purchase_quantity,
    fetch_last_price,
    fetch_order_status,
    fetch_token_from_secret,
    refresh_token_if_needed,
)
from strategy_runtime import load_strategy_runtime
from decision_mapper import map_strategy_decision_to_plan

def get_project_id():
    try:
        _, project_id = main.google.auth.default()
        return project_id
    except Exception:
        return main.os.getenv('GOOGLE_CLOUD_PROJECT')
RUNTIME_SETTINGS = load_platform_runtime_settings(project_id_resolver=get_project_id)
PROJECT_ID = RUNTIME_SETTINGS.project_id
SECRET_NAME = RUNTIME_SETTINGS.secret_name
ACCOUNT_PREFIX = RUNTIME_SETTINGS.account_prefix
STRATEGY_PROFILE = RUNTIME_SETTINGS.strategy_profile
STRATEGY_DISPLAY_NAME = RUNTIME_SETTINGS.strategy_display_name

def _normalize_plugin_mounts_strategy(raw_mounts_json: str | None) -> str | None:
    normalized, _corrections = main.normalize_plugin_mounts_strategy(raw_mounts_json, strategy_profile=main.STRATEGY_PROFILE)
    return normalized

def _adopt_startup_config(settings, env):
    """Explicitly normalize startup config without mutating ``os.environ`` (B08).

    Returns ``(settings, monitor_dispatch_env_overrides, receipt)``.
    """
    corrections = []
    if hasattr(settings, 'strategy_plugin_mounts_json'):
        normalized, plugin_corrections = main.normalize_plugin_mounts_strategy(settings.strategy_plugin_mounts_json, strategy_profile=main.STRATEGY_PROFILE)
        corrections.extend(plugin_corrections)
        if normalized != settings.strategy_plugin_mounts_json:
            if main.dataclasses.is_dataclass(settings) and (not isinstance(settings, type)):
                settings = main.dataclasses.replace(settings, strategy_plugin_mounts_json=normalized)
            else:
                setattr(settings, 'strategy_plugin_mounts_json', normalized)
    overrides, monitor_corrections = main.normalize_monitor_dispatch_targets(env, strategy_profile=main.STRATEGY_PROFILE)
    corrections.extend(monitor_corrections)
    receipt = main.StartupConfigReceipt(strategy_profile=main.STRATEGY_PROFILE, corrections=tuple(corrections), env_overrides=tuple(sorted(overrides)))
    return (settings, overrides, receipt)
RUNTIME_SETTINGS, MONITOR_DISPATCH_ENV_OVERRIDES, STARTUP_CONFIG_RECEIPT = _adopt_startup_config(RUNTIME_SETTINGS, os.environ)

def _load_monitor_dispatch_targets():
    if not main.MONITOR_DISPATCH_ENV_OVERRIDES:
        return main.load_monitor_targets()
    return main.load_monitor_targets(main.overlay_env(main.os.environ, main.MONITOR_DISPATCH_ENV_OVERRIDES))
ACCOUNT_REGION = RUNTIME_SETTINGS.account_region
MARKET = RUNTIME_SETTINGS.market
MARKET_CALENDAR = RUNTIME_SETTINGS.market_calendar
MARKET_TIMEZONE = RUNTIME_SETTINGS.market_timezone
SYMBOL_SUFFIX = RUNTIME_SETTINGS.symbol_suffix
TRADING_CURRENCY = RUNTIME_SETTINGS.trading_currency
NOTIFY_LANG = RUNTIME_SETTINGS.notify_lang
TG_TOKEN = RUNTIME_SETTINGS.tg_token
TG_CHAT_ID = RUNTIME_SETTINGS.tg_chat_id
from quant_platform_kit.notifications.cycle_channel import resolve_cycle_channel_and_url
_NOTIFICATION_CHANNEL, _NOTIFICATION_WEBHOOK_URL = resolve_cycle_channel_and_url(explicit_channel=RUNTIME_SETTINGS.notification_channel, telegram_token=TG_TOKEN, telegram_chat_id=TG_CHAT_ID, wecom_url=RUNTIME_SETTINGS.wecom_webhook_url, dingtalk_url=RUNTIME_SETTINGS.dingtalk_webhook_url, feishu_url=RUNTIME_SETTINGS.feishu_webhook_url, serverchan_url=RUNTIME_SETTINGS.serverchan_webhook_url)
CASH_ONLY_EXECUTION = getattr(RUNTIME_SETTINGS, 'cash_only_execution', True)
STRATEGY_RUNTIME = load_strategy_runtime(STRATEGY_PROFILE, runtime_settings=RUNTIME_SETTINGS, logger=lambda message: print(message, flush=True))
STRATEGY_RUNTIME_CONFIG = dict(STRATEGY_RUNTIME.merged_runtime_config)
MANAGED_SYMBOLS = STRATEGY_RUNTIME.managed_symbols
AVAILABLE_INPUTS = frozenset(STRATEGY_RUNTIME.runtime_adapter.available_inputs)
BENCHMARK_SYMBOL = str(STRATEGY_RUNTIME_CONFIG.get('benchmark_symbol', 'QQQ'))

def _emit_v7_loaded_version_log() -> None:
    """Emit one safe startup receipt for the disabled candidate revision."""
    if main.STRATEGY_PROFILE != main.V7_LONGTERM_COMPOUNDING_PROFILE:
        return
    from application.v7_paper_application import V7PaperApplicationError, build_v7_loaded_version_readback, load_v7_paper_application_binding
    binding = load_v7_paper_application_binding(main.os.environ)
    if binding is None:
        return
    try:
        readback = build_v7_loaded_version_readback(binding, runtime_settings=main.RUNTIME_SETTINGS, strategy_runtime=main.STRATEGY_RUNTIME, runtime_env=main.os.environ)
        print(main.json.dumps({'event': 'v7_paper_application_loaded', 'status': 'applied_paused', 'readback': readback}, ensure_ascii=True, separators=(',', ':')), flush=True)
    except V7PaperApplicationError:
        print(main.json.dumps({'event': 'v7_paper_application_loaded', 'status': 'uncertain'}, ensure_ascii=True, separators=(',', ':')), flush=True)
_emit_v7_loaded_version_log()
SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS = getattr(getattr(STRATEGY_RUNTIME.runtime_adapter, 'runtime_policy', None), 'signal_effective_after_trading_days', None)
LIMIT_SELL_DISCOUNT = 0.995
LIMIT_BUY_PREMIUM = 1.005
DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL = {'SOXL': 1.015, 'TQQQ': 1.01}
ORDER_POLL_INTERVAL_SEC = 1
ORDER_POLL_MAX_ATTEMPTS = 8
TOKEN_REFRESH_THRESHOLD_DAYS = 30
DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0
DEFAULT_MIN_ORDER_NOTIONAL_USD = 100.0
_FRACTIONAL_BUY_EXECUTION = fractional_buy_execution_enabled(STRATEGY_PROFILE)

def _profile_submit_order(t_ctx, symbol, **kwargs):
    return main.submit_order(t_ctx, symbol, allow_fractional_shares=main._FRACTIONAL_BUY_EXECUTION, quantity_step=main.FRACTIONAL_BUY_QUANTITY_STEP if main._FRACTIONAL_BUY_EXECUTION else 1.0, **kwargs)

def _profile_estimate_max_purchase_quantity(t_ctx, symbol, **kwargs):
    return main.estimate_max_purchase_quantity(t_ctx, symbol, fractional_shares=main._FRACTIONAL_BUY_EXECUTION, **kwargs)
SEPARATOR = '━━━━━━━━━━━━━━━━━━'
COMPACT_ERROR_NOTIFICATION_MAX_CHARS = 3500

def _load_limit_buy_premium_by_symbol(*env_names: str) -> dict[str, float]:
    raw_value = ''
    for env_name in env_names:
        value = main.os.getenv(env_name)
        if value and value.strip():
            raw_value = value.strip()
            break
    if not raw_value:
        return dict(main.DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL)
    try:
        payload = main.json.loads(raw_value)
    except main.json.JSONDecodeError as exc:
        raise ValueError(f'Invalid limit buy premium map JSON: {raw_value!r}') from exc
    if not isinstance(payload, dict):
        raise ValueError('Limit buy premium map must be a JSON object keyed by symbol.')
    parsed: dict[str, float] = {}
    for symbol, premium in payload.items():
        symbol_text = str(symbol or '').strip().upper()
        if not symbol_text:
            continue
        premium_value = float(premium)
        if premium_value <= 0.0:
            raise ValueError(f'Limit buy premium for {symbol_text} must be positive.')
        parsed[symbol_text] = premium_value
    return parsed
LIMIT_BUY_PREMIUM_BY_SYMBOL = _load_limit_buy_premium_by_symbol('LONGBRIDGE_LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON', 'LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON')

def t(key, **kwargs):
    return main.build_translator(main.NOTIFY_LANG)(key, **kwargs)

def _build_notification_delivery_log_for_report(*, platform: str, strategy_profile: str, run_id: str, dry_run: bool, orders_previewed_count: int, delivery_events: list[dict]) -> dict:
    events = [dict(event) for event in delivery_events if dict(event).get('delivery_status') == 'sent']
    if not dry_run or orders_previewed_count <= 0 or (not events):
        return {}
    return {'notification_schema_version': 'hk_live_enablement_notification.v1', 'notification_event_type': 'hk_snapshot_live_enablement_dry_run', 'notification_correlation_id': str(run_id or ''), 'locales': ['en', 'zh-Hans'], 'profile': str(strategy_profile or ''), 'platform': str(platform or ''), 'validation_status': 'passed', 'orders_previewed': int(orders_previewed_count), 'delivery_events': events, 'notification_contains_profile': True, 'notification_contains_platform': True, 'notification_contains_validation_status': True, 'notification_contains_order_preview_summary': True, 'notification_redacts_sensitive_fields': True, 'redaction_policy': 'raw notification text is not persisted; only sha256 and length are recorded'}

def _build_notification_delivery_summary(delivery_events: list[dict]) -> dict:
    safe_fields = ('sink', 'delivery_status', 'transport_acknowledged', 'error_type', 'compact_text_sha256', 'compact_text_length')
    events = [{key: event[key] for key in safe_fields if key in event} for event in (dict(item) for item in delivery_events)]
    if not events:
        return {}
    sent_count = sum((event.get('delivery_status') == 'sent' for event in events))
    failed_count = sum((event.get('delivery_status') == 'failed' for event in events))
    return {'attempted_count': len(events), 'sent_count': sent_count, 'failed_count': failed_count, 'all_acknowledged': failed_count == 0 and sent_count == len(events), 'delivery_events': events}
signal_text = build_signal_text(t)
strategy_display_name = build_strategy_display_name(t)(STRATEGY_PROFILE, fallback_name=STRATEGY_DISPLAY_NAME, metadata=RUNTIME_SETTINGS.strategy_metadata, locale=NOTIFY_LANG)

def log_position_snapshot(message):
    print(f'[{main.ACCOUNT_REGION}] {message}', flush=True)

def log_runtime_warning(message):
    print(f'[{main.ACCOUNT_REGION}] [warning] {message}', flush=True)
BROKER_ADAPTERS = build_runtime_broker_adapters(strategy_symbols=tuple(MANAGED_SYMBOLS), account_hash=ACCOUNT_PREFIX or ACCOUNT_REGION or 'longbridge', fetch_last_price_fn=fetch_last_price, fetch_strategy_account_state_fn=lambda quote_context, trade_context: fetch_strategy_account_state(quote_context, trade_context, list(MANAGED_SYMBOLS), cash_currency=TRADING_CURRENCY, position_log_fn=log_position_snapshot if getattr(RUNTIME_SETTINGS, 'debug_position_snapshot', False) else None, warning_log_fn=log_runtime_warning), submit_order_fn=_profile_submit_order, symbol_suffix=SYMBOL_SUFFIX, currency=TRADING_CURRENCY, cash_only_execution=CASH_ONLY_EXECUTION)
STRATEGY_ADAPTERS = build_runtime_strategy_adapters(strategy_runtime=STRATEGY_RUNTIME, strategy_profile=STRATEGY_PROFILE, strategy_runtime_config=STRATEGY_RUNTIME_CONFIG, available_inputs=AVAILABLE_INPUTS, benchmark_symbol=BENCHMARK_SYMBOL, signal_text_fn=signal_text, translator=t, broker_adapters=BROKER_ADAPTERS, calculate_rotation_indicators_fn=calculate_rotation_indicators, build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs, map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan, execution_policy={'reserved_cash_floor_usd': getattr(RUNTIME_SETTINGS, 'reserved_cash_floor_usd', 0.0), 'reserved_cash_ratio': getattr(RUNTIME_SETTINGS, 'reserved_cash_ratio', 0.0), 'cash_only_execution': CASH_ONLY_EXECUTION}, execution_materials=getattr(STRATEGY_RUNTIME, 'execution_materials', {}), build_strategy_plugin_report_payload_fn=build_strategy_plugin_report_payload, load_configured_strategy_plugin_signals_fn=load_configured_strategy_plugin_signals, parse_strategy_plugin_mounts_fn=parse_strategy_plugin_mounts)

def _safe_haven_cash_substitute_threshold_usd() -> float:
    return float(getattr(main.RUNTIME_SETTINGS, 'safe_haven_cash_substitute_threshold_usd', main.DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD))

def build_composer(*, dry_run_only_override: bool | None=None):
    return main.build_runtime_composer(project_id=main.PROJECT_ID, secret_name=main.SECRET_NAME, token_refresh_threshold_days=main.TOKEN_REFRESH_THRESHOLD_DAYS, account_prefix=main.ACCOUNT_PREFIX, account_region=main.ACCOUNT_REGION, strategy_profile=main.STRATEGY_PROFILE, strategy_display_name=main.STRATEGY_DISPLAY_NAME, strategy_display_name_localized=main.strategy_display_name, strategy_domain=main.RUNTIME_SETTINGS.strategy_domain, notify_lang=main.NOTIFY_LANG, tg_token=main.TG_TOKEN, tg_chat_id=main.TG_CHAT_ID, notification_channel=main._NOTIFICATION_CHANNEL, webhook_url=main._NOTIFICATION_WEBHOOK_URL, managed_symbols=tuple(main.MANAGED_SYMBOLS), benchmark_symbol=main.BENCHMARK_SYMBOL, signal_effective_after_trading_days=main.SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS, separator=main.SEPARATOR, limit_sell_discount=main.LIMIT_SELL_DISCOUNT, limit_buy_premium=main.LIMIT_BUY_PREMIUM, limit_buy_premium_by_symbol=main.LIMIT_BUY_PREMIUM_BY_SYMBOL, order_poll_interval_sec=main.ORDER_POLL_INTERVAL_SEC, order_poll_max_attempts=main.ORDER_POLL_MAX_ATTEMPTS, safe_haven_cash_substitute_threshold_usd=main._safe_haven_cash_substitute_threshold_usd(), min_order_notional_usd=main._min_order_notional_usd(), market=main.MARKET, symbol_suffix=main.SYMBOL_SUFFIX, trading_currency=main.TRADING_CURRENCY, dry_run_only=main.RUNTIME_SETTINGS.dry_run_only, dry_run_only_override=dry_run_only_override, broker_adapters=main.BROKER_ADAPTERS, strategy_adapters=main.STRATEGY_ADAPTERS, estimate_max_purchase_quantity_fn=main._profile_estimate_max_purchase_quantity, fetch_order_status_fn=main.fetch_order_status, fetch_token_from_secret_fn=main.fetch_token_from_secret, fetch_token_with_metadata_fn=main.fetch_account_snapshot_token_with_metadata, refresh_token_if_needed_fn=main.refresh_token_if_needed, build_contexts_fn=main.build_contexts, run_id_builder=main.build_run_id, event_logger=main.emit_runtime_log, report_builder=main.build_runtime_report_base, report_persister=main.persist_runtime_report, translator=main.t, runtime_target=main.RUNTIME_SETTINGS.runtime_target, env_reader=main.os.getenv, sleeper=main.time.sleep, printer=print, extra_reporting_fields={'market': main.MARKET, 'market_calendar': main.MARKET_CALENDAR, 'market_timezone': main.MARKET_TIMEZONE, 'symbol_suffix': main.SYMBOL_SUFFIX, 'trading_currency': main.TRADING_CURRENCY})

def fetch_account_snapshot_token_with_metadata(project_id, secret_name):
    """Read one configured secret-store response. A metadata failure is not reread."""
    from types import SimpleNamespace
    from quant_platform_kit.cloud import get_secret_store
    store = get_secret_store()
    read_metadata = getattr(store, 'get_secret_with_metadata', None)
    if callable(read_metadata):
        return read_metadata(secret_name, project_id=project_id)
    return SimpleNamespace(value=store.get_secret(secret_name, project_id=project_id), version_name=None)
