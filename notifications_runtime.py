"""Runtime error / notification helpers for HTTP routes."""

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

def _runtime_failure_reason(exc: Exception) -> str:
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_journal_read_failed':
        return 'validation_journal_read_failed'
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_journal_unavailable':
        return 'validation_journal_unavailable'
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_journal_empty':
        return 'validation_journal_empty'
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_side_effect_enabled':
        return 'validation_side_effect_enabled'
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_marker_write_blocked':
        return 'validation_marker_write_blocked'
    if isinstance(exc, RuntimeError) and str(exc) == 'validation_read_only_bootstrap_unavailable':
        return 'validation_read_only_bootstrap_unavailable'
    if isinstance(exc, RuntimeError) and str(exc) in {'durable execution command producer is paper-only and cannot be enabled live', 'durable execution command consumer is paper-only and cannot be enabled live', 'strategy risk state persistence is paper-only and cannot be enabled live'}:
        return 'paper_live_conflict'
    return 'unknown'

def _compact_error_notification(exc: Exception, *, title: str | None=None, prefix: str='', phase: str='strategy_cycle') -> str:
    error_text = type(exc).__name__
    known_phases = {'market_hours', 'runtime_configuration', 'strategy_cycle', 'reporting', 'health_probe'}
    safe_phase = phase if phase in known_phases else 'strategy_cycle'
    reason = main._runtime_failure_reason(exc)
    message = '\n'.join((title or main.t('error_title'), main.t('failure_phase', phase=main.t(f'failure_phase_{safe_phase}')), main.t(f'failure_reason_{reason}'), f'{prefix}{error_text}'))
    if len(message) <= main.COMPACT_ERROR_NOTIFICATION_MAX_CHARS:
        return message
    return message[:main.COMPACT_ERROR_NOTIFICATION_MAX_CHARS - 1].rstrip() + '…'

def _split_env_list(value: str | None) -> tuple[str, ...]:
    return tuple((item.strip() for item in str(value or '').replace(';', ',').split(',') if item.strip()))

def _runtime_error_notification_targets() -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    if main.TG_TOKEN and main.TG_CHAT_ID:
        targets.append((main.TG_TOKEN, main.TG_CHAT_ID))
    seen: set[tuple[str, str]] = set()
    unique_targets: list[tuple[str, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        unique_targets.append(target)
    return tuple(unique_targets)

def _runtime_error_notification_message(exc: Exception, *, route_label: str) -> str:
    title = 'runtime_failure_title'
    return '\n'.join((main.t(title), main.t('strategy_label', name=main.strategy_display_name or main.STRATEGY_PROFILE), main.t('runtime_failure_context', context=main.ACCOUNT_REGION), main.t('runtime_failure_result'), main.t('runtime_failure_action')))

def _send_runtime_error(exc: Exception, *, route_label: str) -> bool:
    targets = main._runtime_error_notification_targets()
    if not targets:
        print(main.t('runtime_notification_missing_target'), flush=True)
        return False
    message = main._runtime_error_notification_message(exc, route_label=route_label)
    outcomes = []
    for token, chat_id in targets:
        try:
            response = main.requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat_id, 'text': message}, timeout=10)
            status_code = int(getattr(response, 'status_code', 200) or 200)
            acknowledged = 200 <= status_code < 300
            load_payload = getattr(response, 'json', None)
            payload = load_payload() if acknowledged and callable(load_payload) else None
            if isinstance(payload, dict) and payload.get('ok') is False:
                acknowledged = False
            outcomes.append(acknowledged)
        except Exception:
            print(main.t('runtime_notification_delivery_failed'), flush=True)
            outcomes.append(False)
    return bool(outcomes) and all(outcomes)

def _notify_runtime_error(exc: Exception, *, route_label: str) -> bool:
    return main.unified_notifications.dispatch_immediate(lambda: main._send_runtime_error(exc, route_label=route_label), kind='real_error', incident=f'runtime_route:{route_label}:{main._runtime_failure_reason(exc)}', occurred_at=main.datetime.now(main.timezone.utc), run_id=main.build_run_id()) if main.unified_notifications.enabled() else main._send_runtime_error(exc, route_label=route_label)

def _handle_route_runtime_error(exc: Exception, *, route_label: str):
    print(main.t('runtime_failure_log'), flush=True)
    if main.validation_cycle_observation.current() is None:
        main._notify_runtime_error(exc, route_label=route_label)
    return ('Error', 500)

def _route_with_runtime_error_fallback(handler, *, success_body: str, route_label: str, **kwargs):
    try:
        if main.unified_notifications.enabled():
            with main.unified_notifications.bind_route(route_label):
                result = handler(**kwargs)
        else:
            result = handler(**kwargs)
    except Exception as exc:
        return main._handle_route_runtime_error(exc, route_label=route_label)
    if result is False:
        return ('Error', 500)
    if isinstance(result, tuple):
        return result
    return (success_body, 200)
