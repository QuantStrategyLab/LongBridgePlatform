from types import SimpleNamespace

from notifications.order_alerts import submit_order_with_alert
from notifications.telegram import build_translator


def test_submit_order_with_alert_localizes_order_id_suffix_for_zh():
    logs = []
    printed = []

    submitted = submit_order_with_alert(
        trade_context=object(),
        symbol="BOXX.US",
        order_type="market",
        side="buy",
        quantity=49,
        logs=logs,
        log_message="📈 [市价买入] BOXX: 49股 @ $116.31",
        submit_order=lambda *_args, **_kwargs: SimpleNamespace(broker_order_id="1227343614540054528"),
        fetch_order_status=lambda *_args, **_kwargs: None,
        translator=build_translator("zh"),
        send_tg_message=lambda _message: None,
        notify_issue=lambda *_args, **_kwargs: None,
        order_poll_interval_sec=0,
        order_poll_max_attempts=0,
        sleeper=lambda _seconds: None,
        print_with_prefix=printed.append,
    )

    assert submitted is True
    assert logs == ["📈 [市价买入] BOXX: 49股 @ $116.31 （订单号: 1227343614540054528）"]
    assert "order_id=" not in logs[0]
    assert printed == ["OK 📈 [市价买入] BOXX: 49股 @ $116.31 （订单号: 1227343614540054528）"]
