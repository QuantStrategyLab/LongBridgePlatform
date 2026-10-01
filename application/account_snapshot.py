"""Request-local capture of one broker balance read for history projection."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any

from application.broker_reconciliation import _normalize_broker_balance, _normalize_cash

_CAPTURE: ContextVar[dict[str, Any] | None] = ContextVar("longbridge_natural_cycle_history", default=None)


def begin_natural_cycle_history() -> Token[dict[str, Any] | None]:
    return _CAPTURE.set({"seen": False, "balance": None, "projection": None, "started": None, "finished": None})


def end_natural_cycle_history(token: Token[dict[str, Any] | None]) -> None:
    _CAPTURE.reset(token)


def read_cycle_account_balance(reader):
    slot = _CAPTURE.get()
    if slot is None:
        return reader()
    if slot["seen"]:
        if slot["balance"] is None:
            raise RuntimeError("captured account balance unavailable")
        return slot["balance"]
    slot["seen"] = True
    slot["started"] = datetime.now(timezone.utc)
    balance = reader()
    slot["finished"] = datetime.now(timezone.utc)
    slot["balance"] = balance
    slot["projection"] = project_cycle_history_balances(balance)
    return balance


def cycle_history_observation() -> dict[str, Any] | None:
    slot = _CAPTURE.get()
    if slot is None or not slot["seen"] or not isinstance(slot["projection"], Mapping):
        return None
    return {"projection": slot["projection"], "started": slot["started"], "finished": slot["finished"]}


def project_cycle_history_balances(account_balance: Any) -> dict[str, list[dict[str, str]]] | None:
    if isinstance(account_balance, (str, bytes)) or not isinstance(account_balance, (list, tuple)) or not account_balance:
        return None
    balances: list[dict[str, str]] = []
    cash: list[dict[str, str]] = []
    balance_currencies: set[str] = set()
    cash_currencies: set[str] = set()
    try:
        for account in account_balance:
            normalized = _normalize_broker_balance(account)
            currency = str(normalized["currency"])
            if currency in balance_currencies:
                return None
            balance_currencies.add(currency)
            balances.append({"currency": currency, "net_assets": str(normalized["net_assets"]), "total_cash": str(normalized["total_cash"])})
            for row in _normalize_cash(account)["cash_infos"]:
                currency = str(row["currency"])
                if currency in cash_currencies:
                    return None
                cash_currencies.add(currency)
                cash.append({field: str(row[field]) for field in ("currency", "available_cash", "frozen_cash", "settling_cash")})
    except Exception:
        return None
    if not balances or not cash:
        return None
    balances.sort(key=lambda row: row["currency"])
    cash.sort(key=lambda row: row["currency"])
    return {"broker_reported_balances": balances, "cash": cash}
