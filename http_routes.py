"""Flask application, cycle runners, and HTTP route handlers."""

from __future__ import annotations


import sys

_FALLBACK_MODULE_NAME = __name__


class _MainModuleProxy:
    """Prefer ``main.<name>`` after re-exports; fall back during module import."""

    def __getattr__(self, name):
        main_mod = sys.modules.get("main")
        if main_mod is not None and name in getattr(main_mod, "__dict__", {}):
            return main_mod.__dict__[name]
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

app = Flask(__name__)
READ_ONLY_BROKER_RECONCILIATION_COLLECTOR = collect_read_only_reconciliation_observations

def _summarize_cycle_result_for_report(cycle_result, *, dry_run: bool) -> dict:
    if cycle_result is None:
        return {'action_done': False, 'order_events_count': 0, 'orders_previewed_count': 0, 'orders_skipped_count': 0, 'notes_count': 0, 'dry_run_order_preview_available': False}
    logs = tuple(getattr(cycle_result, 'logs', ()) or ())
    skip_logs = tuple(getattr(cycle_result, 'skip_logs', ()) or ())
    note_logs = tuple(getattr(cycle_result, 'note_logs', ()) or ())
    dry_run_orders = tuple(getattr(cycle_result, 'dry_run_orders', ()) or ())
    pending_orders = tuple(getattr(cycle_result, 'pending_orders', ()) or ())
    quote_snapshots = tuple(getattr(cycle_result, 'quote_snapshots', ()) or ())
    order_events_count = 0 if pending_orders else len(logs)
    orders_previewed_count = len(dry_run_orders) if dry_run_orders else order_events_count if dry_run else 0
    broker_submission_done = bool(getattr(cycle_result, 'action_done', False))
    summary = {'action_done': broker_submission_done and (not pending_orders), 'broker_submission_done': broker_submission_done, 'execution_status': 'pending_reconciliation' if pending_orders else 'previewed' if dry_run and broker_submission_done else 'no_action', 'order_events_count': order_events_count, 'orders_pending_count': len(pending_orders), 'orders_previewed_count': orders_previewed_count, 'orders_skipped_count': len(skip_logs), 'notes_count': len(note_logs), 'dry_run_order_preview_available': bool(dry_run and orders_previewed_count > 0)}
    if dry_run_orders:
        summary['orders_previewed'] = [dict(order) for order in dry_run_orders]
    if pending_orders:
        summary['orders_pending'] = [dict(order) for order in pending_orders]
    if quote_snapshots:
        summary['quote_snapshot'] = {'quotes': [dict(snapshot) for snapshot in quote_snapshots]}
    execution = dict(getattr(cycle_result, 'execution', {}) or {})
    account_snapshot = execution.get('heartbeat_account_snapshot')
    if isinstance(account_snapshot, dict):
        summary['heartbeat_account_snapshot'] = dict(account_snapshot)
    if execution.get('execution_status') == 'blocked':
        summary['execution_status'] = 'blocked'
        summary['blocked_reason'] = 'durable_live_execution_command_binding_invalid' if execution.get('blocked_reason') == 'durable_live_execution_command_binding_invalid' else 'unknown'
    for field in ('signal_date', 'effective_date', 'execution_timing_contract', 'execution_calendar_source'):
        if execution.get(field) not in (None, ''):
            summary[field] = execution[field]
    durable_command = execution.get('durable_execution_command')
    if isinstance(durable_command, dict):
        summary['durable_execution_command'] = dict(durable_command)
    live_command = execution.get('durable_live_execution_command')
    if isinstance(live_command, dict):
        summary['durable_live_execution_command'] = dict(live_command)
    account_identity = execution.get('account_identity')
    if isinstance(account_identity, dict):
        summary['account_identity'] = dict(account_identity)
    _, summary['notification_digest'] = main.unified_notifications.cycle_facts(cycle_result, dry_run=dry_run)
    return summary

