"""Application orchestration for LongBridgePlatform."""

from __future__ import annotations

import os
import traceback
from datetime import datetime

_ZH_REASON_REPLACEMENTS = (
    ("feature snapshot guard blocked execution", "特征快照校验阻止执行"),
    ("feature snapshot required", "需要特征快照"),
    ("feature snapshot compute failed", "特征快照计算失败"),
    ("feature_snapshot_download_failed", "特征快照下载失败"),
    ("feature_snapshot_compute_failed", "特征快照计算失败"),
    ("feature_snapshot_path_missing", "缺少特征快照路径"),
    ("feature_snapshot_missing", "特征快照不存在"),
    ("feature_snapshot_stale", "特征快照过旧"),
    ("feature_snapshot_manifest_missing", "缺少快照清单"),
    ("feature_snapshot_profile_mismatch", "快照策略名不匹配"),
    ("feature_snapshot_config_name_mismatch", "快照配置名不匹配"),
    ("feature_snapshot_config_path_mismatch", "快照配置路径不匹配"),
    ("feature_snapshot_contract_version_mismatch", "快照契约版本不匹配"),
    ("RISK-ON", "风险开启"),
    ("DE-LEVER", "降杠杆"),
    ("regime=hard_defense", "市场阶段=强防御"),
    ("regime=soft_defense", "市场阶段=软防御"),
    ("regime=risk_on", "市场阶段=进攻"),
    ("benchmark_trend=down", "基准趋势=向下"),
    ("benchmark_trend=up", "基准趋势=向上"),
    ("benchmark=down", "基准趋势=向下"),
    ("benchmark=up", "基准趋势=向上"),
    ("breadth=", "市场宽度="),
    ("target_stock=", "目标股票仓位="),
    ("realized_stock=", "实际股票仓位="),
    ("stock_exposure=", "股票目标仓位="),
    ("safe_haven=", "避险仓位="),
    ("selected=", "入选标的数="),
    ("top=", "前排标的="),
    ("no_selection", "无入选标的"),
    ("outside_execution_window", "当前不在执行窗口"),
    ("insufficient_buying_power", "购买力不足"),
    ("missing_price", "缺少报价"),
    ("no_equity", "无净值"),
    ("fail_closed", "关闭执行"),
    ("reason=", "原因="),
)


def _plan_portfolio(plan):
    return dict(plan.get("portfolio") or {})


def _plan_execution(plan):
    return dict(plan.get("execution") or {})


def _plan_allocation(plan):
    return dict(plan.get("allocation") or {})


def _noop_sleep(_seconds):
    return None


def _has_text(value):
    return bool(str(value or "").strip())


def _translator_uses_zh(translator) -> bool:
    sample = str(translator("no_trades"))
    return any("\u4e00" <= ch <= "\u9fff" for ch in sample)


def _localize_notification_text(text, *, translator):
    value = str(text or "").strip()
    if not value or not _translator_uses_zh(translator):
        return value
    localized = value
    for source, target in _ZH_REASON_REPLACEMENTS:
        localized = localized.replace(source, target)
    return localized


def _has_benchmark_context(execution):
    return any(
        float(execution.get(key) or 0.0) > 0.0
        for key in ("benchmark_price", "long_trend_value", "exit_line")
    )


def _build_benchmark_line(execution):
    if not _has_benchmark_context(execution):
        return None
    benchmark_symbol = str(execution.get("benchmark_symbol") or "QQQ")
    benchmark_price = float(execution.get("benchmark_price") or 0.0)
    long_trend_value = float(execution.get("long_trend_value") or 0.0)
    exit_line = float(execution.get("exit_line") or 0.0)
    return (
        f"{benchmark_symbol}: {benchmark_price:.2f} | "
        f"MA200: {long_trend_value:.2f} | Exit: {exit_line:.2f}"
    )


