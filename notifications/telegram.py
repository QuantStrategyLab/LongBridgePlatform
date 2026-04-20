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
        "dry_run_banner": "🧪 模拟运行模式，本次不会真实下单",
        "strategy_label": "🧭 策略: {name}",
        "market_status": "📊 市场状态: {status}",
        "risk_position": "💼 交易层风险仓位: {ratio}",
        "income_target": "💰 收入层目标: {ratio}",
        "income_locked": "🏦 收入层锁定占比: {ratio}",
        "signal": "🎯 触发信号: {msg}",
        "heartbeat_title": "💓 【心跳检测】",
        "equity": "💰 净值: ${value}",
        "cash_summary": "💵 资金\n  - 账户现金: ${available}\n  - 可投资现金: ${investable}",
        "cash_by_currency": "  - 各币种现金: {currencies}",
        "cash_label": "现金",
        "portfolio_summary_title": "📌 账户概览",
        "portfolio_total_assets": "总资产（策略标的+现金）: ${value}",
        "portfolio_buying_power": "购买力: ${available} | 可投资现金: ${investable}",
        "holding_line": "{symbol}: ${value} / {qty}股",
        "holdings_title": "💼 持仓",
        "order_logs_title": "🧾 执行明细",
        "benchmark_title": "📈 {symbol} 基准",
        "benchmark_price": "{symbol}: {value}",
        "benchmark_ma200": "MA200: {value}",
        "benchmark_exit": "退出线: {value}",
        "heartbeat_signal": "🎯 信号: {msg}",
        "no_trades": "✅ 无需调仓",
        "no_executable_orders": "⚠️ 本轮没有可执行订单",
        "skipped_actions": "⚠️ 跳过项：",
        "notes_title": "ℹ️ 说明：",
        "order_filled": "✅ 订单成交 | {symbol} {side} {qty}股 均价 ${price}（订单号: {order_id}）",
        "order_partial": "⚠️ 订单部分成交 | {symbol} {side} 已成交 {executed}/{qty}股 均价 ${price}（订单号: {order_id}）",
        "order_error": "❌ 订单异常 | {symbol} {side} {qty}股 已{status}（订单号: {order_id}）原因: {reason}",
        "small_account_warning_note": "小账户提示：净值 {portfolio_equity} 低于建议 {min_recommended_equity}；{reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "整数股和最小仓位限制可能导致实盘无法完全复现回测",
        "order_id_suffix": "（订单号: {order_id}）",
        "error_title": "🚨 【策略异常】",
        "buy_skipped": "⚪️ [买入跳过] {detail}",
        "sell_skipped": "⚪️ [卖出跳过] {detail}",
        "buy_deferred": "ℹ️ [买入说明] {detail}",
        "buy_deferred_no_investable_cash": "账户现金 ${available} 低于策略保留阈值，可投资现金为 ${investable}，本轮不发起买单",
        "buy_deferred_non_usd_cash": "检测到非 USD 现金（{currencies}），但美股策略可用 USD 现金为 ${available}、可投资现金为 ${investable}；请先换汇或入金 USD 后再买入",
        "buy_deferred_small_cash": "{symbol} 目标差额 ${diff}，但可投资现金 ${investable} 不足买入 1 股（价格 ${price}）",
        "buy_deferred_cash_limit": "{symbol} 目标差额 ${diff}，预算可买 {budget_qty} 股，但券商估算可买数量为 0；可能有未完成挂单、结算或购买力占用",
        "limit_buy": "📈 [限价买入] {symbol}: {qty}股 @ ${price}",
        "market_buy": "📈 [市价买入] {symbol}: {qty}股 @ ${price}",
        "limit_sell": "📉 [限价卖出] {symbol}: {qty}股 @ ${price}",
        "market_sell": "📉 [市价卖出] {symbol}: {qty}股 @ ${price}",
        "dry_run_order": "🧪 模拟{order_type}{side} {symbol}: {qty}股 @ {price}",
        "order_type_market": "市价",
        "order_type_limit": "限价",
        "side_buy": "买入",
        "side_sell": "卖出",
        "status_rejected": "拒绝",
        "status_canceled": "取消",
        "status_expired": "过期",
        "market_status_risk_on": "🚀 风险开启（{asset}）",
        "market_status_delever": "🛡️ 降杠杆（{asset}）",
        "signal_risk_on": "SOXL 站上 {window} 日均线，持有 SOXL，交易层风险仓位 {ratio}",
        "signal_delever": "SOXL 跌破 {window} 日均线，切换至 SOXX，交易层风险仓位 {ratio}",
        "market_status_blend_gate_risk_on": "🚀 风险开启（{asset}）",
        "market_status_blend_gate_defensive": "🛡️ 降杠杆（{asset}）",
        "signal_blend_gate_risk_on": "{trend_symbol} 站上 {window} 日门槛线，持有 SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} 跌破门槛线，防守持有 SOXX {soxx_ratio}",
        "signal_hold": "趋势持有",
        "signal_entry": "入场信号",
        "signal_reduce": "减仓信号",
        "signal_exit": "离场信号",
        "signal_idle": "等待信号",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX 半导体趋势收益",
        "strategy_name_tqqq_growth_income": "TQQQ 增长收益",
        "strategy_name_global_etf_rotation": "全球 ETF 轮动",
        "strategy_name_russell_1000_multi_factor_defensive": "罗素1000多因子",
        "strategy_name_tech_communication_pullback_enhancement": "科技通信回调增强",
        "strategy_name_qqq_tech_enhancement": "科技通信回调增强",
        "strategy_name_mega_cap_leader_rotation_aggressive": "Mega Cap 激进龙头轮动",
        "strategy_name_mega_cap_leader_rotation_dynamic_top20": "Mega Cap 动态 Top20 龙头轮动",
        "strategy_name_mega_cap_leader_rotation_top50_balanced": "Mega Cap Top50 平衡龙头轮动",
        "strategy_name_dynamic_mega_leveraged_pullback": "Mega Cap 2x 回调策略",
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
        "cash_summary": "💵 Cash\n  - Account cash: ${available}\n  - Investable cash: ${investable}",
        "cash_by_currency": "  - Cash by currency: {currencies}",
        "cash_label": "Cash",
        "portfolio_summary_title": "📌 Portfolio snapshot",
        "portfolio_total_assets": "Total assets (strategy symbols + cash): ${value}",
        "portfolio_buying_power": "Buying power: ${available} | Investable cash: ${investable}",
        "holding_line": "{symbol}: ${value} / {qty} shares",
        "holdings_title": "💼 Holdings",
        "order_logs_title": "🧾 Execution details",
        "benchmark_title": "📈 {symbol} Benchmark",
        "benchmark_price": "{symbol}: {value}",
        "benchmark_ma200": "MA200: {value}",
        "benchmark_exit": "Exit: {value}",
        "heartbeat_signal": "🎯 Signal: {msg}",
        "no_trades": "✅ No trades needed",
        "no_executable_orders": "⚠️ No executable orders this cycle",
        "skipped_actions": "⚠️ Skipped actions:",
        "notes_title": "ℹ️ Notes:",
        "order_filled": "✅ Order Filled | {symbol} {side} {qty} shares avg ${price} (ID: {order_id})",
        "order_partial": "⚠️ Partial Fill | {symbol} {side} filled {executed}/{qty} shares avg ${price} (ID: {order_id})",
        "order_error": "❌ Order Error | {symbol} {side} {qty} shares {status} (ID: {order_id}) reason: {reason}",
        "small_account_warning_note": "small account warning: portfolio equity {portfolio_equity} is below recommended {min_recommended_equity}; {reason}",
        "small_account_warning_reason_integer_shares_min_position_value_may_prevent_backtest_replication": "integer-share minimum position sizing may prevent backtest replication",
        "order_id_suffix": "[order_id={order_id}]",
        "error_title": "🚨 【Strategy Error】",
        "buy_skipped": "⚪️ [Buy skipped] {detail}",
        "sell_skipped": "⚪️ [Sell skipped] {detail}",
        "buy_deferred": "ℹ️ [Buy note] {detail}",
        "buy_deferred_no_investable_cash": "Account cash ${available} is below the strategy reserve threshold, investable cash is ${investable}; no buy order this cycle",
        "buy_deferred_non_usd_cash": "Non-USD cash is present ({currencies}), but this US-equity strategy has USD cash ${available} and investable cash ${investable}; convert or deposit USD before buying",
        "buy_deferred_small_cash": "{symbol} target gap ${diff}, but investable cash ${investable} is not enough for 1 share at ${price}",
        "buy_deferred_cash_limit": "{symbol} target gap ${diff}, budget supports {budget_qty} shares, but broker estimate returned 0; an open order, settlement, or buying-power hold may still be blocking funds",
        "limit_buy": "📈 [Limit buy] {symbol}: {qty} shares @ ${price}",
        "market_buy": "📈 [Market buy] {symbol}: {qty} shares @ ${price}",
        "limit_sell": "📉 [Limit sell] {symbol}: {qty} shares @ ${price}",
        "market_sell": "📉 [Market sell] {symbol}: {qty} shares @ ${price}",
        "dry_run_order": "🧪 DRY_RUN {order_type} {side} {symbol} {qty} @ {price}",
        "order_type_market": "market",
        "order_type_limit": "limit",
        "side_buy": "Buy",
        "side_sell": "Sell",
        "status_rejected": "Rejected",
        "status_canceled": "Canceled",
        "status_expired": "Expired",
        "market_status_risk_on": "🚀 RISK-ON ({asset})",
        "market_status_delever": "🛡️ DE-LEVER ({asset})",
        "signal_risk_on": "SOXL above {window}d MA, hold SOXL, risk {ratio}",
        "signal_delever": "SOXL below {window}d MA, switch to SOXX, risk {ratio}",
        "market_status_blend_gate_risk_on": "🚀 RISK-ON ({asset})",
        "market_status_blend_gate_defensive": "🛡️ DE-LEVER ({asset})",
        "signal_blend_gate_risk_on": "{trend_symbol} above {window}d gated entry, hold SOXL {soxl_ratio} + SOXX {soxx_ratio}",
        "signal_blend_gate_defensive": "{trend_symbol} below gated entry, hold defensive SOXX {soxx_ratio}",
        "signal_hold": "Trend Hold",
        "signal_entry": "Entry Signal",
        "signal_reduce": "Reduce Signal",
        "signal_exit": "Exit Signal",
        "signal_idle": "Idle",
        "strategy_name_soxl_soxx_trend_income": "SOXL/SOXX Semiconductor Trend Income",
        "strategy_name_tqqq_growth_income": "TQQQ Growth Income",
        "strategy_name_global_etf_rotation": "Global ETF Rotation",
        "strategy_name_russell_1000_multi_factor_defensive": "Russell 1000 Multi-Factor",
        "strategy_name_tech_communication_pullback_enhancement": "Tech/Communication Pullback Enhancement",
        "strategy_name_qqq_tech_enhancement": "Tech/Communication Pullback Enhancement",
        "strategy_name_mega_cap_leader_rotation_aggressive": "Mega Cap Leader Rotation Aggressive",
        "strategy_name_mega_cap_leader_rotation_dynamic_top20": "Mega Cap Leader Rotation Dynamic Top20",
        "strategy_name_mega_cap_leader_rotation_top50_balanced": "Mega Cap Leader Rotation Top50 Balanced",
        "strategy_name_dynamic_mega_leveraged_pullback": "Dynamic Mega Leveraged Pullback",
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