def _min_order_notional_usd() -> float:
    return float(getattr(main.RUNTIME_SETTINGS, 'min_order_notional_usd', main.DEFAULT_MIN_ORDER_NOTIONAL_USD))

def run_strategy(*, force_run: bool=False, validation_only: bool=False, validation_label: str='backfill'):
    if main._v7_application_candidate_bound() and (not validation_only):
        print(f'[{main.datetime.now()}] V7 paper candidate is paused; skip all execution paths.', flush=True)
        return True
    if not validation_only and (not force_run) and (not getattr(main.RUNTIME_SETTINGS, 'runtime_target_enabled', True)):
        print(f'[{main.datetime.now()}] Runtime target disabled; skip strategy execution.', flush=True)
        return True
    composer = main.build_composer(dry_run_only_override=True if validation_only else None)
    observation = main.validation_cycle_observation.current() if validation_only else None
    if observation is not None:
        composer = main.validation_cycle_observation.instrument_composer(composer, observation)
    reporting_adapters = composer.build_reporting_adapters()
    log_context, report = reporting_adapters.start_run()
    notification_delivery_events: list[dict] = []
    try:
        notification_adapters = composer.build_notification_adapters(delivery_events=notification_delivery_events)
    except TypeError as exc:
        if 'delivery_events' not in str(exc):
            raise
        notification_adapters = composer.build_notification_adapters()
    strategy_plugin_signals, strategy_plugin_error = composer.load_strategy_plugin_signals(getattr(main.RUNTIME_SETTINGS, 'strategy_plugin_mounts_json', None))
    composer.attach_strategy_plugin_report(report, signals=strategy_plugin_signals, error=strategy_plugin_error)
    failure_phase = 'market_hours'
    try:
        reporting_adapters.log_event(log_context, 'strategy_cycle_started', message='Starting strategy execution')
        print(composer.with_prefix(f'[{main.datetime.now()}] Starting strategy...'), flush=True)
        market_open = main.is_market_open_now(calendar_name=main.MARKET_CALENDAR, timezone_name=main.MARKET_TIMEZONE)
        if isinstance(market_open, tuple):
            market_open, error = market_open
            if error is not None:
                reporting_adapters.log_event(log_context, 'market_hours_check_failed', message='Market hours check failed', severity='WARNING', error_message=str(error), market=main.MARKET, market_calendar=main.MARKET_CALENDAR, market_timezone=main.MARKET_TIMEZONE)
                print(composer.with_prefix(f'Market hours check failed: {error}'), flush=True)
        if not market_open and (not force_run):
            reporting_adapters.log_event(log_context, 'outside_market_hours', message='Outside market hours; skip execution', market=main.MARKET, market_calendar=main.MARKET_CALENDAR, market_timezone=main.MARKET_TIMEZONE)
            main.finalize_runtime_report(report, status='skipped', diagnostics={'skip_reason': 'market_closed'})
            print(composer.with_prefix('Outside market hours; skip.'), flush=True)
            return True
        if force_run and (not market_open):
            reporting_adapters.log_event(log_context, 'market_hours_bypassed', message=f'Market hours bypassed for {validation_label} execution', market=main.MARKET, market_calendar=main.MARKET_CALENDAR, market_timezone=main.MARKET_TIMEZONE)
            print(composer.with_prefix(f'Market hours bypassed for {validation_label} verification; validation only, no orders will be submitted.'), flush=True)
        unsupported_reason = main.dca_execution_unsupported_reason(main.STRATEGY_PROFILE)
        if unsupported_reason is not None:
            reporting_adapters.log_event(log_context, 'strategy_execution_unsupported', message='Strategy requires fractional-share execution; skip', skip_reason=unsupported_reason, strategy_profile=main.STRATEGY_PROFILE, market=main.MARKET, market_calendar=main.MARKET_CALENDAR, market_timezone=main.MARKET_TIMEZONE)
            main.finalize_runtime_report(report, status='skipped', diagnostics={'skip_reason': unsupported_reason})
            print(composer.with_prefix(f'Strategy {main.STRATEGY_PROFILE} requires fractional-share execution; skip.'), flush=True)
            return True
        if not validation_only:
            main.publish_strategy_plugin_alerts(strategy_plugin_signals, report=report)
        failure_phase = 'runtime_configuration'
        try:
            rebalance_runtime = composer.build_rebalance_runtime(silent_cycle_notifications=validation_only, notification_delivery_events=notification_delivery_events)
        except TypeError as exc:
            if 'notification_delivery_events' not in str(exc):
                raise
            rebalance_runtime = composer.build_rebalance_runtime(silent_cycle_notifications=validation_only)
        if observation is not None:
            validation_bootstrap = getattr(rebalance_runtime.bootstrap, 'validation_cycle', None)
            if not callable(validation_bootstrap):
                raise RuntimeError('validation_read_only_bootstrap_unavailable')
            rebalance_runtime = main.dataclasses.replace(rebalance_runtime, bootstrap=validation_bootstrap, notify_issue=lambda _title, _detail: None)
        rebalance_config = composer.build_rebalance_config(strategy_plugin_signals=strategy_plugin_signals, strategy_plugin_error=strategy_plugin_error, notification_title_key='dry_run_title' if validation_only and validation_label == 'dry_run' else '', cash_only_execution=main.CASH_ONLY_EXECUTION, live_execution_session_authorized=bool(market_open and (not validation_only)))
        if observation is not None:
            main.validation_cycle_observation.require_matching_journal_source(rebalance_config, reporting_adapters)
            observation.report_base_dir = getattr(reporting_adapters, 'report_base_dir', None)
        failure_phase = 'strategy_cycle'
        if observation is not None:
            observation.cycle_entered = True
        with main.unified_notifications.bind_report(report, validation_only=validation_only):
            cycle_result = main.run_rebalance_cycle(runtime=rebalance_runtime, config=rebalance_config)
        if observation is not None:
            observation.cycle_returned = True
        failure_phase = 'reporting'
        signal_snapshot = {}
        if cycle_result is not None:
            execution = dict(getattr(cycle_result, 'execution', {}) or {})
            signal_snapshot = dict(execution.get('signal_snapshot') or {})
        execution_summary = main._summarize_cycle_result_for_report(cycle_result, dry_run=bool(report.get('dry_run')))
        if 'routine_observation_counts' in (report.get('summary') or {}):
            execution_summary['routine_observation_counts'] = report['summary']['routine_observation_counts']
        if not validation_only:
            try:
                main.unified_notifications.attach_cycle_event(execution_summary, cycle_result, report=report, config=rebalance_config)
            except Exception:
                print('notification_event_attachment_unconfirmed', flush=True)
                main.unified_notifications.record_failure('notification_event_attachment_unconfirmed', report=report)
        notification_delivery_log = main._build_notification_delivery_log_for_report(platform='longbridge', strategy_profile=main.STRATEGY_PROFILE, run_id=str(report.get('run_id') or ''), dry_run=bool(report.get('dry_run')), orders_previewed_count=int(execution_summary.get('orders_previewed_count') or 0), delivery_events=notification_delivery_events)
        if notification_delivery_log:
            execution_summary['notification_delivery_log'] = notification_delivery_log
        if main.unified_notifications.enabled():
            notification_delivery_events.extend(({'sink': 'notification_store', 'delivery_status': 'failed', 'transport_acknowledged': False, 'error_type': code} for code in report.get('notification_unified_errors', ())))
        notification_delivery_summary = main._build_notification_delivery_summary(notification_delivery_events)
        if notification_delivery_summary:
            execution_summary['notification_delivery_summary'] = notification_delivery_summary
        if signal_snapshot:
            reporting_adapters.log_event(log_context, 'strategy_signal_snapshot', message='Strategy signal snapshot', **signal_snapshot)
        try:
            main.attach_cycle_execution_receipt(report, cycle_result)
        except ValueError:
            pass
        main.finalize_runtime_report(report, status='ok', summary=execution_summary, diagnostics={'signal_snapshot': signal_snapshot} if signal_snapshot else None)
        reporting_adapters.log_event(log_context, 'strategy_cycle_completed', message='Strategy execution completed')
        return True
    except Exception as exc:
        failure_reason = main._runtime_failure_reason(exc)
        main.append_runtime_report_error(report, stage='strategy_cycle', message='strategy_cycle_failed', error_type=type(exc).__name__)
        reporting_adapters.log_event(log_context, 'strategy_cycle_failed', message='Strategy execution failed', severity='ERROR', error_type=type(exc).__name__, error_message='strategy_cycle_failed', failure_phase=failure_phase, failure_reason=failure_reason)
        if observation is None and (not (validation_only and main._v7_application_candidate_bound())):
            try:

                def publish_failure():
                    return notification_adapters.publish_cycle_notification(detailed_text=main._compact_error_notification(exc, phase=failure_phase), compact_text=main._compact_error_notification(exc, phase=failure_phase))
                main.unified_notifications.dispatch_immediate(publish_failure, kind='real_error', incident=f'runtime_failure:{failure_phase}:{failure_reason}', report=report, actually_ran=True)
            except Exception as notification_exc:
                notification_delivery_events.append({'sink': 'telegram', 'delivery_status': 'failed', 'transport_acknowledged': False, 'error_type': type(notification_exc).__name__})
                reporting_adapters.log_event(log_context, 'strategy_error_notification_failed', message='Strategy error notification failed', severity='ERROR', error_type=type(notification_exc).__name__)
        error_summary = {}
        notification_delivery_summary = main._build_notification_delivery_summary(notification_delivery_events)
        if notification_delivery_summary:
            error_summary['notification_delivery_summary'] = notification_delivery_summary
        main.finalize_runtime_report(report, status='error', summary=error_summary or None, diagnostics={'failure_phase': failure_phase, 'failure_reason': failure_reason})
        return False
    finally:
        if 'execution_receipt' not in report:
            try:
                main.attach_terminal_fallback_execution_receipt(report)
            except ValueError:
                pass
        if observation is not None:
            status = str(report.get('status') or '').strip().lower()
            observation.report_status = status if status in {'ok', 'error', 'skipped'} else 'unknown'
            observation.execution_receipt_present = isinstance(report.get('execution_receipt'), dict)
        if observation is None and (not (validation_only and main._v7_application_candidate_bound())):
            try:
                report_path = reporting_adapters.persist_execution_report(report)
                print(f'execution_report {report_path}', flush=True)
                if not validation_only and report.get('status') in {'ok', 'completed', 'success'}:
                    if not main.unified_notifications.report_archive_confirmed(report, report_path, reporting_adapters.report_gcs_prefix_uri):
                        raise ValueError('notification_report_archive_unconfirmed')
                    if main.unified_notifications.persist_archived_report(report, env=main.os.environ) is not True:
                        raise ValueError('notification_archive_candidate_unconfirmed')
                if observation is None and main.unified_notifications.enabled():
                    main.unified_notifications.recover_runtime(report=report)
            except Exception as persist_exc:
                print(f'failed to persist execution report: {type(persist_exc).__name__}', flush=True)
                if not validation_only and report.get('status') in {'ok', 'completed', 'success'}:
                    main.unified_notifications._storage_failure(lambda title, detail: rebalance_runtime.notifications.send_text(f'{title}\n{detail}'))

