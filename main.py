"""
LongPort semiconductor rotation + income layer.
Trading: SOXL/SOXX by 150d MA, remainder in BOXX. Income: QQQI/SPYI above equity threshold.
Runs on Cloud Run; token from Secret Manager, orders via LongPort OpenAPI, alerts via Telegram.
"""
import os
import time
import traceback
import requests
import numpy as np
from datetime import datetime

import pytz
import pandas_market_calendars as mcal
from flask import Flask

import google.auth
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
    """True if order fully filled (no partial)."""
    return "Filled" in status and "PartialFilled" not in status

def is_partial_filled_status(status):
    return "PartialFilled" in status

def is_terminal_error_status(status):
    """True if Rejected, Canceled, or Expired."""
    return any(keyword in status for keyword in ["Rejected", "Canceled", "Expired"])

def send_order_status_message(title, symbol, side_text, quantity, order_id, status, executed_qty="0", executed_price="0", reason=""):
    localized_side = t("side_buy") if side_text == "Buy" else t("side_sell")
    root_symbol = symbol.split('.')[0] if '.' in symbol else symbol

    if is_filled_status(status):
        msg = t("order_filled", symbol=root_symbol, side=localized_side, qty=quantity, price=executed_price, order_id=order_id)
    elif is_partial_filled_status(status):
        msg = t("order_partial", symbol=root_symbol, side=localized_side, executed=executed_qty, qty=quantity, price=executed_price, order_id=order_id)
    elif is_terminal_error_status(status):
        status_label = t("status_rejected") if "Rejected" in status else (t("status_canceled") if "Canceled" in status else t("status_expired"))
        msg = t("order_error", symbol=root_symbol, side=localized_side, qty=quantity, status=status_label, order_id=order_id, reason=reason or "—")
    else:
        msg = t("order_filled", symbol=root_symbol, side=localized_side, qty=quantity, price=executed_price, order_id=order_id)

    send_tg_message(msg)


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
    """Poll today_orders for up to 8s; send Telegram on fill, partial, or terminal error."""
    if not order_id:
        return

    try:
        for _ in range(ORDER_POLL_MAX_ATTEMPTS):
            time.sleep(ORDER_POLL_INTERVAL_SEC)
            order_status = fetch_order_status(t_ctx, order_id)
            if not order_status:
                continue

            status = order_status["status"]
            reject_msg = order_status["reason"]
            executed_qty = order_status["executed_qty"]
            executed_price = order_status["executed_price"]

            if is_filled_status(status):
                send_order_status_message("Order filled", symbol, side_text, quantity, order_id, status, executed_qty, executed_price)
                return

            if is_partial_filled_status(status):
                send_order_status_message("Order partial fill", symbol, side_text, quantity, order_id, status, executed_qty, executed_price)

            if is_terminal_error_status(status):
                send_order_status_message("Order rejected/error", symbol, side_text, quantity, order_id, status, executed_qty, executed_price, reject_msg)
                return
    except Exception:
        notify_issue(
            "Order status poll failed",
            f"Symbol: {symbol} Side: {side_text} Qty: {quantity} Order: {order_id}\n{traceback.format_exc()}"
        )

def submit_order_with_alert(t_ctx, symbol, order_type, side, quantity, logs, log_message, submitted_price=None):
    """Submit order (LO or MO), append to logs, then poll status and notify via Telegram."""
    side_text = "Buy" if side == "buy" else "Sell"

    try:
        report = submit_order(
            t_ctx,
            symbol,
            order_kind=order_type,
            side=side,
            quantity=quantity,
            submitted_price=submitted_price,
        )
        order_id = report.broker_order_id or ""
        log_with_order_id = f"{log_message} [order_id={order_id}]" if order_id else log_message
        print(with_prefix(f"OK {log_with_order_id}"), flush=True)
        logs.append(log_with_order_id)
        monitor_submitted_order_status(t_ctx, symbol, side_text, quantity, order_id)
        return True
    except Exception:
        notify_issue(
            "Order submit failed",
            f"Symbol: {symbol} Side: {side_text} Qty: {quantity} Type: {order_type} Price: {submitted_price if submitted_price is not None else 'MO'}\n{traceback.format_exc()}"
        )
        return False

