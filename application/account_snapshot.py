"""Request-local capture of one natural-cycle account-balance observation."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

from application.account_financing import project_native_financing
from application.broker_reconciliation import _normalize_broker_balance, _normalize_cash


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
