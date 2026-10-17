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
    is_filled_status, is_partial_filled_status, is_terminal_error_status,
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
    legacy_notify_issue = build_issue_notifier(
        with_prefix_fn=with_prefix,
        send_tg_message_fn=send_recorded_message,
    )

    def issue_with_ack(title, detail):
        before = len(recorded_delivery_events)
        legacy_notify_issue(title, detail)
        events = recorded_delivery_events[before:]
        return bool(events) and all(event.get("transport_acknowledged") is True for event in events)

    def notify_issue(title, detail):
        from notifications import unified
        if not unified.enabled():
            return legacy_notify_issue(title, detail)
        # Existing callers provide fixed issue titles. Hash the category, never
        # provider detail text, run ids or credentials into a marker identity.
        reason = hashlib.sha256(str(title).encode()).hexdigest()
        return unified.dispatch_immediate(lambda: issue_with_ack(title, detail),
            kind="real_error", incident=f"runtime_issue:{reason}")
    order_event_publisher = NotificationPublisher(
        log_message=lambda _message: None,
        send_message=send_recorded_message,
    )

    def publish_order_event(event: OrderLifecycleEvent) -> None:
        from notifications import unified
        def publish():
            before = len(recorded_delivery_events)
            outcome = publish_order_lifecycle_event(event, translator=translator, publisher=order_event_publisher)
            if not unified.enabled():
                return outcome
            events = recorded_delivery_events[before:]
            return bool(events) and all(item.get("transport_acknowledged") is True for item in events)
        if unified.enabled() and not (is_filled_status(event.status) or is_partial_filled_status(event.status)):
            from notifications.digest_snapshot.unified_contract import order_identity_digest
            unified.dispatch_immediate(publish, kind="order_status_unknown",
                incident=f"order_status:{order_identity_digest(event.order_id)}", state=event.status,
                severity=2 if is_terminal_error_status(event.status) else 1)
        else:
            if unified.enabled() and is_filled_status(event.status):
                unified.record_order_resolution(event)
            publish()

    def post_submit_order(trade_context, order_intent, report) -> None:
        from notifications import unified
        issue_notifier = legacy_notify_issue
        if unified.enabled():
            from notifications.digest_snapshot.unified_contract import order_identity_digest
            if report.broker_order_id:
                incident = f"order_status_poll:{order_identity_digest(report.broker_order_id)}"
                def issue_notifier(title, detail):
                    return unified.dispatch_immediate(lambda: issue_with_ack(title, detail), kind="real_error", incident=incident)
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
            notify_issue=issue_notifier,
            sleeper=sleeper,
        )

    return LongBridgeNotificationAdapters(
        notification_port=CallableNotificationPort(send_recorded_message),
        notify_issue=notify_issue,
        post_submit_order=post_submit_order,
        cycle_publisher=cycle_publisher,
        delivery_events=recorded_delivery_events,
    )
