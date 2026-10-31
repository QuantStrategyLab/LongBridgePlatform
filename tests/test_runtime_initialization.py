"""Keep startup synchronous and preserve the supported launcher contracts."""

from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch

import flask
from gunicorn.util import import_app
import pytest

import test_http_composition as composition
from test_http_composition import ROUTE_EVENTS, STARTUP
from test_http_composition import import_entrypoint as import_entrypoint


@contextmanager
def _gunicorn_import(selector):
    """Use the real resolver for the fixture's first, guarded main import."""
    original_import = importlib.import_module
    resolved = []

    def load(name, package=None):
        if name != "main":
            return original_import(name, package)
        with patch.object(importlib, "import_module", original_import):
            resolved.append(import_app(selector))
        return sys.modules["main"]

    with patch.object(importlib, "import_module", load):
        yield resolved


def test_legacy_gunicorn_load_completes_startup_before_returning(import_entrypoint):
    with _gunicorn_import("main:app") as resolved:
        with import_entrypoint() as state:
            assert state.error is None
            assert resolved == [state.module.app]
            assert state.events == list(STARTUP) + ROUTE_EVENTS
            assert state.module.app.view_functions["handle_trigger"] is state.module.handle_trigger


@pytest.mark.parametrize("error_type", [AttributeError, TypeError, ValueError])
@pytest.mark.parametrize("failure_stage", ["settings", "broker_adapters"])
def test_legacy_gunicorn_load_preserves_startup_exception_identity(
    import_entrypoint, error_type, failure_stage,
):
    with patch.object(composition, "RuntimeError", error_type, create=True):
        with _gunicorn_import("main:app") as resolved:
            with import_entrypoint(failure_stage=failure_stage) as state:
                assert resolved == []
                assert state.module is None
                assert state.error is state.injected_error
                assert type(state.error) is error_type
                assert state.events == list(STARTUP[:STARTUP.index(failure_stage) + 1])


def test_legacy_gunicorn_load_preserves_route_attribute_error(import_entrypoint):
    route_failure = ("route", "/healthz")
    with patch.object(composition, "RuntimeError", AttributeError, create=True):
        with _gunicorn_import("main:app") as resolved:
            with import_entrypoint(route_failure=route_failure) as state:
                assert resolved == []
                assert state.module is None
                assert state.error is state.injected_error
                failed_event = next(i for i, item in enumerate(ROUTE_EVENTS) if item[:2] == route_failure)
                assert state.events == list(STARTUP) + ROUTE_EVENTS[:failed_event + 1]


@pytest.mark.parametrize("port", [None, "8181"])
def test_cli_retains_startup_and_bind_arguments(import_entrypoint, monkeypatch, port):
    with import_entrypoint() as state:
        assert state.error is None
        calls = []

        def run(application, **kwargs):
            calls.append((application, kwargs, list(state.events)))

        monkeypatch.setattr(flask.Flask, "run", run)
        if port is None:
            monkeypatch.delenv("PORT", raising=False)
        else:
            monkeypatch.setenv("PORT", port)
        state.events.clear()
        namespace = runpy.run_path(str(Path(state.module.__file__)), run_name="__main__")
        assert calls == [(
            namespace["app"],
            {"host": "0.0.0.0", "port": int(port or "8080")},
            list(STARTUP) + ROUTE_EVENTS,
        )]
        assert namespace["app"].view_functions["handle_trigger"] is namespace["handle_trigger"]


def test_broker_callback_retains_module_global_lookup(import_entrypoint, monkeypatch):
    with import_entrypoint() as state:
        assert state.error is None
        module = state.module
        calls = []
        result = object()
        position_log = object()
        warning_log = object()

        def fetch(*args, **kwargs):
            calls.append((args, kwargs))
            return result

        monkeypatch.setattr(module, "fetch_strategy_account_state", fetch)
        monkeypatch.setattr(module, "MANAGED_SYMBOLS", ("SYNTHETIC",))
        monkeypatch.setattr(module, "TRADING_CURRENCY", "SYNTHETIC_CURRENCY")
        monkeypatch.setattr(module, "RUNTIME_SETTINGS", SimpleNamespace(debug_position_snapshot=True))
        monkeypatch.setattr(module, "log_position_snapshot", position_log)
        monkeypatch.setattr(module, "log_runtime_warning", warning_log)
        callback = module.BROKER_ADAPTERS.fetch_strategy_account_state_fn
        assert callback("quote", "trade") is result
        assert calls == [(("quote", "trade", ["SYNTHETIC"]), {
            "cash_currency": "SYNTHETIC_CURRENCY",
            "position_log_fn": position_log,
            "warning_log_fn": warning_log,
        })]


def test_explicit_initializer_reuses_completed_runtime(import_entrypoint):
    with import_entrypoint() as state:
        assert state.error is None
        module = state.module
        events = list(state.events)
        environment = dict(os.environ)
        assert module.initialize_runtime() is module.app
        assert module.initialize_runtime() is module.app
        assert state.events == events
        assert dict(os.environ) == environment
        assert module.RUNTIME_SETTINGS is state.settings
        assert module.STRATEGY_RUNTIME is state.strategy
        assert module.BROKER_ADAPTERS is state.broker
        assert module.STRATEGY_ADAPTERS is state.adapters


def test_explicit_gunicorn_factory_reuses_import_bootstrap(import_entrypoint):
    with _gunicorn_import("main:initialize_runtime()") as resolved:
        with import_entrypoint() as state:
            assert state.error is None
            assert resolved == [state.module.app]
            assert state.events == list(STARTUP) + ROUTE_EVENTS
