from __future__ import annotations

import time
from typing import Any

from quant_platform_kit.common.models import ExecutionReport

_qpk_submit_order = None


def _get_qpk_submit_order():
    global _qpk_submit_order
    if _qpk_submit_order is None:
        from quant_platform_kit.longbridge.execution import submit_order as qpk_submit_order

        _qpk_submit_order = qpk_submit_order
    return _qpk_submit_order


def _get_error_code(exc: Exception) -> str | None:
    code = getattr(exc, "code", None)
    if code is None:
        payload = getattr(exc, "args", None)
        if payload:
            text = " ".join(str(item) for item in payload)
            if "603203" in text:
                return "603203"
    return str(code).strip() if code is not None else None


def _is_retriable_internal_error(exc: Exception) -> bool:
    code = _get_error_code(exc)
    if code == "603203":
        return True
    message = str(exc).lower()
    return "internal server error" in message


def submit_order(
    t_ctx: Any,
    symbol: str,
    *,
    order_kind: str,
    side: str,
    quantity: float,
    submitted_price: float | None = None,
    allow_fractional_shares: bool = False,
    quantity_step: float = 1.0,
) -> ExecutionReport:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            return _get_qpk_submit_order()(
                t_ctx,
                symbol,
                order_kind=order_kind,
                side=side,
                quantity=quantity,
                submitted_price=submitted_price,
                allow_fractional_shares=allow_fractional_shares,
                quantity_step=quantity_step,
            )
        except Exception as exc:
            last_error = exc
            if attempt == 0 and _is_retriable_internal_error(exc):
                time.sleep(0.5)
                continue
            raise
    raise last_error or RuntimeError("LongBridge submit_order failed")
