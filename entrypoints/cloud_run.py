"""Cloud Run request helpers for LongBridgePlatform."""

from __future__ import annotations

from datetime import datetime

import pandas_market_calendars as mcal
import pytz


def is_market_open_now(*, calendar_name="NYSE"):
    try:
        calendar = mcal.get_calendar(calendar_name)
        now_utc = datetime.now(pytz.utc)
        schedule = calendar.schedule(start_date=now_utc, end_date=now_utc)
        return False if schedule.empty else calendar.open_at_time(schedule, now_utc)
    except Exception as exc:
        return False, exc
