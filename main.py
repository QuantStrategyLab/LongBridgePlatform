"""Platform HTTP entrypoint with explicit named re-exports."""

from __future__ import annotations

import sys

for _split_module_name in (
    "runtime_wiring",
    "notifications_runtime",
    "strategy_plugins_runtime",
    "http_routes",
):
    sys.modules.pop(_split_module_name, None)
del _split_module_name

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

from runtime_wiring import (
    get_project_id,
    RUNTIME_SETTINGS,
    PROJECT_ID,
    SECRET_NAME,
    ACCOUNT_PREFIX,
    STRATEGY_PROFILE,
    STRATEGY_DISPLAY_NAME,
    _normalize_plugin_mounts_strategy,
    _adopt_startup_config,
    MONITOR_DISPATCH_ENV_OVERRIDES,
    STARTUP_CONFIG_RECEIPT,
    _load_monitor_dispatch_targets,
    ACCOUNT_REGION,
    MARKET,
    MARKET_CALENDAR,
    MARKET_TIMEZONE,
    SYMBOL_SUFFIX,
    TRADING_CURRENCY,
    NOTIFY_LANG,
    TG_TOKEN,
    TG_CHAT_ID,
    _NOTIFICATION_CHANNEL,
    _NOTIFICATION_WEBHOOK_URL,
    CASH_ONLY_EXECUTION,
    STRATEGY_RUNTIME,
    STRATEGY_RUNTIME_CONFIG,
    MANAGED_SYMBOLS,
    AVAILABLE_INPUTS,
    BENCHMARK_SYMBOL,
    _emit_v7_loaded_version_log,
    SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS,
    LIMIT_SELL_DISCOUNT,
    LIMIT_BUY_PREMIUM,
    DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL,
    ORDER_POLL_INTERVAL_SEC,
    ORDER_POLL_MAX_ATTEMPTS,
    TOKEN_REFRESH_THRESHOLD_DAYS,
    DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
    DEFAULT_MIN_ORDER_NOTIONAL_USD,
    _FRACTIONAL_BUY_EXECUTION,
    _profile_submit_order,
    _profile_estimate_max_purchase_quantity,
    SEPARATOR,
    COMPACT_ERROR_NOTIFICATION_MAX_CHARS,
    _load_limit_buy_premium_by_symbol,
    LIMIT_BUY_PREMIUM_BY_SYMBOL,
    t,
    _build_notification_delivery_log_for_report,
    _build_notification_delivery_summary,
    signal_text,
    strategy_display_name,
    log_position_snapshot,
    log_runtime_warning,
    BROKER_ADAPTERS,
    STRATEGY_ADAPTERS,
    _safe_haven_cash_substitute_threshold_usd,
    build_composer,
    fetch_account_snapshot_token_with_metadata,
)

from notifications_runtime import (
    _runtime_failure_reason,
    _compact_error_notification,
    _split_env_list,
    _runtime_error_notification_targets,
    _runtime_error_notification_message,
    _send_runtime_error,
    _notify_runtime_error,
    _handle_route_runtime_error,
    _route_with_runtime_error_fallback,
)

from strategy_plugins_runtime import (
    build_strategy_plugin_alert_messages,
    build_strategy_plugin_alert_state_settings,
    build_strategy_plugin_alert_context_label,
    publish_strategy_plugin_alerts,
)

from http_routes import (
    app,
    READ_ONLY_BROKER_RECONCILIATION_COLLECTOR,
    _summarize_cycle_result_for_report,
    _min_order_notional_usd,
    run_strategy,
    _bounded_probe_error_code,
    run_probe,
    _paper_command_consumer_session_date,
    _paper_command_consumer_runtime_is_isolated,
    run_paper_execution_command_consumer,
    run_broker_reconciliation,
    _open_account_history_store,
    run_account_snapshot,
    handle_trigger,
    handle_backfill,
    handle_dry_run,
    handle_probe,
    handle_paper_execution_command_consumer,
    handle_broker_reconciliation,
    handle_account_snapshot,
    handle_v7_paper_application_loaded,
    _v7_application_candidate_bound,
    handle_monitor_dispatch,
    request_method,
    health,
)

