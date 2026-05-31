from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def _normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _fetch_last_prices(q_ctx: Any, symbols: list[str]) -> dict[str, float]:
    normalized_symbols = []
    for symbol in symbols:
        normalized = _normalize_symbol(symbol)
        if normalized:
            normalized_symbols.append(normalized)
    normalized_symbols = list(dict.fromkeys(normalized_symbols))
    if not normalized_symbols:
        return {}

    quotes = list(q_ctx.quote(normalized_symbols) or [])
    prices: dict[str, float] = {}
    for index, quote in enumerate(quotes):
        fallback_symbol = normalized_symbols[index] if index < len(normalized_symbols) else ""
        quoted_symbol = _normalize_symbol(getattr(quote, "symbol", "") or fallback_symbol)
        if not quoted_symbol:
            continue
        last_done = getattr(quote, "last_done", None)
        if last_done is None:
            continue
        try:
            prices[quoted_symbol] = float(last_done)
        except (TypeError, ValueError):
            continue
    return prices


def fetch_strategy_account_state(
    q_ctx: Any,
    t_ctx: Any,
    strategy_assets: Iterable[str],
    *,
    cash_currency: str = "USD",
    position_log_fn: Callable[[str], None] | None = None,
    warning_log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def warn(message: str) -> None:
        if warning_log_fn is not None:
            warning_log_fn(message)

    trading_currency = str(cash_currency or "USD").strip().upper()
    available_cash = 0.0
    cash_by_currency: dict[str, float] = {}
    try:
        account_balance = t_ctx.account_balance()
    except Exception as exc:
        warn(
            "[longbridge_account_balance_failed] "
            f"error_type={type(exc).__name__} error={exc}"
        )
        account_balance = ()
    for account in account_balance:
        for cash_info in getattr(account, "cash_infos", []):
            currency = str(getattr(cash_info, "currency", "") or "").strip().upper()
            if not currency:
                continue
            cash_amount = float(getattr(cash_info, "available_cash", 0.0))
            cash_by_currency[currency] = cash_by_currency.get(currency, 0.0) + cash_amount
            if currency == trading_currency:
                available_cash += cash_amount

    assets = [str(symbol).strip().upper() for symbol in strategy_assets if str(symbol).strip()]
    market_values = {symbol: 0.0 for symbol in assets}
    quantities = {symbol: 0.0 for symbol in assets}
    sellable_quantities = {symbol: 0.0 for symbol in assets}
    filter_enabled = bool(assets)

    position_rows: list[tuple[str, str, Any, Any]] = []
    try:
        positions_response = t_ctx.stock_positions()
    except Exception as exc:
        warn(
            "[longbridge_stock_positions_failed] "
            f"error_type={type(exc).__name__} error={exc}"
        )
        positions_response = None
    if positions_response and hasattr(positions_response, "channels"):
        for channel in positions_response.channels:
            for position in getattr(channel, "positions", []):
                full_symbol = str(getattr(position, "symbol", "") or "").strip().upper()
                if not full_symbol:
                    continue
                root_symbol = full_symbol.split(".")[0].strip().upper()
                if filter_enabled and root_symbol not in market_values:
                    continue
                if root_symbol not in market_values:
                    market_values[root_symbol] = 0.0
                    quantities[root_symbol] = 0.0
                    sellable_quantities[root_symbol] = 0.0

                raw_quantity = getattr(position, "quantity", 0)
                raw_available_quantity = getattr(position, "available_quantity", raw_quantity)
                if raw_quantity is None:
                    raw_quantity = 0
                if raw_available_quantity is None:
                    raw_available_quantity = raw_quantity
                if position_log_fn is not None:
                    position_log_fn(
                        "[position_snapshot] raw "
                        f"symbol={root_symbol} full_symbol={full_symbol} "
                        f"quantity={raw_quantity} available_quantity={raw_available_quantity}"
                    )

                position_rows.append((root_symbol, full_symbol, raw_quantity, raw_available_quantity))

    prices = _fetch_last_prices(q_ctx, [full_symbol for _root_symbol, full_symbol, _quantity, _available in position_rows])
    for root_symbol, full_symbol, raw_quantity, raw_available_quantity in position_rows:
        last_price = prices.get(full_symbol)
        if last_price is None:
            continue

        quantity = float(raw_quantity)
        available_quantity = float(raw_available_quantity)
        market_values[root_symbol] += quantity * last_price
        quantities[root_symbol] += quantity
        sellable_quantities[root_symbol] += available_quantity

    if position_log_fn is not None:
        for symbol in assets or tuple(sorted(quantities)):
            position_log_fn(
                "[position_snapshot] aggregate "
                f"symbol={symbol} quantity={quantities.get(symbol, 0.0)} "
                f"sellable_quantity={sellable_quantities.get(symbol, 0.0)} "
                f"market_value={market_values.get(symbol, 0.0):.2f}"
            )

    return {
        "available_cash": available_cash,
        "cash_by_currency": cash_by_currency,
        "market_values": market_values,
        "quantities": quantities,
        "sellable_quantities": sellable_quantities,
        "total_strategy_equity": available_cash + sum(market_values.values()),
        "trading_currency": trading_currency,
    }
