"""Order submission and status notification helpers for LongBridgePlatform."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import traceback

from notifications.events import NotificationPublisher, RenderedNotification


@dataclass(frozen=True)
class OrderLifecycleEvent:
    symbol: str
    side_text: str
    quantity: int | str
    order_id: str
    status: str
    executed_qty: str = "0"
    executed_price: str = "0"
    reason: str = ""


def is_filled_status(status):
    return "Filled" in status and "PartialFilled" not in status


def is_partial_filled_status(status):
    return "PartialFilled" in status


def is_terminal_error_status(status):
    return any(keyword in status for keyword in ["Rejected", "Canceled", "Expired"])


def build_order_lifecycle_event(
    symbol,
    side_text,
    quantity,
    order_id,
    status,
    *,
    executed_qty="0",
    executed_price="0",
    reason="",
):
    return OrderLifecycleEvent(
        symbol=str(symbol),
        side_text=str(side_text),
        quantity=quantity,
        order_id=str(order_id or ""),
        status=str(status or ""),
        executed_qty=str(executed_qty or "0"),
        executed_price=str(executed_price or "0"),
        reason=str(reason or ""),
    )


def render_order_lifecycle_message(event: OrderLifecycleEvent, *, translator) -> str:
    localized_side = translator("side_buy") if event.side_text == "Buy" else translator("side_sell")
    root_symbol = event.symbol.split(".")[0] if "." in event.symbol else event.symbol

    if is_filled_status(event.status):
        return translator(
            "order_filled",
            symbol=root_symbol,
            side=localized_side,
            qty=event.quantity,
            price=event.executed_price,
            order_id=event.order_id,
        )
    if is_partial_filled_status(event.status):
        return translator(
            "order_partial",
            symbol=root_symbol,
            side=localized_side,
            executed=event.executed_qty,
            qty=event.quantity,
            price=event.executed_price,
            order_id=event.order_id,
        )
    if is_terminal_error_status(event.status):
        status_label = (
            translator("status_rejected")
            if "Rejected" in event.status
            else (translator("status_canceled") if "Canceled" in event.status else translator("status_expired"))
        )
        return translator(
            "order_error",
            symbol=root_symbol,
            side=localized_side,
            qty=event.quantity,
            status=status_label,
            order_id=event.order_id,
            reason=event.reason or "—",
        )
    return translator(
        "order_filled",
        symbol=root_symbol,
        side=localized_side,
        qty=event.quantity,
        price=event.executed_price,
        order_id=event.order_id,
    )


def render_order_lifecycle_notification(
    event: OrderLifecycleEvent,
    *,
    translator,
    include_detailed_text: bool = False,
) -> RenderedNotification:
    message = render_order_lifecycle_message(event, translator=translator)
    return RenderedNotification(
        detailed_text=message if include_detailed_text else "",
        compact_text=message,
    )


def publish_order_lifecycle_event(
    event: OrderLifecycleEvent,
    *,
    translator,
    publisher: NotificationPublisher,
    include_detailed_text: bool = False,
) -> None:
    publisher.publish(
        render_order_lifecycle_notification(
            event,
            translator=translator,
            include_detailed_text=include_detailed_text,
        )
    )

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
    publish_order_event: Callable[[OrderLifecycleEvent], None],
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
            event = build_order_lifecycle_event(
                symbol,
                side_text,
                quantity,
                order_id,
                status,
                executed_qty=executed_qty,
                executed_price=executed_price,
                reason=reject_message,
            )

            if is_filled_status(status):
                publish_order_event(event)
                return

            if is_partial_filled_status(status):
                publish_order_event(event)

            if is_terminal_error_status(status):
                publish_order_event(event)
                return
    except Exception:
        notify_issue(
            "Order status poll failed",
            f"Symbol: {symbol} Side: {side_text} Qty: {quantity} Order: {order_id}\n{traceback.format_exc()}",
        )
