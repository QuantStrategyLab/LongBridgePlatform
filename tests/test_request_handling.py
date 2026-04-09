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


@contextmanager
def install_stub_modules():
    flask_module = types.ModuleType("flask")

    class Flask:
        def __init__(self, _name):
            self._routes = {}

        def route(self, path, methods=None):
            def decorator(func):
                self._routes[(path, tuple(methods or []))] = func
                return func

            return decorator

        def test_request_context(self, *_args, **_kwargs):
            class _Context:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Context()

        def run(self, *args, **kwargs):
            return None

    flask_module.Flask = Flask

    requests_module = types.ModuleType("requests")
    requests_module.post = lambda *args, **kwargs: None

    cloud_run_module = types.ModuleType("entrypoints.cloud_run")
    cloud_run_module.is_market_open_now = lambda: True

    runtime_config_support_module = types.ModuleType("runtime_config_support")
    runtime_config_support_module.load_platform_runtime_settings = lambda **_kwargs: types.SimpleNamespace(
        project_id=None,
        secret_name="longport_token_hk",
        account_prefix="HK",
        strategy_profile="soxl_soxx_trend_income",
        strategy_display_name="SOXL/SOXX Semiconductor Trend Income",
        strategy_domain="us_equity",
        account_region="HK",
        notify_lang="en",
        tg_token=None,
        tg_chat_id="shared-chat-id",
        dry_run_only=False,
    )

    qpk_longbridge_module = types.ModuleType("quant_platform_kit.longbridge")
    qpk_longbridge_module.build_contexts = lambda *args, **kwargs: ("quote-context", "trade-context")
    qpk_longbridge_module.calculate_rotation_indicators = lambda *args, **kwargs: {}
    qpk_longbridge_module.estimate_max_purchase_quantity = lambda *args, **kwargs: 0
    qpk_longbridge_module.fetch_last_price = lambda *args, **kwargs: 0.0
    qpk_longbridge_module.fetch_order_status = lambda *args, **kwargs: None
    qpk_longbridge_module.fetch_strategy_account_state = lambda *args, **kwargs: {}
    qpk_longbridge_module.fetch_token_from_secret = lambda *args, **kwargs: "token"
    qpk_longbridge_module.refresh_token_if_needed = lambda *args, **kwargs: "token"
    qpk_longbridge_module.submit_order = lambda *args, **kwargs: None

    google_module = types.ModuleType("google")
    google_module.__path__ = []

    google_auth_module = types.ModuleType("google.auth")
    google_auth_module.default = lambda *args, **kwargs: (None, None)

    google_cloud_module = types.ModuleType("google.cloud")
    google_cloud_module.__path__ = []
    google_secretmanager_module = types.ModuleType("google.cloud.secretmanager_v1")

    google_module.auth = google_auth_module
    google_cloud_module.secretmanager_v1 = google_secretmanager_module

    pandas_market_calendars = types.ModuleType("pandas_market_calendars")

    strategy_runtime_module = types.ModuleType("strategy_runtime")
    strategy_runtime_module.load_strategy_runtime = lambda *_args, **_kwargs: types.SimpleNamespace(
        merged_runtime_config={"trend_ma_window": 150},
        managed_symbols=("SOXL", "SOXX", "BOXX", "QQQI", "SPYI"),
        runtime_adapter=types.SimpleNamespace(
            available_inputs=frozenset({"derived_indicators", "portfolio_snapshot"})
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

    modules = {
        "flask": flask_module,
        "requests": requests_module,
        "entrypoints.cloud_run": cloud_run_module,
        "runtime_config_support": runtime_config_support_module,
        "quant_platform_kit.longbridge": qpk_longbridge_module,
        "google": google_module,
        "google.auth": google_auth_module,
        "google.cloud": google_cloud_module,
        "google.cloud.secretmanager_v1": google_secretmanager_module,
        "pandas_market_calendars": pandas_market_calendars,
        "strategy_runtime": strategy_runtime_module,
        "longport": longport_module,
        "longport.openapi": openapi_module,
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


def load_module():
    with install_stub_modules():
        with patch.dict(
            os.environ,
            {
                "GLOBAL_TELEGRAM_CHAT_ID": "shared-chat-id",
            },
            clear=False,
        ):
            sys.modules.pop("main", None)
            module = importlib.import_module("main")
            return importlib.reload(module)


class RequestHandlingTests(unittest.TestCase):
    def test_handle_trigger_runs_strategy(self):
        module = load_module()
        observed = {"called": False}

        def fake_run_strategy():
            observed["called"] = True

        module.run_strategy = fake_run_strategy

        with module.app.test_request_context("/", method="POST"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 200)
        self.assertEqual(body, "OK",)
        self.assertTrue(observed["called"])

    def test_handle_trigger_allows_get(self):
        module = load_module()
        observed = {"called": False}

        def fake_run_strategy():
            observed["called"] = True

        module.run_strategy = fake_run_strategy

        with module.app.test_request_context("/", method="GET"):
            body, status = module.handle_trigger()

        self.assertEqual(status, 200)
        self.assertEqual(body, "OK")
        self.assertTrue(observed["called"])

    def test_run_strategy_emits_structured_runtime_events(self):
        module = load_module()
        observed = []

        module.build_run_id = lambda: "run-001"
        module.emit_runtime_log = lambda context, event, **fields: observed.append((context.run_id, event, fields))
        module.is_market_open_now = lambda: True
        module.run_rebalance_cycle = lambda **_kwargs: None

        module.run_strategy()

        self.assertEqual(
            [event for _run_id, event, _fields in observed],
            ["strategy_cycle_started", "strategy_cycle_completed"],
        )
        self.assertTrue(all(run_id == "run-001" for run_id, _event, _fields in observed))

    def test_run_strategy_persists_machine_readable_report(self):
        module = load_module()
        observed_reports = []

        module.build_run_id = lambda: "run-001"
        module.emit_runtime_log = lambda *args, **kwargs: None
        module.is_market_open_now = lambda: True
        module.run_rebalance_cycle = lambda **_kwargs: None
        module.persist_execution_report = (
            lambda report: observed_reports.append(dict(report)) or "/tmp/runtime-report.json"
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


if __name__ == "__main__":
    unittest.main()
