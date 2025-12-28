"""
LongPort semiconductor rotation + income layer.
Trading: SOXL/SOXX by 150d MA, remainder in BOXX. Income: QQQI/SPYI above equity threshold.
Runs on Cloud Run; token from Secret Manager, orders via LongPort OpenAPI, alerts via Telegram.
"""
import os
import time
import json
import base64
import traceback
import hmac
import hashlib
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import pytz
import pandas_market_calendars as mcal
from flask import Flask

import google.auth
try:
    import google.cloud.secretmanager_v1 as secret_manager
except ImportError:
    from google.cloud import secret_manager
    
from longport.openapi import (
    Config, QuoteContext, TradeContext, 
    Period, AdjustType, OrderType, OrderSide, 
    TimeInForceType, OrderStatus
)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config and constants (GCP project, Telegram, execution and strategy params)
# ---------------------------------------------------------------------------
def get_project_id():
    try:
        _, project_id = google.auth.default()
        return project_id
    except:
        return os.getenv("GOOGLE_CLOUD_PROJECT")

PROJECT_ID = get_project_id()
SECRET_NAME = "longport_token"
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Execution: reserve ratio, minimum trade size (ratio of equity and absolute floor)
CASH_RESERVE_RATIO = 0.03
MIN_TRADE_RATIO = 0.01
MIN_TRADE_FLOOR = 100.0

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

def send_tg_message(message):
    """Send text to Telegram; no-op if token or chat_id missing."""
    if not TG_TOKEN or not TG_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        print(f"TG:\n{message}", flush=True)
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
    except: pass

def notify_issue(title, detail):
    """Log and send to Telegram (alerts for order/API failures)."""
    message = f"{title}\n{detail}"
    print(message, flush=True)
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
    extra = f"\nReason: {reason}" if reason else ""
    send_tg_message(
        f"{title}\nSymbol: {symbol}\nSide: {side_text}\nQty: {quantity}\nExecuted: {executed_qty}\nAvg: {executed_price}\nOrder: {order_id}\nStatus: {status}{extra}"
    )


def estimate_cash_buy_quantity(t_ctx, symbol, order_type, ref_price):
    """Max buy quantity by cash; ref_price required even for market orders. Returns None on error."""
    try:
        resp = t_ctx.estimate_max_purchase_quantity(
            symbol=symbol,
            order_type=order_type,
            side=OrderSide.Buy,
            price=Decimal(str(ref_price)),
        )
        cash_max_qty = getattr(resp, "cash_max_qty", 0)
        return max(0, int(Decimal(str(cash_max_qty or "0"))))
    except Exception:
        notify_issue(
            "Estimate max buy failed",
            f"Symbol: {symbol}\nOrderType: {order_type}\n{traceback.format_exc()}"
        )
        return None

def monitor_submitted_order_status(t_ctx, symbol, side_text, quantity, order_id):
    """Poll today_orders for up to 8s; send Telegram on fill, partial, or terminal error."""
    if not order_id:
        return

    try:
        for _ in range(8):
            time.sleep(1)
            resp = t_ctx.today_orders(order_id=order_id)
            orders = getattr(resp, "orders", None) or []
            if not orders:
                continue

            order = orders[0]
            status = str(getattr(order, "status", "UNKNOWN"))
            reject_msg = getattr(order, "msg", "") or "—"
            executed_qty = str(getattr(order, "executed_quantity", "0"))
            executed_price = str(getattr(order, "executed_price", "0"))

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
    side_text = "Buy" if side == OrderSide.Buy else "Sell"

    try:
        kwargs = {}
        if submitted_price is not None:
            kwargs["submitted_price"] = Decimal(str(submitted_price))

        resp = t_ctx.submit_order(
            symbol,
            order_type,
            side,
            Decimal(str(quantity)),
            TimeInForceType.Day,
            **kwargs
        )
        order_id = getattr(resp, "order_id", "")
        log_with_order_id = f"{log_message} [order_id={order_id}]" if order_id else log_message
        print(f"OK {log_with_order_id}", flush=True)
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
# Auth: LongPort token from Secret Manager; refresh when expiry < 30 days
# ---------------------------------------------------------------------------
def fetch_token_strict():
    """Read latest longport_token from GCP Secret Manager."""
    client = secret_manager.SecretManagerServiceClient()
    resource_name = f"projects/{PROJECT_ID}/secrets/{SECRET_NAME}/versions/latest"
    return client.access_secret_version(request={"name": resource_name}).payload.data.decode("UTF-8").strip()

def longport_sign(method, uri, headers, params, body, secret):
    """HMAC-SHA256 signature for LongPort token refresh API."""
    ts = headers["X-Timestamp"]
    access_token = headers["Authorization"]
    app_key = headers["X-Api-Key"]
    canonical_request = f"{method.upper()}|{uri}|{params}|authorization:{access_token}\nx-api-key:{app_key}\nx-timestamp:{ts}\n|authorization;x-api-key;x-timestamp|"
    if body: canonical_request += hashlib.sha1(body.encode("utf-8")).hexdigest()
    sign_str = "HMAC-SHA256|" + hashlib.sha1(canonical_request.encode("utf-8")).hexdigest()
    return f"HMAC-SHA256 SignedHeaders=authorization;x-api-key;x-timestamp, Signature={hmac.new(secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()}"

