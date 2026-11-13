"""Strategy plugin signal presentation and alert dispatch."""

from __future__ import annotations


import sys

_FALLBACK_MODULE_NAME = __name__


class _MainModuleProxy:
    """Prefer ``main.<name>`` (facade-aware); fall back during module import."""

    def __getattr__(self, name):
        main_mod = sys.modules.get("main")
        if main_mod is not None:
            # Use getattr so ModuleType facades can resolve mutable globals.
            try:
                return getattr(main_mod, name)
            except AttributeError:
                pass
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

def build_strategy_plugin_alert_messages(signals):
    return main.STRATEGY_ADAPTERS.build_strategy_plugin_alert_messages(signals)

def build_strategy_plugin_alert_state_settings():
    return main.StrategyPluginAlertStateSettings.from_env(project_id=main.PROJECT_ID)

def build_strategy_plugin_alert_context_label() -> str:
    return main.build_alert_context_label(platform_id='longbridge', strategy_profile=main.STRATEGY_PROFILE, account_scope=main.ACCOUNT_REGION, service_name=main.SECRET_NAME, runtime_target=main.RUNTIME_SETTINGS.runtime_target)

def publish_strategy_plugin_alerts(signals, *, report=None):
    from notifications.digest_snapshot.digest_routing import immediate_plugin_signals
    from notifications.digest_snapshot.digest_candidates import routine_observation_counts
    if report is not None:
        report.setdefault('summary', {})['routine_observation_counts'] = routine_observation_counts(signals)
    result = main.dispatch_strategy_plugin_alerts(immediate_plugin_signals(signals, mode='daily_digest'), notification_settings=main.RUNTIME_SETTINGS, translator=main.t, strategy_label=main.STRATEGY_PROFILE, context_label=main.build_strategy_plugin_alert_context_label(), state_settings=main.build_strategy_plugin_alert_state_settings(), log_message=print)
    if report is not None:
        result.attach_to_report(report)
    return result
