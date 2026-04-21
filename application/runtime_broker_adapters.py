"""Builder helpers for LongBridge broker-side runtime adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from quant_platform_kit.common.models import PricePoint, PriceSeries, QuoteSnapshot
from quant_platform_kit.common.port_adapters import (
    CallableExecutionPort,
    CallableMarketDataPort,
    CallablePortfolioPort,
)
from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort, PortfolioPort
from quant_platform_kit.strategy_contracts import (
    build_account_state_from_portfolio_snapshot,
    build_portfolio_snapshot_from_account_state,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class LongBridgeBrokerAdapters:
    strategy_symbols: tuple[str, ...]
    account_hash: str
    fetch_last_price_fn: Callable[[Any, str], float | None]
    fetch_strategy_account_state_fn: Callable[[Any, Any], Mapping[str, Any]]
    submit_order_fn: Callable[..., Any]
    clock: Callable[[], datetime] = _utcnow
    price_history_lookback: int = 260

    def normalize_market_symbol(self, symbol: str) -> str:
        value = str(symbol or "").strip().upper()
        if not value:
            raise ValueError("Market data symbol must be non-empty.")
        if "." not in value:
            return f"{value}.US"
        return value

    def fetch_daily_price_history(self, quote_context, symbol: str, *, lookback: int | None = None):
        from longport.openapi import AdjustType, Period

        normalized_symbol = self.normalize_market_symbol(symbol)
        bars = quote_context.candlesticks(
            normalized_symbol,
            Period.Day,
            int(lookback or self.price_history_lookback),
            AdjustType.ForwardAdjust,
        )
        if not bars:
            return None
        history = []
        for bar in bars:
            close = float(bar.close)
            history.append(
                {
                    "close": close,
                    "high": float(getattr(bar, "high", close)),
                    "low": float(getattr(bar, "low", close)),
                    "datetime": (
                        getattr(bar, "timestamp", None)
                        or getattr(bar, "time", None)
                        or getattr(bar, "datetime", None)
                    ),
                }
            )
        return history

    def _coerce_history_datetime(self, raw_value, fallback_timestamp):
        if raw_value is None:
            return fallback_timestamp.to_pydatetime().replace(tzinfo=timezone.utc)
        if isinstance(raw_value, datetime):
            return (
                raw_value.astimezone(timezone.utc)
                if raw_value.tzinfo is not None
                else raw_value.replace(tzinfo=timezone.utc)
            )
        if isinstance(raw_value, (int, float)):
            unit = "ms" if abs(raw_value) > 10_000_000_000 else "s"
            return pd.to_datetime(raw_value, unit=unit, utc=True).to_pydatetime()
        timestamp = pd.Timestamp(raw_value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()

    def build_market_data_port(self, quote_context) -> MarketDataPort:
        quote_cache: dict[str, QuoteSnapshot] = {}
        price_series_cache: dict[str, PriceSeries] = {}

        def load_quote(symbol: str) -> QuoteSnapshot:
            normalized_symbol = self.normalize_market_symbol(symbol)
            cached = quote_cache.get(normalized_symbol)
            if cached is not None:
                return cached
            price = self.fetch_last_price_fn(quote_context, normalized_symbol)
            if price is None:
                raise ValueError(f"Quote unavailable for {normalized_symbol}")
            snapshot = QuoteSnapshot(
                symbol=normalized_symbol,
                as_of=self.clock(),
                last_price=float(price),
            )
            quote_cache[normalized_symbol] = snapshot
            return snapshot

        def load_price_series(symbol: str) -> PriceSeries:
            normalized_symbol = self.normalize_market_symbol(symbol)
            cached = price_series_cache.get(normalized_symbol)
            if cached is not None:
                return cached
            history = self.fetch_daily_price_history(quote_context, normalized_symbol)
            if not history:
                raise ValueError(f"Price history unavailable for {normalized_symbol}")
            fallback_index = pd.bdate_range(end=pd.Timestamp.now("UTC").normalize(), periods=len(history))
            points = []
            for index, bar in enumerate(history):
                points.append(
                    PricePoint(
                        as_of=self._coerce_history_datetime(bar.get("datetime"), fallback_index[index]),
                        close=float(bar["close"]),
                    )
                )
            series = PriceSeries(
                symbol=normalized_symbol,
                currency="USD",
                points=tuple(points),
            )
            price_series_cache[normalized_symbol] = series
            return series

        return CallableMarketDataPort(
            quote_loader=load_quote,
            price_series_loader=load_price_series,
        )

    def build_market_history_loader(self, market_data_port: MarketDataPort):
        def load_market_history(_broker_client, symbol, *_args, **_kwargs):
            series = market_data_port.get_price_series(str(symbol).strip().upper())
            if not series.points:
                return pd.Series(dtype=float)
            index = pd.DatetimeIndex([pd.Timestamp(point.as_of) for point in series.points])
            closes = [float(point.close) for point in series.points]
            return pd.Series(closes, index=index, dtype=float)

        return load_market_history

    def build_price_history(self, market_data_port: MarketDataPort, symbol: str):
        series = market_data_port.get_price_series(symbol)
        return [
            {
                "close": float(point.close),
                "high": float(point.close),
                "low": float(point.close),
            }
            for point in series.points
        ]

    def build_portfolio_snapshot_from_account_state(self, account_state):
        return build_portfolio_snapshot_from_account_state(
            account_state,
            strategy_symbols=self.strategy_symbols,
            metadata={"account_hash": self.account_hash},
        )

    def build_account_state_from_snapshot(self, snapshot):
        return build_account_state_from_portfolio_snapshot(
            snapshot,
            strategy_symbols=self.strategy_symbols,
        )

    def build_managed_portfolio_snapshot(self, quote_context, trade_context):
        return self.build_portfolio_snapshot_from_account_state(
            self.fetch_strategy_account_state_fn(quote_context, trade_context)
        )

    def build_portfolio_port(self, quote_context, trade_context) -> PortfolioPort:
        return CallablePortfolioPort(
            lambda: self.build_managed_portfolio_snapshot(quote_context, trade_context)
        )

    def build_execution_port(self, trade_context) -> ExecutionPort:
        return CallableExecutionPort(
            lambda order_intent: self.submit_order_fn(
                trade_context,
                str(order_intent.symbol),
                order_kind=str(order_intent.order_type),
                side=str(order_intent.side),
                quantity=int(order_intent.quantity),
                submitted_price=order_intent.limit_price,
            )
        )


def build_runtime_broker_adapters(
    *,
    strategy_symbols: tuple[str, ...],
    account_hash: str,
    fetch_last_price_fn: Callable[[Any, str], float | None],
    fetch_strategy_account_state_fn: Callable[[Any, Any], Mapping[str, Any]],
    submit_order_fn: Callable[..., Any],
    clock: Callable[[], datetime] = _utcnow,
    price_history_lookback: int = 260,
) -> LongBridgeBrokerAdapters:
    return LongBridgeBrokerAdapters(
        strategy_symbols=tuple(strategy_symbols),
        account_hash=str(account_hash),
        fetch_last_price_fn=fetch_last_price_fn,
        fetch_strategy_account_state_fn=fetch_strategy_account_state_fn,
        submit_order_fn=submit_order_fn,
        clock=clock,
        price_history_lookback=int(price_history_lookback),
    )
