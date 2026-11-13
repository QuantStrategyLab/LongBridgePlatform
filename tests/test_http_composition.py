"""Characterize HTTP composition without connecting to broker or cloud services."""

from contextlib import ExitStack, contextmanager
import builtins
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import flask
import pytest

from test_request_handling import install_stub_modules


ROUTES = (
    ("/run", ("POST",), "handle_trigger"),
    ("/backfill", ("POST", "GET"), "handle_backfill"),
    ("/dry-run", ("POST", "GET"), "handle_dry_run"),
    ("/probe", ("POST",), "handle_probe"),
    ("/paper-command-consumer", ("POST",), "handle_paper_execution_command_consumer"),
    ("/reconcile", ("POST",), "handle_broker_reconciliation"),
    ("/account-snapshot", ("GET",), "handle_account_snapshot"),
    ("/v7-paper-application-loaded", ("GET",), "handle_v7_paper_application_loaded"),
    ("/monitor-dispatch", ("POST", "GET"), "handle_monitor_dispatch"),
    ("/health", ("GET",), "health"),
    ("/healthz", ("GET",), "health"),
)
ROUTE_EVENTS = []
for _path, _methods, _handler in ROUTES[:-2]:
    ROUTE_EVENTS.extend((("route_factory", _path, _methods), ("route", _path, _methods, _handler)))
ROUTE_EVENTS.extend((
    ("route_factory", "/healthz", ("GET",)),
    ("route_factory", "/health", ("GET",)),
    ("route", "/health", ("GET",), "health"),
    ("route", "/healthz", ("GET",), "health"),
))
STARTUP = (
    "app", "settings", "normalize_plugins", "normalize_monitors",
    "notification_channel", "strategy", "fractional_policy",
    "premium_primary", "premium_fallback", "signal_text",
    "display_name", "broker_adapters", "strategy_adapters",
)
_REAL_FLASK = flask.Flask


