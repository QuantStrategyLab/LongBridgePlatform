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
from application.runtime_broker_adapters import build_runtime_broker_adapters
from application.runtime_composer import build_runtime_composer
from application.rebalance_service import run_strategy as run_rebalance_cycle
from application.runtime_strategy_adapters import build_runtime_strategy_adapters
from entrypoints.cloud_run import is_market_open_now
from runtime_config_support import load_platform_runtime_settings
from notifications.telegram import build_signal_text, build_strategy_display_name, build_translator
from quant_platform_kit.common.runtime_reports import (
    append_runtime_report_error,
    build_runtime_report_base,
    finalize_runtime_report,
    persist_runtime_report,
)
from quant_platform_kit.strategy_contracts import build_strategy_evaluation_inputs
from runtime_logging import build_run_id, emit_runtime_log
from quant_platform_kit.longbridge import (
    build_contexts,
    calculate_rotation_indicators,
    estimate_max_purchase_quantity,
    fetch_last_price,
    fetch_order_status,
    fetch_strategy_account_state,
    fetch_token_from_secret,
    refresh_token_if_needed,
    submit_order,
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

SEPARATOR = "━━━━━━━━━━━━━━━━━━"

def t(key, **kwargs):
    return build_translator(NOTIFY_LANG)(key, **kwargs)


signal_text = build_signal_text(t)
strategy_display_name = build_strategy_display_name(t)(
    STRATEGY_PROFILE,
    fallback_name=STRATEGY_DISPLAY_NAME,
)
BROKER_ADAPTERS = build_runtime_broker_adapters(
    strategy_symbols=tuple(MANAGED_SYMBOLS),
    account_hash=ACCOUNT_PREFIX or ACCOUNT_REGION or "longbridge",
    fetch_last_price_fn=fetch_last_price,
    fetch_strategy_account_state_fn=lambda quote_context, trade_context: fetch_strategy_account_state(
        quote_context,
        trade_context,
        list(MANAGED_SYMBOLS),
    ),
    submit_order_fn=submit_order,
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
)


def build_composer():
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
        dry_run_only=RUNTIME_SETTINGS.dry_run_only,
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
        env_reader=os.getenv,
        sleeper=time.sleep,
        printer=print,
    )


def run_strategy():
    composer = build_composer()
    reporting_adapters = composer.build_reporting_adapters()
    log_context, report = reporting_adapters.start_run()
    notification_adapters = composer.build_notification_adapters()
    try:
        reporting_adapters.log_event(
            log_context,
            "strategy_cycle_started",
            message="Starting strategy execution",
        )
        print(composer.with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

        market_open = is_market_open_now()
        if isinstance(market_open, tuple):
            market_open, error = market_open
            reporting_adapters.log_event(
                log_context,
                "market_hours_check_failed",
                message="Market hours check failed",
                severity="WARNING",
                error_message=str(error),
            )
            print(composer.with_prefix(f"Market hours check failed: {error}"), flush=True)
        if not market_open:
            reporting_adapters.log_event(
                log_context,
                "outside_market_hours",
                message="Outside market hours; skip execution",
            )
            finalize_runtime_report(
                report,
                status="skipped",
                diagnostics={
                    "skip_reason": "market_closed",
                },
            )
            print(composer.with_prefix("Outside market hours; skip."), flush=True)
            return
        run_rebalance_cycle(
            runtime=composer.build_rebalance_runtime(),
            config=composer.build_rebalance_config(),
        )
        finalize_runtime_report(report, status="ok")
        reporting_adapters.log_event(
            log_context,
            "strategy_cycle_completed",
            message="Strategy execution completed",
        )
        
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
    finally:
        try:
            report_path = reporting_adapters.persist_execution_report(report)
            print(f"execution_report {report_path}", flush=True)
        except Exception as persist_exc:
            print(f"failed to persist execution report: {persist_exc}", flush=True)

@app.route("/", methods=["POST", "GET"])
def handle_trigger():
    """Entrypoint for Cloud Run / scheduler: run strategy and return 200."""
    run_strategy()
    return "OK", 200


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
