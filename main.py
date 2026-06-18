"""
LongPort Cloud Run strategy executor.
Runs the configured shared UsEquityStrategies profile for the deployed LongBridge account.
Runs on Cloud Run; token from Secret Manager, orders via LongPort OpenAPI, alerts via Telegram.
"""
import os
import time
import traceback
from datetime import datetime

from flask import Flask

import google.auth
import requests
from application.runtime_broker_adapters import build_runtime_broker_adapters
from application.runtime_composer import build_runtime_composer
from application.rebalance_service import run_strategy as run_rebalance_cycle
from application.runtime_strategy_adapters import build_runtime_strategy_adapters
from application.longbridge_execution import submit_order
from application.longbridge_portfolio import fetch_strategy_account_state
from entrypoints.cloud_run import is_market_open_now
from runtime_config_support import load_platform_runtime_settings
from notifications.telegram import build_signal_text, build_strategy_display_name, build_translator
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
from quant_platform_kit.strategy_contracts import build_strategy_evaluation_inputs
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

# ---------------------------------------------------------------------------
# Config and constants (GCP project, Telegram, execution and strategy params)
# ---------------------------------------------------------------------------
def get_project_id():
    try:
        _, project_id = google.auth.default()
        return project_id
    except Exception:
        return os.getenv("GOOGLE_CLOUD_PROJECT")

RUNTIME_SETTINGS = load_platform_runtime_settings(project_id_resolver=get_project_id)
PROJECT_ID = RUNTIME_SETTINGS.project_id
SECRET_NAME = RUNTIME_SETTINGS.secret_name
ACCOUNT_PREFIX = RUNTIME_SETTINGS.account_prefix
STRATEGY_PROFILE = RUNTIME_SETTINGS.strategy_profile
STRATEGY_DISPLAY_NAME = RUNTIME_SETTINGS.strategy_display_name
ACCOUNT_REGION = RUNTIME_SETTINGS.account_region
MARKET = RUNTIME_SETTINGS.market
MARKET_CALENDAR = RUNTIME_SETTINGS.market_calendar
MARKET_TIMEZONE = RUNTIME_SETTINGS.market_timezone
SYMBOL_SUFFIX = RUNTIME_SETTINGS.symbol_suffix
TRADING_CURRENCY = RUNTIME_SETTINGS.trading_currency
NOTIFY_LANG = RUNTIME_SETTINGS.notify_lang
TG_TOKEN = RUNTIME_SETTINGS.tg_token
TG_CHAT_ID = RUNTIME_SETTINGS.tg_chat_id
STRATEGY_RUNTIME = load_strategy_runtime(
    STRATEGY_PROFILE,
    runtime_settings=RUNTIME_SETTINGS,
    logger=lambda message: print(message, flush=True),
)
STRATEGY_RUNTIME_CONFIG = dict(STRATEGY_RUNTIME.merged_runtime_config)
MANAGED_SYMBOLS = STRATEGY_RUNTIME.managed_symbols
AVAILABLE_INPUTS = frozenset(STRATEGY_RUNTIME.runtime_adapter.available_inputs)
BENCHMARK_SYMBOL = str(STRATEGY_RUNTIME_CONFIG.get("benchmark_symbol", "QQQ"))
SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS = getattr(
    getattr(STRATEGY_RUNTIME.runtime_adapter, "runtime_policy", None),
    "signal_effective_after_trading_days",
    None,
)

# Order pricing: limit order discount/premium relative to last price
LIMIT_SELL_DISCOUNT = 0.995               # sell limit at 0.5% below last
LIMIT_BUY_PREMIUM = 1.005                 # buy limit at 0.5% above last

# Order monitoring: poll interval and max attempts for fill check
ORDER_POLL_INTERVAL_SEC = 1
ORDER_POLL_MAX_ATTEMPTS = 8

# Token refresh: days before expiry to trigger refresh
TOKEN_REFRESH_THRESHOLD_DAYS = 30
DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0
DEFAULT_MIN_ORDER_NOTIONAL_USD = 100.0

SEPARATOR = "━━━━━━━━━━━━━━━━━━"

def t(key, **kwargs):
    return build_translator(NOTIFY_LANG)(key, **kwargs)


def _split_env_list(value: str | None) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    )