@pytest.fixture
def import_entrypoint():
    @contextmanager
    def load(*, failure_stage=None, premium=None, route_failure=None):
        state = SimpleNamespace(
            events=[], module=None, error=None,
            injected_error=RuntimeError("synthetic startup failure"),
            settings=None, strategy=None, broker=None, adapters=None,
        )

        def event(stage):
            state.events.append(stage)
            if stage == failure_stage:
                raise state.injected_error

        class RecordingFlask(_REAL_FLASK):
            def __init__(self, name):
                event("app")
                super().__init__(name)

            def route(self, path, methods=None):
                state.events.append(("route_factory", path, tuple(methods or ())))
                if route_failure == ("route_factory", path):
                    raise state.injected_error
                register = super().route(path, methods=methods)

                def decorate(handler):
                    state.events.append(("route", path, tuple(methods or ()), handler.__name__))
                    if route_failure == ("route", path):
                        raise state.injected_error
                    return register(handler)

                return decorate

        previous_main = sys.modules.pop("main", None)
        try:
            with ExitStack() as stack:
                stack.enter_context(install_stub_modules())
                stack.enter_context(patch.dict(os.environ, {}, clear=True))
                modules = stack.enter_context(pytest.MonkeyPatch.context())
                modules.setitem(sys.modules, "flask", flask)
                stack.enter_context(patch.object(flask, "Flask", RecordingFlask))

                def unexpected_runtime_call(*_args, **_kwargs):
                    pytest.fail("HTTP composition invoked a broker, credential or notification dependency")

                modules.setattr(sys.modules["requests"], "post", unexpected_runtime_call)
                modules.setattr(sys.modules["google.auth"], "default", unexpected_runtime_call)
                for name in (
                    "build_contexts", "fetch_last_price", "fetch_order_status", "fetch_token_from_secret",
                    "refresh_token_if_needed", "estimate_max_purchase_quantity", "calculate_rotation_indicators",
                ):
                    modules.setattr(sys.modules["quant_platform_kit.longbridge"], name, unexpected_runtime_call)
                for module_name, name in (
                    ("application.longbridge_execution", "submit_order"),
                    ("application.longbridge_portfolio", "fetch_strategy_account_state"),
                ):
                    modules.setattr(importlib.import_module(module_name), name, unexpected_runtime_call)

                def wrap(module_name, attribute, stage, *, result_attribute=None):
                    module = importlib.import_module(module_name)
                    original = getattr(module, attribute)

                    def invoke(*args, **kwargs):
                        event(stage)
                        result = original(*args, **kwargs)
                        if result_attribute is not None:
                            setattr(state, result_attribute, result)
                        return result

                    stack.enter_context(patch.object(module, attribute, invoke))

                settings_module = sys.modules["runtime_config_support"]
                original_settings = settings_module.load_platform_runtime_settings

                def settings(*, project_id_resolver):
                    event("settings")
                    assert project_id_resolver.__name__ == "get_project_id"
                    state.settings = original_settings(project_id_resolver=project_id_resolver)
                    state.settings.strategy_plugin_mounts_json = "[]"
                    return state.settings

                stack.enter_context(patch.object(settings_module, "load_platform_runtime_settings", settings))
                wrap("application.startup_config", "normalize_plugin_mounts_strategy", "normalize_plugins")
                wrap("application.startup_config", "normalize_monitor_dispatch_targets", "normalize_monitors")
                wrap("quant_platform_kit.notifications.cycle_channel", "resolve_cycle_channel_and_url", "notification_channel")
                wrap("strategy_runtime", "load_strategy_runtime", "strategy", result_attribute="strategy")
                wrap("runtime_execution_policy", "fractional_buy_execution_enabled", "fractional_policy")
                wrap("notifications.telegram", "build_signal_text", "signal_text")
                wrap("notifications.telegram", "build_strategy_display_name", "display_name")
                wrap("application.runtime_broker_adapters", "build_runtime_broker_adapters", "broker_adapters", result_attribute="broker")
                wrap("application.runtime_strategy_adapters", "build_runtime_strategy_adapters", "strategy_adapters", result_attribute="adapters")

                original_getenv = os.getenv

                def getenv(name, *args):
                    if name == "LONGBRIDGE_LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON":
                        event("premium_primary")
                        return premium
                    if name == "LIMIT_BUY_PREMIUM_BY_SYMBOL_JSON":
                        event("premium_fallback")
                    return original_getenv(name, *args)

                stack.enter_context(patch.object(os, "getenv", getenv))
                try:
                    state.module = importlib.import_module("main")
                except Exception as exc:
                    state.error = exc
                yield state
        finally:
            sys.modules.pop("main", None)
            if previous_main is not None:
                sys.modules["main"] = previous_main

    return load


@pytest.fixture(params=["legacy", "fresh_app"])
def prepared_app(request, import_entrypoint):
    with import_entrypoint() as state:
        assert state.error is None
        module = state.module
        application = module.app if request.param == "legacy" else module.create_app()
        application.config["TESTING"] = True
        yield module, application, state


def test_startup_order_and_runtime_lifetimes(import_entrypoint):
    with import_entrypoint() as state:
        assert state.error is None
        assert state.events == list(STARTUP) + ROUTE_EVENTS
        module = state.module
        assert module.RUNTIME_SETTINGS is state.settings
        assert module.STRATEGY_RUNTIME is state.strategy
        assert module.BROKER_ADAPTERS is state.broker
        assert module.STRATEGY_ADAPTERS is state.adapters
        assert module.STRATEGY_PROFILE == state.settings.strategy_profile
        assert module.PROJECT_ID == state.settings.project_id


@pytest.mark.parametrize("failure_stage", STARTUP)
def test_startup_errors_propagate_before_route_registration(import_entrypoint, failure_stage):
    with import_entrypoint(failure_stage=failure_stage) as state:
        assert state.module is None
        assert state.error is state.injected_error
        assert state.events == list(STARTUP[:STARTUP.index(failure_stage) + 1])


