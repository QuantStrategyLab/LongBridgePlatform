import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from application.runtime_composer import LongBridgeRuntimeComposer


def test_runtime_composer_builds_runtime_and_config_from_local_builders():
    observed = {}

    def fake_notification_builder(**kwargs):
        observed["notification_builder"] = kwargs
        return SimpleNamespace(
            notification_port="notification-port",
            notify_issue="notify-issue",
            post_submit_order="post-submit-order",
        )

    def fake_reporting_builder(**kwargs):
        observed["reporting_builder"] = kwargs
        return "reporting-adapters"

    def fake_bootstrap_builder(**kwargs):
        observed["bootstrap_builder"] = kwargs
        return "bootstrap"

    composer = LongBridgeRuntimeComposer(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        account_prefix="HK",
        account_region="HK",
        strategy_profile="soxl_soxx_trend_income",
        strategy_display_name="SOXL/SOXX Semiconductor Trend Income",
        strategy_display_name_localized="SOXL/SOXX 半导体趋势收益",
        strategy_domain="us_equity",
        notify_lang="en",
        tg_token="tg-token",
        tg_chat_id="chat-id",
        managed_symbols=("SOXL", "SOXX"),
        benchmark_symbol="QQQ",
        signal_effective_after_trading_days=1,
        separator="━━━━━━━━━━━━━━━━━━",
        limit_sell_discount=0.995,
        limit_buy_premium=1.005,
        order_poll_interval_sec=1,
        order_poll_max_attempts=8,
        dry_run_only=True,
        broker_adapters=SimpleNamespace(
            build_market_data_port="market-data-port-factory",
            build_portfolio_port="portfolio-port-factory",
            build_execution_port="execution-port-factory",
        ),
        strategy_adapters=SimpleNamespace(
            calculate_strategy_indicators="strategy-indicators",
            resolve_rebalance_plan="resolve-plan",
        ),
        estimate_max_purchase_quantity_fn="estimate-max-purchase",
        fetch_order_status_fn="fetch-order-status",
        fetch_token_from_secret_fn="fetch-token",
        refresh_token_if_needed_fn="refresh-token",
        build_contexts_fn="build-contexts",
        run_id_builder=lambda: "run-001",
        event_logger="event-logger",
        report_builder="report-builder",
        report_persister="report-persister",
        translator=lambda key, **_kwargs: key,
        prefixer_builder=lambda prefix: lambda message: f"[{prefix}] {message}",
        sender_builder=lambda token, chat_id, *, with_prefix_fn: lambda message: observed.setdefault(
            "sent_message",
            (token, chat_id, with_prefix_fn(message)),
        ),
        env_reader=lambda name, default="": {
            "K_SERVICE": "longbridge-platform",
            "EXECUTION_REPORT_OUTPUT_DIR": "/tmp/runtime-reports",
            "EXECUTION_REPORT_GCS_URI": "gs://bucket/runtime-reports",
        }.get(name, default),
        sleeper=lambda _seconds: None,
        printer=lambda *_args, **_kwargs: None,
        notification_adapter_builder=fake_notification_builder,
        reporting_adapter_builder=fake_reporting_builder,
        bootstrap_builder=fake_bootstrap_builder,
    )

    assert composer.with_prefix("hello") == "[HK] hello"
    composer.send_tg_message("hello")
    assert observed["sent_message"] == ("tg-token", "chat-id", "[HK] hello")

    notification_adapters = composer.build_notification_adapters()
    reporting_adapters = composer.build_reporting_adapters()
    runtime = composer.build_rebalance_runtime()
    config = composer.build_rebalance_config()

    assert notification_adapters.notification_port == "notification-port"
    assert reporting_adapters == "reporting-adapters"
    assert observed["notification_builder"]["fetch_order_status"] == "fetch-order-status"
    assert observed["reporting_builder"]["service_name"] == "longbridge-platform"
    assert observed["reporting_builder"]["report_base_dir"] == "/tmp/runtime-reports"
    assert observed["reporting_builder"]["signal_effective_after_trading_days"] == 1
    assert observed["bootstrap_builder"]["secret_name"] == "secret-1"
    assert observed["bootstrap_builder"]["calculate_strategy_indicators_fn"] == "strategy-indicators"
    assert runtime.bootstrap == "bootstrap"
    assert runtime.resolve_rebalance_plan == "resolve-plan"
    assert runtime.market_data_port_factory == "market-data-port-factory"
    assert runtime.notifications == "notification-port"
    assert runtime.post_submit_order == "post-submit-order"
    assert config.limit_sell_discount == 0.995
    assert config.limit_buy_premium == 1.005
    assert config.strategy_display_name == "SOXL/SOXX 半导体趋势收益"
    assert config.dry_run_only is True
