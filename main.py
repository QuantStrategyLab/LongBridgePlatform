"""
LongPort semiconductor rotation + income layer.
Trading: SOXL/SOXX by 150d MA, remainder in BOXX. Income: QQQI/SPYI above equity threshold.
Runs on Cloud Run; token from Secret Manager, orders via LongPort OpenAPI, alerts via Telegram.
"""
import os
import time
import traceback
import requests
from datetime import datetime

from flask import Flask

import google.auth
from application.rebalance_service import run_strategy as run_rebalance_cycle
from entrypoints.cloud_run import is_market_open_now
from notifications.order_alerts import (
    is_filled_status as notifications_is_filled_status,
    is_partial_filled_status as notifications_is_partial_filled_status,
    is_terminal_error_status as notifications_is_terminal_error_status,
    monitor_submitted_order_status as notifications_monitor_submitted_order_status,
    send_order_status_message as notifications_send_order_status_message,
    submit_order_with_alert as notifications_submit_order_with_alert,
)
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
from strategy.allocation import (
    get_dynamic_allocation as strategy_get_dynamic_allocation,
    get_income_layer_ratio as strategy_get_income_layer_ratio,
)

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

PROJECT_ID = get_project_id()
SECRET_NAME = os.getenv("LONGPORT_SECRET_NAME", "longport_token")
ACCOUNT_PREFIX = os.getenv("ACCOUNT_PREFIX", "DEFAULT")
SERVICE_NAME = os.getenv("SERVICE_NAME", "longbridge-quant")
NOTIFY_LANG = os.getenv("NOTIFY_LANG", "en")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("GLOBAL_TELEGRAM_CHAT_ID")

# Execution: reserve ratio, minimum trade size (ratio of equity and absolute floor)
CASH_RESERVE_RATIO = 0.03
MIN_TRADE_RATIO = 0.01
MIN_TRADE_FLOOR = 100.0
REBALANCE_THRESHOLD_RATIO = 0.01          # 1% of equity to trigger rebalance

# Order pricing: limit order discount/premium relative to last price
LIMIT_SELL_DISCOUNT = 0.995               # sell limit at 0.5% below last
LIMIT_BUY_PREMIUM = 1.005                 # buy limit at 0.5% above last

# Order monitoring: poll interval and max attempts for fill check
ORDER_POLL_INTERVAL_SEC = 1
ORDER_POLL_MAX_ATTEMPTS = 8

# Token refresh: days before expiry to trigger refresh
TOKEN_REFRESH_THRESHOLD_DAYS = 30

# Trading layer: SOXL 150d MA for trend; deploy ratio by account size, log decay above 180k
TREND_MA_WINDOW = 150
SMALL_ACCOUNT_DEPLOY_RATIO = 0.60
MID_ACCOUNT_DEPLOY_RATIO = 0.57
LARGE_ACCOUNT_DEPLOY_RATIO = 0.50
TRADE_LAYER_DECAY_COEFF = 0.04

# Income layer: starts at INCOME_LAYER_START_USD, caps at INCOME_LAYER_MAX_RATIO; QQQI/SPYI weights
INCOME_LAYER_START_USD = 150000.0
INCOME_LAYER_MAX_RATIO = 0.15
INCOME_LAYER_QQQI_WEIGHT = 0.70
INCOME_LAYER_SPYI_WEIGHT = 0.30

SEPARATOR = "━━━━━━━━━━━━━━━━━━"

I18N = {
    "zh": {
        "rebalance_title": "🔔 【调仓指令】",
        "market_status": "📊 市场状态: {status}",
        "risk_position": "💼 交易层风险仓位: {ratio}",
        "income_target": "💰 收入层目标: {ratio}",
        "income_locked": "🏦 收入层锁定占比: {ratio}",
        "signal": "🎯 触发信号: {msg}",
        "heartbeat_title": "💓 【心跳检测】",
        "equity": "💰 净值: ${value}",
        "cash_label": "现金",
        "heartbeat_signal": "🎯 信号: {msg}",
        "no_trades": "✅ 无需调仓",
        "order_filled": "✅ 订单成交 | {symbol} {side} {qty}股 均价 ${price} (ID: {order_id})",
        "order_partial": "⚠️ 订单部分成交 | {symbol} {side} 已成交 {executed}/{qty}股 均价 ${price} (ID: {order_id})",
        "order_error": "❌ 订单异常 | {symbol} {side} {qty}股 已{status} (ID: {order_id}) 原因: {reason}",
        "error_title": "🚨 【策略异常】",
        "limit_buy": "📈 [限价买入] {symbol}: {qty}股 @ ${price}",
        "market_buy": "📈 [市价买入] {symbol}: {qty}股 @ ${price}",
        "limit_sell": "📉 [限价卖出] {symbol}: {qty}股 @ ${price}",
        "market_sell": "📉 [市价卖出] {symbol}: {qty}股 @ ${price}",
        "side_buy": "买入",
        "side_sell": "卖出",
        "status_rejected": "拒绝",
        "status_canceled": "取消",
        "status_expired": "过期",
        "signal_risk_on": "SOXL 站上 {window} 日均线，持有 SOXL，交易层风险仓位 {ratio}",
        "signal_delever": "SOXL 跌破 {window} 日均线，切换至 SOXX，交易层风险仓位 {ratio}",
    },
    "en": {
        "rebalance_title": "🔔 【Trade Execution Report】",
        "market_status": "📊 Market: {status}",
        "risk_position": "💼 Risk Position: {ratio}",
        "income_target": "💰 Income Target: {ratio}",
        "income_locked": "🏦 Income Locked: {ratio}",
        "signal": "🎯 Signal: {msg}",
        "heartbeat_title": "💓 【Heartbeat】",
        "equity": "💰 Equity: ${value}",
        "cash_label": "Cash",
        "heartbeat_signal": "🎯 Signal: {msg}",
        "no_trades": "✅ No trades needed",
        "order_filled": "✅ Order Filled | {symbol} {side} {qty} shares avg ${price} (ID: {order_id})",
        "order_partial": "⚠️ Partial Fill | {symbol} {side} filled {executed}/{qty} shares avg ${price} (ID: {order_id})",
        "order_error": "❌ Order Error | {symbol} {side} {qty} shares {status} (ID: {order_id}) reason: {reason}",
        "error_title": "🚨 【Strategy Error】",
        "limit_buy": "📈 [Limit buy] {symbol}: {qty} shares @ ${price}",
        "market_buy": "📈 [Market buy] {symbol}: {qty} shares @ ${price}",
        "limit_sell": "📉 [Limit sell] {symbol}: {qty} shares @ ${price}",
        "market_sell": "📉 [Market sell] {symbol}: {qty} shares @ ${price}",
        "side_buy": "Buy",
        "side_sell": "Sell",
        "status_rejected": "Rejected",
        "status_canceled": "Canceled",
        "status_expired": "Expired",
        "signal_risk_on": "SOXL above {window}d MA, hold SOXL, risk {ratio}",
        "signal_delever": "SOXL below {window}d MA, switch to SOXX, risk {ratio}",
    },
}

