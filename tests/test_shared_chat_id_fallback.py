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
