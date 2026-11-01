#!/usr/bin/env python3
"""Render or send ONE LongBridge PAPER Telegram notification preview.

The preview renders a single consolidated synthetic message in the unified
daily-digest format (the QuantSentinel daily digest contract) by reusing the
pinned QRS digest renderer vendor snapshot in ``tests/vendor/qrs_notification``.
With ``--send`` the workflow delivers that one message through the existing
Telegram sender; without ``--send`` it prints the rendered message to the job
log and sends nothing.

Does not trade, read real accounts/positions/quotes, deploy, or change
configuration.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications.telegram import build_prefixer, build_sender  # noqa: E402
from scripts.send_paper_notification_canary import (  # noqa: E402
    resolve_telegram_chat_id,
    resolve_telegram_token,
)

_QRS_SNAPSHOT = ROOT / "tests" / "vendor" / "qrs_notification"
_PREVIEW_PLATFORM_ID = "longbridge"
_PREVIEW_STRATEGY_PROFILE = "us_equity_combo"
_PREVIEW_COPY = {
    "zh": {
        "extra_lines": (
            "🧪 【PREVIEW】PAPER 通知预览",
            "合成样例 · 不会下单",
        ),
    },
    "en": {
        "extra_lines": (
            "🧪 【PREVIEW】PAPER notification preview",
            "Synthetic example · No order will be placed",
        ),
    },
}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _resolve_locale(raw: str | None = None) -> str:
    value = str(raw or os.environ.get("NOTIFY_LANG") or "zh").strip().lower()
    return "en" if value.startswith("en") else "zh"


def _digest_renderer():
    """Load the pinned QRS daily-digest renderer vendor snapshot (a pure formatter)."""
    snapshot = str(_QRS_SNAPSHOT)
    if snapshot not in sys.path:
        sys.path.insert(0, snapshot)
    import daily_digest_notify

    return daily_digest_notify


def _default_business_day() -> str:
    return datetime.now(_SHANGHAI).date().isoformat()


def build_preview_message(*, locale: str | None = None, business_day: str | None = None) -> str:
    """Render ONE synthetic message in the unified daily-digest format."""

    resolved_locale = _resolve_locale(locale)
    digest = _digest_renderer()
    payload = digest.DailyDigestInput(
        business_day=business_day or _default_business_day(),
        locale=resolved_locale,
        runs=(
            digest.DigestRunEntry(
                platform_id=_PREVIEW_PLATFORM_ID,
                strategy_profile=_PREVIEW_STRATEGY_PROFILE,
                status="ok",
                order_count=0,
                fill_count=0,
                cycle_count=1,
            ),
        ),
    )
    body = digest.render_daily_digest(payload)
    return "\n".join((body, *_PREVIEW_COPY[resolved_locale]["extra_lines"]))


def send_preview(*, requests_module=None, locale: str | None = None) -> bool:
    message = build_preview_message(locale=locale)
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
    if not sender(message):
        print("Notification preview delivery failed.", file=sys.stderr)
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render (or send with --send) one LongBridge PAPER notification "
            "preview in the unified daily-digest format."
        )
    )
    parser.add_argument(
        "--locale",
        default=os.environ.get("NOTIFY_LANG"),
        help="Optional notification locale override (zh/en). Defaults to NOTIFY_LANG.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        default=(os.environ.get("PREVIEW_SEND") or "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Send the rendered preview message to Telegram. Default: print only.",
    )
    args = parser.parse_args(argv)

    message = build_preview_message(locale=args.locale)
    if not args.send:
        print("Rendered PAPER notification preview (send=false; nothing sent):")
        print(message)
        print(
            "Preview rendered only: one consolidated unified daily-digest "
            "message; no Telegram delivery."
        )
        return 0

    delivered = send_preview(locale=args.locale)
    if not delivered:
        return 1
    print(
        "Notification preview delivered: one consolidated synthetic unified "
        "daily-digest message (no orders)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