def test_invalid_premium_configuration_still_fails_during_import(import_entrypoint):
    with import_entrypoint(premium="{invalid") as state:
        assert state.module is None
        assert isinstance(state.error, ValueError)
        assert str(state.error) == "Invalid limit buy premium map JSON: '{invalid'"
        assert state.events == list(STARTUP[:STARTUP.index("premium_primary") + 1])


@pytest.mark.parametrize("route_failure", [
    ("route_factory", "/healthz"), ("route_factory", "/health"),
    ("route", "/health"), ("route", "/healthz"),
])
def test_health_decorator_failure_keeps_evaluation_and_registration_order(import_entrypoint, route_failure):
    with import_entrypoint(route_failure=route_failure) as state:
        assert state.module is None
        assert state.error is state.injected_error
        failed_event = next(i for i, item in enumerate(ROUTE_EVENTS) if item[:2] == route_failure)
        assert state.events == list(STARTUP) + ROUTE_EVENTS[:failed_event + 1]


def test_registered_routes_and_public_handler_identity(prepared_app):
    module, application, _state = prepared_app
    expected = {
        (path, handler, tuple(sorted(set(methods) | {"OPTIONS"} | ({"HEAD"} if "GET" in methods else set()))))
        for path, methods, handler in ROUTES
    }
    expected.add(("/static/<path:filename>", "static", ("GET", "HEAD", "OPTIONS")))
    assert {(rule.rule, rule.endpoint, tuple(sorted(rule.methods))) for rule in application.url_map.iter_rules()} == expected
    assert application.name == "main"
    for _path, _methods, handler in ROUTES:
        assert application.view_functions[handler] is getattr(module, handler)


@pytest.mark.parametrize(("path", "method", "target", "kwargs", "body"), [
    ("/run", "POST", "run_strategy", {}, "OK"),
    ("/backfill", "GET", "run_strategy", {"force_run": True, "validation_only": True}, "OK"),
    ("/backfill", "POST", "run_strategy", {"force_run": True, "validation_only": True}, "OK"),
    ("/backfill", "HEAD", "run_strategy", {"force_run": True, "validation_only": True}, ""),
    ("/dry-run", "GET", "run_strategy", {"force_run": True, "validation_only": True, "validation_label": "dry_run"}, "Dry Run OK"),
    ("/dry-run", "POST", "run_strategy", {"force_run": True, "validation_only": True, "validation_label": "dry_run"}, "Dry Run OK"),
    ("/dry-run", "HEAD", "run_strategy", {"force_run": True, "validation_only": True, "validation_label": "dry_run"}, ""),
    ("/probe", "POST", "run_probe", {}, "Probe OK"),
    ("/paper-command-consumer", "POST", "run_paper_execution_command_consumer", {}, "Paper command consumer OK"),
])
def test_execution_routes_preserve_callback_arguments(prepared_app, monkeypatch, path, method, target, kwargs, body):
    module, application, _state = prepared_app
    calls = []

    def run(**received):
        calls.append(received)
        return None

    monkeypatch.setattr(module, target, run)
    monkeypatch.setattr(module.unified_notifications, "enabled", lambda: False)
    response = application.test_client().open(path, method=method)
    assert response.status_code == 200
    assert response.get_data(as_text=True) == body
    assert calls == [kwargs]


@pytest.mark.parametrize(("result", "status", "body"), [
    (False, 500, "Error"), (("Deferred", 409), 409, "Deferred"), (None, 200, "OK"),
])
def test_run_result_is_not_reclassified(prepared_app, monkeypatch, result, status, body):
    module, application, _state = prepared_app
    monkeypatch.setattr(module, "run_strategy", lambda: result)
    monkeypatch.setattr(module.unified_notifications, "enabled", lambda: False)
    response = application.test_client().post("/run")
    assert response.status_code == status
    assert response.get_data(as_text=True) == body