def _summarize_cycle_result_for_report(cycle_result, *, dry_run: bool) -> dict:
    if cycle_result is None:
        return {
            "action_done": False,
            "order_events_count": 0,
            "orders_previewed_count": 0,
            "orders_skipped_count": 0,
            "notes_count": 0,
            "dry_run_order_preview_available": False,
        }
    logs = tuple(getattr(cycle_result, "logs", ()) or ())
    skip_logs = tuple(getattr(cycle_result, "skip_logs", ()) or ())
    note_logs = tuple(getattr(cycle_result, "note_logs", ()) or ())
    dry_run_orders = tuple(getattr(cycle_result, "dry_run_orders", ()) or ())
    quote_snapshots = tuple(getattr(cycle_result, "quote_snapshots", ()) or ())
    order_events_count = len(logs)
    orders_previewed_count = len(dry_run_orders) if dry_run_orders else (order_events_count if dry_run else 0)
    summary = {
        "action_done": bool(getattr(cycle_result, "action_done", False)),
        "order_events_count": order_events_count,
        "orders_previewed_count": orders_previewed_count,
        "orders_skipped_count": len(skip_logs),
        "notes_count": len(note_logs),
        "dry_run_order_preview_available": bool(dry_run and orders_previewed_count > 0),
    }
    if dry_run_orders:
        summary["orders_previewed"] = [dict(order) for order in dry_run_orders]
    if quote_snapshots:
        summary["quote_snapshot"] = {
            "quotes": [dict(snapshot) for snapshot in quote_snapshots],
        }
    return summary


def _build_notification_delivery_log_for_report(
    *,
    platform: str,
    strategy_profile: str,
    run_id: str,
    dry_run: bool,
    orders_previewed_count: int,
    delivery_events: list[dict],
) -> dict:
    events = [dict(event) for event in delivery_events if dict(event).get("delivery_status") == "sent"]
    if not dry_run or orders_previewed_count <= 0 or not events:
        return {}
    return {
        "notification_schema_version": "hk_live_enablement_notification.v1",
        "notification_event_type": "hk_snapshot_live_enablement_dry_run",
        "notification_correlation_id": str(run_id or ""),
        "locales": ["en", "zh-Hans"],
        "profile": str(strategy_profile or ""),
        "platform": str(platform or ""),
        "validation_status": "passed",
        "orders_previewed": int(orders_previewed_count),
        "delivery_events": events,
        "notification_contains_profile": True,
        "notification_contains_platform": True,
        "notification_contains_validation_status": True,
        "notification_contains_order_preview_summary": True,
        "notification_redacts_sensitive_fields": True,
        "redaction_policy": "raw notification text is not persisted; only sha256 and length are recorded",
    }


signal_text = build_signal_text(t)
strategy_display_name = build_strategy_display_name(t)(
    STRATEGY_PROFILE,
    fallback_name=STRATEGY_DISPLAY_NAME,
)


def log_position_snapshot(message):
    print(f"[{ACCOUNT_REGION}] {message}", flush=True)


def log_runtime_warning(message):
    print(f"[{ACCOUNT_REGION}] [warning] {message}", flush=True)


BROKER_ADAPTERS = build_runtime_broker_adapters(
    strategy_symbols=tuple(MANAGED_SYMBOLS),
    account_hash=ACCOUNT_PREFIX or ACCOUNT_REGION or "longbridge",
    fetch_last_price_fn=fetch_last_price,
    fetch_strategy_account_state_fn=lambda quote_context, trade_context: fetch_strategy_account_state(
        quote_context,
        trade_context,
        list(MANAGED_SYMBOLS),
        cash_currency=TRADING_CURRENCY,
        position_log_fn=(
            log_position_snapshot
            if getattr(RUNTIME_SETTINGS, "debug_position_snapshot", False)
            else None
        ),
        warning_log_fn=log_runtime_warning,
    ),
    submit_order_fn=submit_order,
    symbol_suffix=SYMBOL_SUFFIX,
    currency=TRADING_CURRENCY,
)
STRATEGY_ADAPTERS = build_runtime_strategy_adapters(
    strategy_runtime=STRATEGY_RUNTIME,
    strategy_profile=STRATEGY_PROFILE,
    strategy_runtime_config=STRATEGY_RUNTIME_CONFIG,
    available_inputs=AVAILABLE_INPUTS,
    benchmark_symbol=BENCHMARK_SYMBOL,
    signal_text_fn=signal_text,
    translator=t,
    broker_adapters=BROKER_ADAPTERS,
    calculate_rotation_indicators_fn=calculate_rotation_indicators,
    build_strategy_evaluation_inputs_fn=build_strategy_evaluation_inputs,
    map_strategy_decision_to_plan_fn=map_strategy_decision_to_plan,
    execution_policy={
        "reserved_cash_floor_usd": getattr(RUNTIME_SETTINGS, "reserved_cash_floor_usd", 0.0),
        "reserved_cash_ratio": getattr(RUNTIME_SETTINGS, "reserved_cash_ratio", 0.0),
    },
    build_strategy_plugin_report_payload_fn=build_strategy_plugin_report_payload,
    load_configured_strategy_plugin_signals_fn=load_configured_strategy_plugin_signals,
    parse_strategy_plugin_mounts_fn=parse_strategy_plugin_mounts,
)


