#!/usr/bin/env python3
"""Send a bounded LongBridge PAPER Telegram notification preview pack.

Renders synthetic compact_text via existing notification renderers/order alerts.
Does not trade, read real accounts/positions/quotes, deploy, or change configuration.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications.order_alerts import (
    build_order_lifecycle_event,
    render_order_lifecycle_notification,
)
from notifications.renderers import (
    render_heartbeat_notification,
    render_rebalance_notification,
)
from notifications.telegram import build_prefixer, build_sender, build_translator
from scripts.send_paper_notification_canary import (
    resolve_telegram_chat_id,
    resolve_telegram_token,
)

_SEPARATOR = "━━━━━━━━━━━━━━━━━━"
_MAX_PREVIEW_MESSAGES = 6
_PREVIEW_STRATEGY_NAME = "PAPER Notification Preview"
_PREVIEW_EXTRA_LINES = (
    "🧪 【PREVIEW】PAPER notification preview",
    "synthetic / 合成样例 · 不会下单 · No order will be placed",
)


def _resolve_locale(raw: str | None = None) -> str:
    value = str(raw or os.environ.get("NOTIFY_LANG") or "zh").strip().lower()
    return "en" if value.startswith("en") else "zh"


def _synthetic_execution(*, with_dashboard: bool) -> dict:
    execution: dict = {
        "cash_only_execution": True,
        "status_display": "preview_hold",
        "signal_display": "synthetic preview signal",
    }
    if with_dashboard:
        execution["dashboard_text"] = (
            "📌 PAPER PREVIEW\n"
            "  - 可用现金: $0.00 | 可投资现金: $0.00\n"
            "  - synthetic positions only"
        )
    return execution


def build_preview_messages(*, locale: str | None = None) -> list[str]:
    translator = build_translator(_resolve_locale(locale))
    messages: list[str] = []

    heartbeat = render_heartbeat_notification(
        execution=_synthetic_execution(with_dashboard=True),
        skip_logs=(),
        note_logs=(),
        translator=translator,
        separator=_SEPARATOR,
        strategy_display_name=_PREVIEW_STRATEGY_NAME,
        dry_run_only=True,
        extra_notification_lines=_PREVIEW_EXTRA_LINES,
    )
    messages.append(heartbeat.compact_text)

    rebalance = render_rebalance_notification(
        execution=_synthetic_execution(with_dashboard=True),
        logs=("SYNTHETIC Buy PREVIEW.US x1 @ market (preview only)",),
        skip_logs=(),
        note_logs=(),
        translator=translator,
        separator=_SEPARATOR,
        strategy_display_name=_PREVIEW_STRATEGY_NAME,
        dry_run_only=True,
        extra_notification_lines=_PREVIEW_EXTRA_LINES,
    )
    messages.append(rebalance.compact_text)

    order_specs = (
        ("StatusCheckTimeout", "0", "0", ""),
        ("Filled", "1", "100.00", ""),
        ("Rejected", "0", "0", "synthetic reject"),
        ("WeirdBrokerCode", "0", "0", ""),
    )
    for status, executed_qty, executed_price, reason in order_specs:
        rendered = render_order_lifecycle_notification(
            build_order_lifecycle_event(
                "PREVIEW.US",
                "Buy",
                1,
                "preview-synthetic-order",
                status,
                executed_qty=executed_qty,
                executed_price=executed_price,
                reason=reason,
            ),
            translator=translator,
        )
        body = rendered.compact_text
        messages.append("\n".join((body, *_PREVIEW_EXTRA_LINES)))

    if len(messages) > _MAX_PREVIEW_MESSAGES:
        raise RuntimeError(
            f"preview message count {len(messages)} exceeds cap {_MAX_PREVIEW_MESSAGES}"
        )
    return messages


def send_preview(*, requests_module=None, locale: str | None = None) -> bool:
    messages = build_preview_messages(locale=locale)
    token = resolve_telegram_token()
    chat_id = resolve_telegram_chat_id()
    if not token or not chat_id:
        print(
            "Notification preview not sent: Telegram target is not configured.",
            file=sys.stderr,
        )
        return False
    sender = build_sender(
        token,
        chat_id,
        with_prefix_fn=build_prefixer("PAPER"),
        requests_module=requests_module,
    )
    for message in messages:
        if not sender(message):
            print("Notification preview delivery failed.", file=sys.stderr)
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a bounded LongBridge PAPER Telegram notification preview pack."
    )
    parser.add_argument(
        "--locale",
        default=os.environ.get("NOTIFY_LANG"),
        help="Optional notification locale override (zh/en). Defaults to NOTIFY_LANG.",
    )
    args = parser.parse_args(argv)
    delivered = send_preview(locale=args.locale)
    if not delivered:
        return 1
    print(
        "Notification preview delivered bounded synthetic PAPER pack "
        f"(at most {_MAX_PREVIEW_MESSAGES} messages; no orders)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
