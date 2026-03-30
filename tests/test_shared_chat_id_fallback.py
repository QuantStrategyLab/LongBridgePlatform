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


class SharedChatIdTests(unittest.TestCase):
    def test_global_telegram_chat_id_is_used(self):
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
                module = importlib.reload(module)

        self.assertEqual(module.TG_CHAT_ID, "shared-chat-id")


if __name__ == "__main__":
    unittest.main()
