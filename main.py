"""
LongPort semiconductor rotation + income layer.
Trading: SOXL/SOXX by 150d MA, remainder in BOXX. Income: QQQI/SPYI above equity threshold.
Runs on Cloud Run; token from Secret Manager, orders via LongPort OpenAPI, alerts via Telegram.
"""
import os
import time
import traceback
from datetime import datetime

from flask import Flask

import google.auth
from application.rebalance_service import run_strategy as run_rebalance_cycle
from entrypoints.cloud_run import is_market_open_now
from runtime_config_support import load_platform_runtime_settings
from decision_mapper import map_strategy_decision_to_plan
from notifications.order_alerts import (
    is_filled_status as notifications_is_filled_status,
    is_partial_filled_status as notifications_is_partial_filled_status,
    is_terminal_error_status as notifications_is_terminal_error_status,
    monitor_submitted_order_status as notifications_monitor_submitted_order_status,
    send_order_status_message as notifications_send_order_status_message,
    submit_order_with_alert as notifications_submit_order_with_alert,
)
from notifications.telegram import (
    build_issue_notifier,
    build_prefixer,
    build_sender,
    build_translator,
)
from runtime_logging import RuntimeLogContext, build_run_id, emit_runtime_log
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
SERVICE_NAME = RUNTIME_SETTINGS.service_name
STRATEGY_PROFILE = RUNTIME_SETTINGS.strategy_profile
ACCOUNT_REGION = RUNTIME_SETTINGS.account_region
NOTIFY_LANG = RUNTIME_SETTINGS.notify_lang
TG_TOKEN = RUNTIME_SETTINGS.tg_token
TG_CHAT_ID = RUNTIME_SETTINGS.tg_chat_id
STRATEGY_RUNTIME = load_strategy_runtime(STRATEGY_PROFILE)
STRATEGY_RUNTIME_CONFIG = dict(STRATEGY_RUNTIME.merged_runtime_config)
MANAGED_SYMBOLS = STRATEGY_RUNTIME.managed_symbols

# Order pricing: limit order discount/premium relative to last price
LIMIT_SELL_DISCOUNT = 0.995               # sell limit at 0.5% below last
LIMIT_BUY_PREMIUM = 1.005                 # buy limit at 0.5% above last

# Order monitoring: poll interval and max attempts for fill check
ORDER_POLL_INTERVAL_SEC = 1
ORDER_POLL_MAX_ATTEMPTS = 8

# Token refresh: days before expiry to trigger refresh
TOKEN_REFRESH_THRESHOLD_DAYS = 30

SEPARATOR = "━━━━━━━━━━━━━━━━━━"
RUNTIME_LOG_CONTEXT = RuntimeLogContext(
    platform="longbridge",
    deploy_target="cloud_run",
    service_name=SERVICE_NAME or os.getenv("K_SERVICE", "longbridge-platform"),
    strategy_profile=STRATEGY_PROFILE,
    account_scope=ACCOUNT_REGION,
    account_region=ACCOUNT_REGION,
    project_id=PROJECT_ID,
    extra_fields={"account_prefix": ACCOUNT_PREFIX},
)

def t(key, **kwargs):
    return build_translator(NOTIFY_LANG)(key, **kwargs)

def with_prefix(message: str) -> str:
    return build_prefixer(ACCOUNT_PREFIX, SERVICE_NAME)(message)

def send_tg_message(message):
    return build_sender(TG_TOKEN, TG_CHAT_ID, with_prefix_fn=with_prefix)(message)

def notify_issue(title, detail):
    return build_issue_notifier(with_prefix_fn=with_prefix, send_tg_message_fn=send_tg_message)(title, detail)


def log_runtime_event(log_context, event, **fields):
    return emit_runtime_log(
        log_context,
        event,
        printer=lambda line: print(line, flush=True),
        **fields,
    )


def is_filled_status(status):
    return notifications_is_filled_status(status)

def is_partial_filled_status(status):
    return notifications_is_partial_filled_status(status)

def is_terminal_error_status(status):
    return notifications_is_terminal_error_status(status)

def send_order_status_message(title, symbol, side_text, quantity, order_id, status, executed_qty="0", executed_price="0", reason=""):
    del title
    notifications_send_order_status_message(
        symbol,
        side_text,
        quantity,
        order_id,
        status,
        translator=t,
        send_tg_message=send_tg_message,
        executed_qty=executed_qty,
        executed_price=executed_price,
        reason=reason,
    )


def safe_quote_last_price(q_ctx, symbol):
    """Get last done price for a symbol; returns None if quote unavailable."""
    try:
        return fetch_last_price(q_ctx, symbol)
    except Exception as e:
        notify_issue("Quote failed", f"Symbol: {symbol}\n{e}")
        return None


