"""Order submission and status notification helpers for LongBridgePlatform."""

from __future__ import annotations

import traceback


def is_filled_status(status):
    return "Filled" in status and "PartialFilled" not in status


def is_partial_filled_status(status):
    return "PartialFilled" in status


def is_terminal_error_status(status):
    return any(keyword in status for keyword in ["Rejected", "Canceled", "Expired"])


def send_order_status_message(
    symbol,
    side_text,
    quantity,
    order_id,
    status,
    *,
    translator,
    send_tg_message,
    executed_qty="0",
    executed_price="0",
    reason="",
):
    localized_side = translator("side_buy") if side_text == "Buy" else translator("side_sell")
    root_symbol = symbol.split(".")[0] if "." in symbol else symbol

    if is_filled_status(status):
        message = translator(
            "order_filled",
            symbol=root_symbol,
            side=localized_side,
            qty=quantity,
            price=executed_price,
            order_id=order_id,
        )
    elif is_partial_filled_status(status):
        message = translator(
            "order_partial",
            symbol=root_symbol,
            side=localized_side,
            executed=executed_qty,
            qty=quantity,
            price=executed_price,
            order_id=order_id,
        )
    elif is_terminal_error_status(status):
        status_label = (
            translator("status_rejected")
            if "Rejected" in status
            else (translator("status_canceled") if "Canceled" in status else translator("status_expired"))
        )
        message = translator(
            "order_error",
            symbol=root_symbol,
            side=localized_side,
            qty=quantity,
            status=status_label,
            order_id=order_id,
            reason=reason or "—",
        )
    else:
        message = translator(
            "order_filled",
            symbol=root_symbol,
            side=localized_side,
            qty=quantity,
            price=executed_price,
            order_id=order_id,
        )

    send_tg_message(message)


def monitor_submitted_order_status(
    trade_context,
    symbol,
    side_text,
    quantity,
    order_id,
    *,
    fetch_order_status,
    order_poll_interval_sec,
    order_poll_max_attempts,
    translator,
    send_tg_message,
    notify_issue,
    sleeper,
):
    if not order_id:
        return

    try:
        for _ in range(order_poll_max_attempts):
            sleeper(order_poll_interval_sec)
            order_status = fetch_order_status(trade_context, order_id)
            if not order_status:
                continue

            status = order_status["status"]
            reject_message = order_status["reason"]
            executed_qty = order_status["executed_qty"]
            executed_price = order_status["executed_price"]

            if is_filled_status(status):
                send_order_status_message(
                    symbol,
                    side_text,
                    quantity,
                    order_id,
                    status,
                    translator=translator,
                    send_tg_message=send_tg_message,
                    executed_qty=executed_qty,
                    executed_price=executed_price,
                )
                return

            if is_partial_filled_status(status):
                send_order_status_message(
                    symbol,
                    side_text,
                    quantity,
                    order_id,
                    status,
                    translator=translator,
                    send_tg_message=send_tg_message,
                    executed_qty=executed_qty,
                    executed_price=executed_price,
                )

            if is_terminal_error_status(status):
                send_order_status_message(
                    symbol,
                    side_text,
                    quantity,
                    order_id,
                    status,
                    translator=translator,
                    send_tg_message=send_tg_message,
                    executed_qty=executed_qty,
                    executed_price=executed_price,
                    reason=reject_message,
                )
                return
    except Exception:
        notify_issue(
            "Order status poll failed",
            f"Symbol: {symbol} Side: {side_text} Qty: {quantity} Order: {order_id}\n{traceback.format_exc()}",
        )


def submit_order_with_alert(
    trade_context,
    symbol,
    order_type,
    side,
    quantity,
    logs,
    log_message,
    *,
    submit_order,
    fetch_order_status,
    translator,
    send_tg_message,
    notify_issue,
    order_poll_interval_sec,
    order_poll_max_attempts,
    sleeper,
    print_with_prefix,
    submitted_price=None,
):
    side_text = "Buy" if side == "buy" else "Sell"

    try:
        report = submit_order(
            trade_context,
            symbol,
            order_kind=order_type,
            side=side,
            quantity=quantity,
            submitted_price=submitted_price,
        )
        order_id = report.broker_order_id or ""
        log_with_order_id = f"{log_message} [order_id={order_id}]" if order_id else log_message
        print_with_prefix(f"OK {log_with_order_id}")
        logs.append(log_with_order_id)
        monitor_submitted_order_status(
            trade_context,
            symbol,
            side_text,
            quantity,
            order_id,
            fetch_order_status=fetch_order_status,
            order_poll_interval_sec=order_poll_interval_sec,
            order_poll_max_attempts=order_poll_max_attempts,
            translator=translator,
            send_tg_message=send_tg_message,
            notify_issue=notify_issue,
            sleeper=sleeper,
        )
        return True
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