def _safe_haven_cash_substitute_threshold_usd() -> float:
    return float(
        getattr(
            RUNTIME_SETTINGS,
            "safe_haven_cash_substitute_threshold_usd",
            DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
        )
    )


def _min_order_notional_usd() -> float:
    return float(getattr(RUNTIME_SETTINGS, "min_order_notional_usd", DEFAULT_MIN_ORDER_NOTIONAL_USD))


def build_composer(*, dry_run_only_override: bool | None = None):
    return build_runtime_composer(
        project_id=PROJECT_ID,
        secret_name=SECRET_NAME,
        token_refresh_threshold_days=TOKEN_REFRESH_THRESHOLD_DAYS,
        account_prefix=ACCOUNT_PREFIX,
        account_region=ACCOUNT_REGION,
        strategy_profile=STRATEGY_PROFILE,
        strategy_display_name=STRATEGY_DISPLAY_NAME,
        strategy_display_name_localized=strategy_display_name,
        strategy_domain=RUNTIME_SETTINGS.strategy_domain,
        notify_lang=NOTIFY_LANG,
        tg_token=TG_TOKEN,
        tg_chat_id=TG_CHAT_ID,
        managed_symbols=tuple(MANAGED_SYMBOLS),
        benchmark_symbol=BENCHMARK_SYMBOL,
        signal_effective_after_trading_days=SIGNAL_EFFECTIVE_AFTER_TRADING_DAYS,
        separator=SEPARATOR,
        limit_sell_discount=LIMIT_SELL_DISCOUNT,
        limit_buy_premium=LIMIT_BUY_PREMIUM,
        order_poll_interval_sec=ORDER_POLL_INTERVAL_SEC,
        order_poll_max_attempts=ORDER_POLL_MAX_ATTEMPTS,
        safe_haven_cash_substitute_threshold_usd=_safe_haven_cash_substitute_threshold_usd(),
        min_order_notional_usd=_min_order_notional_usd(),
        market=MARKET,
        symbol_suffix=SYMBOL_SUFFIX,
        trading_currency=TRADING_CURRENCY,
        dry_run_only=RUNTIME_SETTINGS.dry_run_only,
        dry_run_only_override=dry_run_only_override,
        broker_adapters=BROKER_ADAPTERS,
        strategy_adapters=STRATEGY_ADAPTERS,
        estimate_max_purchase_quantity_fn=estimate_max_purchase_quantity,
        fetch_order_status_fn=fetch_order_status,
        fetch_token_from_secret_fn=fetch_token_from_secret,
        refresh_token_if_needed_fn=refresh_token_if_needed,
        build_contexts_fn=build_contexts,
        run_id_builder=build_run_id,
        event_logger=emit_runtime_log,
        report_builder=build_runtime_report_base,
        report_persister=persist_runtime_report,
        translator=t,
        runtime_target=RUNTIME_SETTINGS.runtime_target,
        env_reader=os.getenv,
        sleeper=time.sleep,
        printer=print,
        extra_reporting_fields={
            "market": MARKET,
            "market_calendar": MARKET_CALENDAR,
            "market_timezone": MARKET_TIMEZONE,
            "symbol_suffix": SYMBOL_SUFFIX,
            "trading_currency": TRADING_CURRENCY,
        },
    )