def refresh_token_logic(current_token):
    """If token expires in < 30 days, call refresh API and write new token to Secret Manager; destroy old versions."""
    app_key = os.getenv("LONGPORT_APP_KEY")
    app_secret = os.getenv("LONGPORT_APP_SECRET")
    
    if not all([app_key, app_secret]): 
        print("LONGPORT_APP_KEY or LONGPORT_APP_SECRET not set; skip refresh.")
        send_tg_message("LONGPORT_APP_KEY or LONGPORT_APP_SECRET not set; token refresh skipped.")
        return current_token

    try:
        parts = current_token.split('.')
        if len(parts) > 1:
            payload_b64 = parts[1]
            padded_payload = payload_b64 + '=' * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded_payload).decode('utf-8'))
            
            if (payload.get('exp', 0) - time.time()) / 86400 > 30: 
                return current_token
        
        print("Token expiry < 30 days; refreshing...")
        
        headers = {
            "X-Api-Key": app_key, 
            "Authorization": current_token, 
            "X-Timestamp": str(int(time.time())), 
            "Content-Type": "application/json; charset=utf-8"
        }
        headers['X-Api-Signature'] = longport_sign("GET", "/v1/token/refresh", headers, "", "", app_secret)
        
        resp = requests.get(
            "https://openapi.longportapp.com/v1/token/refresh", 
            headers=headers, 
            timeout=15
        ).json()
        
        if resp.get("code") == 0:
            new_token = resp["data"]["token"]
            
            client = secret_manager.SecretManagerServiceClient()
            parent = f"projects/{PROJECT_ID}/secrets/{SECRET_NAME}"
            
            new_version = client.add_secret_version(
                request={"parent": parent, "payload": {"data": new_token.encode("UTF-8")}}
            )
            print(f"Token refreshed and saved to Secret Manager: {new_version.name}")
            
            try:
                versions = client.list_secret_versions(request={"parent": parent})
                for version in versions:
                    if version.name == new_version.name:
                        continue
                    
                    if version.state != secret_manager.SecretVersion.State.DESTROYED:
                        client.destroy_secret_version(request={"name": version.name})
                        print(f"Destroyed old secret version: {version.name}")
            except Exception as e:
                print(f"Error cleaning old secret versions: {e}")
                
            return new_token
        else:
            print(f"LongPort token refresh API error: {resp}")
            return current_token

    except Exception as e:
        print(f"Token refresh error:\n{traceback.format_exc()}")
        send_tg_message(f"LongPort token refresh failed.\n{e}")
        return current_token

# ---------------------------------------------------------------------------
# Indicators: SOXL/SOXX daily bars, SOXL 150d MA for trend
# ---------------------------------------------------------------------------
def calculate_dynamic_indicators(q_ctx):
    """Fetch SOXL (lookback for MA) and SOXX daily close; return last price and SOXL MA. None if insufficient data."""
    lookback = max(220, TREND_MA_WINDOW + 20)
    soxl_bars = q_ctx.candlesticks("SOXL.US", Period.Day, lookback, AdjustType.ForwardAdjust)
    soxx_bars = q_ctx.candlesticks("SOXX.US", Period.Day, 20, AdjustType.ForwardAdjust)
    
    if not soxl_bars or not soxx_bars:
        print("No quote data from API")
        return None

    df_soxl = pd.DataFrame([{
        'close': float(k.close)
    } for k in soxl_bars])
    
    df_soxx = pd.DataFrame([float(k.close) for k in soxx_bars], columns=['close'])

    if len(df_soxl) < TREND_MA_WINDOW or len(df_soxx) < 1:
        print(f"Insufficient bars: SOXL {len(df_soxl)}, SOXX {len(df_soxx)}")
        return None
    df_soxl['ma_trend'] = df_soxl['close'].rolling(TREND_MA_WINDOW).mean()
    
    indicators = {
        'soxl': {
            'price': df_soxl['close'].iloc[-1],
            'ma_trend': df_soxl['ma_trend'].iloc[-1],
        },
        'soxx': {
            'price': df_soxx['close'].iloc[-1],
        }
    }
    return indicators