def estimate_cash_buy_quantity_safe(t_ctx, symbol, order_kind, ref_price):
    """Max buy quantity by cash; ref_price required even for market orders. Returns None on error."""
    try:
        return estimate_max_purchase_quantity(
            t_ctx,
            symbol,
            order_kind=order_kind,
            ref_price=ref_price,
        )
    except Exception:
        notify_issue(
            "Estimate max buy failed",
            f"Symbol: {symbol}\nOrderKind: {order_kind}\n{traceback.format_exc()}"
        )
        return None

def monitor_submitted_order_status(t_ctx, symbol, side_text, quantity, order_id):
    notifications_monitor_submitted_order_status(
        t_ctx,
        symbol,
        side_text,
        quantity,
        order_id,
        fetch_order_status=fetch_order_status,
        order_poll_interval_sec=ORDER_POLL_INTERVAL_SEC,
        order_poll_max_attempts=ORDER_POLL_MAX_ATTEMPTS,
        translator=t,
        send_tg_message=send_tg_message,
        notify_issue=notify_issue,
        sleeper=time.sleep,
    )

def submit_order_with_alert(t_ctx, symbol, order_type, side, quantity, logs, log_message, submitted_price=None):
    return notifications_submit_order_with_alert(
        t_ctx,
        symbol,
        order_type,
        side,
        quantity,
        logs,
        log_message,
        submit_order=submit_order,
        fetch_order_status=fetch_order_status,
        translator=t,
        send_tg_message=send_tg_message,
        notify_issue=notify_issue,
        order_poll_interval_sec=ORDER_POLL_INTERVAL_SEC,
        order_poll_max_attempts=ORDER_POLL_MAX_ATTEMPTS,
        sleeper=time.sleep,
        print_with_prefix=lambda message: print(with_prefix(message), flush=True),
        submitted_price=submitted_price,
    )

# ---------------------------------------------------------------------------
# Strategy: NYSE hours check, indicators, balance/positions, target allocation, sell then buy
# ---------------------------------------------------------------------------
def calculate_strategy_indicators(quote_context):
    trend_ma_window = int(STRATEGY_RUNTIME_CONFIG.get("trend_ma_window", 150))
    return calculate_rotation_indicators(quote_context, trend_window=trend_ma_window)


def fetch_managed_account_state(quote_context, trade_context):
    return fetch_strategy_account_state(quote_context, trade_context, list(MANAGED_SYMBOLS))


def resolve_rebalance_plan(*, indicators, account_state):
    evaluation = STRATEGY_RUNTIME.evaluate(
        indicators=indicators,
        account_state=account_state,
        translator=t,
    )
    return map_strategy_decision_to_plan(
        evaluation.decision,
        account_state=account_state,
        strategy_profile=STRATEGY_PROFILE,
    )


def run_strategy():
    log_context = RUNTIME_LOG_CONTEXT.with_run(build_run_id())
    try:
        log_runtime_event(
            log_context,
            "strategy_cycle_started",
            message="Starting strategy execution",
        )
        print(with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

        market_open = is_market_open_now()
        if isinstance(market_open, tuple):
            market_open, error = market_open
            log_runtime_event(
                log_context,
                "market_hours_check_failed",
                message="Market hours check failed",
                severity="WARNING",
                error_message=str(error),
            )
            print(with_prefix(f"Market hours check failed: {error}"), flush=True)
        if not market_open:
            log_runtime_event(
                log_context,
                "outside_market_hours",
                message="Outside market hours; skip execution",
            )
            print(with_prefix("Outside market hours; skip."), flush=True)
            return
        run_rebalance_cycle(
            project_id=PROJECT_ID,
            secret_name=SECRET_NAME,
            token_refresh_threshold_days=TOKEN_REFRESH_THRESHOLD_DAYS,
            limit_sell_discount=LIMIT_SELL_DISCOUNT,
            limit_buy_premium=LIMIT_BUY_PREMIUM,
            separator=SEPARATOR,
            translator=t,
            with_prefix=with_prefix,
            send_tg_message=send_tg_message,
            notify_issue=notify_issue,
            fetch_token_from_secret=fetch_token_from_secret,
            refresh_token_if_needed=refresh_token_if_needed,
            build_contexts=build_contexts,
            calculate_strategy_indicators=calculate_strategy_indicators,
            fetch_strategy_account_state=fetch_managed_account_state,
            resolve_rebalance_plan=resolve_rebalance_plan,
            fetch_last_price=fetch_last_price,
            estimate_max_purchase_quantity=estimate_max_purchase_quantity,
            submit_order_with_alert=submit_order_with_alert,
        )
        log_runtime_event(
            log_context,
            "strategy_cycle_completed",
            message="Strategy execution completed",
        )
        
    except Exception as exc:
        log_runtime_event(
            log_context,
            "strategy_cycle_failed",
            message="Strategy execution failed",
            severity="ERROR",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        err = traceback.format_exc()
        print(with_prefix(f"Strategy error:\n{err}"), flush=True)
        send_tg_message(f"{t('error_title')}\n{err}")

@app.route("/", methods=["POST", "GET"])
def handle_trigger():
    """Entrypoint for Cloud Run / scheduler: run strategy and return 200."""
    run_strategy()
    return "OK", 200


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
