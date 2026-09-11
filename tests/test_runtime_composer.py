import sys
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quant_platform_kit.common import build_runtime_target  # noqa: E402
from application import runtime_composer as runtime_composer_module
from application.runtime_composer import LongBridgeRuntimeComposer


def _build_live_command_test_composer(*, dry_run_only, dry_run_only_override=None):
    legacy_order_status = object()
    notification_calls = []
    env = {
        "LONGBRIDGE_DURABLE_EXECUTION_COMMAND_LIVE_ENABLED": "true",
        "LONGBRIDGE_EXECUTION_STATE_CLOUD_URI": "gs://test-bucket/claims",
        "LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI": "gs://test-bucket/commands",
        "LONGBRIDGE_PHYSICAL_ACCOUNT_ID": "test-account",
    }

    def notification_builder(**kwargs):
        notification_calls.append(kwargs)
        return SimpleNamespace(
            notification_port="notification-port",
            notify_issue="notify-issue",
            post_submit_order="post-submit-order",
        )

    composer = runtime_composer_module.build_runtime_composer(
        project_id="project-1",
        secret_name="secret-1",
        token_refresh_threshold_days=30,
        account_prefix="SG",
        account_region="SG",
        strategy_profile="soxl_soxx_trend_income",
        strategy_display_name="Profile",
        strategy_display_name_localized="Profile",
        strategy_domain="us_equity",
        notify_lang="en",
        tg_token=None,
        tg_chat_id=None,
        managed_symbols=(),
        benchmark_symbol="QQQ",
        signal_effective_after_trading_days=1,
        separator="-",
        limit_sell_discount=0.99,
        limit_buy_premium=1.01,
        order_poll_interval_sec=1,
        order_poll_max_attempts=1,
        safe_haven_cash_substitute_threshold_usd=0.0,
        min_order_notional_usd=1.0,
        dry_run_only=dry_run_only,
        dry_run_only_override=dry_run_only_override,
        broker_adapters=SimpleNamespace(),
        strategy_adapters=SimpleNamespace(
            strategy_runtime_config={},
            build_strategy_plugin_notification_lines=lambda _signals: (),
            build_strategy_plugin_error_notification_lines=lambda _error: (),
        ),
        estimate_max_purchase_quantity_fn=lambda *_a, **_k: 0.0,
        fetch_order_status_fn=legacy_order_status,
        fetch_token_from_secret_fn=lambda *_a, **_k: "",
        refresh_token_if_needed_fn=lambda *_a, **_k: "",
        build_contexts_fn=lambda *_a, **_k: (None, None),
        run_id_builder=lambda: "run",
        event_logger=lambda *_a, **_k: {},
        report_builder=lambda *_a, **_k: None,
        report_persister=lambda *_a, **_k: None,
        translator=lambda key, **_kwargs: key,
        runtime_target=None,
        env_reader=lambda name, default="": env.get(name, default),
        sleeper=lambda _seconds: None,
    )
    object.__setattr__(composer, "notification_adapter_builder", notification_builder)
    return composer, legacy_order_status, notification_calls