def build_strategy_plugin_alert_messages(signals):
    return STRATEGY_ADAPTERS.build_strategy_plugin_alert_messages(signals)


def _runtime_error_notification_targets() -> tuple[tuple[str, str], ...]:
    targets: list[tuple[str, str]] = []
    if TG_TOKEN and TG_CHAT_ID:
        targets.append((TG_TOKEN, TG_CHAT_ID))

    seen: set[tuple[str, str]] = set()
    unique_targets: list[tuple[str, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        unique_targets.append(target)
    return tuple(unique_targets)


def _runtime_error_notification_message(exc: Exception, *, route_label: str) -> str:
    error_text = f"{type(exc).__name__}: {exc}"
    if len(error_text) > 1200:
        error_text = error_text[:1197] + "..."
    if str(NOTIFY_LANG or "").strip().lower().startswith("zh"):
        return "\n".join(
            (
                "LongBridge 策略运行失败",
                f"服务: {os.getenv('K_SERVICE') or SECRET_NAME or 'longbridge-platform'}",
                f"版本: {os.getenv('K_REVISION') or '<unknown>'}",
                f"路由: {route_label}",
                f"策略: {STRATEGY_PROFILE}",
                f"账户范围: {ACCOUNT_REGION}",
                f"错误: {error_text}",
            )
        )
    return "\n".join(
        (
            "LongBridge strategy run failed",
            f"service: {os.getenv('K_SERVICE') or SECRET_NAME or 'longbridge-platform'}",
            f"revision: {os.getenv('K_REVISION') or '<unknown>'}",
            f"route: {route_label}",
            f"strategy: {STRATEGY_PROFILE}",
            f"account_scope: {ACCOUNT_REGION}",
            f"error: {error_text}",
        )
    )


def _notify_runtime_error(exc: Exception, *, route_label: str) -> bool:
    targets = _runtime_error_notification_targets()
    if not targets:
        print("LongBridge runtime error notification skipped: no Telegram target configured.", flush=True)
        return False
    message = _runtime_error_notification_message(exc, route_label=route_label)
    for token, chat_id in targets:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message},
                timeout=10,
            )
        except Exception as send_exc:
            print(f"LongBridge runtime error Telegram send failed: {send_exc}", flush=True)
    return True


