"""Optional LongBridge native financing projection and wire validation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_NATIVE_DETAIL = re.compile(r"^-?(?:0|[1-9]\d{0,29})(?:\.\d{1,28})?$")
_RISK_LEVELS = frozenset({"0", "1", "2", "3"})
_MONEY_FIELDS = (
    "max_finance_amount",
    "remaining_finance_amount",
    "init_margin",
    "maintenance_margin",
    "margin_call",
    "buy_power",
)
_ALLOWED_FIELDS = frozenset(("currency", "risk_level", *_MONEY_FIELDS))
_MAX_ROWS = 32


class FinancingPayloadError(ValueError):
    """Optional financing wire payload is malformed."""


def project_native_financing(
    account_balances: Any,
    *,
    balance_currencies: Iterable[str],
) -> list[dict[str, str]]:
    """Project optional financing from already-read balance objects.

    Invalid optional cells and currency-only rows are omitted. Required cash
    and balance facts are never rewritten here.
    """

    allowed = {str(currency) for currency in balance_currencies}
    if (
        isinstance(account_balances, (str, bytes))
        or not isinstance(account_balances, (list, tuple))
        or not account_balances
        or not allowed
    ):
        return []

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for account in account_balances:
        currency = _project_currency(getattr(account, "currency", None))
        if currency is None or currency not in allowed or currency in seen:
            continue
        row: dict[str, str] = {"currency": currency}
        for field in _MONEY_FIELDS:
            cell = _project_money(getattr(account, field, None))
            if cell is not None:
                row[field] = cell
        risk = _project_risk_level(getattr(account, "risk_level", None))
        if risk is not None:
            row["risk_level"] = risk
        if len(row) == 1:
            continue
        seen.add(currency)
        rows.append(row)

    if not rows or len(rows) > _MAX_ROWS:
        return []
    rows.sort(key=lambda item: item["currency"])
    return rows


def validate_financing_payload(
    value: Any,
    *,
    balance_currencies: Iterable[str],
) -> list[dict[str, str]]:
    """Reject malformed optional financing before any write or consumer accept."""

    allowed = {str(currency) for currency in balance_currencies}
    if not isinstance(value, list) or not value or len(value) > _MAX_ROWS:
        raise FinancingPayloadError("financing payload is invalid")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    previous: str | None = None
    for item in value:
        if not isinstance(item, Mapping):
            raise FinancingPayloadError("financing payload is invalid")
        if set(item) - _ALLOWED_FIELDS:
            raise FinancingPayloadError("financing payload is invalid")
        currency = item.get("currency")
        if (
            not isinstance(currency, str)
            or _CURRENCY.fullmatch(currency) is None
            or currency not in allowed
            or currency in seen
        ):
            raise FinancingPayloadError("financing payload is invalid")
        if previous is not None and currency < previous:
            raise FinancingPayloadError("financing payload is invalid")
        row: dict[str, str] = {"currency": currency}
        for field in _MONEY_FIELDS:
            if field not in item:
                continue
            cell = item[field]
            if (
                isinstance(cell, bool)
                or not isinstance(cell, str)
                or _NATIVE_DETAIL.fullmatch(cell) is None
            ):
                raise FinancingPayloadError("financing payload is invalid")
            try:
                if not Decimal(cell).is_finite():
                    raise FinancingPayloadError("financing payload is invalid")
            except (InvalidOperation, ValueError) as exc:
                raise FinancingPayloadError("financing payload is invalid") from exc
            row[field] = cell
        if "risk_level" in item:
            risk = item["risk_level"]
            if not isinstance(risk, str) or risk not in _RISK_LEVELS:
                raise FinancingPayloadError("financing payload is invalid")
            row["risk_level"] = risk
        if len(row) == 1:
            raise FinancingPayloadError("financing payload is invalid")
        seen.add(currency)
        previous = currency
        rows.append(row)
    return rows


def _project_currency(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    currency = value.strip().upper()
    if _CURRENCY.fullmatch(currency) is None:
        return None
    return currency


def _project_money(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        text = format(value, "f")
    elif isinstance(value, str):
        text = value
    else:
        return None
    if _NATIVE_DETAIL.fullmatch(text) is None:
        return None
    try:
        if not Decimal(text).is_finite():
            return None
    except (InvalidOperation, ValueError):
        return None
    return text


def _project_risk_level(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1, 2, 3):
        return str(value)
    if isinstance(value, str) and value in _RISK_LEVELS:
        return value
    return None


def financing_shape_ok(value: object) -> bool:
    """Lightweight optional-key shape check for inspection schema matching."""

    return isinstance(value, list) and bool(value) and len(value) <= _MAX_ROWS


__all__ = [
    "FinancingPayloadError",
    "financing_shape_ok",
    "project_native_financing",
    "validate_financing_payload",
]
