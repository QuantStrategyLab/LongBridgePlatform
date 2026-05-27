"""Order execution helpers for LongBridgePlatform."""

from __future__ import annotations

import traceback
from collections.abc import Mapping
from dataclasses import dataclass

try:
    from quant_platform_kit.common.cash_sweep import (
        estimate_cash_sweep_sale_quantity_to_fund_buy,
        should_sell_cash_sweep_to_fund_whole_share_buy,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    import math

    def estimate_cash_sweep_sale_quantity_to_fund_buy(
        max_quantity,
        cash_sweep_price,
        base_buying_power,
        funding_needs,
    ):
        if max_quantity <= 0:
            return 0
        sweep_price = float(cash_sweep_price or 0.0)
        if sweep_price <= 0.0:
            return 0
        current_buying_power = max(0.0, float(base_buying_power or 0.0))

        for underweight_value, ask_price in funding_needs:
            needed_value = float(underweight_value or 0.0)
            quote_price = float(ask_price or 0.0)
            if needed_value <= 0.0 or quote_price <= 0.0:
                continue
            max_buy_quantity = max(1, int(needed_value // quote_price))
            required_buying_power = max_buy_quantity * quote_price
            if current_buying_power >= required_buying_power:
                return 0
            return min(
                int(max_quantity),
                max(1, math.ceil((required_buying_power - current_buying_power) / sweep_price)),
        )
        return 0

    def should_sell_cash_sweep_to_fund_whole_share_buy(
        max_quantity,
        cash_sweep_price,
        base_buying_power,
        funding_needs,
    ):
        if max_quantity <= 0:
            return False
        sweep_price = float(cash_sweep_price or 0.0)
        if sweep_price <= 0.0:
            return False
        current_buying_power = max(0.0, float(base_buying_power or 0.0))
        sweep_capacity = float(max_quantity) * sweep_price
        if sweep_capacity <= 0.0:
            return False

        for underweight_value, ask_price in funding_needs:
            _ = underweight_value
            quote_price = float(ask_price or 0.0)
            if quote_price <= 0.0:
                continue
            if current_buying_power >= quote_price:
                return False
            if current_buying_power + sweep_capacity >= quote_price:
                return True
        return False
try:
    from quant_platform_kit.common.small_account_compatibility import (
        apply_small_account_cash_compatibility,
        format_small_account_cash_substitution_notes,
    )
except ImportError:  # pragma: no cover - compatibility with older pinned shared wheels
    @dataclass(frozen=True)
    class _SmallAccountCashCompatibilityResult:
        targets: dict
        whole_share_substituted_symbols: tuple[str, ...]
        safe_haven_cash_substituted_symbols: tuple[str, ...]
        cash_substitution_notes: tuple[dict, ...]

    def _project_unbuyable_value_targets_to_cash(
        target_values,
        prices,
        *,
        candidate_symbols=None,
        quantity_step=1.0,
    ):
        adjusted = {
            str(symbol or "").strip().upper(): float(value or 0.0)
            for symbol, value in dict(target_values or {}).items()
        }
        step = max(0.0, float(quantity_step or 0.0))
        if step <= 0.0:
            return adjusted, ()
        normalized_candidates = (
            tuple(adjusted)
            if candidate_symbols is None
            else tuple(dict.fromkeys(str(symbol or "").strip().upper() for symbol in candidate_symbols))
        )
        normalized_prices = {
            str(symbol or "").strip().upper(): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
        }
        substituted = []
        for symbol in normalized_candidates:
            target_value = max(0.0, float(adjusted.get(symbol, 0.0) or 0.0))
            price = max(0.0, float(normalized_prices.get(symbol, 0.0) or 0.0))
            if price > 0.0 and 0.0 < target_value < (price * step):
                adjusted[symbol] = 0.0
                substituted.append(symbol)
        return adjusted, tuple(dict.fromkeys(substituted))

    def apply_small_account_cash_compatibility(
        target_values,
        prices,
        *,
        candidate_symbols=None,
        safe_haven_cash_symbols=(),
        quantity_step=1.0,
        cash_substitute_limit_usd=2000.0,
    ):
        adjusted_targets, substituted = _project_unbuyable_value_targets_to_cash(
            target_values,
            prices,
            candidate_symbols=candidate_symbols,
            quantity_step=quantity_step,
        )
        normalized_candidates = (
            tuple(adjusted_targets)
            if candidate_symbols is None
            else tuple(dict.fromkeys(str(symbol or "").strip().upper() for symbol in candidate_symbols))
        )
        remaining_non_safe_targets = [
            symbol
            for symbol in normalized_candidates
            if float(adjusted_targets.get(str(symbol or "").strip().upper(), 0.0) or 0.0) > 0.0
        ]
        safe_haven_symbols = tuple(
            dict.fromkeys(
                str(symbol or "").strip().upper()
                for symbol in safe_haven_cash_symbols
                if str(symbol or "").strip()
            )
        )
        safe_haven_substituted = []
        if (
            substituted
            and not remaining_non_safe_targets
            and _positive_target_total(adjusted_targets) <= max(0.0, float(cash_substitute_limit_usd or 0.0))
        ):
            for symbol in safe_haven_symbols:
                if float(adjusted_targets.get(symbol, 0.0) or 0.0) > 0.0:
                    adjusted_targets[symbol] = 0.0
                    safe_haven_substituted.append(symbol)
        normalized_targets = {
            str(symbol or "").strip().upper(): float(value or 0.0)
            for symbol, value in dict(target_values or {}).items()
        }
        normalized_prices = {
            str(symbol or "").strip().upper(): float(price or 0.0)
            for symbol, price in dict(prices or {}).items()
        }
        notes = []
        if safe_haven_substituted:
            for symbol in substituted:
                target_value = max(0.0, float(normalized_targets.get(symbol, 0.0) or 0.0))
                price = max(0.0, float(normalized_prices.get(symbol, 0.0) or 0.0))
                if target_value <= 0.0 or price <= 0.0:
                    continue
                notes.append(
                    {
                        "symbol": symbol,
                        "target_value": target_value,
                        "price": price,
                        "cash_symbols": tuple(safe_haven_substituted),
                    }
                )
        return _SmallAccountCashCompatibilityResult(
            targets=adjusted_targets,
            whole_share_substituted_symbols=substituted,
            safe_haven_cash_substituted_symbols=tuple(safe_haven_substituted),
            cash_substitution_notes=tuple(notes),
        )

    def format_small_account_cash_substitution_notes(
        notes,
        *,
        translator,
        wrapper_key="buy_deferred",
        detail_key="buy_deferred_small_account_cash_substitution",
        cash_label_key="cash_label",
        symbol_suffix=".US",
    ):
        messages = []
        seen_keys = set()
        for note in tuple(notes or ()):
            if not isinstance(note, Mapping):
                continue
            symbol = str(note.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            target_value = max(0.0, float(note.get("target_value") or 0.0))
            price = max(0.0, float(note.get("price") or 0.0))
            if target_value <= 0.0 or price <= 0.0:
                continue
            cash_symbols = tuple(
                dict.fromkeys(
                    str(cash_symbol or "").strip().upper()
                    for cash_symbol in tuple(note.get("cash_symbols") or ())
                    if str(cash_symbol or "").strip()
                )
            )
            cash_symbols_text = ", ".join(f"{cash_symbol}{symbol_suffix}" for cash_symbol in cash_symbols)
            if not cash_symbols_text:
                cash_symbols_text = translator(cash_label_key)
            note_key = (symbol, f"{target_value:.2f}", cash_symbols_text)
            if note_key in seen_keys:
                continue
            seen_keys.add(note_key)
            detail = translator(
                detail_key,
                symbol=f"{symbol}{symbol_suffix}",
                diff=f"{target_value:.2f}",
                price=f"{price:.2f}",
                cash_symbols=cash_symbols_text,
            )
            messages.append(translator(wrapper_key, detail=detail))
        return tuple(messages)
from quant_platform_kit.common.quantity import (
    floor_to_quantity_step,
    format_quantity,
    normalize_order_quantity,
)
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


DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD = 1000.0
SMALL_ACCOUNT_SAFE_HAVEN_CASH_SUBSTITUTE_LIMIT_USD = 2000.0


def _noop_sleep(_seconds):
    return None


def _safe_haven_cash_symbols(*, portfolio: dict, allocation: dict) -> tuple[str, ...]:
    symbols: list[str] = []
    for symbol in allocation.get("safe_haven_symbols", ()):
        normalized = str(symbol or "").strip().upper()
        if normalized:
            symbols.append(normalized)
    cash_sweep_symbol = str(portfolio.get("cash_sweep_symbol") or "").strip().upper()
    if cash_sweep_symbol:
        symbols.append(cash_sweep_symbol)
    return tuple(dict.fromkeys(symbols))


def _positive_target_total(targets: dict) -> float:
    total = 0.0
    for value in dict(targets or {}).values():
        try:
            total += max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            continue
    return total


def _apply_safe_haven_cash_substitution(
    *,
    plan,
    portfolio,
    allocation,
    threshold_usd,
) -> tuple[dict, dict]:
    threshold = max(0.0, float(threshold_usd or 0.0))
    target_values = {
        str(symbol).strip().upper(): float(value or 0.0)
        for symbol, value in dict(allocation.get("targets") or {}).items()
    }
    if threshold <= 0.0:
        return dict(plan or {}), {**dict(allocation or {}), "targets": target_values}

    changed = False
    for symbol in _safe_haven_cash_symbols(portfolio=portfolio, allocation=allocation):
        target_value = float(target_values.get(symbol, 0.0) or 0.0)
        if 0.0 < target_value < threshold:
            target_values[symbol] = 0.0
            changed = True
    adjusted_allocation = {**dict(allocation or {}), "targets": target_values}
    adjusted_plan = dict(plan or {})
    if changed:
        adjusted_plan["allocation"] = adjusted_allocation
    return adjusted_plan, adjusted_allocation


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


def record_small_account_cash_substitution_notes(
    note_logs,
    *,
    allocation,
    translator,
    with_prefix,
    seen_keys,
):
    for message in format_small_account_cash_substitution_notes(
        allocation.get("small_account_whole_share_cash_notes") or (),
        translator=translator,
    ):
        if message in seen_keys:
            continue
        seen_keys.add(message)
        note_logs.append(message)
        print(with_prefix(message), flush=True)


def _floor_whole_share_quantity(quantity):
    return normalize_order_quantity(floor_to_quantity_step(quantity, 1.0))


def _normalize_trade_quantity(quantity):
    raw_quantity = max(0.0, float(quantity or 0.0))
    if raw_quantity <= 0.0:
        return 0
    return _floor_whole_share_quantity(raw_quantity)


def _sell_order_quantity(
    *,
    current_value,
    target_value,
    price,
    sellable_quantity,
):
    sellable = max(0.0, float(sellable_quantity or 0.0))
    if sellable <= 0.0:
        return 0

    target = max(0.0, float(target_value or 0.0))
    if target <= 0.0:
        return _normalize_trade_quantity(sellable)

    sell_value = max(0.0, float(current_value or 0.0) - target)
    if sell_value <= 0.0 or float(price or 0.0) <= 0.0:
        return 0
    return _normalize_trade_quantity(
        min(sell_value / float(price), sellable),
    )


def safe_quote_last_price(symbol, *, market_data_port, notify_issue):
    try:
        return float(market_data_port.get_quote(symbol).last_price)
    except Exception as exc:
        notify_issue("Quote failed", f"Symbol: {symbol}\n{exc}")
        return None


def _apply_small_account_whole_share_compatibility(
    *,
    plan,
    allocation,
    strategy_assets,
    market_data_port,
    notify_issue,
) -> tuple[dict, dict]:
    target_values = dict(allocation.get("targets") or {})
    candidate_symbols = tuple(
        dict.fromkeys(
            str(symbol or "").strip().upper()
            for symbol in tuple(allocation.get("risk_symbols", ()))
            + tuple(allocation.get("income_symbols", ()))
            if str(symbol or "").strip()
        )
    )
    if not candidate_symbols:
        safe_haven_symbols = set(
            _safe_haven_cash_symbols(portfolio=dict((plan or {}).get("portfolio") or {}), allocation=allocation)
        )
        candidate_symbols = tuple(
            str(symbol or "").strip().upper()
            for symbol in target_values
            if str(symbol or "").strip().upper() not in safe_haven_symbols
        )
    quote_prices = {}
    for symbol in candidate_symbols:
        try:
            price = float(market_data_port.get_quote(f"{symbol}.US").last_price)
        except Exception:
            continue
        if price > 0.0:
            quote_prices[symbol] = price
    safe_haven_symbols = _safe_haven_cash_symbols(
        portfolio=dict((plan or {}).get("portfolio") or {}),
        allocation=allocation,
    )
    compatibility = apply_small_account_cash_compatibility(
        target_values,
        quote_prices,
        candidate_symbols=candidate_symbols,
        safe_haven_cash_symbols=safe_haven_symbols,
        quantity_step=1.0,
        cash_substitute_limit_usd=SMALL_ACCOUNT_SAFE_HAVEN_CASH_SUBSTITUTE_LIMIT_USD,
    )
    adjusted_targets = compatibility.targets
    substituted = compatibility.whole_share_substituted_symbols
    safe_haven_substituted = compatibility.safe_haven_cash_substituted_symbols
    adjusted_allocation = {**dict(allocation or {}), "targets": adjusted_targets}
    adjusted_allocation.pop("small_account_whole_share_cash_notes", None)
    if substituted:
        adjusted_allocation["small_account_whole_share_substituted_symbols"] = substituted
    if safe_haven_substituted:
        adjusted_allocation["small_account_safe_haven_cash_substituted_symbols"] = tuple(safe_haven_substituted)
    if compatibility.cash_substitution_notes:
        adjusted_allocation["small_account_whole_share_cash_notes"] = tuple(compatibility.cash_substitution_notes)
    adjusted_plan = dict(plan or {})
    if substituted or safe_haven_substituted:
        adjusted_plan["allocation"] = adjusted_allocation
    return adjusted_plan, adjusted_allocation


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


def _estimate_buy_quantity_candidate(
    trade_context,
    symbol,
    order_kind,
    ref_price,
    *,
    can_buy_value,
    estimate_max_purchase_quantity,
    notify_issue,
    dry_run_only=False,
):
    budget_quantity = floor_to_quantity_step(can_buy_value / ref_price, 1.0)
    cash_limit_quantity = estimate_cash_buy_quantity_safe(
        trade_context,
        symbol,
        order_kind,
        ref_price,
        estimate_max_purchase_quantity=estimate_max_purchase_quantity,
        notify_issue=notify_issue,
    )
    if cash_limit_quantity is None:
        return None
    candidate_quantity = _normalize_trade_quantity(
        min(budget_quantity, float(cash_limit_quantity)),
    )
    return candidate_quantity, budget_quantity, float(cash_limit_quantity)


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
    safe_haven_cash_substitute_threshold_usd=DEFAULT_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD,
) -> ExecutionCycleResult:
    logs: list[str] = []
    skip_logs: list[str] = []
    note_logs: list[str] = []
    small_account_cash_note_keys: set[str] = set()
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
    plan, allocation = _apply_safe_haven_cash_substitution(
        plan=plan,
        portfolio=portfolio,
        allocation=allocation,
        threshold_usd=safe_haven_cash_substitute_threshold_usd,
    )
    cash_sweep_symbol = str(portfolio.get("cash_sweep_symbol") or "").strip().upper()
    plan, allocation = _apply_small_account_whole_share_compatibility(
        plan=plan,
        allocation=allocation,
        strategy_assets=strategy_assets,
        market_data_port=market_data_port,
        notify_issue=notify_issue,
    )
    record_small_account_cash_substitution_notes(
        note_logs,
        allocation=allocation,
        translator=translator,
        with_prefix=with_prefix,
        seen_keys=small_account_cash_note_keys,
    )
    target_values = dict(allocation["targets"])
    available_cash = float(portfolio["liquid_cash"])
    cash_by_currency = _normalize_cash_by_currency(portfolio.get("cash_by_currency"))
    investable_cash = float(execution["investable_cash"])
    current_min_trade = float(execution["current_min_trade"])
    dry_run_sale_proceeds = 0.0
    cash_sweep_sold_this_cycle = False

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
            quantity=_floor_whole_share_quantity(quantity),
            order_type=order_type,
            limit_price=float(submitted_price) if submitted_price is not None else None,
        )
        side_text = "Buy" if side == "buy" else "Sell"
        if float(order_intent.quantity or 0.0) <= 0.0:
            notify_issue(
                "Order submit skipped",
                f"Symbol: {symbol} Side: {side_text} Qty: {quantity} Type: {order_type} Price: {submitted_price if submitted_price is not None else 'MO'}",
            )
            return False
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
            quantity = _sell_order_quantity(
                current_value=market_values[symbol],
                target_value=target_values[symbol],
                price=price,
                sellable_quantity=sellable_quantities[symbol],
            )
            if quantity > 0:
                quantity_text = format_quantity(quantity)
                if symbol in limit_order_symbols:
                    limit_price = round(price * limit_sell_discount, 2)
                    if dry_run_only:
                        submitted = record_dry_run(
                            f"{symbol}.US",
                            "sell",
                            quantity_text,
                            limit_price,
                            order_type="limit",
                        )
                    else:
                        submitted = submit_order_via_port(
                            f"{symbol}.US",
                            "limit",
                            "sell",
                            quantity,
                            translator("limit_sell", symbol=symbol, qty=quantity_text, price=limit_price),
                            submitted_price=limit_price,
                        )
                else:
                    if dry_run_only:
                        submitted = record_dry_run(
                            f"{symbol}.US",
                            "sell",
                            quantity_text,
                            round(price, 2),
                            order_type="market",
                        )
                        if submitted:
                            dry_run_sale_proceeds += float(quantity) * round(price, 2)
                    else:
                        submitted = submit_order_via_port(
                            f"{symbol}.US",
                            "market",
                            "sell",
                            quantity,
                            translator("market_sell", symbol=symbol, qty=quantity_text, price=round(price, 2)),
                        )

                if submitted:
                    action_done = True
                    sell_submitted = True
                    if symbol == cash_sweep_symbol:
                        cash_sweep_sold_this_cycle = True
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

    buy_candidates = [
        symbol
        for symbol in strategy_assets
        if (target_values[symbol] - market_values[symbol]) > threshold_value
        and abs(target_values[symbol] - market_values[symbol]) > current_min_trade
    ]
    funding_buy_candidates = [
        symbol
        for symbol in buy_candidates
        if symbol != cash_sweep_symbol
    ]
    if (
        not sell_submitted
        and funding_buy_candidates
        and cash_sweep_symbol
        and sellable_quantities.get(cash_sweep_symbol, 0.0) > 0.0
    ):
        sweep_price = safe_quote_last_price(
            f"{cash_sweep_symbol}.US",
            market_data_port=market_data_port,
            notify_issue=notify_issue,
        )
        if sweep_price is not None and sweep_price > 0.0:
            funding_needs = []
            for buy_symbol in funding_buy_candidates:
                buy_price = safe_quote_last_price(
                    f"{buy_symbol}.US",
                    market_data_port=market_data_port,
                    notify_issue=notify_issue,
                )
                if buy_price is None:
                    continue
                funding_needs.append(
                    (
                        target_values[buy_symbol] - market_values[buy_symbol],
                        round(buy_price * limit_buy_premium, 2)
                        if buy_symbol in limit_order_symbols
                        else round(buy_price, 2),
                    )
                )
            if should_sell_cash_sweep_to_fund_whole_share_buy(
                float(sellable_quantities[cash_sweep_symbol]),
                sweep_price,
                investable_cash,
                funding_needs,
            ):
                sweep_quantity = float(sellable_quantities[cash_sweep_symbol])
                quantity_text = format_quantity(sweep_quantity)
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{cash_sweep_symbol}.US",
                        "sell",
                        quantity_text,
                        round(sweep_price, 2),
                        order_type="market",
                    )
                    if submitted:
                        dry_run_sale_proceeds += float(sweep_quantity) * round(sweep_price, 2)
                else:
                    submitted = submit_order_via_port(
                        f"{cash_sweep_symbol}.US",
                        "market",
                        "sell",
                        sweep_quantity,
                        translator(
                            "market_sell",
                            symbol=cash_sweep_symbol,
                            qty=quantity_text,
                            price=round(sweep_price, 2),
                        ),
                    )
                if submitted:
                    action_done = True
                    sell_submitted = True
                    cash_sweep_sold_this_cycle = True

    if sell_submitted:
        if dry_run_only and dry_run_sale_proceeds > 0.0:
            simulated_cash = float(dry_run_sale_proceeds)
            available_cash = max(0.0, available_cash + simulated_cash)
            investable_cash = max(0.0, investable_cash + simulated_cash)
            validation_message = (
                f"🧪 验证回款已入账: cash=${simulated_cash:.2f} "
                f"investable=${investable_cash:.2f}"
            )
            note_logs.append(validation_message)
            print(with_prefix(validation_message), flush=True)
        else:
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
            plan, allocation = _apply_safe_haven_cash_substitution(
                plan=plan,
                portfolio=portfolio,
                allocation=allocation,
                threshold_usd=safe_haven_cash_substitute_threshold_usd,
            )
            plan, allocation = _apply_small_account_whole_share_compatibility(
                plan=plan,
                allocation=allocation,
                strategy_assets=tuple(allocation["strategy_symbols"]),
                market_data_port=market_data_port,
                notify_issue=notify_issue,
            )
            record_small_account_cash_substitution_notes(
                note_logs,
                allocation=allocation,
                translator=translator,
                with_prefix=with_prefix,
                seen_keys=small_account_cash_note_keys,
            )
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
            limit_order_kind = "limit" if is_limit_order else "market"
            limit_ref_price = round(price * limit_buy_premium, 2) if is_limit_order else round(price, 2)
            limit_candidate = _estimate_buy_quantity_candidate(
                trade_context,
                f"{symbol}.US",
                limit_order_kind,
                limit_ref_price,
                can_buy_value=can_buy_value,
                estimate_max_purchase_quantity=estimate_max_purchase_quantity,
                notify_issue=notify_issue,
                dry_run_only=dry_run_only,
            )
            if limit_candidate is None:
                continue
            limit_candidate_quantity, limit_budget_quantity, limit_cash_limit_quantity = limit_candidate
            limit_quantity = _normalize_trade_quantity(limit_candidate_quantity)
            order_kind = limit_order_kind
            ref_price = limit_ref_price
            quantity = limit_quantity
            cost_estimate = 0.0
            if quantity <= 0:
                record_note_log(
                    note_logs,
                    translator=translator,
                    with_prefix=with_prefix,
                    kind="buy_deferred_cash_limit",
                    symbol=f"{symbol}.US",
                    diff=f"{diff:.2f}",
                    budget_qty=format_quantity(limit_budget_quantity),
                )
                continue

            quantity_text = format_quantity(quantity)
            if order_kind == "limit":
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{symbol}.US",
                        "buy",
                        quantity_text,
                        ref_price,
                        order_type="limit",
                    )
                else:
                    submitted = submit_order_via_port(
                        f"{symbol}.US",
                        "limit",
                        "buy",
                        quantity,
                        translator("limit_buy", symbol=symbol, qty=quantity_text, price=ref_price),
                        submitted_price=ref_price,
                    )
                cost_estimate = quantity * ref_price
            else:
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{symbol}.US",
                        "buy",
                        quantity_text,
                        round(price, 2),
                        order_type="market",
                    )
                else:
                    submitted = submit_order_via_port(
                        f"{symbol}.US",
                        "market",
                        "buy",
                        quantity,
                        translator("market_buy", symbol=symbol, qty=quantity_text, price=round(price, 2)),
                    )
                cost_estimate = quantity * price

            if submitted:
                investable_cash = max(0, investable_cash - cost_estimate)
                action_done = True
        else:
            if diff <= investable_cash:
                note_kind = "buy_deferred_small_target_gap"
                note_kwargs = {
                    "symbol": f"{symbol}.US",
                    "diff": f"{diff:.2f}",
                    "price": f"{price:.2f}",
                }
            else:
                note_kind = "buy_deferred_small_cash"
                note_kwargs = {
                    "symbol": f"{symbol}.US",
                    "diff": f"{diff:.2f}",
                    "investable": f"{investable_cash:.2f}",
                    "price": f"{price:.2f}",
                }
            record_note_log(
                note_logs,
                translator=translator,
                with_prefix=with_prefix,
                kind=note_kind,
                **note_kwargs,
            )

    cash_sweep_substituted_to_cash = bool(
        allocation.get("small_account_safe_haven_cash_substituted_symbols")
    )
    if (
        not cash_sweep_sold_this_cycle
        and cash_sweep_symbol
        and cash_sweep_symbol in strategy_assets
        and (
            float(target_values.get(cash_sweep_symbol, 0.0) or 0.0) > 0.0
            or not cash_sweep_substituted_to_cash
        )
    ):
        cash_sweep_price = safe_quote_last_price(
            f"{cash_sweep_symbol}.US",
            market_data_port=market_data_port,
            notify_issue=notify_issue,
        )
        if cash_sweep_price is not None and cash_sweep_price > 0.0 and investable_cash > cash_sweep_price * 2:
            substitution_threshold = max(
                0.0,
                float(safe_haven_cash_substitute_threshold_usd or 0.0),
            )
            if substitution_threshold <= 0.0 or investable_cash >= substitution_threshold:
                quantity = int(investable_cash // cash_sweep_price)
            else:
                quantity = 0
            if quantity > 0:
                quantity_text = format_quantity(quantity)
                if dry_run_only:
                    submitted = record_dry_run(
                        f"{cash_sweep_symbol}.US",
                        "buy",
                        quantity_text,
                        round(cash_sweep_price, 2),
                        order_type="market",
                    )
                else:
                    submitted = submit_order_via_port(
                        f"{cash_sweep_symbol}.US",
                        "market",
                        "buy",
                        quantity,
                        translator(
                            "market_buy",
                            symbol=cash_sweep_symbol,
                            qty=quantity_text,
                            price=round(cash_sweep_price, 2),
                        ),
                    )
                if submitted:
                    rebuy_message = translator(
                        "cash_sweep_rebuy",
                        symbol=f"{cash_sweep_symbol}.US",
                        qty=quantity_text,
                        price=f"{cash_sweep_price:.2f}",
                    )
                    note_logs.append(rebuy_message)
                    print(with_prefix(rebuy_message), flush=True)
                    action_done = True

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
