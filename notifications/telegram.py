"""Telegram notification helpers for LongBridgePlatform."""

from __future__ import annotations

import requests


SIGNAL_ICONS = {
    "hold": "💎",
    "entry": "🚀",
    "reduce": "⚠️",
    "exit": "🔴",
    "idle": "💤",
}


I18N = {
    "zh": {
        "rebalance_title": "🔔 【调仓指令】",
        "dry_run_banner": "🧪 dry-run 模式，本次不会真实下单",
        "strategy_label": "🧭 策略: {name}",
        "market_status": "📊 市场状态: {status}",
        "risk_position": "💼 交易层风险仓位: {ratio}",
        "income_target": "💰 收入层目标: {ratio}",
        "income_locked": "🏦 收入层锁定占比: {ratio}",
        "signal": "🎯 触发信号: {msg}",
        "heartbeat_title": "💓 【心跳检测】",
        "equity": "💰 净值: ${value}",
        "cash_summary": "💵 账户现金: ${available} | 可投资现金: ${investable}",
        "cash_label": "现金",
        "heartbeat_signal": "🎯 信号: {msg}",
        "no_trades": "✅ 无需调仓",
        "no_executable_orders": "⚠️ 本轮没有可执行订单",
        "skipped_actions": "⚠️ 跳过项：",
        "notes_title": "ℹ️ 说明：",
        "order_filled": "✅ 订单成交 | {symbol} {side} {qty}股 均价 ${price} (ID: {order_id})",
        "order_partial": "⚠️ 订单部分成交 | {symbol} {side} 已成交 {executed}/{qty}股 均价 ${price} (ID: {order_id})",
        "order_error": "❌ 订单异常 | {symbol} {side} {qty}股 已{status} (ID: {order_id}) 原因: {reason}",
        "error_title": "🚨 【策略异常】",
        "buy_skipped": "⚪️ [买入跳过] {detail}",
        "sell_skipped": "⚪️ [卖出跳过] {detail}",
        "buy_deferred": "ℹ️ [买入说明] {detail}",
        "buy_deferred_no_investable_cash": "账户现金 ${available} 低于策略保留阈值，可投资现金为 ${investable}，本轮不发起买单",
        "buy_deferred_small_cash": "{symbol} 目标差额 ${diff}，但可投资现金 ${investable} 不足买入 1 股（价格 ${price}）",
        "buy_deferred_cash_limit": "{symbol} 目标差额 ${diff}，预算可买 {budget_qty} 股，但券商估算可买数量为 0；可能有未完成挂单、结算或购买力占用",
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
        "signal_hold": "趋势持有",
        "signal_entry": "入场信号",
        "signal_reduce": "减仓信号",
        "signal_exit": "离场信号",
        "signal_idle": "等待信号",
        "strategy_name_semiconductor_rotation_income": "SOXL/SOXX 半导体趋势收益",
        "strategy_name_hybrid_growth_income": "TQQQ 增长收益",
        "strategy_name_global_etf_rotation": "全球 ETF 轮动",
        "strategy_name_russell_1000_multi_factor_defensive": "罗素1000多因子",
        "strategy_name_tech_pullback_cash_buffer": "QQQ 科技增强",
    },
    "en": {
        "rebalance_title": "🔔 【Trade Execution Report】",
        "dry_run_banner": "🧪 Dry run mode, no real orders will be submitted",
        "strategy_label": "🧭 Strategy: {name}",
        "market_status": "📊 Market: {status}",
        "risk_position": "💼 Risk Position: {ratio}",
        "income_target": "💰 Income Target: {ratio}",
        "income_locked": "🏦 Income Locked: {ratio}",
        "signal": "🎯 Signal: {msg}",
        "heartbeat_title": "💓 【Heartbeat】",
        "equity": "💰 Equity: ${value}",
        "cash_summary": "💵 Cash: ${available} | Investable cash: ${investable}",
        "cash_label": "Cash",
        "heartbeat_signal": "🎯 Signal: {msg}",
        "no_trades": "✅ No trades needed",
        "no_executable_orders": "⚠️ No executable orders this cycle",
        "skipped_actions": "⚠️ Skipped actions:",
        "notes_title": "ℹ️ Notes:",
        "order_filled": "✅ Order Filled | {symbol} {side} {qty} shares avg ${price} (ID: {order_id})",
        "order_partial": "⚠️ Partial Fill | {symbol} {side} filled {executed}/{qty} shares avg ${price} (ID: {order_id})",
        "order_error": "❌ Order Error | {symbol} {side} {qty} shares {status} (ID: {order_id}) reason: {reason}",
        "error_title": "🚨 【Strategy Error】",
        "buy_skipped": "⚪️ [Buy skipped] {detail}",
        "sell_skipped": "⚪️ [Sell skipped] {detail}",
        "buy_deferred": "ℹ️ [Buy note] {detail}",
        "buy_deferred_no_investable_cash": "Account cash ${available} is below the strategy reserve threshold, investable cash is ${investable}; no buy order this cycle",
        "buy_deferred_small_cash": "{symbol} target gap ${diff}, but investable cash ${investable} is not enough for 1 share at ${price}",
        "buy_deferred_cash_limit": "{symbol} target gap ${diff}, budget supports {budget_qty} shares, but broker estimate returned 0; an open order, settlement, or buying-power hold may still be blocking funds",
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
        "signal_hold": "Trend Hold",
        "signal_entry": "Entry Signal",
        "signal_reduce": "Reduce Signal",
        "signal_exit": "Exit Signal",
        "signal_idle": "Idle",
        "strategy_name_semiconductor_rotation_income": "SOXL/SOXX Semiconductor Trend Income",
        "strategy_name_hybrid_growth_income": "TQQQ Growth Income",
        "strategy_name_global_etf_rotation": "Global ETF Rotation",
        "strategy_name_russell_1000_multi_factor_defensive": "Russell 1000 Multi-Factor",
        "strategy_name_tech_pullback_cash_buffer": "QQQ Tech Enhancement",
    },
}


def build_translator(lang):
    def translate(key, **kwargs):
        active_lang = lang if lang in I18N else "en"
        template = I18N[active_lang].get(key, key)
        return template.format(**kwargs) if kwargs else template

    return translate


def build_signal_text(translate_fn):
    def signal_text(icon_key):
        emoji = SIGNAL_ICONS.get(icon_key, "❓")
        name = translate_fn(f"signal_{icon_key}")
        return f"{emoji} {name}"

    return signal_text


def build_strategy_display_name(translate_fn):
    def strategy_display_name(profile: str, *, fallback_name: str | None = None) -> str:
        key = f"strategy_name_{str(profile or '').strip()}"
        translated = translate_fn(key)
        if translated != key:
            return translated
        fallback = str(fallback_name or "").strip()
        if fallback:
            return fallback
        return str(profile or "").strip()

    return strategy_display_name


def build_prefixer(account_prefix: str, service_name: str | None = None):
    def with_prefix(message: str) -> str:
        account_label = str(account_prefix or "").strip()
        service_label = str(service_name or "").strip()
        prefix = account_label or service_label
        if not prefix:
            return message
        return f"[{prefix}] {message}"

    return with_prefix


def build_sender(token, chat_id, *, with_prefix_fn, requests_module=requests):
    def send_tg_message(message):
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            prefixed = with_prefix_fn(message)
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