def _handle_route_runtime_error(exc: Exception, *, route_label: str):
    print(f"LongBridge route failed before strategy-cycle handling: {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()
    _notify_runtime_error(exc, route_label=route_label)
    return "Error", 500


def _route_with_runtime_error_fallback(handler, *, success_body: str, route_label: str, **kwargs):
    try:
        result = handler(**kwargs)
    except Exception as exc:
        return _handle_route_runtime_error(exc, route_label=route_label)
    if result is False:
        return "Error", 500
    if isinstance(result, tuple):
        return result
    return success_body, 200


def build_strategy_plugin_alert_state_settings():
    return StrategyPluginAlertStateSettings.from_env(
        gcp_project_id=PROJECT_ID,
    )


def build_strategy_plugin_alert_context_label() -> str:
    return build_alert_context_label(
        platform_id="longbridge",
        strategy_profile=STRATEGY_PROFILE,
        account_scope=ACCOUNT_REGION,
        service_name=SECRET_NAME,
        runtime_target=RUNTIME_SETTINGS.runtime_target,
    )


def publish_strategy_plugin_alerts(signals, *, report=None):
    result = dispatch_strategy_plugin_alerts(
        signals,
        notification_settings=RUNTIME_SETTINGS,
        translator=t,
        strategy_label=STRATEGY_PROFILE,
        context_label=build_strategy_plugin_alert_context_label(),
        state_settings=build_strategy_plugin_alert_state_settings(),
        log_message=print,
    )
    if report is not None:
        result.attach_to_report(report)
    return result


def run_strategy(*, force_run: bool = False, validation_only: bool = False, validation_label: str = "backfill"):
    if not validation_only and not force_run and not getattr(RUNTIME_SETTINGS, "runtime_target_enabled", True):
        print(f"[{datetime.now()}] Runtime target disabled; skip strategy execution.", flush=True)
        return True
    composer = build_composer(dry_run_only_override=True if validation_only else None)
    reporting_adapters = composer.build_reporting_adapters()
    log_context, report = reporting_adapters.start_run()
    notification_adapters = composer.build_notification_adapters()
    strategy_plugin_signals, strategy_plugin_error = composer.load_strategy_plugin_signals(
        getattr(RUNTIME_SETTINGS, "strategy_plugin_mounts_json", None)
    )
    composer.attach_strategy_plugin_report(
        report,
        signals=strategy_plugin_signals,
        error=strategy_plugin_error,
    )
    try:
        reporting_adapters.log_event(
            log_context,
            "strategy_cycle_started",
            message="Starting strategy execution",
        )
        print(composer.with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

        market_open = is_market_open_now(
            calendar_name=MARKET_CALENDAR,
            timezone_name=MARKET_TIMEZONE,
        )
        if isinstance(market_open, tuple):
            market_open, error = market_open
            reporting_adapters.log_event(
                log_context,
                "market_hours_check_failed",
                message="Market hours check failed",
                severity="WARNING",
                error_message=str(error),
                market=MARKET,
                market_calendar=MARKET_CALENDAR,
                market_timezone=MARKET_TIMEZONE,
            )
            print(composer.with_prefix(f"Market hours check failed: {error}"), flush=True)
        if not market_open and not force_run:
            reporting_adapters.log_event(
                log_context,
                "outside_market_hours",
                message="Outside market hours; skip execution",
                market=MARKET,
                market_calendar=MARKET_CALENDAR,
                market_timezone=MARKET_TIMEZONE,
            )
            finalize_runtime_report(
                report,
                status="skipped",
                diagnostics={
                    "skip_reason": "market_closed",
                },
            )
            print(composer.with_prefix("Outside market hours; skip."), flush=True)
            return True
        if force_run and not market_open:
            reporting_adapters.log_event(
                log_context,
                "market_hours_bypassed",
                message=f"Market hours bypassed for {validation_label} execution",
                market=MARKET,
                market_calendar=MARKET_CALENDAR,
                market_timezone=MARKET_TIMEZONE,
            )
            print(
                composer.with_prefix(
                    f"Market hours bypassed for {validation_label} verification; validation only, no orders will be submitted."
                ),
                flush=True,
            )
        if not validation_only:
            publish_strategy_plugin_alerts(strategy_plugin_signals, report=report)
        notification_delivery_events: list[dict] = []
        try:
            rebalance_runtime = composer.build_rebalance_runtime(
                silent_cycle_notifications=validation_only,
                notification_delivery_events=notification_delivery_events,
            )
        except TypeError as exc:
            if "notification_delivery_events" not in str(exc):
                raise
            rebalance_runtime = composer.build_rebalance_runtime(
                silent_cycle_notifications=validation_only,
            )
        cycle_result = run_rebalance_cycle(
            runtime=rebalance_runtime,
            config=composer.build_rebalance_config(
                strategy_plugin_signals=strategy_plugin_signals,
                strategy_plugin_error=strategy_plugin_error,
                notification_title_key=(
                    "dry_run_title"
                    if validation_only and validation_label == "dry_run"
                    else ""
                ),
            ),
        )
        signal_snapshot = {}
        if cycle_result is not None:
            execution = dict(getattr(cycle_result, "execution", {}) or {})
            signal_snapshot = dict(execution.get("signal_snapshot") or {})
        execution_summary = _summarize_cycle_result_for_report(
            cycle_result,
            dry_run=bool(report.get("dry_run")),
        )
        notification_delivery_log = _build_notification_delivery_log_for_report(
            platform="longbridge",
            strategy_profile=STRATEGY_PROFILE,
            run_id=str(report.get("run_id") or ""),
            dry_run=bool(report.get("dry_run")),
            orders_previewed_count=int(execution_summary.get("orders_previewed_count") or 0),
            delivery_events=notification_delivery_events,
        )
        if notification_delivery_log:
            execution_summary["notification_delivery_log"] = notification_delivery_log
        if signal_snapshot:
            reporting_adapters.log_event(
                log_context,
                "strategy_signal_snapshot",
                message="Strategy signal snapshot",
                **signal_snapshot,
            )
        finalize_runtime_report(
            report,
            status="ok",
            summary=execution_summary,
            diagnostics={"signal_snapshot": signal_snapshot} if signal_snapshot else None,
        )
        reporting_adapters.log_event(
            log_context,
            "strategy_cycle_completed",
            message="Strategy execution completed",
        )
        return True

    except Exception as exc:
        append_runtime_report_error(
            report,
            stage="strategy_cycle",
            message=str(exc),
            error_type=type(exc).__name__,
        )
        finalize_runtime_report(report, status="error")
        reporting_adapters.log_event(
            log_context,
            "strategy_cycle_failed",
            message="Strategy execution failed",
            severity="ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        err = traceback.format_exc()
        notification_adapters.publish_cycle_notification(
            detailed_text=f"Strategy error:\n{err}",
            compact_text=f"{t('error_title')}\n{err}",
        )
        return False
    finally:
        try:
            report_path = reporting_adapters.persist_execution_report(report)
            print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)


def run_probe(*, response_body: str = "Probe OK"):
    composer = None
    reporting_adapters = None
    log_context = None
    report = None
    try:
        composer = build_composer(dry_run_only_override=True)
        reporting_adapters = composer.build_reporting_adapters()
        log_context, report = reporting_adapters.start_run()
        reporting_adapters.log_event(
            log_context,
            "health_probe_received",
            message="Received health probe request",
            execution_window="probe",
        )
        runtime = composer.build_rebalance_runtime(silent_cycle_notifications=True)
        quote_context, trade_context, _indicators = runtime.bootstrap()
        snapshot = runtime.portfolio_port_factory(
            quote_context,
            trade_context,
        ).get_portfolio_snapshot()
        positions = tuple(getattr(snapshot, "positions", ()) or ())
        buying_power = float(getattr(snapshot, "buying_power", 0.0) or 0.0)
        total_equity = float(getattr(snapshot, "total_equity", 0.0) or 0.0)
        finalize_runtime_report(
            report,
            status="ok",
            summary={
                "buying_power": buying_power,
                "total_equity": total_equity,
                "positions_count": len(positions),
            },
        )
        reporting_adapters.log_event(
            log_context,
            "health_probe_completed",
            message="Health probe completed",
            execution_window="probe",
            buying_power=buying_power,
            total_equity=total_equity,
            positions_count=len(positions),
        )
        return response_body, 200
    except Exception as exc:
        if report is not None:
            append_runtime_report_error(
                report,
                stage="health_probe",
                message=str(exc),
                error_type=type(exc).__name__,
            )
            finalize_runtime_report(report, status="error")
        if reporting_adapters is not None and log_context is not None:
            reporting_adapters.log_event(
                log_context,
                "health_probe_failed",
                message="Health probe failed",
                severity="ERROR",
                execution_window="probe",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        err = f"{t('health_probe_title')}\n{t('health_probe_error_prefix')}{traceback.format_exc()}"
        if composer is not None:
            composer.build_notification_adapters().publish_cycle_notification(
                detailed_text=err,
                compact_text=err,
            )
        else:
            print(err, flush=True)
        return "Error", 500
    finally:
        try:
            if reporting_adapters is not None and report is not None:
                report_path = reporting_adapters.persist_execution_report(report)
                print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)


@app.route("/run", methods=["POST", "GET"])
def handle_trigger():
    """Entrypoint for Cloud Run / scheduler: run strategy and return 200."""
    return _route_with_runtime_error_fallback(
        run_strategy,
        success_body="OK",
        route_label="POST /",
    )


@app.route("/backfill", methods=["POST", "GET"])
def handle_backfill():
    """Manual backfill entrypoint for verification-only execution."""
    return _route_with_runtime_error_fallback(
        run_strategy,
        force_run=True,
        validation_only=True,
        success_body="OK",
        route_label="POST /backfill",
    )


@app.route("/dry-run", methods=["POST", "GET"])
def handle_dry_run():
    """Strategy dry-run entrypoint."""
    return _route_with_runtime_error_fallback(
        run_strategy,
        force_run=True,
        validation_only=True,
        validation_label="dry_run",
        success_body="Dry Run OK",
        route_label="POST /dry-run",
    )


@app.route("/probe", methods=["POST", "GET"])
def handle_probe():
    """Post-open broker/account health probe; notify only on failure."""
    return _route_with_runtime_error_fallback(
        run_probe,
        success_body="Probe OK",
        route_label="POST /probe",
    )


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
