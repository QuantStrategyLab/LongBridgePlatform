"""Top-level runtime composer for LongBridge application wiring."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from application.runtime_bootstrap_adapters import build_runtime_bootstrap
from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
from application.runtime_notification_adapters import build_runtime_notification_adapters
from application.runtime_reporting_adapters import build_runtime_reporting_adapters
from notifications.telegram import build_prefixer, build_sender


@dataclass(frozen=True)
class LongBridgeRuntimeComposer:
    project_id: str | None
    secret_name: str
    token_refresh_threshold_days: int
    account_prefix: str
    account_region: str
    strategy_profile: str
    strategy_display_name: str
    strategy_display_name_localized: str
    strategy_domain: str | None
    notify_lang: str
    tg_token: str | None
    tg_chat_id: str | None
    managed_symbols: tuple[str, ...]
    benchmark_symbol: str
    signal_effective_after_trading_days: int | None
    separator: str
    limit_sell_discount: float
    limit_buy_premium: float
    order_poll_interval_sec: int
    order_poll_max_attempts: int
    dry_run_only: bool = False
    broker_adapters: Any = None
    strategy_adapters: Any = None
    estimate_max_purchase_quantity_fn: Callable[..., int] | None = None
    fetch_order_status_fn: Callable[..., Any] | None = None
    fetch_token_from_secret_fn: Callable[..., str] | None = None
    refresh_token_if_needed_fn: Callable[..., str] | None = None
    build_contexts_fn: Callable[..., tuple[Any, Any]] | None = None
    run_id_builder: Callable[[], str] | None = None
    event_logger: Callable[..., dict[str, Any]] | None = None
    report_builder: Callable[..., dict[str, Any]] | None = None
    report_persister: Callable[..., Any] | None = None
    translator: Callable[..., str] | None = None
    prefixer_builder: Callable[..., Callable[[str], str]] = build_prefixer
    sender_builder: Callable[..., Callable[[str], None]] = build_sender
    env_reader: Callable[[str, str], str | None] | None = None
    sleeper: Callable[[float], None] | None = None
    printer: Callable[..., Any] = print
    notification_adapter_builder: Callable[..., Any] = build_runtime_notification_adapters
    reporting_adapter_builder: Callable[..., Any] = build_runtime_reporting_adapters
    bootstrap_builder: Callable[..., Any] = build_runtime_bootstrap
    extra_reporting_fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        required = {
            "broker_adapters": self.broker_adapters,
            "strategy_adapters": self.strategy_adapters,
            "estimate_max_purchase_quantity_fn": self.estimate_max_purchase_quantity_fn,
            "fetch_order_status_fn": self.fetch_order_status_fn,
            "fetch_token_from_secret_fn": self.fetch_token_from_secret_fn,
            "refresh_token_if_needed_fn": self.refresh_token_if_needed_fn,
            "build_contexts_fn": self.build_contexts_fn,
            "run_id_builder": self.run_id_builder,
            "event_logger": self.event_logger,
            "report_builder": self.report_builder,
            "report_persister": self.report_persister,
            "translator": self.translator,
            "env_reader": self.env_reader,
            "sleeper": self.sleeper,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"Missing runtime composer dependencies: {', '.join(missing)}")

    def with_prefix(self, message: str) -> str:
        return self.prefixer_builder(self.account_prefix)(message)

    def send_tg_message(self, message: str) -> None:
        sender = self.sender_builder(
            self.tg_token,
            self.tg_chat_id,
            with_prefix_fn=self.with_prefix,
        )
        sender(message)

    def build_notification_adapters(self):
        return self.notification_adapter_builder(
            with_prefix=self.with_prefix,
            send_message=self.send_tg_message,
            translator=self.translator,
            fetch_order_status=self.fetch_order_status_fn,
            order_poll_interval_sec=self.order_poll_interval_sec,
            order_poll_max_attempts=self.order_poll_max_attempts,
            sleeper=self.sleeper,
            log_message=lambda message: self.printer(self.with_prefix(message), flush=True),
        )

    def build_reporting_adapters(self):
        return self.reporting_adapter_builder(
            platform="longbridge",
            deploy_target="cloud_run",
            service_name=self.env_reader("K_SERVICE", "longbridge-platform"),
            strategy_profile=self.strategy_profile,
            strategy_domain=self.strategy_domain,
            account_scope=self.account_region,
            account_region=self.account_region,
            project_id=self.project_id,
            extra_context_fields={
                "account_prefix": self.account_prefix,
                "strategy_display_name": self.strategy_display_name,
                "strategy_display_name_localized": self.strategy_display_name_localized,
                **dict(self.extra_reporting_fields),
            },
            managed_symbols=self.managed_symbols,
            account_prefix=self.account_prefix,
            benchmark_symbol=self.benchmark_symbol,
            strategy_display_name=self.strategy_display_name,
            strategy_display_name_localized=self.strategy_display_name_localized,
            dry_run=self.dry_run_only,
            signal_effective_after_trading_days=self.signal_effective_after_trading_days,
            report_base_dir=self.env_reader("EXECUTION_REPORT_OUTPUT_DIR", ""),
            report_gcs_prefix_uri=self.env_reader("EXECUTION_REPORT_GCS_URI", ""),
            run_id_builder=self.run_id_builder,
            event_logger=self.event_logger,
            report_builder=self.report_builder,
            report_persister=self.report_persister,
            printer=lambda line: self.printer(line, flush=True),
        )

    def build_rebalance_runtime(self) -> LongBridgeRebalanceRuntime:
        notification_adapters = self.build_notification_adapters()
        return LongBridgeRebalanceRuntime(
            bootstrap=self.bootstrap_builder(
                project_id=self.project_id,
                secret_name=self.secret_name,
                token_refresh_threshold_days=self.token_refresh_threshold_days,
                fetch_token_from_secret_fn=self.fetch_token_from_secret_fn,
                refresh_token_if_needed_fn=self.refresh_token_if_needed_fn,
                build_contexts_fn=self.build_contexts_fn,
                calculate_strategy_indicators_fn=self.strategy_adapters.calculate_strategy_indicators,
                env_reader=self.env_reader,
            ),
            resolve_rebalance_plan=self.strategy_adapters.resolve_rebalance_plan,
            market_data_port_factory=self.broker_adapters.build_market_data_port,
            estimate_max_purchase_quantity=self.estimate_max_purchase_quantity_fn,
            notifications=notification_adapters.notification_port,
            notify_issue=notification_adapters.notify_issue,
            portfolio_port_factory=self.broker_adapters.build_portfolio_port,
            execution_port_factory=self.broker_adapters.build_execution_port,
            post_submit_order=notification_adapters.post_submit_order,
        )

    def build_rebalance_config(self) -> LongBridgeRebalanceConfig:
        return LongBridgeRebalanceConfig(
            limit_sell_discount=self.limit_sell_discount,
            limit_buy_premium=self.limit_buy_premium,
            separator=self.separator,
            translator=self.translator,
            with_prefix=self.with_prefix,
            strategy_display_name=self.strategy_display_name_localized,
            dry_run_only=self.dry_run_only,
            post_sell_refresh_attempts=self.order_poll_max_attempts,
            post_sell_refresh_interval_sec=self.order_poll_interval_sec,
            sleeper=self.sleeper,
        )


def build_runtime_composer(
    *,
    project_id: str | None,
    secret_name: str,
    token_refresh_threshold_days: int,
    account_prefix: str,
    account_region: str,
    strategy_profile: str,
    strategy_display_name: str,
    strategy_display_name_localized: str,
    strategy_domain: str | None,
    notify_lang: str,
    tg_token: str | None,
    tg_chat_id: str | None,
    managed_symbols: tuple[str, ...],
    benchmark_symbol: str,
    signal_effective_after_trading_days: int | None,
    separator: str,
    limit_sell_discount: float,
    limit_buy_premium: float,
    order_poll_interval_sec: int,
    order_poll_max_attempts: int,
    dry_run_only: bool,
    broker_adapters: Any,
    strategy_adapters: Any,
    estimate_max_purchase_quantity_fn: Callable[..., int],
    fetch_order_status_fn: Callable[..., Any],
    fetch_token_from_secret_fn: Callable[..., str],
    refresh_token_if_needed_fn: Callable[..., str],
    build_contexts_fn: Callable[..., tuple[Any, Any]],
    run_id_builder: Callable[[], str],
    event_logger: Callable[..., dict[str, Any]],
    report_builder: Callable[..., dict[str, Any]],
    report_persister: Callable[..., Any],
    translator: Callable[..., str],
    env_reader: Callable[[str, str], str | None],
    sleeper: Callable[[float], None],
    printer: Callable[..., Any] = print,
    extra_reporting_fields: Mapping[str, Any] | None = None,
) -> LongBridgeRuntimeComposer:
    return LongBridgeRuntimeComposer(
        project_id=project_id,
        secret_name=secret_name,
        token_refresh_threshold_days=int(token_refresh_threshold_days),
        account_prefix=str(account_prefix or ""),
        account_region=str(account_region or ""),
        strategy_profile=str(strategy_profile),
        strategy_display_name=str(strategy_display_name or ""),
        strategy_display_name_localized=str(strategy_display_name_localized or ""),
        strategy_domain=strategy_domain,
        notify_lang=str(notify_lang or ""),
        tg_token=tg_token,
        tg_chat_id=tg_chat_id,
        managed_symbols=tuple(managed_symbols),
        benchmark_symbol=str(benchmark_symbol or ""),
        signal_effective_after_trading_days=signal_effective_after_trading_days,
        separator=str(separator),
        limit_sell_discount=float(limit_sell_discount),
        limit_buy_premium=float(limit_buy_premium),
        order_poll_interval_sec=int(order_poll_interval_sec),
        order_poll_max_attempts=int(order_poll_max_attempts),
        dry_run_only=bool(dry_run_only),
        broker_adapters=broker_adapters,
        strategy_adapters=strategy_adapters,
        estimate_max_purchase_quantity_fn=estimate_max_purchase_quantity_fn,
        fetch_order_status_fn=fetch_order_status_fn,
        fetch_token_from_secret_fn=fetch_token_from_secret_fn,
        refresh_token_if_needed_fn=refresh_token_if_needed_fn,
        build_contexts_fn=build_contexts_fn,
        run_id_builder=run_id_builder,
        event_logger=event_logger,
        report_builder=report_builder,
        report_persister=report_persister,
        translator=translator,
        env_reader=env_reader,
        sleeper=sleeper,
        printer=printer,
        extra_reporting_fields=dict(extra_reporting_fields or {}),
    )