def _append_status_lines(lines, *, execution, translator, signal_key):
    status_display = _localize_notification_text(execution.get("status_display"), translator=translator)
    if status_display:
        lines.append(translator("market_status", status=status_display))

    deploy_ratio_text = str(execution.get("deploy_ratio_text") or "").strip()
    if deploy_ratio_text:
        lines.append(translator("risk_position", ratio=deploy_ratio_text))

    income_ratio_text = str(execution.get("income_ratio_text") or "").strip()
    if income_ratio_text:
        lines.append(translator("income_target", ratio=income_ratio_text))

    income_locked_ratio_text = str(execution.get("income_locked_ratio_text") or "").strip()
    if income_locked_ratio_text:
        lines.append(translator("income_locked", ratio=income_locked_ratio_text))

    signal_display = _localize_notification_text(execution.get("signal_display"), translator=translator)
    if signal_display:
        lines.append(translator(signal_key, msg=signal_display))

    benchmark_line = _build_benchmark_line(execution)
    if benchmark_line:
        lines.append(benchmark_line)



def _append_strategy_line(lines, *, strategy_display_name, translator):
    name = str(strategy_display_name or "").strip()
    if name:
        lines.append(translator("strategy_label", name=name))



def record_skip_log(skip_logs, *, translator, with_prefix, kind, detail):
    message = translator(kind, detail=detail)
    skip_logs.append(message)
    print(with_prefix(message), flush=True)


def record_note_log(note_logs, *, translator, with_prefix, kind, **kwargs):
    detail = translator(kind, **kwargs)
    message = translator("buy_deferred", detail=detail)
    note_logs.append(message)
    print(with_prefix(message), flush=True)


