from types import SimpleNamespace

from application.runtime_notification_adapters import build_runtime_notification_adapters
from notifications.telegram import build_translator


def test_runtime_notification_adapters_publish_cycle_notification_uses_log_and_send_sinks():
    logs = []
    messages = []
    adapters = build_runtime_notification_adapters(
        with_prefix=lambda message: f"[HK] {message}",
        send_message=messages.append,
        translator=build_translator("en"),
        fetch_order_status=lambda *_args, **_kwargs: None,
        order_poll_interval_sec=0,
        order_poll_max_attempts=0,
        sleeper=lambda _seconds: None,
        log_message=logs.append,
    )

    adapters.publish_cycle_notification(
        detailed_text="detailed line",
        compact_text="compact line",
    )

    assert logs == ["detailed line"]
    assert messages == ["compact line"]


def test_runtime_notification_adapters_post_submit_order_publishes_order_events():
    messages = []
    adapters = build_runtime_notification_adapters(
        with_prefix=lambda message: f"[HK] {message}",
        send_message=messages.append,
        translator=build_translator("en"),
        fetch_order_status=lambda *_args, **_kwargs: {
            "status": "Filled",
            "reason": "",
            "executed_qty": "10",
            "executed_price": "123.45",
        },
        order_poll_interval_sec=0,
        order_poll_max_attempts=1,
        sleeper=lambda _seconds: None,
        log_message=lambda _message: None,
    )

    adapters.post_submit_order(
        "trade-context",
        SimpleNamespace(symbol="SOXL.US", side="buy", quantity=10),
        SimpleNamespace(broker_order_id="order-1"),
    )

    assert messages == ["✅ Order Filled | SOXL Buy 10 shares avg $123.45 (ID: order-1)"]
