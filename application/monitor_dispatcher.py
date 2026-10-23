"""Compatibility facade for the shared QPK monitor dispatcher.

The implementation lives in :mod:`quant_platform_kit.common.platform_runner.monitor`.
This module keeps the historical ``application.monitor_dispatcher`` import path and
its public names stable for LongBridge callers.
"""

from __future__ import annotations

from quant_platform_kit.common.platform_runner.monitor import (
    DEFAULT_LOOKBACK_MINUTES,
    DEFAULT_MAX_WORKERS,
    DEFAULT_TIMEOUT_SECONDS,
    MONITOR_TARGET_ENV_NAMES,
    MONITOR_WINDOWS,
    MonitorWindow,
    dispatch_due_monitors,
    load_monitor_targets,
)

__all__ = [
    "DEFAULT_LOOKBACK_MINUTES",
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MONITOR_TARGET_ENV_NAMES",
    "MONITOR_WINDOWS",
    "MonitorWindow",
    "dispatch_due_monitors",
    "load_monitor_targets",
]