def _bounded_probe_error_code(exc: Exception) -> int | None:
    try:
        code = getattr(exc, 'code', None)
    except Exception:
        return None
    if type(code) is not int or not -2 ** 31 <= code <= 2 ** 31 - 1:
        return None
    return code

def run_probe(*, response_body: str='Probe OK'):
    if main._v7_application_candidate_bound():
        return (response_body, 200)
    history_scope = str(main.ACCOUNT_REGION or '').strip()
    record_account_history = history_scope in {'HK', 'SG'} and str(main.os.getenv('ACCOUNT_HISTORY_RECORDING_ENABLED') or '').strip() == 'true'
    history_capture = main.begin_natural_cycle_history() if record_account_history else None
    composer = None
    reporting_adapters = None
    log_context = None
    report = None
    probe_step = 'settings'
    try:
        composer = main.build_composer(dry_run_only_override=True)
        reporting_adapters = composer.build_reporting_adapters()
        log_context, report = reporting_adapters.start_run()
        reporting_adapters.log_event(log_context, 'health_probe_received', message='Received health probe request', execution_window='probe')
        probe_step = 'context'
        runtime = composer.build_rebalance_runtime(silent_cycle_notifications=True)
        if record_account_history:
            probe_step = 'context'
            quote_context, trade_context, source_binding = composer.build_account_snapshot_broker_contexts()
            probe_step = 'balance'
            main.read_cycle_account_balance(lambda: trade_context.account_balance(currency='USD'))
            stock_reader = getattr(trade_context, 'stock_positions', None)
            if callable(stock_reader):
                try:
                    main.read_cycle_stock_positions(stock_reader)
                except Exception:
                    pass
            observation = main.cycle_history_observation()
            if observation is None:
                raise RuntimeError('account history observation incomplete')
            probe_step = 'archive'
            result = main.record_projected_daily_account(main.os.environ, account_scope=history_scope, source_binding=source_binding, balances=observation['projection']['broker_reported_balances'], cash=observation['projection']['cash'], started=observation['started'], finished=observation['finished'], open_store=main._open_account_history_store, financing=observation['projection'].get('financing'), positions=observation['projection'].get('broker_reported_positions'))
            print(f"account_history status={result.status} category={result.category or '-'}", flush=True)
            if result.status != 'recorded':
                raise RuntimeError('account history write was not confirmed')
            probe_step = 'indicators'
            _indicators = composer.strategy_adapters.calculate_strategy_indicators(quote_context)
        else:
            probe_step = 'bootstrap'
            quote_context, trade_context, _indicators = runtime.bootstrap()
        probe_step = 'portfolio_snapshot'
        snapshot = runtime.portfolio_port_factory(quote_context, trade_context).get_portfolio_snapshot()
        positions = tuple(getattr(snapshot, 'positions', ()) or ())
        buying_power = float(getattr(snapshot, 'buying_power', 0.0) or 0.0)
        total_equity = float(getattr(snapshot, 'total_equity', 0.0) or 0.0)
        main.finalize_runtime_report(report, status='ok', summary={'buying_power': buying_power, 'total_equity': total_equity, 'positions_count': len(positions)})
        reporting_adapters.log_event(log_context, 'health_probe_completed', message='Health probe completed', execution_window='probe', buying_power=buying_power, total_equity=total_equity, positions_count=len(positions))
        return (response_body, 200)
    except Exception as exc:
        error_code = main._bounded_probe_error_code(exc)
        error_diagnostics = {'probe_step': probe_step, 'error_code_status': 'known' if error_code is not None else 'unknown'}
        if error_code is not None:
            error_diagnostics['error_code'] = error_code
        if report is not None:
            main.append_runtime_report_error(report, stage='health_probe', message='health_probe_failed', error_type=type(exc).__name__, **error_diagnostics)
            main.finalize_runtime_report(report, status='error')
        if reporting_adapters is not None and log_context is not None:
            reporting_adapters.log_event(log_context, 'health_probe_failed', message='Health probe failed', severity='ERROR', execution_window='probe', error_type=type(exc).__name__, error_message='health_probe_failed', **error_diagnostics)
        err = main._compact_error_notification(exc, title=main.t('health_probe_title'), prefix=main.t('health_probe_error_prefix'), phase='health_probe')
        if composer is not None:

            def publish_probe_failure():
                return composer.build_notification_adapters().publish_cycle_notification(detailed_text=err, compact_text=main._compact_error_notification(exc, title=main.t('health_probe_title'), prefix=main.t('health_probe_error_prefix'), phase='health_probe'))
            main.unified_notifications.dispatch_immediate(publish_probe_failure, kind='real_error', incident='runtime_failure:health_probe', report=report)
        else:
            print(err, flush=True)
        return ('Error', 500)
    finally:
        if history_capture is not None:
            main.end_natural_cycle_history(history_capture)
        try:
            if reporting_adapters is not None and report is not None:
                report_path = reporting_adapters.persist_execution_report(report)
                print(f'execution_report {report_path}', flush=True)
                if main.unified_notifications.enabled():
                    main.unified_notifications.recover_runtime(report=report, probe=True)
        except Exception as persist_exc:
            print(f'failed to persist execution report: {type(persist_exc).__name__}', flush=True)

