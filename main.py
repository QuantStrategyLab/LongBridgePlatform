"""LongBridgePlatform HTTP entrypoint.

Thin facade over the platform split modules. Public helpers are
re-exported by explicit name so existing ``import main`` / monkeypatch
call sites keep working. A module facade forwards attribute writes to
the owning implementation module.
"""
from __future__ import annotations

import sys
import types

for _split_module_name in (
    "runtime_wiring",
    "notifications_runtime",
    "strategy_plugins_runtime",
    "http_routes",
):
    sys.modules.pop(_split_module_name, None)
del _split_module_name

import http_routes as _http_routes
import notifications_runtime as _notifications_runtime
import runtime_wiring as _runtime_wiring
import strategy_plugins_runtime as _strategy_plugins_runtime

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

_IMPL_MODULES = (
    _runtime_wiring,
    _notifications_runtime,
    _strategy_plugins_runtime,
    _http_routes,
)

class _LongBridgeMainFacade(types.ModuleType):
    """Resolve missing attributes from split modules without mutating them.

    ``unittest.mock.patch.object`` restores by deleting temporary facade
    attributes. Keeping writes local to the facade lets ``__getattr__`` return
    the original implementation binding after the patch context exits.
    """

    def __getattr__(self, name: str):
        for module in _IMPL_MODULES:
            if name in module.__dict__:
                return module.__dict__[name]
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __delattr__(self, name: str):
        if name in self.__dict__:
            super().__delattr__(name)
            return
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

sys.modules[__name__].__class__ = _LongBridgeMainFacade

if __name__ == "__main__":
    import os

    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
