"""Order execution helpers for LongBridgePlatform."""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import dataclass

from quant_platform_kit.common.models import OrderIntent


@dataclass(frozen=True)
class ExecutionCycleResult:
    plan: dict
    portfolio: dict
    execution: dict
    allocation: dict
    logs: tuple[str, ...]
    skip_logs: tuple[str, ...]
    note_logs: tuple[str, ...]
    action_done: bool


def _noop_sleep(_seconds):
    return None


def _normalize_cash_by_currency(raw_cash) -> dict[str, float]:
    if not isinstance(raw_cash, Mapping):
        return {}
    cash_by_currency: dict[str, float] = {}
    for currency, amount in raw_cash.items():
        normalized_currency = str(currency or "").strip().upper()
        if not normalized_currency:
            continue
        cash_by_currency[normalized_currency] = float(amount)
    return cash_by_currency


def _format_cash_by_currency(cash_by_currency: Mapping[str, float]) -> str:
    parts = []
    for currency in sorted(cash_by_currency, key=lambda value: (value != "USD", value)):
        amount = float(cash_by_currency[currency])
        if amount == 0.0:
            continue
        parts.append(f"{currency} {amount:,.2f}")
    return ", ".join(parts)


def _has_positive_non_usd_cash(cash_by_currency: Mapping[str, float]) -> bool:
    return any(
        currency != "USD" and float(amount) > 0.0
        for currency, amount in cash_by_currency.items()
    )


def record_skip_log(skip_logs, *, translator, with_prefix, kind, detail):
    message = translator(kind, detail=detail)
    skip_logs.append(message)
    print(with_prefix(message), flush=True)


def record_note_log(note_logs, *, translator, with_prefix, kind, **kwargs):
    detail = translator(kind, **kwargs)
    message = translator("buy_deferred", detail=detail)
    note_logs.append(message)
    print(with_prefix(message), flush=True)


def safe_quote_last_price(symbol, *, market_data_port, notify_issue):
    try:
        return float(market_data_port.get_quote(symbol).last_price)
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


def execute_rebalance_cycle(
    *,
    trade_context,
    plan,
    portfolio,
    execution,
    allocation,
    fetch_replanned_state,
    market_data_port,
    estimate_max_purchase_quantity,
    execution_port,
    post_submit_order=None,
    notify_issue,
    translator,
    with_prefix,
    limit_sell_discount,
    limit_buy_premium,
    dry_run_only=False,
    post_sell_refresh_attempts=1,
    post_sell_refresh_interval_sec=0.0,
    sleeper=_noop_sleep,
) -> ExecutionCycleResult:
    logs: list[str] = []
    skip_logs: list[str] = []
    note_logs: list[str] = []
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
    available_cash = float(portfolio["liquid_cash"])
    cash_by_currency = _normalize_cash_by_currency(portfolio.get("cash_by_currency"))
    investable_cash = float(execution["investable_cash"])
    current_min_trade = float(execution["current_min_trade"])

    def append_order_id_suffix(log_message, order_id):
        order_id_text = str(order_id or "").strip()
        if not order_id_text:
            return log_message
        suffix = str(translator("order_id_suffix", order_id=order_id_text)).strip()
        if not suffix or suffix == "order_id_suffix":
            suffix = f"[order_id={order_id_text}]"
        return f"{log_message} {suffix}"

    def submit_order_via_port(symbol, order_type, side, quantity, log_message, *, submitted_price=None):
        order_intent = OrderIntent(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=float(submitted_price) if submitted_price is not None else None,
        )
        side_text = "Buy" if side == "buy" else "Sell"
        try:
            report = execution_port.submit_order(order_intent)
        except Exception:
            notify_issue(
                "Order submit failed",
                (
                    f"Symbol: {symbol} Side: {side_text} Qty: {quantity} "
                    f"Type: {order_type} Price: {submitted_price if submitted_price is not None else 'MO'}\n"
                    f"{traceback.format_exc()}"
                ),
            )
            return False

        status = str(report.status or "").strip().lower()
        if status not in {"submitted", "accepted"}:
            detail = report.raw_payload.get("detail", report.status) if isinstance(report.raw_payload, Mapping) else report.status
            notify_issue(
                "Order submit failed",
                (
                    f"Symbol: {symbol} Side: {side_text} Qty: {quantity} "
                    f"Type: {order_type} Price: {submitted_price if submitted_price is not None else 'MO'}\n"
                    f"Status: {detail}"
                ),
            )
            return False

        log_with_order_id = append_order_id_suffix(log_message, report.broker_order_id)
        print(with_prefix(f"OK {log_with_order_id}"), flush=True)
        logs.append(log_with_order_id)
        if post_submit_order is not None:
            try:
                post_submit_order(trade_context, order_intent, report)
            except Exception:
                notify_issue(
                    "Order post-submit hook failed",
                    f"Symbol: {symbol} Side: {side_text} Qty: {quantity}\n{traceback.format_exc()}",
                )
        return True

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
                f"{symbol}.US",
                market_data_port=market_data_port,
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
                        submitted = submit_order_via_port(
                            f"{symbol}.US",
                            "limit",
                            "sell",
                            quantity,
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
                        submitted = submit_order_via_port(
                            f"{symbol}.US",
                            "market",
                            "sell",
                            quantity,
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
        available_cash = float(portfolio["liquid_cash"])
        cash_by_currency = _normalize_cash_by_currency(portfolio.get("cash_by_currency"))
        investable_cash = float(execution["investable_cash"])
        current_min_trade = float(execution["current_min_trade"])

    if (
        available_cash <= 0.0
        and investable_cash <= 0.0
        and _has_positive_non_usd_cash(cash_by_currency)
    ):
        record_note_log(
            note_logs,
            translator=translator,
            with_prefix=with_prefix,
            kind="buy_deferred_non_usd_cash",
            available=f"{available_cash:.2f}",
            investable=f"{investable_cash:.2f}",
            currencies=_format_cash_by_currency(cash_by_currency),
        )

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
            f"{symbol}.US",
            market_data_port=market_data_port,
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
                    submitted = submit_order_via_port(
                        f"{symbol}.US",
                        "limit",
                        "buy",
                        quantity,
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
                    submitted = submit_order_via_port(
                        f"{symbol}.US",
                        "market",
                        "buy",
                        quantity,
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

    return ExecutionCycleResult(
        plan=dict(plan or {}),
        portfolio=dict(portfolio or {}),
        execution=dict(execution or {}),
        allocation=dict(allocation or {}),
        logs=tuple(logs),
        skip_logs=tuple(skip_logs),
        note_logs=tuple(note_logs),
        action_done=action_done,
    )
