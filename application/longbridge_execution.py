from __future__ import annotations

from typing import Any

from application.account_new_risk_gate_support import (
    evaluate_cycle_new_risk_admission,
    is_account_new_risk_gate_enabled,
    new_risk_buy_prohibited,
)
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
    side_normalized = str(side or "").strip().lower()
    if is_account_new_risk_gate_enabled() and side_normalized == "buy":
        admission = evaluate_cycle_new_risk_admission()
        if new_risk_buy_prohibited(admission):
            return ExecutionReport(
                symbol=str(symbol or "").strip().upper(),
                side=side_normalized,
                quantity=float(quantity or 0.0),
                status="rejected",
                raw_payload={
                    "detail": "account_new_risk_gate",
                    "reason_codes": list(admission.reason_codes),
                    "live_authority_granted": admission.live_authority_granted,
                },
            )
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
