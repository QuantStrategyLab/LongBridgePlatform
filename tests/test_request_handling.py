import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PLATFORM_KIT_SRC = ROOT.parent / "QuantPlatformKit" / "src"
if str(PLATFORM_KIT_SRC) not in sys.path:
    sys.path.insert(0, str(PLATFORM_KIT_SRC))


def install_stub_modules():
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
        "google": google_module,
        "google.auth": google_auth_module,
        "google.cloud": google_cloud_module,
        "google.cloud.secretmanager_v1": google_secretmanager_module,
        "pandas_market_calendars": pandas_market_calendars,
        "longport": longport_module,
        "longport.openapi": openapi_module,
    }
    return patch.dict(sys.modules, modules)


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


if __name__ == "__main__":
    unittest.main()