def _paper_command_consumer_session_date() -> str:
    """Return the exchange-local session date used to claim due commands."""
    try:
        import pytz
        return main.datetime.now(pytz.timezone(main.MARKET_TIMEZONE)).date().isoformat()
    except Exception:
        return main.datetime.now().date().isoformat()

def _paper_command_consumer_runtime_is_isolated() -> bool:
    runtime_target = getattr(main.RUNTIME_SETTINGS, 'runtime_target', None)
    return bool(getattr(main.RUNTIME_SETTINGS, 'dry_run_only', False) and runtime_target is not None and (str(getattr(runtime_target, 'execution_mode', '') or '').lower() == 'paper'))

def run_paper_execution_command_consumer() -> bool:
    """Run an explicitly invoked, read-only paper command consumer.

    This handler never constructs an execution port or calls the LongBridge
    order API.  It is deliberately separate from ``/run`` so a normal cycle
    cannot start consuming delayed commands by accident.
    """
    if main._v7_application_candidate_bound():
        return True
    if not main._paper_command_consumer_runtime_is_isolated():
        raise RuntimeError('paper command consumer requires RUNTIME_TARGET_JSON.execution_mode=paper and LONGBRIDGE_DRY_RUN_ONLY=true')
    if not main.resolve_paper_execution_command_consumer_enabled(env_reader=main.os.getenv, dry_run_only=bool(main.RUNTIME_SETTINGS.dry_run_only)):
        raise RuntimeError('paper command consumer is not enabled')
    composer = main.build_composer()
    config = composer.build_rebalance_config()
    reporting_adapters = composer.build_reporting_adapters()
    log_context, report = reporting_adapters.start_run()
    try:
        reporting_adapters.log_event(log_context, 'paper_execution_command_consumer_started', message='Starting read-only paper execution command consumer')
        quote_context, trade_context = composer.build_read_only_broker_contexts()
        portfolio = composer.broker_adapters.build_portfolio_port(quote_context, trade_context).get_portfolio_snapshot()
        result = main.consume_due_paper_execution_commands(store=config.execution_command_store, as_of_session=main._paper_command_consumer_session_date(), claimant=str(main.os.getenv('K_SERVICE') or 'longbridge-paper-command-consumer'), portfolio=portfolio, market_data_port=composer.broker_adapters.build_market_data_port(quote_context), runtime_release_receipt=config.runtime_release_receipt, expected_strategy_release=config.expected_strategy_release, expected_command_binding={'platform': 'longbridge', 'account_scope': str(config.execution_state_account_scope or 'unknown'), 'strategy_profile': str(config.strategy_profile or 'unknown')})
        report_status = 'ok' if result.get('status') == 'ok' else 'skipped'
        main.finalize_runtime_report(report, status=report_status, summary={'paper_execution_command_consumer': result})
        reporting_adapters.log_event(log_context, 'paper_execution_command_consumer_completed', message='Paper execution command consumer completed', result_status=result.get('status'), commands_count=len(tuple(result.get('commands') or ())))
        return True
    except Exception as exc:
        main.append_runtime_report_error(report, stage='paper_execution_command_consumer', message=str(exc), error_type=type(exc).__name__)
        main.finalize_runtime_report(report, status='error')
        reporting_adapters.log_event(log_context, 'paper_execution_command_consumer_failed', message='Paper execution command consumer failed', severity='ERROR', error_type=type(exc).__name__, error_message=str(exc))
        return False
    finally:
        try:
            report_path = reporting_adapters.persist_execution_report(report)
            print(f'execution_report {report_path}', flush=True)
        except Exception as persist_exc:
            print(f'failed to persist execution report: {persist_exc}', flush=True)