def test_runtime_composer_builds_runtime_and_config_from_local_builders(monkeypatch):
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

    def fake_cycle_sender(**kwargs):
        observed["cycle_sender"] = kwargs
        return lambda message: (
            observed.setdefault(
                "sent_message",
                (kwargs["telegram_token"], kwargs["telegram_chat_id"], message),
            ),
            False,
        )[1]

    monkeypatch.setattr(runtime_composer_module, "build_cycle_sender", fake_cycle_sender)

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
        safe_haven_cash_substitute_threshold_usd=1000.0,
        min_order_notional_usd=100.0,
        dry_run_only=True,
        runtime_target=build_runtime_target(
            platform_id="longbridge",
            strategy_profile="soxl_soxx_trend_income",
            dry_run_only=True,
            deployment_selector="HK",
            account_scope="HK",
            service_name="longbridge-platform",
        ),
        broker_adapters=SimpleNamespace(
            build_market_data_port="market-data-port-factory",
            build_portfolio_port="portfolio-port-factory",
            build_execution_port="execution-port-factory",
        ),
        strategy_adapters=SimpleNamespace(
            calculate_strategy_indicators="strategy-indicators",
            resolve_rebalance_plan="resolve-plan",
            build_strategy_plugin_notification_lines=lambda signals: tuple(signals),
            build_strategy_plugin_error_notification_lines=lambda error: (f"plugin-error:{error}",) if error else (),
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
    sent = composer.send_tg_message("hello")
    assert sent is False
    assert observed["sent_message"] == ("tg-token", "chat-id", "[HK] hello")

    notification_adapters = composer.build_notification_adapters()
    reporting_adapters = composer.build_reporting_adapters()
    runtime = composer.build_rebalance_runtime()
    silent_runtime = composer.build_rebalance_runtime(silent_cycle_notifications=True)
    config = composer.build_rebalance_config(
        strategy_plugin_signals=("plugin-line",),
        strategy_plugin_error="bad config",
    )

    assert notification_adapters.notification_port == "notification-port"
    assert reporting_adapters == "reporting-adapters"
    assert observed["notification_builder"]["fetch_order_status"] == "fetch-order-status"
    assert observed["reporting_builder"]["runtime_assembly"].service_name == "longbridge-platform"
    assert observed["reporting_builder"]["runtime_assembly"].project_id == "project-1"
    assert observed["reporting_builder"]["report_base_dir"] == "/tmp/runtime-reports"
    assert observed["reporting_builder"]["signal_effective_after_trading_days"] == 1
    assert observed["reporting_builder"]["runtime_assembly"].runtime_target.platform_id == "longbridge"
    assert observed["reporting_builder"]["runtime_assembly"].runtime_target.strategy_profile == "soxl_soxx_trend_income"
    assert observed["reporting_builder"]["runtime_assembly"].runtime_target.execution_mode == "paper"
    assert "plugin-line" in config.extra_notification_lines
    assert "plugin-error:bad config" in config.extra_notification_lines
    assert observed["bootstrap_builder"]["secret_name"] == "secret-1"
    assert observed["bootstrap_builder"]["calculate_strategy_indicators_fn"] == "strategy-indicators"
    assert runtime.bootstrap == "bootstrap"
    assert runtime.resolve_rebalance_plan == "resolve-plan"
    assert runtime.market_data_port_factory == "market-data-port-factory"
    assert runtime.notifications == "notification-port"
    assert runtime.fetch_order_status == "fetch-order-status"
    assert runtime.account_identity_observer is not None
    silent_runtime.notifications.send_text("precheck heartbeat")
    assert observed["sent_message"] == ("tg-token", "chat-id", "[HK] hello")
    assert runtime.post_submit_order == "post-submit-order"
    assert config.limit_sell_discount == 0.995
    assert config.limit_buy_premium == 1.005
    assert config.strategy_display_name == "SOXL/SOXX 半导体趋势收益"
    assert config.dry_run_only is True
    assert config.safe_haven_cash_substitute_threshold_usd == 1000.0
    assert config.min_order_notional_usd == 100.0
    assert config.notify_no_trade_cycles is False
    assert config.execution_dedup_enabled is True
    assert config.execution_state_account_scope == "HK"
    assert config.execution_state_store.cloud_prefix_uri == "gs://bucket/runtime-reports"
    assert config.account_identity_policy.is_configured is False


def test_runtime_composer_parks_live_without_durable_execution_claim_backend():
    class MinimalComposer(LongBridgeRuntimeComposer):
        pass

    fields = {
        "project_id": "project-1",
        "secret_name": "secret-1",
        "token_refresh_threshold_days": 30,
        "account_prefix": "HK",
        "account_region": "HK",
        "strategy_profile": "profile",
        "strategy_display_name": "Profile",
        "strategy_display_name_localized": "Profile",
        "strategy_domain": "us_equity",
        "notify_lang": "en",
        "tg_token": None,
        "tg_chat_id": None,
        "managed_symbols": (),
        "benchmark_symbol": "QQQ",
        "signal_effective_after_trading_days": 1,
        "separator": "-",
        "limit_sell_discount": 0.99,
        "limit_buy_premium": 1.01,
        "order_poll_interval_sec": 1,
        "order_poll_max_attempts": 1,
        "safe_haven_cash_substitute_threshold_usd": 0.0,
        "min_order_notional_usd": 1.0,
        "dry_run_only": False,
        "broker_adapters": SimpleNamespace(),
        "strategy_adapters": SimpleNamespace(
            translator=lambda key, **_kwargs: key,
            build_strategy_plugin_notification_lines=lambda _signals: (),
            build_strategy_plugin_error_notification_lines=lambda _error: (),
        ),
        "estimate_max_purchase_quantity_fn": lambda *_a, **_k: 0.0,
        "fetch_order_status_fn": lambda *_a, **_k: None,
        "fetch_token_from_secret_fn": lambda *_a, **_k: "",
        "refresh_token_if_needed_fn": lambda *_a, **_k: "",
        "build_contexts_fn": lambda *_a, **_k: (None, None),
        "run_id_builder": lambda: "run",
        "event_logger": lambda *_a, **_k: {},
        "report_builder": lambda *_a, **_k: None,
        "report_persister": lambda *_a, **_k: None,
        "translator": lambda key, **_kwargs: key,
        "env_reader": lambda _name, default="": default,
        "sleeper": lambda _seconds: None,
    }
    composer = MinimalComposer(**fields)

    try:
        composer.build_rebalance_config()
    except RuntimeError as exc:
        assert "requires a gs:// execution state URI" in str(exc)
    else:
        raise AssertionError("live execution must fail closed without durable atomic claims")

    dry = replace(composer, dry_run_only=True)
    dry_config = dry.build_rebalance_config()
    assert dry_config.dry_run_only is True
    assert not str(dry_config.execution_state_store.cloud_prefix_uri or "").startswith("gs://")


def test_live_target_validation_override_suppresses_live_commands_for_probe_and_precheck():
    composer, legacy_order_status, notification_calls = _build_live_command_test_composer(
        dry_run_only=False,
        dry_run_only_override=True,
    )

    composer.build_notification_adapters()
    config = composer.build_rebalance_config()

    assert composer.dry_run_only is True
    assert notification_calls[0]["fetch_order_status"] is legacy_order_status
    assert config.durable_execution_command_live_enabled is False
    assert config.durable_live_execution_session_authorized is False


def test_live_target_without_override_keeps_durable_execution_conditions():
    composer, legacy_order_status, notification_calls = _build_live_command_test_composer(
        dry_run_only=False,
    )

    composer.build_notification_adapters()
    config = composer.build_rebalance_config(live_execution_session_authorized=True)

    assert notification_calls[0]["fetch_order_status"] is not legacy_order_status
    assert config.durable_execution_command_live_enabled is True
    assert config.durable_live_execution_session_authorized is True


def test_real_dry_run_target_still_rejects_live_command_flag():
    composer, _legacy_order_status, _notification_calls = _build_live_command_test_composer(
        dry_run_only=True,
    )

    try:
        composer.build_notification_adapters()
    except RuntimeError as exc:
        assert "durable live execution command is live-only" in str(exc)
    else:
        raise AssertionError("real dry-run target must reject the live command flag")


def test_suppression_cannot_disable_commands_on_an_effective_live_composer():
    composer, legacy_order_status, notification_calls = _build_live_command_test_composer(
        dry_run_only=False,
        dry_run_only_override=True,
    )
    object.__setattr__(composer, "dry_run_only", False)

    composer.build_notification_adapters()

    assert notification_calls[0]["fetch_order_status"] is not legacy_order_status


def test_validation_override_still_runs_live_command_flag_validation(monkeypatch):
    composer, _legacy_order_status, _notification_calls = _build_live_command_test_composer(
        dry_run_only=False,
        dry_run_only_override=True,
    )

    def reject_bad_flag(**_kwargs):
        raise RuntimeError("invalid durable live execution command flag")

    monkeypatch.setattr(
        runtime_composer_module,
        "resolve_live_execution_command_enabled",
        reject_bad_flag,
    )

    try:
        composer.build_notification_adapters()
    except RuntimeError as exc:
        assert "invalid durable live execution command flag" in str(exc)
    else:
        raise AssertionError("validation override must not bypass flag validation")
