import importlib
import os
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLATFORM_KIT_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(PLATFORM_KIT_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_KIT_SRC))

from quant_platform_kit.common.runtime_target import build_runtime_target


@contextmanager
def install_stub_modules(*, notify_lang="en"):
    flask_module = types.ModuleType("flask")
    request = types.SimpleNamespace(method="GET")

    class Flask:
        def __init__(self, _name):
            self._routes = {}

        def route(self, path, methods=None):
            def decorator(func):
                self._routes[(path, tuple(methods or []))] = func
                return func

            return decorator

        def test_request_context(self, *_args, **_kwargs):
            method = _kwargs.get("method", "GET")

            class _Context:
                def __enter__(self_inner):
                    self_inner.previous_method = request.method
                    request.method = method
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    request.method = self_inner.previous_method
                    return False

            return _Context()

        def run(self, *args, **kwargs):
            return None

    flask_module.Flask = Flask
    flask_module.request = request

    requests_module = types.ModuleType("requests")
    requests_module.post = lambda *args, **kwargs: None

    cloud_run_module = types.ModuleType("entrypoints.cloud_run")
    cloud_run_module.is_market_open_now = lambda **_kwargs: True

    runtime_config_support_module = types.ModuleType("runtime_config_support")
    runtime_config_support_module.load_platform_runtime_settings = lambda **_kwargs: types.SimpleNamespace(
        project_id=None,
        secret_name="longport_token_hk",
        account_prefix="HK",
        strategy_profile="soxl_soxx_trend_income",
        strategy_display_name="SOXL/SOXX Semiconductor Trend Income",
        strategy_domain="us_equity",
        account_region="HK",
        market="HK",
        market_calendar="XHKG",
        market_timezone="Asia/Hong_Kong",
        symbol_suffix=".HK",
        trading_currency="HKD",
        notify_lang=notify_lang,
        tg_token=None,
        tg_chat_id="shared-chat-id",
        dry_run_only=False,
        notification_channel="telegram",
        wecom_webhook_url=None,
        dingtalk_webhook_url=None,
        feishu_webhook_url=None,
        serverchan_webhook_url=None,
        strategy_metadata=None,
        strategy_plugin_alert_email_recipients=(),
        strategy_plugin_alert_email_sender_email=None,
        strategy_plugin_alert_email_sender_password=None,
        strategy_plugin_alert_sms_recipients=(),
        strategy_plugin_alert_sms_account_id=None,
        strategy_plugin_alert_sms_auth_token=None,
        runtime_target=build_runtime_target(
            platform_id="longbridge",
            strategy_profile="soxl_soxx_trend_income",
            dry_run_only=False,
            deployment_selector="HK",
            account_selector=("HK",),
            account_scope="HK",
            service_name="longbridge-quant-hk-service",
        ),
    )

    qpk_longbridge_module = types.ModuleType("quant_platform_kit.longbridge")
    qpk_longbridge_module.__path__ = []
    qpk_longbridge_module.build_contexts = lambda *args, **kwargs: ("quote-context", "trade-context")
    qpk_longbridge_module.calculate_rotation_indicators = lambda *args, **kwargs: {}
    qpk_longbridge_module.estimate_max_purchase_quantity = lambda *args, **kwargs: 0
    qpk_longbridge_module.fetch_last_price = lambda *args, **kwargs: 0.0
    qpk_longbridge_module.fetch_order_status = lambda *args, **kwargs: None
    qpk_longbridge_module.fetch_strategy_account_state = lambda *args, **kwargs: {}
    qpk_longbridge_module.fetch_token_from_secret = lambda *args, **kwargs: "token"
    qpk_longbridge_module.refresh_token_if_needed = lambda *args, **kwargs: "token"
    qpk_longbridge_module.submit_order = lambda *args, **kwargs: None
    qpk_longbridge_market_data_module = types.ModuleType(
        "quant_platform_kit.longbridge.market_data"
    )
    qpk_longbridge_market_data_module.fetch_lot_sizes = (
        lambda *_args, **_kwargs: {}
    )

    google_module = types.ModuleType("google")
    google_module.__path__ = []

    google_auth_module = types.ModuleType("google.auth")
    google_auth_module.default = lambda *args, **kwargs: (None, None)
    google_auth_transport_module = types.ModuleType("google.auth.transport")
    google_auth_transport_requests_module = types.ModuleType("google.auth.transport.requests")
    google_auth_transport_requests_module.Request = type("Request", (), {})
    google_oauth2_module = types.ModuleType("google.oauth2")
    google_oauth2_id_token_module = types.ModuleType("google.oauth2.id_token")
    google_oauth2_id_token_module.fetch_id_token = lambda *_args, **_kwargs: "id-token"

    google_cloud_module = types.ModuleType("google.cloud")
    google_cloud_module.__path__ = []
    google_secretmanager_module = types.ModuleType("google.cloud.secretmanager_v1")

    google_module.auth = google_auth_module
    google_auth_module.transport = google_auth_transport_module
    google_auth_transport_module.requests = google_auth_transport_requests_module
    google_oauth2_module.id_token = google_oauth2_id_token_module
    google_cloud_module.secretmanager_v1 = google_secretmanager_module

    pandas_module = types.ModuleType("pandas")
    pandas_module.Timestamp = lambda value=None: value

    pandas_market_calendars = types.ModuleType("pandas_market_calendars")

    strategy_runtime_module = types.ModuleType("strategy_runtime")
    strategy_runtime_module.load_strategy_runtime = lambda *_args, **_kwargs: types.SimpleNamespace(
        merged_runtime_config={"trend_ma_window": 150},
        managed_symbols=("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        runtime_adapter=types.SimpleNamespace(
            available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"}),
            runtime_policy=types.SimpleNamespace(signal_effective_after_trading_days=1),
        ),
        evaluate=lambda **_kwargs: None,
    )

    longport_module = types.ModuleType("longport")
    longport_module.__path__ = []
    openapi_module = types.ModuleType("longport.openapi")
    for name in (
        "Config",
        "QuoteContext",
        "TradeContext",
        "Period",
        "AdjustType",
        "OrderType",
        "OrderSide",
        "TimeInForceType",
        "OrderStatus",
    ):
        setattr(openapi_module, name, type(name, (), {}))

    us_equity_strategies_module = types.ModuleType("us_equity_strategies")
    us_equity_strategies_module.__path__ = []
    cash_only_equity_module = types.ModuleType("us_equity_strategies.cash_only_equity")
    cash_only_equity_module.normalize_account_state_from_snapshot = (
        lambda snapshot, **_kwargs: snapshot
    )
    catalog_module = types.ModuleType("us_equity_strategies.catalog")
    catalog_module.resolve_canonical_profile = lambda profile: profile

    strategy_registry_module = types.ModuleType("strategy_registry")
    strategy_registry_module.LONGBRIDGE_PLATFORM = "longbridge"
    strategy_registry_module.PLATFORM_CAPABILITY_MATRIX = types.SimpleNamespace(
        supported_capabilities=frozenset()
    )
    strategy_registry_module.STRATEGY_CATALOG = types.SimpleNamespace(definitions={})
    strategy_registry_module.resolve_strategy_definition = lambda profile, **_kwargs: types.SimpleNamespace(
        profile=profile
    )

    modules = {
        "flask": flask_module,
        "requests": requests_module,
        "entrypoints.cloud_run": cloud_run_module,
        "runtime_config_support": runtime_config_support_module,
        "quant_platform_kit.longbridge": qpk_longbridge_module,
        "quant_platform_kit.longbridge.market_data": qpk_longbridge_market_data_module,
        "google": google_module,
        "google.auth": google_auth_module,
        "google.auth.transport": google_auth_transport_module,
        "google.auth.transport.requests": google_auth_transport_requests_module,
        "google.oauth2": google_oauth2_module,
        "google.oauth2.id_token": google_oauth2_id_token_module,
        "google.cloud": google_cloud_module,
        "google.cloud.secretmanager_v1": google_secretmanager_module,
        "pandas": pandas_module,
        "pandas_market_calendars": pandas_market_calendars,
        "strategy_runtime": strategy_runtime_module,
        "longport": longport_module,
        "longport.openapi": openapi_module,
        "us_equity_strategies": us_equity_strategies_module,
        "us_equity_strategies.cash_only_equity": cash_only_equity_module,
        "us_equity_strategies.catalog": catalog_module,
        "strategy_registry": strategy_registry_module,
    }
    original = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    try:
        yield
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def load_module(*, notify_lang="en"):
    with install_stub_modules(notify_lang=notify_lang):
        with patch.dict(
            os.environ,
            {
                "GLOBAL_TELEGRAM_CHAT_ID": "shared-chat-id",
            },
            clear=False,
        ):
            sys.modules.pop("main", None)
            return importlib.import_module("main")