# ---------------------------------------------------------------------------
# Allocation: fraction of core equity to deploy to SOXL/SOXX (rest in BOXX)
# ---------------------------------------------------------------------------
def get_dynamic_allocation(total_equity_usd):
    """Deploy ratio by band; above 180k USD applies log decay (no floor)."""
    if total_equity_usd <= 10000:
        return SMALL_ACCOUNT_DEPLOY_RATIO

    if total_equity_usd <= 80000:
        return float(np.interp(
            total_equity_usd,
            [10000, 80000],
            [SMALL_ACCOUNT_DEPLOY_RATIO, MID_ACCOUNT_DEPLOY_RATIO],
        ))

    if total_equity_usd <= 180000:
        return float(np.interp(
            total_equity_usd,
            [80000, 180000],
            [MID_ACCOUNT_DEPLOY_RATIO, LARGE_ACCOUNT_DEPLOY_RATIO],
        ))

    decayed_ratio = LARGE_ACCOUNT_DEPLOY_RATIO - (
        TRADE_LAYER_DECAY_COEFF * np.log10(total_equity_usd / 180000)
    )
    return max(0.0, decayed_ratio)

def get_income_layer_ratio(total_equity_usd):
    """Target income layer as fraction of total equity; ramps from 0 to MAX between START and 2*START."""
    if total_equity_usd <= INCOME_LAYER_START_USD:
        return 0.0

    if total_equity_usd <= (INCOME_LAYER_START_USD * 2):
        return float(np.interp(
            total_equity_usd,
            [INCOME_LAYER_START_USD, INCOME_LAYER_START_USD * 2],
            [0.0, INCOME_LAYER_MAX_RATIO],
        ))

    return INCOME_LAYER_MAX_RATIO