__all__ = [
    'FRACTIONAL_BUY_QUANTITY_STEP',
    'Flask',
    'StartupConfigReceipt',
    'StrategyPluginAlertStateSettings',
    'V7_LONGTERM_COMPOUNDING_PROFILE',
    'account_snapshot_enabled',
    'append_runtime_report_error',
    'attach_cycle_execution_receipt',
    'attach_terminal_fallback_execution_receipt',
    'begin_natural_cycle_history',
    'build_alert_context_label',
    'build_contexts',
    'build_run_id',
    'build_runtime_broker_adapters',
    'build_runtime_composer',
    'build_runtime_report_base',
    'build_runtime_strategy_adapters',
    'build_signal_text',
    'build_strategy_display_name',
    'build_strategy_evaluation_inputs',
    'build_strategy_plugin_report_payload',
    'build_translator',
    'calculate_rotation_indicators',
    'collect_read_only_reconciliation_observations',
    'consume_due_paper_execution_commands',
    'cycle_history_observation',
    'dataclasses',
    'datetime',
    'dca_execution_unsupported_reason',
    'dispatch_due_monitors',
    'dispatch_strategy_plugin_alerts',
    'emit_runtime_log',
    'end_natural_cycle_history',
    'estimate_max_purchase_quantity',
    'fetch_last_price',
    'fetch_order_status',
    'fetch_strategy_account_state',
    'fetch_token_from_secret',
    'finalize_runtime_report',
    'fractional_buy_execution_enabled',
    'google',
    'importlib',
    'is_market_open_now',
    'json',
    'load_configured_strategy_plugin_signals',
    'load_monitor_targets',
    'load_platform_runtime_settings',
    'load_strategy_runtime',
    'map_strategy_decision_to_plan',
    'normalize_monitor_dispatch_targets',
    'normalize_plugin_mounts_strategy',
    'os',
    'overlay_env',
    'parse_strategy_plugin_mounts',
    'persist_runtime_report',
    'read_cycle_account_balance',
    'read_cycle_stock_positions',
    'reconciliation_enabled',
    'record_projected_daily_account',
    'refresh_token_if_needed',
    'request',
    'requests',
    'resolve_cycle_channel_and_url',
    'resolve_paper_execution_command_consumer_enabled',
    'run_read_only_account_snapshot',
    'run_read_only_broker_reconciliation',
    'run_rebalance_cycle',
    'submit_order',
    'time',
    'timezone',
    'unified_notifications',
    'validation_cycle_observation',
    'get_project_id',
    'RUNTIME_SETTINGS',
    'PROJECT_ID',
    'SECRET_NAME',
    'ACCOUNT_PREFIX',
    'STRATEGY_PROFILE',
    'STRATEGY_DISPLAY_NAME',
    '_normalize_plugin_mounts_strategy',
    '_adopt_startup_config',
    'MONITOR_DISPATCH_ENV_OVERRIDES',
    'STARTUP_CONFIG_RECEIPT',
    '_load_monitor_dispatch_targets',
    'ACCOUNT_REGION',
    'MARKET',
    'MARKET_CALENDAR',
    'MARKET_TIMEZONE',
    'SYMBOL_SUFFIX',
    'TRADING_CURRENCY',
    'NOTIFY_LANG',
    'TG_TOKEN',
    'TG_CHAT_ID',
    '_NOTIFICATION_CHANNEL',
    '_NOTIFICATION_WEBHOOK_URL',
    'CASH_ONLY_EXECUTION',
    'STRATEGY_RUNTIME',
    'STRATEGY_RUNTIME_CONFIG',
    'MANAGED_SYMBOLS',
    'AVAILABLE_INPUTS',
    'BENCHMARK_SYMBOL',
    '_emit_v7_loaded_version_log',
    'SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS',
    'LIMIT_SELL_DISCOUNT',
    'LIMIT_BUY_PREMIUM',
    'DEFAULT_LIMIT_BUY_PREMIUM_BY_SYMBOL',
    'ORDER_POLL_INTERVAL_SEC',
    'ORDER_POLL_MAX_ATTEMPTS',
    'TOKEN_REFRESH_THRESHOLD_DAYS',
    'DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD',
    'DEFAULT_MIN_ORDER_NOTIONAL_USD',
    '_FRACTIONAL_BUY_EXECUTION',
    '_profile_submit_order',
    '_profile_estimate_max_purchase_quantity',
    'SEPARATOR',
    'COMPACT_ERROR_NOTIFICATION_MAX_CHARS',
    '_load_limit_buy_premium_by_symbol',
    'LIMIT_BUY_PREMIUM_BY_SYMBOL',
    't',
    '_build_notification_delivery_log_for_report',
    '_build_notification_delivery_summary',
    'signal_text',
    'strategy_display_name',
    'log_position_snapshot',
    'log_runtime_warning',
    'BROKER_ADAPTERS',
    'STRATEGY_ADAPTERS',
    '_safe_haven_cash_substitute_threshold_usd',
    'build_composer',
    'fetch_account_snapshot_token_with_metadata',
    '_runtime_failure_reason',
    '_compact_error_notification',
    '_split_env_list',
    '_runtime_error_notification_targets',
    '_runtime_error_notification_message',
    '_send_runtime_error',
    '_notify_runtime_error',
    '_handle_route_runtime_error',
    '_route_with_runtime_error_fallback',
    'build_strategy_plugin_alert_messages',
    'build_strategy_plugin_alert_state_settings',
    'build_strategy_plugin_alert_context_label',
    'publish_strategy_plugin_alerts',
    'app',
    'READ_ONLY_BROKER_RECONCILIATION_COLLECTOR',
    '_summarize_cycle_result_for_report',
    '_min_order_notional_usd',
    'run_strategy',
    '_bounded_probe_error_code',
    'run_probe',
    '_paper_command_consumer_session_date',
    '_paper_command_consumer_runtime_is_isolated',
    'run_paper_execution_command_consumer',
    'run_broker_reconciliation',
    '_open_account_history_store',
    'run_account_snapshot',
    'handle_trigger',
    'handle_backfill',
    'handle_dry_run',
    'handle_probe',
    'handle_paper_execution_command_consumer',
    'handle_broker_reconciliation',
    'handle_account_snapshot',
    'handle_v7_paper_application_loaded',
    '_v7_application_candidate_bound',
    'handle_monitor_dispatch',
    'request_method',
    'health',
]

if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
