"""Builder helpers for LongBridge runtime notification adapters."""

from __future__ import annotations

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
from quant_platform_kit.common.port_adapters import CallableNotificationPort
from quant_platform_kit.common.ports import NotificationPort


@dataclass(frozen=True)
class LongBridgeNotificationAdapters:
    notification_port: NotificationPort
    notify_issue: Callable[[str, str], None]
    post_submit_order: Callable[[Any, Any, Any], None]
    cycle_publisher: NotificationPublisher

    def publish_cycle_notification(self, *, detailed_text: str, compact_text: str) -> None:
        self.cycle_publisher.publish(
            RenderedNotification(
                detailed_text=detailed_text,
                compact_text=compact_text,
            )
        )


def build_runtime_notification_adapters(
    *,
    with_prefix: Callable[[str], str],
    send_message: Callable[[str], None],
    translator: Callable[..., str],
    fetch_order_status: Callable[..., Any],
    order_poll_interval_sec: int,
    order_poll_max_attempts: int,
    sleeper: Callable[[float], None],
    log_message: Callable[[str], None] | None = None,
) -> LongBridgeNotificationAdapters:
    cycle_publisher = NotificationPublisher(
        log_message=log_message or (lambda message: print(with_prefix(message), flush=True)),
        send_message=send_message,
    )
    notify_issue = build_issue_notifier(
        with_prefix_fn=with_prefix,
        send_tg_message_fn=send_message,
    )
    order_event_publisher = NotificationPublisher(
        log_message=lambda _message: None,
        send_message=send_message,
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
            int(order_intent.quantity),
            report.broker_order_id or "",
            fetch_order_status=fetch_order_status,
            order_poll_interval_sec=order_poll_interval_sec,
            order_poll_max_attempts=order_poll_max_attempts,
            publish_order_event=publish_order_event,
            notify_issue=notify_issue,
            sleeper=sleeper,
        )

    return LongBridgeNotificationAdapters(
        notification_port=CallableNotificationPort(send_message),
        notify_issue=notify_issue,
        post_submit_order=post_submit_order,
        cycle_publisher=cycle_publisher,
    )
