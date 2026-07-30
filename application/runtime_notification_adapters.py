"""Builder helpers for LongBridge runtime notification adapters."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from notifications.events import NotificationPublisher, RenderedNotification
from notifications.order_alerts import (
    OrderLifecycleEvent,
    monitor_submitted_order_status,
    publish_order_lifecycle_event,
)
from notifications.telegram import build_issue_notifier
from quant_platform_kit.common.quantity import format_quantity
from quant_platform_kit.common.port_adapters import CallableNotificationPort
from quant_platform_kit.common.ports import NotificationPort


@dataclass(frozen=True)
class LongBridgeNotificationAdapters:
    notification_port: NotificationPort
    notify_issue: Callable[[str, str], None]
    post_submit_order: Callable[[Any, Any, Any], None]
    cycle_publisher: NotificationPublisher
    delivery_events: list[dict[str, Any]]

    def publish_cycle_notification(self, *, detailed_text: str, compact_text: str) -> bool:
        before_count = len(self.delivery_events)
        outcome = self.cycle_publisher.publish(
            RenderedNotification(
                detailed_text=detailed_text,
                compact_text=compact_text,
            )
        )
        deliveries = self.delivery_events[before_count:]
        if deliveries:
            return all(event.get("delivery_status") == "sent" for event in deliveries)
        return outcome is not False


def build_runtime_notification_adapters(
    *,
    with_prefix: Callable[[str], str],
    send_message: Callable[[str], None],
    notification_channel: str = "telegram",
    translator: Callable[..., str],
    fetch_order_status: Callable[..., Any],
    order_poll_interval_sec: int,
    order_poll_max_attempts: int,
    sleeper: Callable[[float], None],
    log_message: Callable[[str], None] | None = None,
    delivery_events: list[dict[str, Any]] | None = None,
) -> LongBridgeNotificationAdapters:
    recorded_delivery_events = delivery_events if delivery_events is not None else []

    def send_recorded_message(message: str) -> bool:
        compact = str(message or "")
        event = {
            "sink": notification_channel,
            "compact_text_sha256": hashlib.sha256(compact.encode("utf-8")).hexdigest(),
            "compact_text_length": len(compact),
        }
        try:
            outcome = send_message(message)
        except Exception as exc:
            event.update(
                {
                    "delivery_status": "failed",
                    "transport_acknowledged": False,
                    "error_type": type(exc).__name__,
                }
            )
            recorded_delivery_events.append(event)
            return False
        acknowledged = outcome is not False
        event.update(
            {
                "delivery_status": "sent" if acknowledged else "failed",
                "transport_acknowledged": acknowledged,
            }
        )
        recorded_delivery_events.append(event)
        return acknowledged

    cycle_publisher = NotificationPublisher(
        log_message=log_message or (lambda message: print(with_prefix(message), flush=True)),
        send_message=send_recorded_message,
    )
    notify_issue = build_issue_notifier(
        with_prefix_fn=with_prefix,
        send_tg_message_fn=send_recorded_message,
    )
    order_event_publisher = NotificationPublisher(
        log_message=lambda _message: None,
        send_message=send_recorded_message,
    )

    def publish_order_event(event: OrderLifecycleEvent) -> None:
        publish_order_lifecycle_event(
            event,
            translator=translator,
            publisher=order_event_publisher,
        )

    def post_submit_order(trade_context, order_intent, report) -> None:
        monitor_submitted_order_status(
            trade_context,
            str(order_intent.symbol),
            "Buy" if str(order_intent.side).lower() == "buy" else "Sell",
            format_quantity(order_intent.quantity),
            report.broker_order_id or "",
            fetch_order_status=fetch_order_status,
            order_poll_interval_sec=order_poll_interval_sec,
            order_poll_max_attempts=order_poll_max_attempts,
            publish_order_event=publish_order_event,
            notify_issue=notify_issue,
            sleeper=sleeper,
        )

    return LongBridgeNotificationAdapters(
        notification_port=CallableNotificationPort(send_recorded_message),
        notify_issue=notify_issue,
        post_submit_order=post_submit_order,
        cycle_publisher=cycle_publisher,
        delivery_events=recorded_delivery_events,
    )