def test_route_error_keeps_exception_and_route_label(prepared_app, monkeypatch):
    module, application, _state = prepared_app
    error = RuntimeError("synthetic runtime error")
    calls = []

    def fail():
        raise error

    def report(exc, *, route_label):
        calls.append((exc, route_label))
        return "Error", 500

    monkeypatch.setattr(module, "run_strategy", fail)
    monkeypatch.setattr(module, "_handle_route_runtime_error", report)
    monkeypatch.setattr(module.unified_notifications, "enabled", lambda: False)
    response = application.test_client().post("/run")
    assert response.status_code == 500
    assert response.get_data(as_text=True) == "Error"
    assert calls == [(error, "POST /run")]


@pytest.mark.parametrize(("path", "target", "method", "no_store"), [
    ("/reconcile", "run_broker_reconciliation", "POST", False),
    ("/account-snapshot", "run_account_snapshot", "GET", True),
])
def test_read_only_routes_preserve_unknown_status_and_headers(prepared_app, monkeypatch, path, target, method, no_store):
    module, application, _state = prepared_app
    calls = []

    def read():
        calls.append(target)
        return {"status": "unknown", "source": None}, 503

    monkeypatch.setattr(module, target, read)
    response = application.test_client().open(path, method=method)
    assert response.status_code == 503
    assert response.get_json() == {"status": "unknown", "source": None}
    assert response.headers["Content-Type"] == "application/json"
    assert response.headers.get("Cache-Control") == ("no-store" if no_store else None)
    assert calls == [target]


@pytest.mark.parametrize(("path", "method", "status"), [
    ("/run", "GET", 405), ("/probe", "GET", 405),
    ("/account-snapshot", "POST", 405), ("/absent", "GET", 404),
    ("/run", "OPTIONS", 200),
])
def test_framework_responses_do_not_run_strategy(prepared_app, monkeypatch, path, method, status):
    module, application, _state = prepared_app

    def unexpected(**_kwargs):
        pytest.fail("framework-only response called strategy")

    monkeypatch.setattr(module, "run_strategy", unexpected)
    response = application.test_client().open(path, method=method)
    assert response.status_code == status
    if method == "OPTIONS":
        assert {value.strip() for value in response.headers["Allow"].split(",")} == {"POST", "OPTIONS"}


def test_monitor_get_does_not_dispatch_and_post_preserves_payload(prepared_app, monkeypatch):
    module, application, _state = prepared_app
    calls = []
    targets = [{"service_name": "synthetic-service"}]
    monkeypatch.setattr(module, "_load_monitor_dispatch_targets", lambda: targets)

    def dispatch(received):
        calls.append(received)
        return {"status": "unknown", "checks": None}

    monkeypatch.setattr(module, "dispatch_due_monitors", dispatch)
    client = application.test_client()
    response = client.get("/monitor-dispatch")
    assert response.status_code == 200
    assert calls == []
    response = client.post("/monitor-dispatch")
    assert response.status_code == 200
    assert response.get_json() == {"status": "unknown", "checks": None}
    assert calls == [targets]


def test_health_aliases_preserve_import_failure_status(prepared_app, monkeypatch):
    module, application, _state = prepared_app
    calls = []

    def import_module(name):
        calls.append(name)
        if name == "runtime_config_support":
            raise ImportError("synthetic missing contract")
        return SimpleNamespace()

    monkeypatch.setattr(module, "importlib", SimpleNamespace(import_module=import_module))
    for path in ("/health", "/healthz"):
        response = application.test_client().get(path)
        assert response.status_code == 500
        assert response.mimetype == "text/html"
        assert json.loads(response.get_data(as_text=True)) == {
            "status": "unhealthy",
            "errors": ["runtime_config_support: ImportError: synthetic missing contract"],
        }
    assert calls == [
        "application.runtime_composer", "application.runtime_reporting_adapters",
        "application.runtime_strategy_adapters", "runtime_config_support", "runtime_execution_policy",
    ] * 2