def run_broker_reconciliation():
    """Run the explicitly enabled, order-port-free reconciliation boundary."""
    return main.run_read_only_broker_reconciliation(enabled=main.reconciliation_enabled(main.os.getenv), account_scope=getattr(main.RUNTIME_SETTINGS, 'account_region', None), runtime_target=getattr(main.RUNTIME_SETTINGS, 'runtime_target', None), strategy_profile=main.STRATEGY_PROFILE, project_id=main.PROJECT_ID, build_read_only_contexts=lambda: main.build_composer().build_read_only_broker_contexts(), collect_evidence=main.READ_ONLY_BROKER_RECONCILIATION_COLLECTOR)

def _open_account_history_store(project_id):
    from quant_platform_kit.cloud import get_object_store
    return get_object_store(project_id=project_id)

def run_account_snapshot():
    """Read bounded account facts through the order-port-free broker contexts."""
    return main.run_read_only_account_snapshot(enabled=main.account_snapshot_enabled(main.os.getenv), account_scope=getattr(main.RUNTIME_SETTINGS, 'account_region', None), build_read_only_contexts=lambda: main.build_composer().build_account_snapshot_broker_contexts(), collect_evidence=main.READ_ONLY_BROKER_RECONCILIATION_COLLECTOR)

@app.route('/run', methods=['POST'])
def handle_trigger():
    """Entrypoint for Cloud Run / scheduler: run strategy and return 200."""
    if main.request_method() != 'POST':
        return ('Method Not Allowed', 405)
    return main._route_with_runtime_error_fallback(main.run_strategy, success_body='OK', route_label='POST /run')

