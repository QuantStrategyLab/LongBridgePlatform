from notifications.events import NotificationPublisher
from notifications.order_alerts import (
    build_order_lifecycle_event,
    monitor_submitted_order_status,
    publish_order_lifecycle_event,
    render_order_lifecycle_message,
)
from notifications.telegram import build_translator


def test_render_order_lifecycle_message_localizes_order_fill_for_zh():
    message = render_order_lifecycle_message(
        build_order_lifecycle_event(
            "BOXX.US",
            "Buy",
            49,
            "1227343614540054528",
            "Filled",
            executed_price="116.31",
        ),
        translator=build_translator("zh"),
    )

    assert message == "✅ 订单成交 | BOXX 买入 49股 均价 $116.31（订单号: 1227343614540054528）"


def test_publish_order_lifecycle_event_routes_rendering_through_publisher():
    messages = []

    publish_order_lifecycle_event(
        build_order_lifecycle_event(
            "SOXL.US",
            "Buy",
            10,
            "order-1",
            "Filled",
            executed_price="123.45",
        ),
        translator=build_translator("en"),
        publisher=NotificationPublisher(
            log_message=lambda _message: None,
            send_message=messages.append,
        ),
    )

    assert messages == ["✅ Order Filled | SOXL Buy 10 shares avg $123.45 (ID: order-1)"]


def test_monitor_submitted_order_status_emits_lifecycle_events_via_callback():
    events = []

    monitor_submitted_order_status(
        trade_context=object(),
        symbol="SOXL.US",
        side_text="Buy",
        quantity=10,
        order_id="order-1",
        fetch_order_status=lambda *_args, **_kwargs: {
            "status": "PartialFilled",
            "reason": "",
            "executed_qty": "4",
            "executed_price": "122.10",
        },
        order_poll_interval_sec=0,
        order_poll_max_attempts=1,
        publish_order_event=events.append,
        notify_issue=lambda *_args, **_kwargs: None,
        sleeper=lambda _seconds: None,
    )

    assert len(events) == 1
    assert events[0].symbol == "SOXL.US"
    assert events[0].status == "PartialFilled"
    assert events[0].executed_qty == "4"
