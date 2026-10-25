"""Request-local capture of one natural-cycle account-balance observation."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import datetime, timezone
import re
from typing import Any

from application.account_financing import project_native_financing
from application.broker_reconciliation import (
    _normalize_broker_balance,
    _normalize_cash,
    _normalize_position,
)


_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar(
    "longbridge_natural_cycle_history", default=None
)


def begin_natural_cycle_history() -> Token[dict[str, Any] | None]:
    return _CAPTURE.set(
        {
            "seen": False,
            "balance": None,
            "projection": None,
            "started": None,
            "finished": None,
            "stocks_seen": False,
            "stocks": None,
            "stocks_error": None,
        }
    )


def end_natural_cycle_history(token: Token[dict[str, Any] | None]) -> None:
    _CAPTURE.reset(token)


def read_cycle_account_balance(reader):
    """Read once in an active capture and let later portfolio checks reuse it."""
    slot = _CAPTURE.get()
    if slot is None:
        return reader()
    if slot["seen"]:
        if slot["balance"] is None:
            raise RuntimeError("captured account balance unavailable")
        return slot["balance"]
    slot["seen"] = True
    slot["started"] = datetime.now(timezone.utc)
    try:
        account_balance = reader()
    except Exception:
        raise
    slot["finished"] = datetime.now(timezone.utc)
    slot["balance"] = account_balance
    slot["projection"] = project_cycle_history_balances(account_balance)
    return account_balance


def cycle_history_observation() -> dict[str, Any] | None:
    slot = _CAPTURE.get()
    if slot is None or not slot["seen"] or not isinstance(slot["projection"], Mapping):
        return None
    return {
        "projection": slot["projection"],
        "started": slot["started"],
        "finished": slot["finished"],
    }


def read_cycle_stock_positions(reader):
    """Read once in an active capture and archive the full native inventory.

    The probe pre-reads before strategy filtering; the portfolio later reuses
    the same response (or error) without a second native call.
    """
    slot = _CAPTURE.get()
    if slot is None:
        return reader()
    if slot["stocks_seen"]:
        if slot["stocks_error"] is not None:
            raise slot["stocks_error"]
        return slot["stocks"]
    slot["stocks_seen"] = True
    try:
        response = reader()
    except Exception as exc:
        slot["stocks_error"] = exc
        raise
    finally:
        slot["finished"] = datetime.now(timezone.utc)
    slot["stocks"] = response
    rows = project_cycle_stock_positions(response)
    if rows is not None and isinstance(slot["projection"], dict):
        slot["projection"]["broker_reported_positions"] = rows
    return response


def project_cycle_stock_positions(response: Any) -> list[dict[str, Any]] | None:
    """Missing/malformed is unknown; a successfully read empty inventory stays empty."""
    channels = getattr(response, "channels", None)
    # No channel at all is not evidence of an empty book (see reconciliation).
    if not isinstance(channels, (list, tuple)) or not channels:
        return None
    rows = []
    seen = set()
    try:
        for channel in channels:
            positions = getattr(channel, "positions", None)
            if not isinstance(positions, (list, tuple)):
                return None
            for position in positions:
                row = _normalize_position(position, account_channel="")
                symbol, currency, quantity = (
                    row[key] for key in ("symbol", "currency", "quantity")
                )
                whole, _, fraction = quantity.lstrip("-").partition(".")
                if (
                    re.fullmatch(r"[A-Z0-9][A-Z0-9./ -]{0,31}", symbol) is None
                    or re.fullmatch(r"[A-Z]{3}", currency) is None
                    or len(whole) > 15
                    or len(fraction) > 8
                    or symbol in seen
                    or len(rows) >= 64
                ):
                    return None
                seen.add(symbol)
                rows.append(
                    {
                        "symbol": symbol,
                        "currency": currency,
                        "quantity": quantity,
                        "market_value": None,
                    }
                )
    except Exception:
        return None
    return sorted(rows, key=lambda row: (row["currency"], row["symbol"]))


def project_cycle_history_balances(account_balance: Any) -> dict[str, list[dict[str, str]]] | None:
    """Keep complete, currency-specific broker facts as decimal strings."""

    if isinstance(account_balance, (str, bytes)) or not isinstance(account_balance, (list, tuple)):
        return None
    if not account_balance:
        return None
    balances: list[dict[str, str]] = []
    cash: list[dict[str, str]] = []
    balance_currencies: set[str] = set()
    cash_currencies: set[str] = set()
    try:
        for account in account_balance:
            normalized_balance = _normalize_broker_balance(account)
            currency = str(normalized_balance["currency"])
            if currency in balance_currencies:
                return None
            balance_currencies.add(currency)
            balances.append(
                {
                    "currency": currency,
                    "net_assets": str(normalized_balance["net_assets"]),
                    "total_cash": str(normalized_balance["total_cash"]),
                }
            )
            normalized_cash = _normalize_cash(account)["cash_infos"]
            for row in normalized_cash:
                cash_currency = str(row["currency"])
                if cash_currency in cash_currencies:
                    return None
                cash_currencies.add(cash_currency)
                cash.append(
                    {
                        "currency": cash_currency,
                        "available_cash": str(row["available_cash"]),
                        "frozen_cash": str(row["frozen_cash"]),
                        "settling_cash": str(row["settling_cash"]),
                    }
                )
    except Exception:
        return None
    if not balances or not cash:
        return None
    balances.sort(key=lambda row: row["currency"])
    cash.sort(key=lambda row: row["currency"])
    projection: dict[str, list[dict[str, str]]] = {
        "broker_reported_balances": balances,
        "cash": cash,
    }
    financing = project_native_financing(account_balance, balance_currencies=balance_currencies)
    if financing:
        projection["financing"] = financing
    return projection