def run_strategy(
    *,
    project_id,
    secret_name,
    token_refresh_threshold_days,
    limit_sell_discount,
    limit_buy_premium,
    separator,
    translator,
    with_prefix,
    send_tg_message,
    notify_issue,
    fetch_token_from_secret,
    refresh_token_if_needed,
    build_contexts,
    calculate_strategy_indicators,
    fetch_strategy_account_state,
    resolve_rebalance_plan,
    fetch_last_price,
    estimate_max_purchase_quantity,
    submit_order_with_alert,
    dry_run_only=False,
    strategy_display_name="",
    post_sell_refresh_attempts=1,
    post_sell_refresh_interval_sec=0.0,
    sleeper=_noop_sleep,
):
    print(with_prefix(f"[{datetime.now()}] Starting strategy..."), flush=True)

    token = refresh_token_if_needed(
        fetch_token_from_secret(project_id, secret_name),
        project_id=project_id,
        secret_name=secret_name,
        app_key=os.getenv("LONGPORT_APP_KEY"),
        app_secret=os.getenv("LONGPORT_APP_SECRET"),
        refresh_threshold_days=token_refresh_threshold_days,
    )
    app_key = os.getenv("LONGPORT_APP_KEY", "")
    app_secret = os.getenv("LONGPORT_APP_SECRET", "")
    quote_context, trade_context = build_contexts(app_key, app_secret, token)

    indicators = calculate_strategy_indicators(quote_context)
    if indicators is None:
        raise Exception("Quote data missing or API limited; cannot compute indicators")

    def load_plan(current_account_state):
        current_plan = resolve_rebalance_plan(
            indicators=indicators,
            account_state=current_account_state,
        )
        current_portfolio = _plan_portfolio(current_plan)
        current_execution = _plan_execution(current_plan)
        current_allocation = _plan_allocation(current_plan)
        if current_allocation.get("target_mode") != "value":
            raise ValueError("LongBridgePlatform requires allocation.target_mode=value")
        return current_plan, current_portfolio, current_execution, current_allocation

    def fetch_replanned_state():
        current_account_state = fetch_strategy_account_state(quote_context, trade_context)
        return load_plan(current_account_state)

    plan, portfolio, execution, allocation = fetch_replanned_state()

    logs = []
    skip_logs = []
    note_logs = []
    action_done = False
    sell_submitted = False
    threshold_value = float(execution["trade_threshold_value"])
    limit_order_symbols = set(
        allocation.get("risk_symbols", ()) + allocation.get("income_symbols", ())
    )

    strategy_assets = tuple(allocation["strategy_symbols"])
    market_values = dict(portfolio["market_values"])
    quantities = dict(portfolio["quantities"])
    sellable_quantities = dict(portfolio["sellable_quantities"])
    target_values = dict(allocation["targets"])
    total_strategy_equity = float(portfolio["total_equity"])
    available_cash = float(portfolio["liquid_cash"])
    investable_cash = float(execution["investable_cash"])
    current_min_trade = float(execution["current_min_trade"])
    portfolio_rows = tuple(portfolio["portfolio_rows"])
    def record_dry_run(symbol, side, quantity, price, *, order_type):
        price_text = f"${price:.2f}" if price is not None else translator("order_type_market")
        side_key = "side_buy" if str(side).lower() == "buy" else "side_sell"
        order_type_key = "order_type_limit" if order_type == "limit" else "order_type_market"
        message = translator(
            "dry_run_order",
            side=translator(side_key),
            symbol=symbol,
            qty=quantity,
            price=price_text,
            order_type=translator(order_type_key),
        )
        logs.append(message)
        print(with_prefix(message), flush=True)
        return True

    for symbol in strategy_assets:
        diff = target_values[symbol] - market_values[symbol]
        if diff < -threshold_value and abs(diff) > current_min_trade:
            price = safe_quote_last_price(
                quote_context,
                f"{symbol}.US",
                fetch_last_price=fetch_last_price,
                notify_issue=notify_issue,
            )
            if price is None:
                continue
            quantity = min(
                int(abs(diff) // price),
                sellable_quantities[symbol],
            )
            if quantity > 0:
                if symbol in limit_order_symbols:
                    limit_price = round(price * limit_sell_discount, 2)
                    if dry_run_only:
                        submitted = record_dry_run(
                            f"{symbol}.US",
                            "sell",
                            quantity,
                            limit_price,
                            order_type="limit",
                        )
                    else:
                        submitted = submit_order_with_alert(
                            trade_context,
                            f"{symbol}.US",
                            "limit",
                            "sell",
                            quantity,
                            logs,
                            translator("limit_sell", symbol=symbol, qty=quantity, price=limit_price),
                            submitted_price=limit_price,
                        )
                else:
                    if dry_run_only:
                        submitted = record_dry_run(
                            f"{symbol}.US",
                            "sell",
                            quantity,
                            round(price, 2),
                            order_type="market",
                        )
                    else:
                        submitted = submit_order_with_alert(
                            trade_context,
                            f"{symbol}.US",
                            "market",
                            "sell",
                            quantity,
                            logs,
                            translator("market_sell", symbol=symbol, qty=quantity, price=round(price, 2)),
                        )

                if submitted:
                    action_done = True
                    sell_submitted = True
            elif sellable_quantities[symbol] <= 0 and quantities[symbol] > 0:
                record_skip_log(
                    skip_logs,
                    translator=translator,
                    with_prefix=with_prefix,
                    kind="sell_skipped",
                    detail=(
                        f"Symbol: {symbol}.US Diff: ${abs(diff):.2f} "
                        f"Held: {quantities[symbol]} Sellable: {sellable_quantities[symbol]} "
                        f"(no sellable)"
                    ),
                )

    if sell_submitted:
        previous_investable_cash = investable_cash
        refresh_attempts = max(1, int(post_sell_refresh_attempts or 1))
        refresh_interval = max(0.0, float(post_sell_refresh_interval_sec or 0.0))
        best_refreshed_state = None
        best_investable_cash = previous_investable_cash
        for attempt in range(refresh_attempts):
            if attempt > 0:
                sleeper(refresh_interval)
            refreshed_state = fetch_replanned_state()
            refreshed_execution = refreshed_state[2]
            refreshed_investable_cash = float(refreshed_execution["investable_cash"])
            if best_refreshed_state is None or refreshed_investable_cash > best_investable_cash:
                best_refreshed_state = refreshed_state
                best_investable_cash = refreshed_investable_cash
            if refreshed_investable_cash > previous_investable_cash:
                best_refreshed_state = refreshed_state
                break
        plan, portfolio, execution, allocation = best_refreshed_state
        threshold_value = float(execution["trade_threshold_value"])
        limit_order_symbols = set(
            allocation.get("risk_symbols", ()) + allocation.get("income_symbols", ())
        )
        strategy_assets = tuple(allocation["strategy_symbols"])
        market_values = dict(portfolio["market_values"])
        quantities = dict(portfolio["quantities"])
        sellable_quantities = dict(portfolio["sellable_quantities"])
        target_values = dict(allocation["targets"])
        total_strategy_equity = float(portfolio["total_equity"])
        available_cash = float(portfolio["liquid_cash"])
        investable_cash = float(execution["investable_cash"])
        current_min_trade = float(execution["current_min_trade"])
        portfolio_rows = tuple(portfolio["portfolio_rows"])
    buy_candidates = [
        symbol
        for symbol in strategy_assets
        if (target_values[symbol] - market_values[symbol]) > threshold_value
        and abs(target_values[symbol] - market_values[symbol]) > current_min_trade
    ]
    if buy_candidates and investable_cash <= 0:
        buy_candidates = []
    for symbol in buy_candidates:
        diff = target_values[symbol] - market_values[symbol]
        price = safe_quote_last_price(
            quote_context,
            f"{symbol}.US",
            fetch_last_price=fetch_last_price,
            notify_issue=notify_issue,
        )
        if price is None:
            continue
        can_buy_value = min(diff, investable_cash)
        if can_buy_value > price:
            is_limit_order = symbol in limit_order_symbols
            order_kind = "limit" if is_limit_order else "market"
            ref_price = round(price * limit_buy_premium, 2) if is_limit_order else round(price, 2)
            budget_price = ref_price if is_limit_order else price
            budget_quantity = int(can_buy_value // budget_price)
            cash_limit_quantity = estimate_cash_buy_quantity_safe(
                trade_context,
                f"{symbol}.US",
                order_kind,
                ref_price,
                estimate_max_purchase_quantity=estimate_max_purchase_quantity,
                notify_issue=notify_issue,
            )
            if cash_limit_quantity is None:
                continue

            quantity = min(budget_quantity, cash_limit_quantity)
            cost_estimate = 0.0
            if quantity <= 0:
                record_note_log(
                    note_logs,
                    translator=translator,
                    with_prefix=with_prefix,
                    kind="buy_deferred_cash_limit",
                    symbol=f"{symbol}.US",
                    diff=f"{diff:.2f}",
                    budget_qty=budget_quantity,
                )
                continue

            if is_limit_order:
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{symbol}.US",
                        "buy",
                        quantity,
                        ref_price,
                        order_type="limit",
                    )
                else:
                    submitted = submit_order_with_alert(
                        trade_context,
                        f"{symbol}.US",
                        "limit",
                        "buy",
                        quantity,
                        logs,
                        translator("limit_buy", symbol=symbol, qty=quantity, price=ref_price),
                        submitted_price=ref_price,
                    )
                cost_estimate = quantity * budget_price
            else:
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{symbol}.US",
                        "buy",
                        quantity,
                        round(price, 2),
                        order_type="market",
                    )
                else:
                    submitted = submit_order_with_alert(
                        trade_context,
                        f"{symbol}.US",
                        "market",
                        "buy",
                        quantity,
                        logs,
                        translator("market_buy", symbol=symbol, qty=quantity, price=round(price, 2)),
                    )
                cost_estimate = quantity * budget_price

            if submitted:
                investable_cash = max(0, investable_cash - cost_estimate)
                action_done = True
        else:
            record_note_log(
                note_logs,
                translator=translator,
                with_prefix=with_prefix,
                kind="buy_deferred_small_cash",
                symbol=f"{symbol}.US",
                diff=f"{diff:.2f}",
                investable=f"{investable_cash:.2f}",
                price=f"{price:.2f}",
            )

    if action_done:
        cash_summary = translator(
            "cash_summary",
            available=f"{available_cash:.2f}",
            investable=f"{investable_cash:.2f}",
        )
        formatted_logs = "\n".join(f"  {log}" for log in [*logs, *skip_logs, *note_logs])
        tg_lines = [translator("rebalance_title")]
        _append_strategy_line(
            tg_lines,
            strategy_display_name=strategy_display_name,
            translator=translator,
        )
        if dry_run_only:
            tg_lines.append(translator("dry_run_banner"))
        tg_lines.append(cash_summary)
        _append_status_lines(
            tg_lines,
            execution=execution,
            translator=translator,
            signal_key="signal",
        )
        tg_lines.extend([separator, formatted_logs])
        tg_message = "\n".join(tg_lines)
        print(with_prefix(tg_message), flush=True)
        send_tg_message(tg_message)
    else:
        equity_text = f"{total_strategy_equity:,.2f}"
        cash_summary = translator(
            "cash_summary",
            available=f"{available_cash:.2f}",
            investable=f"{investable_cash:.2f}",
        )
        holdings_lines = []
        for row in portfolio_rows:
            holdings_lines.append(
                "  ".join(
                    f"{symbol}: ${market_values[symbol]:,.2f}"
                    for symbol in row
                )
            )
        no_trade_lines = [
            translator("heartbeat_title"),
        ]
        _append_strategy_line(
            no_trade_lines,
            strategy_display_name=strategy_display_name,
            translator=translator,
        )
        no_trade_lines.append(translator("equity", value=equity_text))
        if dry_run_only:
            no_trade_lines.append(translator("dry_run_banner"))
        no_trade_lines.extend(
            [
                cash_summary,
                separator,
                *holdings_lines,
                separator,
            ]
        )
        _append_status_lines(
            no_trade_lines,
            execution=execution,
            translator=translator,
            signal_key="heartbeat_signal",
        )
        no_trade_lines.extend(
            [
                separator,
                translator("no_executable_orders") if (skip_logs or note_logs) else translator("no_trades"),
            ]
        )
        no_trade_message = "\n".join(no_trade_lines)
        if skip_logs:
            no_trade_message += (
                f"\n{separator}\n"
                f"{translator('skipped_actions')}\n"
                + "\n".join(f"  {log}" for log in skip_logs)
            )
        if note_logs:
            no_trade_message += (
                f"\n{separator}\n"
                f"{translator('notes_title')}\n"
                + "\n".join(f"  {log}" for log in note_logs)
            )
        print(with_prefix(no_trade_message), flush=True)
        send_tg_message(no_trade_message)


def safe_quote_last_price(quote_context, symbol, *, fetch_last_price, notify_issue):
    try:
        return fetch_last_price(quote_context, symbol)
    except Exception as exc:
        notify_issue("Quote failed", f"Symbol: {symbol}\n{exc}")
        return None


def estimate_cash_buy_quantity_safe(
    trade_context,
    symbol,
    order_kind,
    ref_price,
    *,
    estimate_max_purchase_quantity,
    notify_issue,
):
    try:
        return estimate_max_purchase_quantity(
            trade_context,
            symbol,
            order_kind=order_kind,
            ref_price=ref_price,
        )
    except Exception:
        notify_issue(
            "Estimate max buy failed",
            f"Symbol: {symbol}\nOrderKind: {order_kind}\n{traceback.format_exc()}",
        )
        return None
