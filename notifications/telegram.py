"""Telegram notification helpers for LongBridgePlatform."""

from __future__ import annotations

import requests


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
        "no_executable_orders": "⚠️ 本轮没有可执行订单",
        "skipped_actions": "⚠️ 跳过项：",
        "order_filled": "✅ 订单成交 | {symbol} {side} {qty}股 均价 ${price} (ID: {order_id})",
        "order_partial": "⚠️ 订单部分成交 | {symbol} {side} 已成交 {executed}/{qty}股 均价 ${price} (ID: {order_id})",
        "order_error": "❌ 订单异常 | {symbol} {side} {qty}股 已{status} (ID: {order_id}) 原因: {reason}",
        "error_title": "🚨 【策略异常】",
        "buy_skipped": "⚪️ [买入跳过] {detail}",
        "sell_skipped": "⚪️ [卖出跳过] {detail}",
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
        "no_executable_orders": "⚠️ No executable orders this cycle",
        "skipped_actions": "⚠️ Skipped actions:",
        "order_filled": "✅ Order Filled | {symbol} {side} {qty} shares avg ${price} (ID: {order_id})",
        "order_partial": "⚠️ Partial Fill | {symbol} {side} filled {executed}/{qty} shares avg ${price} (ID: {order_id})",
        "order_error": "❌ Order Error | {symbol} {side} {qty} shares {status} (ID: {order_id}) reason: {reason}",
        "error_title": "🚨 【Strategy Error】",
        "buy_skipped": "⚪️ [Buy skipped] {detail}",
        "sell_skipped": "⚪️ [Sell skipped] {detail}",
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


def build_translator(lang):
    def translate(key, **kwargs):
        active_lang = lang if lang in I18N else "en"
        template = I18N[active_lang].get(key, key)
        return template.format(**kwargs) if kwargs else template

    return translate


def build_prefixer(account_prefix: str, service_name: str):
    def with_prefix(message: str) -> str:
        return f"[{account_prefix}/{service_name}] {message}"

    return with_prefix


def build_sender(token, chat_id, *, with_prefix_fn, requests_module=requests):
    def send_tg_message(message):
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            prefixed = with_prefix_fn(message)
            print(f"TG:\n{prefixed}", flush=True)
            requests_module.post(url, json={"chat_id": chat_id, "text": prefixed}, timeout=10)
        except Exception as exc:
            print(f"Telegram send failed: {exc}", flush=True)

    return send_tg_message


def build_issue_notifier(*, with_prefix_fn, send_tg_message_fn):
    def notify_issue(title, detail):
        message = f"{title}\n{detail}"
        print(with_prefix_fn(message), flush=True)
        send_tg_message_fn(message)

    return notify_issue