@app.route('/backfill', methods=['POST', 'GET'])
def handle_backfill():
    """Manual backfill entrypoint for verification-only execution."""

    def run_validation():
        return main._route_with_runtime_error_fallback(main.run_strategy, force_run=True, validation_only=True, success_body='OK', route_label='POST /backfill')
    if str(main.ACCOUNT_REGION or '').strip().upper() != 'PAPER':
        return run_validation()
    headers = getattr(main.request, 'headers', {})
    with main.validation_cycle_observation.observe(headers.get('X-Cloud-Trace-Context'), main.PROJECT_ID):
        return run_validation()

@app.route('/dry-run', methods=['POST', 'GET'])
def handle_dry_run():
    """Strategy dry-run entrypoint."""

    def run_validation():
        return main._route_with_runtime_error_fallback(main.run_strategy, force_run=True, validation_only=True, validation_label='dry_run', success_body='Dry Run OK', route_label='POST /dry-run')
    if str(main.ACCOUNT_REGION or '').strip().upper() != 'PAPER':
        return run_validation()
    headers = getattr(main.request, 'headers', {})
    with main.validation_cycle_observation.observe(headers.get('X-Cloud-Trace-Context'), main.PROJECT_ID):
        return run_validation()

@app.route('/probe', methods=['POST'])
def handle_probe():
    """Post-open broker/account health probe; notify only on failure."""
    if main.request_method() != 'POST':
        return ('Method Not Allowed', 405)
    return main._route_with_runtime_error_fallback(main.run_probe, success_body='Probe OK', route_label='POST /probe')

