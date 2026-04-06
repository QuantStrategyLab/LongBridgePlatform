"""Application orchestration for LongBridgePlatform."""

from __future__ import annotations

import os
import traceback
from datetime import datetime


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

    account_state = fetch_strategy_account_state(quote_context, trade_context)
    plan = resolve_rebalance_plan(
        indicators=indicators,
        account_state=account_state,
    )

    logs = []
    skip_logs = []
    note_logs = []
    action_done = False
    sell_submitted = False
    threshold_value = plan["threshold_value"]
    limit_order_symbols = set(plan["limit_order_symbols"])

    for symbol in plan["strategy_assets"]:
        diff = plan["targets"][symbol] - plan["market_values"][symbol]
        if diff < -threshold_value and abs(diff) > plan["current_min_trade"]:
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
                plan["sellable_quantities"][symbol],
            )
            if quantity > 0:
                if symbol in limit_order_symbols:
                    limit_price = round(price * limit_sell_discount, 2)
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
            elif plan["sellable_quantities"][symbol] <= 0 and plan["quantities"][symbol] > 0:
                record_skip_log(
                    skip_logs,
                    translator=translator,
                    with_prefix=with_prefix,
                    kind="sell_skipped",
                    detail=(
                        f"Symbol: {symbol}.US Diff: ${abs(diff):.2f} "
                        f"Held: {plan['quantities'][symbol]} Sellable: {plan['sellable_quantities'][symbol]} "
                        f"(no sellable)"
                    ),
                )

    if sell_submitted:
        account_state = fetch_strategy_account_state(quote_context, trade_context)
        plan = resolve_rebalance_plan(
            indicators=indicators,
            account_state=account_state,
        )
        threshold_value = plan["threshold_value"]
        limit_order_symbols = set(plan["limit_order_symbols"])

    investable_cash = plan["investable_cash"]
    buy_candidates = [
        symbol
        for symbol in plan["strategy_assets"]
        if (plan["targets"][symbol] - plan["market_values"][symbol]) > threshold_value
        and abs(plan["targets"][symbol] - plan["market_values"][symbol]) > plan["current_min_trade"]
    ]
    if buy_candidates and investable_cash <= 0:
        buy_candidates = []
    for symbol in buy_candidates:
        diff = plan["targets"][symbol] - plan["market_values"][symbol]
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
            available=f"{plan['available_cash']:.2f}",
            investable=f"{plan['investable_cash']:.2f}",
        )
        formatted_logs = "\n".join(f"  {log}" for log in [*logs, *skip_logs, *note_logs])
        tg_message = (
            f"{translator('rebalance_title')}\n"
            f"{translator('market_status', status=plan['market_status'])}\n"
            f"{cash_summary}\n"
            f"{translator('risk_position', ratio=plan['deploy_ratio_text'])}\n"
            f"{translator('income_target', ratio=plan['income_ratio_text'])}\n"
            f"{translator('income_locked', ratio=plan['income_locked_ratio_text'])}\n"
            f"{translator('signal', msg=plan['signal_message'])}\n"
            f"{separator}\n"
            f"{formatted_logs}"
        )
        send_tg_message(tg_message)
    else:
        cash_label = translator("cash_label")
        equity_text = f"{plan['total_strategy_equity']:,.2f}"
        cash_summary = translator(
            "cash_summary",
            available=f"{plan['available_cash']:.2f}",
            investable=f"{plan['investable_cash']:.2f}",
        )
        holdings_lines = []
        for row in plan["portfolio_rows"]:
            if len(row) == 1:
                symbol = row[0]
                holdings_lines.append(
                    f"{symbol}: ${plan['market_values'][symbol]:,.2f}  {cash_label}: ${plan['available_cash']:,.2f}"
                )
            else:
                holdings_lines.append(
                    "  ".join(
                        f"{symbol}: ${plan['market_values'][symbol]:,.2f}"
                        for symbol in row
                    )
                )
        no_trade_message = (
            f"{translator('heartbeat_title')}\n"
            f"{translator('market_status', status=plan['market_status'])}\n"
            f"{translator('equity', value=equity_text)}\n"
            f"{cash_summary}\n"
            f"{separator}\n"
            + "\n".join(holdings_lines) + "\n"
            f"{separator}\n"
            f"{translator('risk_position', ratio=plan['deploy_ratio_text'])}\n"
            f"{translator('income_target', ratio=plan['income_ratio_text'])}\n"
            f"{translator('income_locked', ratio=plan['income_locked_ratio_text'])}\n"
            f"{translator('heartbeat_signal', msg=plan['signal_message'])}\n"
            f"{separator}\n"
            f"{translator('no_executable_orders') if (skip_logs or note_logs) else translator('no_trades')}"
        )
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