class RequestHandlingTests(unittest.TestCase):
    def test_cloud_run_route_contracts_are_registered(self):
        module = load_module()

        self.assertIs(module.app._routes[("/run", ("POST",))], module.handle_trigger)
        self.assertIs(
            module.app._routes[("/backfill", ("POST", "GET"))],
            module.handle_backfill,
        )
        self.assertIs(
            module.app._routes[("/dry-run", ("POST", "GET"))],
            module.handle_dry_run,
        )
        self.assertIs(
            module.app._routes[("/probe", ("POST",))],
            module.handle_probe,
        )
        self.assertIs(
            module.app._routes[("/paper-command-consumer", ("POST",))],
            module.handle_paper_execution_command_consumer,
        )
        self.assertIs(
            module.app._routes[("/monitor-dispatch", ("POST", "GET"))],
            module.handle_monitor_dispatch,
        )
        self.assertIs(module.app._routes[("/health", ("GET",))], module.health)

    def test_handle_monitor_dispatch_post_dispatches_due_targets(self):
        module = load_module()
        observed = {}
        monkeypatch = unittest.mock.patch.object

        def fake_dispatch(targets):
            observed["targets"] = targets
            return {"ok": True, "dispatches_due": 0}

        with monkeypatch(module, "request_method", lambda: "POST"), \
            monkeypatch(module, "load_monitor_targets", lambda: [{"service_name": "longbridge-quant-sg-service"}]), \
            monkeypatch(module, "dispatch_due_monitors", fake_dispatch):
            body, status, headers = module.handle_monitor_dispatch()

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn('"dispatches_due": 0', body)
        self.assertEqual(observed["targets"][0]["service_name"], "longbridge-quant-sg-service")

    def test_health_route_returns_ok(self):
        module = load_module()

        with module.app.test_request_context("/health", method="GET"):
            body, status = module.health()

        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")

    def test_handle_trigger_runs_strategy(self):
        module = load_module()
        observed = {"called": False}

        def fake_run_strategy():
            observed["called"] = True

        module.run_strategy = fake_run_strategy

        with patch.dict(
            os.environ,
            {
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_BOT_TOKEN": "plugin-token",
                "STRATEGY_PLUGIN_ALERT_TELEGRAM_CHAT_IDS": "plugin-chat",
            },
            clear=False,
        ):
            with module.app.test_request_context("/run", method="POST"):
                body, status = module.handle_trigger()

        self.assertEqual(status, 200)
        self.assertEqual(body, "OK",)
        self.assertTrue(observed["called"])

    def test_handle_trigger_returns_500_when_strategy_reports_failure(self):
        module = load_module()
        module.run_strategy = lambda: False

        with module.app.test_request_context("/run", method="POST"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 500)
        self.assertEqual(body, "Error")

    def test_handle_trigger_runtime_error_fallback_sends_telegram(self):
        module = load_module()
        observed = {"payloads": []}

        class FakeResponse:
            status_code = 200

        def fake_post(_url, *, json, timeout):
            observed["payloads"].append((json, timeout))
            return FakeResponse()

        module.TG_TOKEN = "token-1"
        module.TG_CHAT_ID = "chat-1"
        module.requests.post = fake_post
        module.run_strategy = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

        with module.app.test_request_context("/run", method="POST"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 500)
        self.assertEqual(body, "Error")
        self.assertEqual(len(observed["payloads"]), 1)
        self.assertEqual(observed["payloads"][0][0]["chat_id"], "chat-1")
        self.assertIn("LongBridge strategy run failed", observed["payloads"][0][0]["text"])
        self.assertIn("RuntimeError: boom", observed["payloads"][0][0]["text"])

    def test_handle_trigger_runtime_error_fallback_uses_chinese_copy(self):
        module = load_module(notify_lang="zh")
        observed = {"payloads": []}

        class FakeResponse:
            status_code = 200

        def fake_post(_url, *, json, timeout):
            observed["payloads"].append((json, timeout))
            return FakeResponse()

        module.TG_TOKEN = "token-1"
        module.TG_CHAT_ID = "chat-1"
        module.requests.post = fake_post
        module.run_strategy = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

        with module.app.test_request_context("/run", method="POST"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 500)
        self.assertEqual(body, "Error")
        text = observed["payloads"][0][0]["text"]
        self.assertIn("LongBridge 策略运行失败", text)
        self.assertIn("服务:", text)
        self.assertIn("错误: RuntimeError: boom", text)

    def test_handle_trigger_rejects_get_without_running_strategy(self):
        module = load_module()
        observed = {"called": False}

        def fake_run_strategy():
            observed["called"] = True

        module.run_strategy = fake_run_strategy

        with module.app.test_request_context("/run", method="GET"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 405)
        self.assertEqual(body, "Method Not Allowed")
        self.assertFalse(observed["called"])

    def test_handle_backfill_forces_strategy_run(self):
        module = load_module()
        observed = {"force_run": None, "validation_only": None}
        def fake_run_strategy(*, force_run=False, validation_only=False, validation_label="backfill"):
            observed["force_run"] = force_run
            observed["validation_only"] = validation_only
            observed["validation_label"] = validation_label

        module.run_strategy = fake_run_strategy

        with module.app.test_request_context("/backfill", method="POST"):
            body, status = module.handle_backfill()

        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")
        self.assertTrue(observed["force_run"])
        self.assertTrue(observed["validation_only"])
    def test_handle_dry_run_forces_strategy_dry_run(self):
        module = load_module()
        observed = {"force_run": None, "validation_only": None}

        def fake_run_strategy(*, force_run=False, validation_only=False, validation_label="backfill"):
            observed["force_run"] = force_run
            observed["validation_only"] = validation_only
            observed["validation_label"] = validation_label

        module.run_strategy = fake_run_strategy

        with module.app.test_request_context("/dry-run", method="POST"):
            body, status = module.handle_dry_run()

        self.assertEqual(status, 200)
        self.assertEqual(body, "Dry Run OK")
        self.assertTrue(observed["force_run"])
        self.assertTrue(observed["validation_only"])
        self.assertEqual(observed["validation_label"], "dry_run")

    def test_paper_command_consumer_rejects_any_non_paper_runtime_before_building_composer(self):
        module = load_module()
        module.build_composer = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe runtime must fail before building broker contexts")
        )

        with self.assertRaisesRegex(RuntimeError, "execution_mode=paper"):
            module.run_paper_execution_command_consumer()

    def test_paper_command_consumer_uses_read_only_contexts_without_an_execution_port(self):
        module = load_module()
        observed = {"events": []}
        module.RUNTIME_SETTINGS = types.SimpleNamespace(
            dry_run_only=True,
            runtime_target=build_runtime_target(
                platform_id="longbridge",
                strategy_profile="soxl_soxx_trend_income",
                dry_run_only=True,
                deployment_selector="paper-command-verify",
                account_scope="paper-command-verify",
                service_name="longbridge-quant-paper-command-verify-service",
            ),
        )

        class FakePortfolioPort:
            def get_portfolio_snapshot(self):
                observed["portfolio_read"] = True
                return "portfolio-snapshot"

        class FakeBrokerAdapters:
            def build_portfolio_port(self, quote_context, trade_context):
                observed["contexts"] = (quote_context, trade_context)
                return FakePortfolioPort()

            def build_market_data_port(self, quote_context):
                observed["market_data_context"] = quote_context
                return "market-data-port"

        class FakeComposer:
            broker_adapters = FakeBrokerAdapters()

            def build_rebalance_config(self):
                return types.SimpleNamespace(
                    execution_command_store="command-store",
                    runtime_release_receipt={"attestation_state": "self_attested"},
                    expected_strategy_release={"release_id": "release-1"},
                    execution_state_account_scope="paper-command-verify",
                    strategy_profile=module.STRATEGY_PROFILE,
                )

            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda _context, event, **fields: observed["events"].append((event, fields)),
                    persist_execution_report=lambda report: observed.setdefault("report", dict(report)) or "/tmp/report.json",
                )

            def build_read_only_broker_contexts(self):
                observed["read_only_contexts_called"] = True
                return "quote-context", "trade-context"

        def fake_consume(**kwargs):
            observed["consumer"] = kwargs
            return {"status": "ok", "commands": []}

        module.build_composer = lambda **_kwargs: FakeComposer()
        module.resolve_paper_execution_command_consumer_enabled = lambda **_kwargs: True
        module.consume_due_paper_execution_commands = fake_consume
        module.finalize_runtime_report = lambda report, **kwargs: report.update(kwargs)
        module._paper_command_consumer_session_date = lambda: "2026-08-25"

        with module.app.test_request_context("/paper-command-consumer", method="POST"):
            body, status = module.handle_paper_execution_command_consumer()

        self.assertEqual(status, 200)
        self.assertEqual(body, "Paper command consumer OK")
        self.assertTrue(observed["read_only_contexts_called"])
        self.assertTrue(observed["portfolio_read"])
        self.assertEqual(observed["contexts"], ("quote-context", "trade-context"))
        self.assertEqual(observed["market_data_context"], "quote-context")
        self.assertEqual(observed["consumer"]["store"], "command-store")
        self.assertEqual(observed["consumer"]["portfolio"], "portfolio-snapshot")
        self.assertEqual(observed["consumer"]["market_data_port"], "market-data-port")
        self.assertEqual(
            observed["consumer"]["expected_command_binding"],
            {
                "platform": "longbridge",
                "account_scope": "paper-command-verify",
                "strategy_profile": module.STRATEGY_PROFILE,
            },
        )

    def test_handle_probe_checks_account_snapshot_without_success_notification(self):
        module = load_module()
        observed = {"override": None, "events": [], "notifications": []}
        snapshot = types.SimpleNamespace(
            buying_power=123.0,
            total_equity=456.0,
            positions=(types.SimpleNamespace(symbol="SOXL"),),
        )

        class FakePortfolioPort:
            def get_portfolio_snapshot(self):
                observed["snapshot_called"] = True
                return snapshot

        class FakeRuntime:
            def __init__(self):
                self.bootstrap = lambda: ("quote-context", "trade-context", {"trend": "ok"})
                self.portfolio_port_factory = lambda quote_context, trade_context: FakePortfolioPort()

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda context, event, **fields: observed["events"].append((event, fields)),
                    persist_execution_report=lambda report: observed.setdefault("report", dict(report)) or "/tmp/report.json",
                )

            def build_rebalance_runtime(self, *, silent_cycle_notifications=False):
                observed["silent_cycle_notifications"] = silent_cycle_notifications
                return FakeRuntime()

            def build_notification_adapters(self):
                raise AssertionError("probe success should stay silent")

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                raise AssertionError("health probe should not load strategy plugins")

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                raise AssertionError("health probe should not attach strategy plugin reports")

        module.build_composer = lambda *, dry_run_only_override=None: observed.__setitem__("override", dry_run_only_override) or FakeComposer()

        with module.app.test_request_context("/probe", method="POST"):
            body, status = module.handle_probe()

        self.assertEqual(status, 200)
        self.assertEqual(body, "Probe OK")
        self.assertTrue(observed["override"])
        self.assertTrue(observed["silent_cycle_notifications"])
        self.assertTrue(observed["snapshot_called"])
        self.assertEqual(
            [event for event, _fields in observed["events"]],
            ["health_probe_received", "health_probe_completed"],
        )
        self.assertEqual(observed["report"]["status"], "ok")
        self.assertEqual(observed["report"]["summary"]["buying_power"], 123.0)
        self.assertEqual(observed["report"]["summary"]["total_equity"], 456.0)
        self.assertEqual(observed["report"]["summary"]["positions_count"], 1)

    def test_handle_probe_rejects_get_without_running_broker_probe(self):
        module = load_module()
        observed = {"called": False}

        def fake_run_probe():
            observed["called"] = True

        module.run_probe = fake_run_probe

        with module.app.test_request_context("/probe", method="GET"):
            body, status = module.handle_probe()

        self.assertEqual(status, 405)
        self.assertEqual(body, "Method Not Allowed")
        self.assertFalse(observed["called"])

    def test_handle_probe_failure_sends_notification(self):
        module = load_module()
        observed = {"events": [], "notifications": []}

        class FakeRuntime:
            def bootstrap(self):
                raise RuntimeError("probe failed " + "x" * 5000)

        class FakeNotifications:
            def publish_cycle_notification(self, **kwargs):
                observed["notifications"].append(kwargs)

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda context, event, **fields: observed["events"].append((event, fields)),
                    persist_execution_report=lambda report: observed.setdefault("report", dict(report)) or "/tmp/report.json",
                )

            def build_rebalance_runtime(self, *, silent_cycle_notifications=False):
                return FakeRuntime()

            def build_notification_adapters(self):
                return FakeNotifications()

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                raise AssertionError("health probe should not load strategy plugins")

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                raise AssertionError("health probe should not attach strategy plugin reports")

        module.build_composer = lambda *, dry_run_only_override=None: FakeComposer()

        with module.app.test_request_context("/probe", method="POST"):
            body, status = module.handle_probe()

        self.assertEqual(status, 500)
        self.assertEqual(body, "Error")
        self.assertEqual(observed["report"]["status"], "error")
        self.assertEqual(observed["report"]["errors"][0]["stage"], "health_probe")
        self.assertEqual(
            [event for event, _fields in observed["events"]],
            ["health_probe_received", "health_probe_failed"],
        )
        self.assertEqual(len(observed["notifications"]), 1)
        notification = observed["notifications"][0]
        self.assertIn("probe failed", notification["detailed_text"])
        self.assertIn("Traceback", notification["detailed_text"])
        self.assertNotIn("Traceback", notification["compact_text"])
        self.assertIn("RuntimeError: probe failed", notification["compact_text"])
        self.assertLessEqual(len(notification["compact_text"]), 3500)

    def test_run_strategy_emits_structured_runtime_events(self):
        module = load_module()
        observed = []

        module.build_run_id = lambda: "run-001"
        module.emit_runtime_log = lambda context, event, **fields: observed.append((context.run_id, event, fields))
        module.is_market_open_now = lambda **_kwargs: True
        module.run_rebalance_cycle = lambda **_kwargs: None

        module.run_strategy()

        self.assertEqual(
            [event for _run_id, event, _fields in observed],
            ["strategy_cycle_started", "strategy_cycle_completed"],
        )
        self.assertTrue(all(run_id == "run-001" for run_id, _event, _fields in observed))

    def test_run_strategy_market_hours_tuple_without_error_does_not_warn(self):
        module = load_module()
        observed = []

        module.emit_runtime_log = (
            lambda context, event, **fields: observed.append((event, fields))
        )
        module.is_market_open_now = lambda **_kwargs: (True, None)
        module.run_rebalance_cycle = lambda **_kwargs: None

        self.assertTrue(module.run_strategy())
        self.assertNotIn(
            "market_hours_check_failed",
            [event for event, _fields in observed],
        )

    def test_run_strategy_error_notification_is_compact_and_bounded(self):
        module = load_module()
        observed = {}

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (
                        types.SimpleNamespace(run_id="run-001"),
                        {"status": "pending"},
                    ),
                    log_event=lambda *args, **kwargs: None,
                    persist_execution_report=lambda report: (
                        observed.update({"report": report})
                        or types.SimpleNamespace(local_path="/tmp/report.json")
                    ),
                )

            def build_notification_adapters(self, *, delivery_events=None):
                def publish_cycle_notification(**kwargs):
                    observed.update(kwargs)
                    if delivery_events is not None:
                        delivery_events.append(
                            {
                                "sink": "telegram",
                                "delivery_status": "sent",
                                "transport_acknowledged": True,
                                "compact_text_sha256": "a" * 64,
                                "compact_text_length": len(kwargs["compact_text"]),
                            }
                        )
                    return True

                return types.SimpleNamespace(
                    publish_cycle_notification=publish_cycle_notification
                )

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                return (), None

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                return None

            def with_prefix(self, message):
                return message

            def build_rebalance_runtime(self, **_kwargs):
                return types.SimpleNamespace()

            def build_rebalance_config(self, **_kwargs):
                return types.SimpleNamespace()

        module.build_composer = lambda *, dry_run_only_override=None: FakeComposer()
        module.is_market_open_now = lambda **_kwargs: True
        module.run_rebalance_cycle = lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("x" * 5000)
        )

        self.assertFalse(module.run_strategy())
        self.assertIn("Traceback", observed["detailed_text"])
        self.assertNotIn("Traceback", observed["compact_text"])
        self.assertIn("RuntimeError:", observed["compact_text"])
        self.assertLessEqual(len(observed["compact_text"]), 3500)
        delivery_summary = observed["report"]["summary"][
            "notification_delivery_summary"
        ]
        self.assertEqual(delivery_summary["sent_count"], 1)
        self.assertTrue(delivery_summary["all_acknowledged"])

    def test_run_strategy_sends_escalated_strategy_plugin_alert(self):
        module = load_module()
        signal = types.SimpleNamespace(
            plugin="crisis_response_shadow",
            effective_mode="shadow",
            canonical_route="true_crisis",
            suggested_action="defend",
            would_trade_if_enabled=True,
            as_of="2026-05-24",
        )
        observed = {"email_alerts": [], "sms_alerts": []}

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda *args, **kwargs: None,
                    persist_execution_report=lambda report: types.SimpleNamespace(local_path="/tmp/report.json"),
                )

            def build_notification_adapters(self):
                return types.SimpleNamespace(publish_cycle_notification=lambda **_kwargs: None)

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                return (signal,), None

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                return None

            def with_prefix(self, message):
                return message

            def build_rebalance_runtime(self, *, silent_cycle_notifications=False):
                return types.SimpleNamespace()

            def build_rebalance_config(
                self,
                *,
                strategy_plugin_signals=(),
                strategy_plugin_error=None,
                notification_title_key="",
                cash_only_execution=True,
            ):
                return types.SimpleNamespace()

        module.build_composer = lambda *, dry_run_only_override=None: FakeComposer()
        module.is_market_open_now = lambda **_kwargs: True
        module.run_rebalance_cycle = lambda **_kwargs: None

        observed["alerts"] = []

        def fake_dispatch(signals, **kwargs):
            observed["alerts"].append((tuple(signals), kwargs))
            return types.SimpleNamespace(attach_to_report=lambda _report: None)

        module.dispatch_strategy_plugin_alerts = fake_dispatch

        module.run_strategy()

        self.assertEqual(observed["alerts"][0][0], (signal,))
        self.assertIn("longbridge", observed["alerts"][0][1]["context_label"])
        self.assertIs(observed["alerts"][0][1]["notification_settings"], module.RUNTIME_SETTINGS)
        self.assertIsNotNone(observed["alerts"][0][1]["state_settings"])

    def test_run_strategy_force_runs_when_market_closed(self):
        module = load_module()
        observed = []

        module.build_run_id = lambda: "run-001"
        module.emit_runtime_log = lambda context, event, **fields: observed.append((context.run_id, event, fields))
        module.is_market_open_now = lambda **_kwargs: False
        module.run_rebalance_cycle = lambda **_kwargs: observed.append(("rebalance", "called", {}))

        module.run_strategy(force_run=True)

        events = [event for _run_id, event, _fields in observed]
        self.assertIn("market_hours_bypassed", events)
        self.assertIn("strategy_cycle_completed", events)
        self.assertIn(("rebalance", "called", {}), observed)

    def test_run_strategy_validation_only_uses_dry_run_composer(self):
        module = load_module()
        observed = {"override": None, "notification_title_key": None}

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda *args, **kwargs: None,
                    persist_execution_report=lambda report: types.SimpleNamespace(local_path="/tmp/report.json"),
                )

            def build_notification_adapters(self):
                return types.SimpleNamespace(publish_cycle_notification=lambda **_kwargs: None)

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                return (), None

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                return None

            def with_prefix(self, message):
                return message

            def build_rebalance_runtime(self, *, silent_cycle_notifications=False):
                observed["silent_cycle_notifications"] = silent_cycle_notifications
                return types.SimpleNamespace()

            def build_rebalance_config(
                self,
                *,
                strategy_plugin_signals=(),
                strategy_plugin_error=None,
                notification_title_key="",
                cash_only_execution=True,
            ):
                observed["notification_title_key"] = notification_title_key
                return types.SimpleNamespace()

        module.build_composer = lambda *, dry_run_only_override=None: observed.__setitem__("override", dry_run_only_override) or FakeComposer()
        module.is_market_open_now = lambda **_kwargs: False
        module.run_rebalance_cycle = lambda **_kwargs: None
        module.persist_execution_report = lambda report: types.SimpleNamespace(local_path="/tmp/report.json")
        module.build_run_id = lambda: "run-001"

        module.run_strategy(force_run=True, validation_only=True)

        self.assertTrue(observed["override"])
        self.assertTrue(observed["silent_cycle_notifications"])
        self.assertEqual(observed["notification_title_key"], "")

    def test_run_strategy_dry_run_sets_dry_run_notification_title(self):
        module = load_module()
        observed = {"notification_title_key": None}

        class FakeComposer:
            def build_reporting_adapters(self):
                return types.SimpleNamespace(
                    start_run=lambda: (types.SimpleNamespace(run_id="run-001"), {"status": "pending"}),
                    log_event=lambda *args, **kwargs: None,
                    persist_execution_report=lambda report: types.SimpleNamespace(local_path="/tmp/report.json"),
                )

            def build_notification_adapters(self):
                return types.SimpleNamespace(publish_cycle_notification=lambda **_kwargs: None)

            def load_strategy_plugin_signals(self, *_args, **_kwargs):
                return (), None

            def attach_strategy_plugin_report(self, *_args, **_kwargs):
                return None

            def with_prefix(self, message):
                return message

            def build_rebalance_runtime(self, *, silent_cycle_notifications=False):
                return types.SimpleNamespace()

            def build_rebalance_config(
                self,
                *,
                strategy_plugin_signals=(),
                strategy_plugin_error=None,
                notification_title_key="",
                cash_only_execution=True,
            ):
                observed["notification_title_key"] = notification_title_key
                return types.SimpleNamespace()

        module.build_composer = lambda *, dry_run_only_override=None: FakeComposer()
        module.is_market_open_now = lambda **_kwargs: False
        module.run_rebalance_cycle = lambda **_kwargs: None

        module.run_strategy(force_run=True, validation_only=True, validation_label="dry_run")

        self.assertEqual(observed["notification_title_key"], "dry_run_title")

    def test_run_strategy_persists_machine_readable_report(self):
        module = load_module()
        observed_reports = []

        module.build_run_id = lambda: "run-001"
        module.emit_runtime_log = lambda *args, **kwargs: None
        module.is_market_open_now = lambda **_kwargs: True
        module.run_rebalance_cycle = lambda **_kwargs: None
        module.persist_runtime_report = (
            lambda report, **_kwargs: observed_reports.append(dict(report)) or types.SimpleNamespace(
                local_path="/tmp/runtime-report.json",
                gcs_uri=None,
            )
        )

        module.run_strategy()

        self.assertEqual(len(observed_reports), 1)
        report = observed_reports[0]
        self.assertEqual(report["platform"], "longbridge")
        self.assertEqual(report["run_source"], "cloud_run")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["strategy_profile"], module.STRATEGY_PROFILE)
        self.assertEqual(report["account_scope"], module.ACCOUNT_REGION)
        self.assertEqual(report["summary"]["managed_symbols"], list(module.MANAGED_SYMBOLS))
        self.assertEqual(report["summary"]["strategy_display_name"], module.STRATEGY_DISPLAY_NAME)
        self.assertEqual(report["summary"]["strategy_display_name_localized"], module.strategy_display_name)
        self.assertEqual(report["summary"]["execution_timing_contract"], "next_trading_day")
        self.assertTrue(report["summary"]["signal_date"])
        self.assertTrue(report["summary"]["effective_date"])

    def test_cycle_result_summary_counts_dry_run_order_previews(self):
        module = load_module()
        cycle_result = types.SimpleNamespace(
            logs=("dry-run sell", "dry-run buy"),
            skip_logs=("skip",),
            note_logs=("note",),
            action_done=True,
            execution={
                "signal_date": "2026-07-31",
                "execution_timing_contract": "monthly_snapshot_window",
                "durable_execution_command": {
                    "command_id": "cmd-paper-1",
                    "consumer_authorized": False,
                    "runtime_command_gate": {"enforcement": "observe", "would_block": True},
                },
            },
            dry_run_orders=(
                {"symbol": "02800.HK", "side": "buy", "quantity": 100, "status": "dry_run"},
                {"symbol": "03033.HK", "side": "buy", "quantity": 200, "status": "dry_run"},
            ),
            quote_snapshots=(
                {"symbol": "02800.HK", "last_price": 30.0, "currency": "HKD"},
                {"symbol": "03033.HK", "last_price": 20.0, "currency": "HKD"},
            ),
        )

        summary = module._summarize_cycle_result_for_report(cycle_result, dry_run=True)

        self.assertTrue(summary["action_done"])
        self.assertEqual(summary["order_events_count"], 2)
        self.assertEqual(summary["orders_previewed_count"], 2)
        self.assertEqual(summary["orders_skipped_count"], 1)
        self.assertEqual(summary["notes_count"], 1)
        self.assertTrue(summary["dry_run_order_preview_available"])
        self.assertEqual(summary["signal_date"], "2026-07-31")
        self.assertEqual(summary["execution_timing_contract"], "monthly_snapshot_window")
        self.assertEqual(summary["orders_previewed"][0]["symbol"], "02800.HK")
        self.assertEqual(summary["quote_snapshot"]["quotes"][0]["symbol"], "02800.HK")
        self.assertEqual(summary["durable_execution_command"]["command_id"], "cmd-paper-1")
        self.assertTrue(summary["durable_execution_command"]["runtime_command_gate"]["would_block"])

    def test_cycle_result_summary_keeps_broker_submission_pending_until_reconciled(self):
        module = load_module()
        cycle_result = types.SimpleNamespace(
            logs=("pending broker order",),
            skip_logs=(),
            note_logs=(),
            action_done=True,
            execution={},
            dry_run_orders=(),
            pending_orders=(
                {
                    "symbol": "SOXL.US",
                    "side": "sell",
                    "quantity": 1,
                    "status": "pending_reconciliation",
                    "broker_order_id": "lb-order-pending",
                },
            ),
            quote_snapshots=(),
        )

        summary = module._summarize_cycle_result_for_report(cycle_result, dry_run=False)

        self.assertFalse(summary["action_done"])
        self.assertTrue(summary["broker_submission_done"])
        self.assertEqual(summary["execution_status"], "pending_reconciliation")
        self.assertEqual(summary["order_events_count"], 0)
        self.assertEqual(summary["orders_pending_count"], 1)
        self.assertEqual(summary["orders_pending"][0]["broker_order_id"], "lb-order-pending")

    def test_notification_delivery_log_summary_records_sent_dry_run_without_raw_text(self):
        module = load_module()

        payload = module._build_notification_delivery_log_for_report(
            platform="longbridge",
            strategy_profile="hk_low_vol_dividend_quality_snapshot",
            run_id="run-001",
            dry_run=True,
            orders_previewed_count=2,
            delivery_events=[
                {
                    "sink": "telegram",
                    "delivery_status": "sent",
                    "compact_text_sha256": "a" * 64,
                    "compact_text_length": 42,
                }
            ],
        )

        self.assertEqual(payload["notification_schema_version"], "hk_live_enablement_notification.v1")
        self.assertEqual(payload["notification_event_type"], "hk_snapshot_live_enablement_dry_run")
        self.assertEqual(payload["notification_correlation_id"], "run-001")
        self.assertEqual(payload["locales"], ["en", "zh-Hans"])
        self.assertEqual(payload["profile"], "hk_low_vol_dividend_quality_snapshot")
        self.assertEqual(payload["platform"], "longbridge")
        self.assertEqual(payload["orders_previewed"], 2)
        self.assertTrue(payload["notification_redacts_sensitive_fields"])
        self.assertNotIn("compact_text", payload["delivery_events"][0])

    def test_notification_delivery_log_summary_stays_empty_without_sent_event(self):
        module = load_module()

        payload = module._build_notification_delivery_log_for_report(
            platform="longbridge",
            strategy_profile="hk_low_vol_dividend_quality_snapshot",
            run_id="run-001",
            dry_run=True,
            orders_previewed_count=2,
            delivery_events=[],
        )

        self.assertEqual(payload, {})

    def test_notification_delivery_summary_keeps_failed_transport_receipt(self):
        module = load_module()

        payload = module._build_notification_delivery_summary(
            [
                {
                    "sink": "telegram",
                    "delivery_status": "failed",
                    "transport_acknowledged": False,
                    "error_type": "RuntimeError",
                    "compact_text_sha256": "a" * 64,
                    "compact_text_length": 42,
                }
            ]
        )

        self.assertEqual(payload["attempted_count"], 1)
        self.assertEqual(payload["sent_count"], 0)
        self.assertEqual(payload["failed_count"], 1)
        self.assertFalse(payload["all_acknowledged"])
        self.assertEqual(payload["delivery_events"][0]["error_type"], "RuntimeError")
        self.assertNotIn("compact_text", payload["delivery_events"][0])


if __name__ == "__main__":
    unittest.main()