@app.route('/paper-command-consumer', methods=['POST'])
def handle_paper_execution_command_consumer():
    """Explicit paper-only command verification endpoint; never scheduler-routed."""
    return main._route_with_runtime_error_fallback(main.run_paper_execution_command_consumer, success_body='Paper command consumer OK', route_label='POST /paper-command-consumer')

@app.route('/reconcile', methods=['POST'])
def handle_broker_reconciliation():
    """Return only redacted QPK E3 evidence; never invoke normal execution."""
    if main.request_method() != 'POST':
        return ('Method Not Allowed', 405)
    payload, status = main.run_broker_reconciliation()
    return (main.json.dumps(payload, ensure_ascii=True), status, {'Content-Type': 'application/json'})

@app.route('/account-snapshot', methods=['GET'])
def handle_account_snapshot():
    """Return a non-cacheable, read-only and authority-free account snapshot."""
    if main.request_method() != 'GET':
        return ('Method Not Allowed', 405)
    payload, status = main.run_account_snapshot()
    return (main.json.dumps(payload, ensure_ascii=True), status, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'})

@app.route('/v7-paper-application-loaded', methods=['GET'])
def handle_v7_paper_application_loaded():
    """Read back the disabled V7 process identity without broker access."""
    if main.request_method() != 'GET':
        return ('Method Not Allowed', 405)
    from application.v7_paper_application import V7PaperApplicationError, build_v7_loaded_version_readback, load_v7_paper_application_binding
    try:
        binding = load_v7_paper_application_binding(main.os.environ)
        if binding is None:
            return (main.json.dumps({'status': 'unavailable', 'reason': 'application_binding_missing'}, ensure_ascii=True), 409, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
        payload = build_v7_loaded_version_readback(binding, runtime_settings=main.RUNTIME_SETTINGS, strategy_runtime=main.STRATEGY_RUNTIME, runtime_env=main.os.environ)
    except V7PaperApplicationError:
        return (main.json.dumps({'status': 'uncertain', 'reason': 'loaded_version_unavailable'}, ensure_ascii=True), 409, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
    return (main.json.dumps({'status': 'applied_paused', 'readback': payload}, ensure_ascii=True), 200, {'Content-Type': 'application/json', 'Cache-Control': 'no-store'})

def _v7_application_candidate_bound() -> bool:
    """Keep the exact V7 binding paused even when a route bypasses the flag."""
    if main.STRATEGY_PROFILE != main.V7_LONGTERM_COMPOUNDING_PROFILE:
        return False
    from application.v7_paper_application import load_v7_paper_application_binding
    return load_v7_paper_application_binding(main.os.environ) is not None

@app.route('/monitor-dispatch', methods=['POST', 'GET'])
def handle_monitor_dispatch():
    if main.request_method() == 'GET':
        return ('Monitor Dispatch OK - use POST to dispatch due monitor checks', 200)
    try:
        result = main.dispatch_due_monitors(main._load_monitor_dispatch_targets())
    except Exception as exc:
        return main._handle_route_runtime_error(exc, route_label='POST /monitor-dispatch')
    return (main.json.dumps(result, ensure_ascii=False), 200, {'Content-Type': 'application/json'})

def request_method() -> str:
    try:
        return main.request.method
    except Exception:
        return 'GET'

@app.route('/healthz', methods=['GET'])
@app.route('/health', methods=['GET'])
def health():
    critical_errors: list[str] = []
    for module_path in ('application.runtime_composer', 'application.runtime_reporting_adapters', 'application.runtime_strategy_adapters', 'runtime_config_support', 'runtime_execution_policy'):
        try:
            main.importlib.import_module(module_path)
        except Exception as exc:
            critical_errors.append(f'{module_path}: {type(exc).__name__}: {exc}')
    if critical_errors:
        return (main.json.dumps({'status': 'unhealthy', 'errors': critical_errors}, ensure_ascii=False), 500)
    return ('OK', 200)