# ---------------------------------------------------------------------------
# Strategy: NYSE hours check, indicators, balance/positions, target allocation, sell then buy
# ---------------------------------------------------------------------------
def run_strategy():
    try:
        print(f"[{datetime.now()}] Starting strategy...")

        token = refresh_token_logic(fetch_token_strict())
        os.environ["LONGPORT_ACCESS_TOKEN"] = token
        config = Config.from_env()
        q_ctx, t_ctx = QuoteContext(config), TradeContext(config)

        # Skip if outside NYSE regular session
        try:
            nyse = mcal.get_calendar('NYSE')
            now_utc = datetime.now(pytz.utc)
            schedule = nyse.schedule(start_date=now_utc, end_date=now_utc)
            is_normal = False if schedule.empty else nyse.open_at_time(schedule, now_utc)
        except: is_normal = False

        if not is_normal:
            print("Outside market hours; skip.")
            return

        ind = calculate_dynamic_indicators(q_ctx)
        if ind is None:
            raise Exception("Quote data missing or API limited; cannot compute indicators")

        # USD available cash and positions for strategy symbols only
        bal = t_ctx.account_balance()
        available_cash = 0.0
        for acc in bal:
            for info in getattr(acc, 'cash_infos', []):
                if info.currency == "USD":
                    available_cash += float(info.available_cash)
        
        pos = t_ctx.stock_positions()
        strategy_assets = ["SOXL", "SOXX", "BOXX", "QQQI", "SPYI"]
        mv = {s: 0.0 for s in strategy_assets}
        qty = {s: 0 for s in strategy_assets}
        sellable_qty = {s: 0 for s in strategy_assets}
        
        if pos and hasattr(pos, 'channels'):
            for ch in pos.channels:
                for p in getattr(ch, 'positions', []):
                    sym = getattr(p, 'symbol', '')
                    root_symbol = sym.split('.')[0]
                    if root_symbol in strategy_assets:
                        last_p = float(q_ctx.quote([sym])[0].last_done)
                        q = int(getattr(p, 'quantity', 0))
                        aq = int(getattr(p, 'available_quantity', q))
                        mv[root_symbol] += q * last_p
                        qty[root_symbol] += q
                        sellable_qty[root_symbol] += aq

        total_strategy_equity = available_cash + sum(mv.values())
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
            f"SOXL above {TREND_MA_WINDOW}d MA, hold SOXL, risk {deploy_ratio_text}"
            if active_risk_asset == "SOXL"
            else f"SOXL below {TREND_MA_WINDOW}d MA, switch to SOXX, risk {deploy_ratio_text}"
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
            if diff < -(total_strategy_equity * 0.01) and abs(diff) > current_min_trade:
                p = float(q_ctx.quote([f"{k}.US"])[0].last_done)
                q_sell = min(int(abs(diff) // p), sellable_qty[k])
                if q_sell > 0:
                    if k in ["SOXL", "SOXX", "QQQI", "SPYI"]:
                        lp = round(p * 0.995, 2)
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            OrderType.LO,
                            OrderSide.Sell,
                            q_sell,
                            logs,
                            f"[Limit sell] {k}: {q_sell} @ ${lp}",
                            submitted_price=lp,
                        )
                    else:
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            OrderType.MO,
                            OrderSide.Sell,
                            q_sell,
                            logs,
                            f"[Market sell] {k}: {q_sell} @ ${round(p, 2)}",
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
            if diff > (total_strategy_equity * 0.01) and abs(diff) > current_min_trade:
                p = float(q_ctx.quote([f"{k}.US"])[0].last_done)
                can_buy_val = min(diff, investable_cash)
                if can_buy_val > p:
                    is_limit_order = k in ["SOXL", "SOXX", "QQQI", "SPYI"]
                    order_type = OrderType.LO if is_limit_order else OrderType.MO
                    ref_price = round(p * 1.005, 2) if is_limit_order else round(p, 2)
                    budget_price = ref_price if is_limit_order else p
                    q_buy_budget = int(can_buy_val // budget_price)
                    q_buy_cash_limit = estimate_cash_buy_quantity(t_ctx, f"{k}.US", order_type, ref_price)

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
                            OrderType.LO,
                            OrderSide.Buy,
                            q_buy,
                            logs,
                            f"[Limit buy] {k}: {q_buy} @ ${ref_price}",
                            submitted_price=ref_price,
                        )
                        cost_estimate = q_buy * budget_price
                    else:
                        submitted = submit_order_with_alert(
                            t_ctx,
                            f"{k}.US",
                            OrderType.MO,
                            OrderSide.Buy,
                            q_buy,
                            logs,
                            f"[Market buy] {k}: {q_buy} @ ${round(p, 2)}",
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
                f"Rebalance\n"
                f"Market: {market_status}\n"
                f"Risk: {deploy_ratio_text}\n"
                f"Income target: {income_ratio_text}\n"
                f"Income locked: {income_locked_ratio_text}\n"
                f"Signal: {msg}\n"
                f"---\n"
                f"{formatted_logs}"
            )
            send_tg_message(tg_msg)
        else: 
            print(f"No trades. Signal: {msg}") 
        
    except Exception:
        err = traceback.format_exc()
        print(f"Strategy error:\n{err}")
        send_tg_message(f"Strategy error:\n{err}")

@app.route("/", methods=["POST", "GET"])
def handle_trigger():
    """Entrypoint for Cloud Run / scheduler: run strategy and return 200."""
    run_strategy()
    return "OK", 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))