# ---------------------------------------------------------------------------
# Strategy: NYSE hours check, indicators, balance/positions, target allocation, sell then buy
# ---------------------------------------------------------------------------
def run_strategy():
    try:
        print(with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

        token = refresh_token_if_needed(
            fetch_token_from_secret(PROJECT_ID, SECRET_NAME),
            project_id=PROJECT_ID,
            secret_name=SECRET_NAME,
            app_key=os.getenv("LONGPORT_APP_KEY"),
            app_secret=os.getenv("LONGPORT_APP_SECRET"),
            refresh_threshold_days=TOKEN_REFRESH_THRESHOLD_DAYS,
        )
        app_key = os.getenv("LONGPORT_APP_KEY", "")
        app_secret = os.getenv("LONGPORT_APP_SECRET", "")
        q_ctx, t_ctx = build_contexts(app_key, app_secret, token)

        # Skip if outside NYSE regular session
        try:
            nyse = mcal.get_calendar('NYSE')
            now_utc = datetime.now(pytz.utc)
            schedule = nyse.schedule(start_date=now_utc, end_date=now_utc)
            is_normal = False if schedule.empty else nyse.open_at_time(schedule, now_utc)
        except Exception as e:
            print(with_prefix(f"Market hours check failed: {e}"), flush=True)
            is_normal = False

        if not is_normal:
            print(with_prefix("Outside market hours; skip."), flush=True)
            return

        ind = calculate_rotation_indicators(q_ctx, trend_window=TREND_MA_WINDOW)
        if ind is None:
            raise Exception("Quote data missing or API limited; cannot compute indicators")

        strategy_assets = ["SOXL", "SOXX", "BOXX", "QQQI", "SPYI"]
        account_state = fetch_strategy_account_state(q_ctx, t_ctx, strategy_assets)
        available_cash = account_state["available_cash"]
        mv = account_state["market_values"]
        qty = account_state["quantities"]
        sellable_qty = account_state["sellable_quantities"]
        total_strategy_equity = account_state["total_strategy_equity"]
        current_min_trade = max(MIN_TRADE_FLOOR, total_strategy_equity * MIN_TRADE_RATIO)

        # Income layer: sticky (buy-only) value; core_equity = total minus locked income
        current_income_layer_value = mv["QQQI"] + mv["SPYI"]
        income_layer_ratio = get_income_layer_ratio(total_strategy_equity)
        desired_income_layer_value = total_strategy_equity * income_layer_ratio
        locked_income_layer_value = max(current_income_layer_value, desired_income_layer_value)
        income_layer_add_value = max(0.0, locked_income_layer_value - current_income_layer_value)
        core_equity = max(0.0, total_strategy_equity - locked_income_layer_value)
        deploy_ratio = get_dynamic_allocation(core_equity)
        deployed_capital = core_equity * deploy_ratio
        deploy_ratio_text = f"{deploy_ratio * 100:.1f}%"
        income_ratio_text = f"{income_layer_ratio * 100:.1f}%"
        income_locked_ratio_text = (
            f"{(locked_income_layer_value / total_strategy_equity) * 100:.1f}%"
            if total_strategy_equity > 0 else "0.0%"
        )
        # Trend: SOXL above 150d MA -> SOXL; else SOXX. Deployed capital and remainder to BOXX
        soxl_p = ind['soxl']['price']
        soxl_ma_trend = ind['soxl']['ma_trend']
        active_risk_asset = "SOXL" if soxl_p > soxl_ma_trend else "SOXX"
        market_status = f"🚀 RISK-ON ({active_risk_asset})" if active_risk_asset == "SOXL" else "🛡️ DE-LEVER (SOXX)"
        msg = (
            t("signal_risk_on", window=TREND_MA_WINDOW, ratio=deploy_ratio_text)
            if active_risk_asset == "SOXL"
            else t("signal_delever", window=TREND_MA_WINDOW, ratio=deploy_ratio_text)
        )

        targets = {
            "SOXL": deployed_capital if active_risk_asset == "SOXL" else 0.0,
            "SOXX": deployed_capital if active_risk_asset == "SOXX" else 0.0,
            "QQQI": mv["QQQI"] + (income_layer_add_value * INCOME_LAYER_QQQI_WEIGHT),
            "SPYI": mv["SPYI"] + (income_layer_add_value * INCOME_LAYER_SPYI_WEIGHT),
            "BOXX": max(0.0, core_equity - deployed_capital),
        }
        logs, action_done = [], False

        # Sell loop: reduce overweight positions (limit for SOXL/SOXX/QQQI/SPYI, market for BOXX)
        for k in strategy_assets:
            diff = targets[k] - mv[k]
            if diff < -(total_strategy_equity * REBALANCE_THRESHOLD_RATIO) and abs(diff) > current_min_trade:
                p = safe_quote_last_price(q_ctx, f"{k}.US")
                if p is None:
                    continue
                q_sell = min(int(abs(diff) // p), sellable_qty[k])
                if q_sell > 0:
                    if k in ["SOXL", "SOXX", "QQQI", "SPYI"]:
                        lp = round(p * LIMIT_SELL_DISCOUNT, 2)
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            "limit",
                            "sell",
                            q_sell,
                            logs,
                            t("limit_sell", symbol=k, qty=q_sell, price=lp),
                            submitted_price=lp,
                        )
                    else:
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            "market",
                            "sell",
                            q_sell,
                            logs,
                            t("market_sell", symbol=k, qty=q_sell, price=round(p, 2)),
                        )

                    if submitted:
                        action_done = True
                elif sellable_qty[k] <= 0 and qty[k] > 0:
                    notify_issue(
                        "Sell skipped",
                        f"Symbol: {k}.US Diff: ${abs(diff):.2f} Held: {qty[k]} Sellable: {sellable_qty[k]} (no sellable)"
                    )

        # Buy loop: investable_cash after reserve; cap qty by estimate_max_purchase_quantity
        investable_cash = max(0, available_cash - (total_strategy_equity * CASH_RESERVE_RATIO))
        for k in strategy_assets:
            diff = targets[k] - mv[k]
            if diff > (total_strategy_equity * REBALANCE_THRESHOLD_RATIO) and abs(diff) > current_min_trade:
                p = safe_quote_last_price(q_ctx, f"{k}.US")
                if p is None:
                    continue
                can_buy_val = min(diff, investable_cash)
                if can_buy_val > p:
                    is_limit_order = k in ["SOXL", "SOXX", "QQQI", "SPYI"]
                    order_kind = "limit" if is_limit_order else "market"
                    ref_price = round(p * LIMIT_BUY_PREMIUM, 2) if is_limit_order else round(p, 2)
                    budget_price = ref_price if is_limit_order else p
                    q_buy_budget = int(can_buy_val // budget_price)
                    q_buy_cash_limit = estimate_cash_buy_quantity_safe(t_ctx, f"{k}.US", order_kind, ref_price)

                    if q_buy_cash_limit is None:
                        continue

                    q_buy = min(q_buy_budget, q_buy_cash_limit)
                    cost_estimate = 0.0

                    if q_buy <= 0:
                        notify_issue(
                            "Buy skipped",
                            f"Symbol: {k}.US Diff: ${diff:.2f} Cash: ${investable_cash:.2f} Budget qty: {q_buy_budget} Cash limit qty: {q_buy_cash_limit}"
                        )
                        continue
                    
                    if is_limit_order:
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            "limit",
                            "buy",
                            q_buy,
                            logs,
                            t("limit_buy", symbol=k, qty=q_buy, price=ref_price),
                            submitted_price=ref_price,
                        )
                        cost_estimate = q_buy * budget_price
                    else:
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            "market",
                            "buy",
                            q_buy,
                            logs,
                            t("market_buy", symbol=k, qty=q_buy, price=round(p, 2)),
                        )
                        cost_estimate = q_buy * budget_price
                    
                    if submitted:
                        investable_cash = max(0, investable_cash - cost_estimate)
                        action_done = True
                else:
                    notify_issue(
                        "Buy skipped",
                        f"Symbol: {k}.US Diff: ${diff:.2f} Cash: ${investable_cash:.2f} Price: ${p:.2f} (insufficient for 1 share)"
                    )

        if action_done:
            formatted_logs = "\n".join([f"  {log}" for log in logs])
            tg_msg = (
                f"{t('rebalance_title')}\n"
                f"{t('market_status', status=market_status)}\n"
                f"{t('risk_position', ratio=deploy_ratio_text)}\n"
                f"{t('income_target', ratio=income_ratio_text)}\n"
                f"{t('income_locked', ratio=income_locked_ratio_text)}\n"
                f"{t('signal', msg=msg)}\n"
                f"{SEPARATOR}\n"
                f"{formatted_logs}"
            )
            send_tg_message(tg_msg)
        else:
            cash_label = t("cash_label")
            no_trade_msg = (
                f"{t('heartbeat_title')}\n"
                f"{t('market_status', status=market_status)}\n"
                f"{t('equity', value=f'{total_strategy_equity:,.2f}')}\n"
                f"{SEPARATOR}\n"
                f"SOXL: ${mv['SOXL']:,.2f}  SOXX: ${mv['SOXX']:,.2f}\n"
                f"QQQI: ${mv['QQQI']:,.2f}  SPYI: ${mv['SPYI']:,.2f}\n"
                f"BOXX: ${mv['BOXX']:,.2f}  {cash_label}: ${available_cash:,.2f}\n"
                f"{SEPARATOR}\n"
                f"{t('risk_position', ratio=deploy_ratio_text)}\n"
                f"{t('income_target', ratio=income_ratio_text)}\n"
                f"{t('income_locked', ratio=income_locked_ratio_text)}\n"
                f"{t('heartbeat_signal', msg=msg)}\n"
                f"{SEPARATOR}\n"
                f"{t('no_trades')}"
            )
            print(with_prefix(no_trade_msg), flush=True)
            send_tg_message(no_trade_msg)
        
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
