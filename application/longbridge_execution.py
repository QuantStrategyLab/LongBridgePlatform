from __future__ import annotations

from typing import Any

from quant_platform_kit.common.models import ExecutionReport

_qpk_submit_order = None


def _get_qpk_submit_order():
    global _qpk_submit_order
    if _qpk_submit_order is None:
        from quant_platform_kit.longbridge.execution import submit_order as qpk_submit_order

        _qpk_submit_order = qpk_submit_order
    return _qpk_submit_order


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
