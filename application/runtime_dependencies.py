"""Runtime dependency bundles for LongBridge rebalance orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from quant_platform_kit.common.ports import ExecutionPort, MarketDataPort, NotificationPort, PortfolioPort


@dataclass(frozen=True)
class LongBridgeRebalanceConfig:
    limit_sell_discount: float
    limit_buy_premium: float
    separator: str
    translator: Callable[..., str]
    with_prefix: Callable[[str], str]
    strategy_display_name: str = ""
    dry_run_only: bool = False
    post_sell_refresh_attempts: int = 1
    post_sell_refresh_interval_sec: float = 0.0
    sleeper: Callable[[float], None] | None = None


@dataclass(frozen=True)
class LongBridgeRebalanceRuntime:
    bootstrap: Callable[[], tuple[Any, Any, Any]]
    resolve_rebalance_plan: Callable[..., dict[str, Any]]
    market_data_port_factory: Callable[[Any], MarketDataPort]
    estimate_max_purchase_quantity: Callable[..., int]
    notifications: NotificationPort
    notify_issue: Callable[[str, str], None]
    portfolio_port_factory: Callable[[Any, Any], PortfolioPort]
    execution_port_factory: Callable[[Any], ExecutionPort]
    post_submit_order: Callable[[Any, Any, Any], None] | None = None