@pytest.mark.parametrize("binding_state", ["missing", "uncertain", "loaded"])
def test_v7_readback_preserves_paused_and_unknown_responses(prepared_app, monkeypatch, binding_state):
    module, application, _state = prepared_app
    calls = []

    class BindingError(Exception):
        pass

    binding = object()

    def load_binding(env):
        assert env is os.environ
        calls.append("binding")
        return None if binding_state == "missing" else binding

    def readback(received, **kwargs):
        assert received is binding
        assert kwargs["runtime_settings"] is module.RUNTIME_SETTINGS
        assert kwargs["strategy_runtime"] is module.STRATEGY_RUNTIME
        assert kwargs["runtime_env"] is os.environ
        calls.append("readback")
        if binding_state == "uncertain":
            raise BindingError("synthetic readback failure")
        return {"strategy": "synthetic-profile"}

    monkeypatch.setitem(sys.modules, "application.v7_paper_application", SimpleNamespace(
        V7PaperApplicationError=BindingError,
        load_v7_paper_application_binding=load_binding,
        build_v7_loaded_version_readback=readback,
    ))
    response = application.test_client().get("/v7-paper-application-loaded")
    assert response.headers["Cache-Control"] == "no-store"
    if binding_state == "missing":
        assert response.status_code == 409
        assert response.get_json() == {"status": "unavailable", "reason": "application_binding_missing"}
        assert calls == ["binding"]
    elif binding_state == "uncertain":
        assert response.status_code == 409
        assert response.get_json() == {"status": "uncertain", "reason": "loaded_version_unavailable"}
        assert calls == ["binding", "readback"]
    else:
        assert response.status_code == 200
        assert response.get_json() == {"status": "applied_paused", "readback": {"strategy": "synthetic-profile"}}
        assert calls == ["binding", "readback"]


def test_fresh_app_does_not_repeat_startup_or_share_http_state(import_entrypoint):
    with import_entrypoint() as state:
        assert state.error is None
        module = state.module
        state.events.clear()
        first = module.create_app()
        second = module.create_app()
        assert first is not second
        assert first is not module.app
        expected = ["app"] + ROUTE_EVENTS
        assert state.events == expected * 2
        first.config["SYNTHETIC_SETTING"] = "first"
        assert "SYNTHETIC_SETTING" not in second.config
        assert "SYNTHETIC_SETTING" not in module.app.config
        first.add_url_rule("/synthetic-only", view_func=lambda: "first")
        assert first.test_client().get("/synthetic-only").status_code == 200
        assert second.test_client().get("/synthetic-only").status_code == 404
        assert module.app.test_client().get("/synthetic-only").status_code == 404
        assert module.RUNTIME_SETTINGS is state.settings
        assert module.BROKER_ADAPTERS is state.broker
        assert module.STRATEGY_ADAPTERS is state.adapters


def test_fresh_app_module_import_and_assembly_do_not_load_runtime_dependencies(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "application/http_app.py"
    spec = importlib.util.spec_from_file_location("synthetic_http_application", path)
    http_module = importlib.util.module_from_spec(spec)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"main", "google", "longport", "quant_platform_kit", "runtime_config_support", "strategy_runtime"}:
            raise AssertionError("HTTP assembly imported a runtime dependency")
        return original_import(name, *args, **kwargs)

    handlers = {}
    for _path, _methods, name in ROUTES:
        if name not in handlers:
            def handler():
                return "synthetic response"
            handler.__name__ = name
            handlers[name] = handler

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "__import__", guarded_import)
        spec.loader.exec_module(http_module)
        application = http_module.create_http_app("synthetic_http", handlers)
        assert application.test_client().get("/health").get_data(as_text=True) == "synthetic response"
    assert len(tuple(application.url_map.iter_rules())) == 12
