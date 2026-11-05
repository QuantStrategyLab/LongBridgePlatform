"""Disabled-by-default scheduler hook for disposable PAPER previews only.

Hosts inject the frozen plan/calendar/facts readers and an aware tick time.
No timer, route, queue, period claim, notification or order writer is created.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from application.plain_dca_paper_adapter import (
    PlainDcaPaperError,
    check_plain_dca_paper_binding,
    consume_plain_dca_preview,
)

_INPUTS = frozenset({"plan", "calendar", "period", "facts", "quote", "capabilities"})


def scheduled_plain_dca_preview(
    *, read_binding: Callable[[], Mapping[str, object]],
    read_inputs: Callable[[], Mapping[str, Any]], now: datetime,
    enabled: object = False,
) -> Any | None:
    """Return None while disabled; enabling permits only the existing preview.

    The host must not set enabled until the separately authorized start work.
    Check protected identity before fetching facts and reread it through the
    adapter after fetching them. Missing/unknown safety evidence never passes.
    Repeat ticks are repeat previews, not exactly-once trading or accounting.
    """
    if enabled is False:
        return None
    if enabled is not True:
        raise PlainDcaPaperError("plain_dca_scheduler_flag_invalid")
    try:
        binding = read_binding()
    except Exception:
        raise PlainDcaPaperError("paper_binding_readback_unavailable") from None
    check_plain_dca_paper_binding(binding, now=now)
    try:
        inputs = read_inputs()
        if not isinstance(inputs, Mapping) or set(inputs) != _INPUTS:
            raise ValueError
        inputs = dict(inputs)
    except Exception:
        raise PlainDcaPaperError("plain_dca_scheduler_inputs_unavailable") from None
    return consume_plain_dca_preview(read_binding=read_binding, now=now, **inputs)
