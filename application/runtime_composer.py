"""Top-level runtime composer for LongBridge application wiring."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from application.runtime_bootstrap_adapters import build_runtime_bootstrap
from application.runtime_dependencies import LongBridgeRebalanceConfig, LongBridgeRebalanceRuntime
from application.execution_state import (
    build_execution_marker_store_from_env,
    resolve_execution_dedup_enabled,
)
from application.runtime_notification_adapters import build_runtime_notification_adapters
from application.runtime_reporting_adapters import build_runtime_reporting_adapters
from quant_platform_kit.common.port_adapters import CallableNotificationPort
from quant_platform_kit.common.runtime_assembly import build_runtime_assembly
from quant_platform_kit.common.runtime_target import build_runtime_context_fields
from quant_platform_kit.common.runtime_target import RuntimeTarget
from notifications.telegram import build_prefixer
from quant_platform_kit.notifications.cycle_channel import build_cycle_sender
from runtime_execution_policy import FRACTIONAL_BUY_QUANTITY_STEP, fractional_buy_execution_enabled


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
    safe_haven_cash_substitute_threshold_usd: float
    min_order_notional_usd: float
    market: str = "US"
    symbol_suffix: str = ".US"
    trading_currency: str = "USD"
    dry_run_only: bool = False
    broker_adapters: Any = None
    strategy_adapters: Any = None
    estimate_max_purchase_quantity_fn: Callable[..., float] | None = None
    fetch_order_status_fn: Callable[..., Any] | None = None
    fetch_token_from_secret_fn: Callable[..., str] | None = None
    refresh_token_if_needed_fn: Callable[..., str] | None = None
    build_contexts_fn: Callable[..., tuple[Any, Any]] | None = None
    run_id_builder: Callable[[], str] | None = None
    event_logger: Callable[..., dict[str, Any]] | None = None
    report_builder: Callable[..., dict[str, Any]] | None = None
    report_persister: Callable[..., Any] | None = None
    translator: Callable[..., str] | None = None
    runtime_target: RuntimeTarget | None = None
    notification_channel: str = "telegram"
    webhook_url: str | None = None
    prefixer_builder: Callable[..., Callable[[str], str]] = build_prefixer
    env_reader: Callable[[str, str], str | None] | None = None
    sleeper: Callable[[float], None] | None = None
    printer: Callable[..., Any] = print
    notification_adapter_builder: Callable[..., Any] = build_runtime_notification_adapters
    reporting_adapter_builder: Callable[..., Any] = build_runtime_reporting_adapters
    bootstrap_builder: Callable[..., Any] = build_runtime_bootstrap
    limit_buy_premium_by_symbol: dict[str, float] | None = None
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

    def send_message(self, message: str) -> None:
        """Send a cycle notification through the configured channel."""
        prefixed = self.with_prefix(message)
        sender = build_cycle_sender(
            channel=self.notification_channel,
            telegram_token=self.tg_token,
            telegram_chat_id=self.tg_chat_id,
            webhook_url=self.webhook_url,
        )
        sender(prefixed)

    send_tg_message = send_message  # backward-compat alias

    def build_notification_adapters(self, *, delivery_events: list[dict[str, Any]] | None = None):
        return self.notification_adapter_builder(
            with_prefix=self.with_prefix,
            send_message=self.send_message,
            notification_channel=self.notification_channel,
            translator=self.translator,
            fetch_order_status=self.fetch_order_status_fn,
            order_poll_interval_sec=self.order_poll_interval_sec,
            order_poll_max_attempts=self.order_poll_max_attempts,
            sleeper=self.sleeper,
            log_message=lambda message: self.printer(self.with_prefix(message), flush=True),
            delivery_events=delivery_events,
        )

    def build_reporting_adapters(self):
        runtime_assembly = build_runtime_assembly(
            platform="longbridge",
            deploy_target="cloud_run",
            service_name=self.env_reader("K_SERVICE", "longbridge-platform"),
            strategy_profile=self.strategy_profile,
            runtime_target=self.runtime_target,
            account_scope=self.account_region,
            account_region=self.account_region,
            project_id=self.project_id,
            extra_context_fields=build_runtime_context_fields(
                {
                    "account_prefix": self.account_prefix,
                    "market": self.market,
                    "symbol_suffix": self.symbol_suffix,
                    "trading_currency": self.trading_currency,
                    "strategy_display_name": self.strategy_display_name,
                    "strategy_display_name_localized": self.strategy_display_name_localized,
                    **dict(self.extra_reporting_fields),
                },
            ),
        )
        return self.reporting_adapter_builder(
            runtime_assembly=runtime_assembly,
            strategy_domain=self.strategy_domain,
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

    def build_rebalance_runtime(
        self,
        *,
        silent_cycle_notifications: bool = False,
        notification_delivery_events: list[dict[str, Any]] | None = None,
    ) -> LongBridgeRebalanceRuntime:
        notification_adapters = self.build_notification_adapters(delivery_events=notification_delivery_events)
        notifications = (
            CallableNotificationPort(lambda _message: None)
            if silent_cycle_notifications
            else notification_adapters.notification_port
        )
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
            notifications=notifications,
            notify_issue=notification_adapters.notify_issue,
            portfolio_port_factory=self.broker_adapters.build_portfolio_port,
            execution_port_factory=self.broker_adapters.build_execution_port,
            post_submit_order=notification_adapters.post_submit_order,
        )

    def build_rebalance_config(
        self,
        *,
        strategy_plugin_signals=(),
        strategy_plugin_error: str | None = None,
        notification_title_key: str = "",
        cash_only_execution: bool = True,
    ) -> LongBridgeRebalanceConfig:
        market_scope_line = self.translator(
            "market_scope_detail",
            market=self.market,
            currency=self.trading_currency,
            symbol_suffix=self.symbol_suffix or "<none>",
        )
        build_plugin_lines = getattr(
            self.strategy_adapters,
            "build_strategy_plugin_notification_lines",
            lambda _signals: (),
        )
        plugin_lines = tuple(build_plugin_lines(tuple(strategy_plugin_signals or ())))
        build_plugin_error_lines = getattr(
            self.strategy_adapters,
            "build_strategy_plugin_error_notification_lines",
            lambda _error: (),
        )
        plugin_error_lines = tuple(build_plugin_error_lines(strategy_plugin_error))
        fractional_buy_execution = fractional_buy_execution_enabled(self.strategy_profile)
        return LongBridgeRebalanceConfig(
            limit_sell_discount=self.limit_sell_discount,
            limit_buy_premium=self.limit_buy_premium,
            limit_buy_premium_by_symbol=self.limit_buy_premium_by_symbol,
            separator=self.separator,
            translator=self.translator,
            with_prefix=self.with_prefix,
            strategy_profile=self.strategy_profile,
            strategy_display_name=self.strategy_display_name_localized,
            dry_run_only=self.dry_run_only,
            symbol_suffix=self.symbol_suffix or ".US",
            post_sell_refresh_attempts=self.order_poll_max_attempts,
            post_sell_refresh_interval_sec=self.order_poll_interval_sec,
            min_order_notional_usd=self.min_order_notional_usd,
            safe_haven_cash_substitute_threshold_usd=self.safe_haven_cash_substitute_threshold_usd,
            cash_only_execution=bool(cash_only_execution),
            fractional_buy_execution=fractional_buy_execution,
            buy_quantity_step=FRACTIONAL_BUY_QUANTITY_STEP if fractional_buy_execution else 1.0,
            sleeper=self.sleeper,
            extra_notification_lines=(market_scope_line, *plugin_lines, *plugin_error_lines),
            notification_title_key=notification_title_key,
            strategy_plugin_signals=tuple(strategy_plugin_signals or ()),
            execution_dedup_enabled=resolve_execution_dedup_enabled(
                env_reader=self.env_reader,
                dry_run_only=self.dry_run_only,
                account_scope=self.account_region,
            ),
            execution_state_store=build_execution_marker_store_from_env(
                env_reader=self.env_reader,
                gcp_project_id=self.project_id,
            ),
            execution_state_account_scope=self.account_region,
        )

    def load_strategy_plugin_signals(self, raw_mounts):
        return getattr(self.strategy_adapters, "load_strategy_plugin_signals", lambda _raw_mounts: ((), None))(raw_mounts)

    def attach_strategy_plugin_report(self, report, *, signals, error: str | None = None):
        attach = getattr(self.strategy_adapters, "attach_strategy_plugin_report", None)
        if attach is None:
            return None
        return attach(
            report,
            signals=signals,
            error=error,
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
    notification_channel: str = "telegram",
    webhook_url: str | None = None,
    managed_symbols: tuple[str, ...],
    benchmark_symbol: str,
    signal_effective_after_trading_days: int | None,
    separator: str,
    limit_sell_discount: float,
    limit_buy_premium: float,
    order_poll_interval_sec: int,
    order_poll_max_attempts: int,
    safe_haven_cash_substitute_threshold_usd: float,
    min_order_notional_usd: float,
    dry_run_only: bool,
    broker_adapters: Any,
    strategy_adapters: Any,
    estimate_max_purchase_quantity_fn: Callable[..., float],
    fetch_order_status_fn: Callable[..., Any],
    fetch_token_from_secret_fn: Callable[..., str],
    refresh_token_if_needed_fn: Callable[..., str],
    build_contexts_fn: Callable[..., tuple[Any, Any]],
    run_id_builder: Callable[[], str],
    event_logger: Callable[..., dict[str, Any]],
    report_builder: Callable[..., dict[str, Any]],
    report_persister: Callable[..., Any],
    translator: Callable[..., str],
    runtime_target: RuntimeTarget | None,
    env_reader: Callable[[str, str], str | None],
    sleeper: Callable[[float], None],
    market: str = "US",
    symbol_suffix: str = ".US",
    trading_currency: str = "USD",
    dry_run_only_override: bool | None = None,
    printer: Callable[..., Any] = print,
    extra_reporting_fields: Mapping[str, Any] | None = None,
    limit_buy_premium_by_symbol: dict[str, float] | None = None,
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
        notification_channel=notification_channel,
        webhook_url=webhook_url,
        managed_symbols=tuple(managed_symbols),
        benchmark_symbol=str(benchmark_symbol or ""),
        signal_effective_after_trading_days=signal_effective_after_trading_days,
        separator=str(separator),
        limit_sell_discount=float(limit_sell_discount),
        limit_buy_premium=float(limit_buy_premium),
        limit_buy_premium_by_symbol=dict(limit_buy_premium_by_symbol or {}),
        order_poll_interval_sec=int(order_poll_interval_sec),
        order_poll_max_attempts=int(order_poll_max_attempts),
        min_order_notional_usd=float(min_order_notional_usd),
        safe_haven_cash_substitute_threshold_usd=float(safe_haven_cash_substitute_threshold_usd),
        market=str(market or "US").upper(),
        symbol_suffix=str(symbol_suffix or ""),
        trading_currency=str(trading_currency or "USD").upper(),
        dry_run_only=bool(dry_run_only if dry_run_only_override is None else dry_run_only_override),
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
        runtime_target=runtime_target,
        env_reader=env_reader,
        sleeper=sleeper,
        printer=printer,
        extra_reporting_fields=dict(extra_reporting_fields or {}),
    )