def t(key, **kwargs):
    """Get translated string for current LANG."""
    lang = NOTIFY_LANG if NOTIFY_LANG in I18N else "en"
    template = I18N[lang].get(key, key)
    return template.format(**kwargs) if kwargs else template

def with_prefix(message: str) -> str:
    return f"[{ACCOUNT_PREFIX}/{SERVICE_NAME}] {message}"

def send_tg_message(message):
    """Send text to Telegram; no-op if token or chat_id missing."""
    if not TG_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        prefixed = with_prefix(message)
        print(f"TG:\n{prefixed}", flush=True)
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": prefixed}, timeout=10)
    except Exception as e:
        print(f"Telegram send failed: {e}", flush=True)

def notify_issue(title, detail):
    """Log and send to Telegram (alerts for order/API failures)."""
    message = f"{title}\n{detail}"
    print(with_prefix(message), flush=True)
    send_tg_message(message)


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
# Allocation: fraction of core equity to deploy to SOXL/SOXX (rest in BOXX)
# ---------------------------------------------------------------------------
def get_dynamic_allocation(total_equity_usd):
    return strategy_get_dynamic_allocation(
        total_equity_usd,
        small_account_deploy_ratio=SMALL_ACCOUNT_DEPLOY_RATIO,
        mid_account_deploy_ratio=MID_ACCOUNT_DEPLOY_RATIO,
        large_account_deploy_ratio=LARGE_ACCOUNT_DEPLOY_RATIO,
        trade_layer_decay_coeff=TRADE_LAYER_DECAY_COEFF,
    )

def get_income_layer_ratio(total_equity_usd):
    return strategy_get_income_layer_ratio(
        total_equity_usd,
        income_layer_start_usd=INCOME_LAYER_START_USD,
        income_layer_max_ratio=INCOME_LAYER_MAX_RATIO,
    )

# ---------------------------------------------------------------------------
# Strategy: NYSE hours check, indicators, balance/positions, target allocation, sell then buy
# ---------------------------------------------------------------------------
def run_strategy():
    try:
        print(with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

        market_open = is_market_open_now()
        if isinstance(market_open, tuple):
            market_open, error = market_open
            print(with_prefix(f"Market hours check failed: {error}"), flush=True)
        if not market_open:
            print(with_prefix("Outside market hours; skip."), flush=True)
            return
        run_rebalance_cycle(
            project_id=PROJECT_ID,
            secret_name=SECRET_NAME,
            trend_ma_window=TREND_MA_WINDOW,
            token_refresh_threshold_days=TOKEN_REFRESH_THRESHOLD_DAYS,
            cash_reserve_ratio=CASH_RESERVE_RATIO,
            min_trade_ratio=MIN_TRADE_RATIO,
            min_trade_floor=MIN_TRADE_FLOOR,
            rebalance_threshold_ratio=REBALANCE_THRESHOLD_RATIO,
            limit_sell_discount=LIMIT_SELL_DISCOUNT,
            limit_buy_premium=LIMIT_BUY_PREMIUM,
            small_account_deploy_ratio=SMALL_ACCOUNT_DEPLOY_RATIO,
            mid_account_deploy_ratio=MID_ACCOUNT_DEPLOY_RATIO,
            large_account_deploy_ratio=LARGE_ACCOUNT_DEPLOY_RATIO,
            trade_layer_decay_coeff=TRADE_LAYER_DECAY_COEFF,
            income_layer_start_usd=INCOME_LAYER_START_USD,
            income_layer_max_ratio=INCOME_LAYER_MAX_RATIO,
            income_layer_qqqi_weight=INCOME_LAYER_QQQI_WEIGHT,
            income_layer_spyi_weight=INCOME_LAYER_SPYI_WEIGHT,
            separator=SEPARATOR,
            translator=t,
            with_prefix=with_prefix,
            send_tg_message=send_tg_message,
            notify_issue=notify_issue,
            fetch_token_from_secret=fetch_token_from_secret,
            refresh_token_if_needed=refresh_token_if_needed,
            build_contexts=build_contexts,
            calculate_rotation_indicators=calculate_rotation_indicators,
            fetch_strategy_account_state=fetch_strategy_account_state,
            fetch_last_price=fetch_last_price,
            estimate_max_purchase_quantity=estimate_max_purchase_quantity,
            submit_order_with_alert=submit_order_with_alert,
        )
        
    except Exception:
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
