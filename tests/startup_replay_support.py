"""Replay reviewed entrypoint sources against the existing offline boundary doubles.

This is synthetic startup and HTTP dispatch evidence, not a production-image
replay or a security sandbox for arbitrary Python source.
"""

import argparse
import ast
import builtins
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from datetime import datetime
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import patch

import flask
import gunicorn
from gunicorn.errors import AppImportError
from gunicorn.util import import_app

import test_http_composition as composition


ROOT = Path(__file__).resolve().parents[1]
CLOCK = "2024-01-02T03:04:05+00:00"
_ACTIVE_EFFECTS = ContextVar("startup_replay_effects", default=None)
_GLOBALS = ("app", "RUNTIME_SETTINGS", "STRATEGY_RUNTIME", "BROKER_ADAPTERS", "STRATEGY_ADAPTERS")


def _audit(event, _arguments):
    attempts = _ACTIVE_EFFECTS.get()
    if attempts is not None and event in {
        "socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system",
    }:
        attempts.append(event)
        raise AssertionError("external I/O is prohibited during startup replay")


sys.addaudithook(_audit)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        instant = datetime.fromisoformat(CLOCK)
        return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)


class _SourceLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return compile(self.get_data(self.path), self.path, "exec")


class _SourceFinder:
    def __init__(self, source):
        self.source = source

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "main":
            return importlib.util.spec_from_file_location(
                "main", self.source, loader=_SourceLoader("main", str(self.source)),
            )
        return None


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _worker_exit(error):
    """Execute only the installed arbiter's failure handlers; never fork a worker."""
    path = Path(gunicorn.__file__).parent / "arbiter.py"
    arbiter = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == "Arbiter")
    spawn = next(n for n in arbiter.body if isinstance(n, ast.FunctionDef) and n.name == "spawn_worker")
    child = next(n for n in spawn.body if isinstance(n, ast.Try) and any(
        isinstance(h.type, ast.Name) and h.type.id == "AppImportError" for h in n.handlers
    ))
    function = ast.parse("def classify(self, worker, load):\n    pass\n").body[0]
    function.body = [ast.Try(
        body=[ast.Expr(ast.Call(ast.Name("load", ast.Load()), [], []))],
        handlers=child.handlers, orelse=[], finalbody=[],
    )]
    namespace = {"sys": sys, "AppImportError": AppImportError}
    exec(compile(ast.fix_missing_locations(ast.Module([function], [])), str(path), "exec"), namespace)
    codes = {
        n.targets[0].id: ast.literal_eval(n.value) for n in arbiter.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id in {"WORKER_BOOT_ERROR", "APP_LOAD_ERROR"}
    }
    receiver = SimpleNamespace(**codes, log=SimpleNamespace(debug=lambda *_a, **_k: None, exception=lambda *_a, **_k: None))

    def fail():
        raise error

    try:
        namespace["classify"](receiver, SimpleNamespace(booted=False), fail)
    except SystemExit as exited:
        return exited.code
    raise AssertionError("worker failure classification did not exit")


def _exception(error):
    if error is None:
        return None
    return {
        "type": type(error).__name__, "message": str(error),
        "cause": _exception(error.__cause__),
        "suppressed_context": error.__suppress_context__,
    }


def _module_state(module):
    return {
        "present": module is not None,
        "initialized": getattr(module, "_runtime_initialized", None),
        "globals_present": [name for name in _GLOBALS if module is not None and name in vars(module)],
        "defined_names": sorted(name for name in vars(module) if not name.startswith("__")) if module is not None else [],
    }


def _dependency_sources():
    roots = {"application", "decision_mapper", "entrypoints", "flask", "google", "gunicorn",
             "longport", "notifications", "quant_platform_kit", "requests", "runtime_config_support",
             "runtime_execution_policy", "runtime_logging", "strategy", "strategy_registry", "strategy_runtime",
             "us_equity_strategies"}
    result = {}
    for name, module in tuple(sys.modules.items()):
        if name.split(".")[0] not in roots:
            continue
        origin = getattr(module, "__file__", None)
        result[name] = {"sha256": _sha(origin)} if isinstance(origin, str) and Path(origin).is_file() else {"test_double_or_namespace": True}
    return result


@contextmanager
def _load(source, selector, case, timeline, effects, dependency_sources=None):
    """Use real import machinery and resolver inside the existing offline fixture."""
    error_type = {"RuntimeError": RuntimeError, "AttributeError": AttributeError,
                  "TypeError": TypeError, "ImportError": ImportError}[case.get("error_type", "RuntimeError")]
    injected = error_type("synthetic startup failure")
    injected.__cause__ = ValueError("synthetic startup cause")
    finder = _SourceFinder(source)
    original_import = importlib.import_module
    original_builtin_import = builtins.__import__
    output = io.StringIO()
    active = _ACTIVE_EFFECTS.set(effects)

    class Events(list):
        def append(self, value):
            timeline.append({"kind": "startup", "value": value})
            super().append(value)

    def state_factory(**kwargs):
        kwargs["events"] = Events()
        return SimpleNamespace(**kwargs)

    def trace_import(name, globals=None, locals=None, fromlist=(), level=0):
        if globals is not None and globals.get("__name__") in {"main", "__main__"}:
            timeline.append({"kind": "import", "name": name, "from": list(fromlist or ()), "level": level})
            if name == case.get("dependency_failure"):
                raise injected
        return original_builtin_import(name, globals, locals, fromlist, level)

    def forbidden(*_args, **_kwargs):
        effects.append("runtime-boundary")
        raise AssertionError("broker, credential, cloud or notification operation attempted")

    with ExitStack() as controls:
        controls.enter_context(redirect_stdout(output))
        controls.enter_context(redirect_stderr(output))
        controls.enter_context(patch.object(composition, "RuntimeError", lambda _message: injected, create=True))
        controls.enter_context(patch.object(composition, "SimpleNamespace", state_factory))
        controls.enter_context(patch.object(sys, "dont_write_bytecode", True))
        controls.enter_context(patch("datetime.datetime", _FrozenDateTime))
        controls.enter_context(patch("time.time", lambda: datetime.fromisoformat(CLOCK).timestamp()))
        controls.enter_context(patch.object(builtins, "__import__", trace_import))
        sys.meta_path.insert(0, finder)

        def import_main(name, package=None):
            if name != "main":
                return original_import(name, package)
            # The fixture has now installed its doubles. Denials remain active
            # during HTTP replay and are recorded even if a handler catches one.
            for module_name, attributes in {
                "requests": ("post",), "google.auth": ("default",),
                "quant_platform_kit.longbridge": (
                    "build_contexts", "fetch_last_price", "fetch_order_status", "fetch_token_from_secret",
                    "refresh_token_if_needed", "estimate_max_purchase_quantity", "calculate_rotation_indicators", "submit_order",
                ),
                "application.longbridge_execution": ("submit_order",),
                "application.longbridge_portfolio": ("fetch_strategy_account_state",),
            }.items():
                for attribute in attributes:
                    controls.enter_context(patch.object(sys.modules[module_name], attribute, forbidden))
            with patch.object(importlib, "import_module", original_import):
                if selector is None:
                    return original_import("main")
                import_app(selector)
                return sys.modules["main"]

        try:
            with patch.object(importlib, "import_module", import_main):
                loader = composition.import_entrypoint.__wrapped__()
                with loader(failure_stage=case.get("failure_stage"), premium=case.get("premium"),
                            route_failure=tuple(case["route_failure"]) if case.get("route_failure") else None) as state:
                    yield state, injected, output
                    if dependency_sources is not None:
                        dependency_sources.update(_dependency_sources())
        finally:
            sys.meta_path.remove(finder)
            _ACTIVE_EFFECTS.reset(active)


def _http_replay(module, app):
    callbacks = []
    responses = []
    app.config["TESTING"] = True
    result = {"strategy": None, "binding": "missing", "health_failure": False}

    def strategy(**kwargs):
        callbacks.append(["run_strategy", kwargs])
        if result["strategy"] == "raise":
            raise RuntimeError("synthetic route failure")
        return result["strategy"]

    def simple(name, value):
        def call(*args, **kwargs):
            callbacks.append([name, list(args), kwargs])
            return value
        return call

    class BindingError(Exception):
        pass

    binding = object()

    def load_binding(env):
        assert env is os.environ
        callbacks.append(["binding"])
        return None if result["binding"] == "missing" else binding

    def readback(value, **kwargs):
        assert value is binding and kwargs["runtime_settings"] is module.RUNTIME_SETTINGS
        assert kwargs["strategy_runtime"] is module.STRATEGY_RUNTIME and kwargs["runtime_env"] is os.environ
        callbacks.append(["readback"])
        if result["binding"] == "uncertain":
            raise BindingError("synthetic readback failure")
        return {"strategy": "synthetic-profile"}

    def health_import(name):
        callbacks.append(["health_import", name])
        if result["health_failure"] and name == "runtime_config_support":
            raise ImportError("synthetic missing contract")
        return SimpleNamespace()

    def route_error(error, *, route_label):
        callbacks.append(["route_error", _exception(error), route_label])
        return "Error", 500

    with ExitStack() as stack:
        stack.enter_context(patch.object(module, "run_strategy", strategy))
        for name, value in {
            "run_probe": None, "run_paper_execution_command_consumer": None,
            "run_broker_reconciliation": ({"status": "unknown", "source": None}, 503),
            "run_account_snapshot": ({"status": "unknown", "source": None}, 503),
            "_load_monitor_dispatch_targets": [{"service_name": "synthetic-service"}],
            "dispatch_due_monitors": {"status": "unknown", "checks": None},
        }.items():
            stack.enter_context(patch.object(module, name, simple(name, value)))
        stack.enter_context(patch.object(module, "_handle_route_runtime_error", route_error))
        stack.enter_context(patch.object(module.unified_notifications, "enabled", lambda: False))
        stack.enter_context(patch.object(module, "importlib", SimpleNamespace(import_module=health_import)))
        stack.enter_context(patch.dict(sys.modules, {"application.v7_paper_application": SimpleNamespace(
            V7PaperApplicationError=BindingError, load_v7_paper_application_binding=load_binding,
            build_v7_loaded_version_readback=readback,
        )}))
        client = app.test_client()

        def request(path, method):
            begin = len(callbacks)
            response = client.open(path, method=method)
            responses.append({
                "path": path, "method": method, "status": response.status_code,
                "body": response.get_data(as_text=True), "headers": sorted(response.headers.items()),
                "callbacks": callbacks[begin:],
            })

        for path, methods, _handler in composition.ROUTES:
            for method in (*methods, *(["HEAD"] if "GET" in methods else []), "OPTIONS", "PUT"):
                request(path, method)
        request("/absent", "GET")
        for value in (False, ("Deferred", 409), "raise"):
            result["strategy"] = value
            request("/run", "POST")
        for value in ("uncertain", "loaded"):
            result["binding"] = value
            request("/v7-paper-application-loaded", "GET")
        result["health_failure"] = True
        for path in ("/health", "/healthz"):
            request(path, "GET")
    return responses


def _capture_service(source, selector, case, dependency_sources=None):
    timeline, effects = [], []
    with _load(source, selector, case, timeline, effects, dependency_sources) as (state, injected, output):
        module = sys.modules.get("main")
        record = {"startup": list(timeline), "output": output.getvalue(), "error": _exception(state.error)}
        diagnostics = {"after_load": _module_state(module)}
        if state.error is not None:
            record.update(same_injected_error=state.error is injected, worker_exit=_worker_exit(state.error))
            timeline.clear()
            try:
                import_app(selector)
            except Exception as repeated:
                record["repeated_failure"] = {
                    "error": _exception(repeated), "same_injected_error": repeated is injected,
                    "worker_exit": _worker_exit(repeated), "timeline": list(timeline),
                }
            else:
                raise AssertionError("persistent startup failure unexpectedly returned an application")
            diagnostics["after_repeated_failure"] = _module_state(sys.modules.get("main"))
        else:
            assert module is state.module
            app = module.app
            record["lifetimes"] = {
                name: getattr(module, name) is getattr(state, field)
                for name, field in (("RUNTIME_SETTINGS", "settings"), ("STRATEGY_RUNTIME", "strategy"),
                                    ("BROKER_ADAPTERS", "broker"), ("STRATEGY_ADAPTERS", "adapters"))
            }
            record["routes"] = [{
                "path": rule.rule, "methods": sorted(rule.methods), "endpoint": rule.endpoint,
                "handler_identity": rule.endpoint == "static" or app.view_functions[rule.endpoint] is getattr(module, rule.endpoint),
            } for rule in app.url_map.iter_rules()]
            before = len(timeline)
            record["repeated_success"] = [module.initialize_runtime() is app, module.initialize_runtime() is app]
            record["repeat_events"] = timeline[before:]
            record["http"] = _http_replay(module, app)
        record["effects"] = list(effects)
        if effects:
            raise AssertionError("external effect attempt invalidates replay")
    return record, diagnostics


def _capture_cli(source, port, dependency_sources=None):
    timeline, effects, calls = [], [], []
    with _load(source, None, {}, timeline, effects, dependency_sources) as (_state, _injected, output):
        timeline.clear()
        output.seek(0)
        output.truncate()

        def run(app, **kwargs):
            calls.append({"arguments": kwargs, "ready_routes": len(list(app.url_map.iter_rules())),
                          "timeline_at_run": list(timeline)})

        with patch.object(flask.Flask, "run", run), patch.dict(os.environ, {"PORT": port} if port else {}, clear=True):
            runpy.run_path(str(source), run_name="__main__")
        if effects:
            raise AssertionError("external effect attempt invalidates CLI replay")
        return {"calls": calls, "output": output.getvalue(), "effects": effects}


def cases():
    result = {"success": {}}
    result.update({"stage-" + stage: {"failure_stage": stage} for stage in composition.STARTUP})
    result.update({f"{kind}-{path}": {"route_failure": [kind, path]}
                   for path, _methods, _handler in composition.ROUTES for kind in ("route_factory", "route")})
    result.update({
        "settings-attribute": {"failure_stage": "settings", "error_type": "AttributeError"},
        "broker-type": {"failure_stage": "broker_adapters", "error_type": "TypeError"},
        "dependency-import": {"dependency_failure": "runtime_config_support", "error_type": "ImportError"},
        "invalid-premium": {"premium": "{invalid"},
    })
    return result


def replay(source, selector):
    source = Path(source).resolve()
    if not source.is_file() or selector not in {"main:app", "main:initialize_runtime()"}:
        raise ValueError("provide a reviewed entrypoint file and a supported explicit selector")
    if gunicorn.__version__ != "26.0.0":
        raise ValueError("replay requires the repository's locked Gunicorn 26.0.0")
    observations, diagnostics, dependencies = {}, {}, {}
    for name, case in cases().items():
        observations[name], diagnostics[name] = _capture_service(source, selector, case, dependencies)
    for port in (None, "8181"):
        observations["cli-" + str(port)] = _capture_cli(source, port, dependencies)
    timeline, effects = [], []
    with _load(source, None, {}, timeline, effects, dependencies) as (state, _error, output):
        diagnostics["bare-import"] = {
            "timeline": timeline, "module": _module_state(state.module),
            "error": _exception(state.error), "output": output.getvalue(), "effects": effects,
        }
        if effects:
            raise AssertionError("external effect attempt invalidates bare-import replay")
    identity_paths = {
        "fixture": Path(composition.__file__), "runtime_doubles": ROOT / "tests/test_request_handling.py",
        "runner": Path(__file__), "pyproject": ROOT / "pyproject.toml", "lock": ROOT / "uv.lock",
        "dockerfile": ROOT / "Dockerfile",
        "gunicorn_resolver": Path(gunicorn.__file__).parent / "util.py",
        "gunicorn_failure_contract": Path(gunicorn.__file__).parent / "arbiter.py",
    }
    docker = json.loads(next(line.removeprefix("CMD ") for line in (ROOT / "Dockerfile").read_text().splitlines() if line.startswith("CMD ")))
    result = {
        "schema": "longbridge.synthetic_startup_replay.v1",
        "identity": {"source_sha256": _sha(source), "selector": selector,
                     "observed_repository_command": docker, "effective_command": [*docker[:-1], selector]},
        "environment": {"python": sys.version, "gunicorn": gunicorn.__version__,
                        "flask": importlib.metadata.version("flask"), "clock": CLOCK,
                        "hash_seed": os.getenv("PYTHONHASHSEED"), "dependency_modules": dependencies,
                        "inputs": cases(), "files": {key: _sha(path) for key, path in identity_paths.items()}},
        "service_cli": observations, "direct_import_diagnostics": diagnostics,
        "scope": "synthetic startup and HTTP dispatch; runtime execution callbacks are test doubles",
        "production_equivalence": "not_established", "execution_authority_granted": False,
    }
    return json.loads(json.dumps(result))


def _json(value):
    """Compare JSON observations without Python's bool/int/float coercions."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fields(value, names):
    if not isinstance(value, dict) or not set(names.split()) <= value.keys():
        raise ValueError("incomplete replay observation")


def _observed_list(value, count=None):
    if not isinstance(value, list) or not value or (count is not None and len(value) != count):
        raise ValueError("incomplete replay observation list")


def _validate_observations(report):
    for name, record in report["service_cli"].items():
        if name.startswith("cli-"):
            _fields(record, "calls output effects")
            _observed_list(record["calls"], 1)
            for call in record["calls"]:
                _fields(call, "arguments ready_routes timeline_at_run")
                _fields(call["arguments"], "host port")
                _observed_list(call["timeline_at_run"])
            continue
        _fields(record, "startup output error effects")
        _observed_list(record["startup"])
        if name == "success":
            _fields(record, "lifetimes routes repeated_success repeat_events http")
            _fields(record["lifetimes"], "RUNTIME_SETTINGS STRATEGY_RUNTIME BROKER_ADAPTERS STRATEGY_ADAPTERS")
            _observed_list(record["routes"], len(composition.ROUTES) + 1)
            request_count = sum(len(methods) + ("GET" in methods) + 2 for _path, methods, _handler in composition.ROUTES) + 8
            _observed_list(record["http"], request_count)
            _observed_list(record["repeated_success"], 2)
            for route in record["routes"]:
                _fields(route, "path methods endpoint handler_identity")
                _observed_list(route["methods"])
            for response in record["http"]:
                _fields(response, "path method status body headers callbacks")
                _observed_list(response["headers"])
        else:
            _fields(record, "same_injected_error worker_exit repeated_failure")
            _fields(record["repeated_failure"], "error same_injected_error worker_exit timeline")
            _observed_list(record["repeated_failure"]["timeline"])
            for error in (record["error"], record["repeated_failure"]["error"]):
                _fields(error, "type message cause suppressed_context")
                while error is not None:
                    _fields(error, "type message cause suppressed_context")
                    error = error["cause"]
    for name, record in report["direct_import_diagnostics"].items():
        if name == "bare-import":
            _fields(record, "timeline module error output effects")
            _fields(record["module"], "present initialized globals_present defined_names")
        else:
            _fields(record, "after_load")
            _fields(record["after_load"], "present initialized globals_present defined_names")
            if name != "success":
                _fields(record, "after_repeated_failure")
                _fields(record["after_repeated_failure"], "present initialized globals_present defined_names")


def compare(baseline, candidate):
    """Keep direct-import differences visible and reject missing/incompatible evidence."""
    for report in (baseline, candidate):
        _json(report)
        if report.get("schema") != "longbridge.synthetic_startup_replay.v1":
            raise ValueError("unsupported replay evidence schema")
        identity = report.get("identity", {})
        if not re.fullmatch(r"[0-9a-f]{64}", identity.get("source_sha256", "")) or identity.get("selector") not in {"main:app", "main:initialize_runtime()"}:
            raise ValueError("missing source or launcher identity")
        command = identity.get("effective_command")
        if not isinstance(command, list) or len(command) < 2 or not all(isinstance(token, str) for token in command) or command[0] != "gunicorn" or command[-1] != identity["selector"]:
            raise ValueError("invalid launcher command identity")
        environment = report.get("environment", {})
        _fields(environment, "python gunicorn flask clock hash_seed dependency_modules inputs files")
        if not environment["python"] or environment["gunicorn"] != "26.0.0" or not environment["flask"]:
            raise ValueError("missing or incompatible runtime identities")
        if not environment.get("files") or not environment.get("dependency_modules") or environment.get("inputs") != cases():
            raise ValueError("missing frozen inputs or dependency identities")
        expected = set(cases()) | {"cli-None", "cli-8181"}
        if set(report.get("service_cli", {})) != expected or set(report.get("direct_import_diagnostics", {})) != set(cases()) | {"bare-import"}:
            raise ValueError("incomplete replay corpus")
        _validate_observations(report)
        effects = [value["effects"] for value in report["service_cli"].values()]
        effects.append(report["direct_import_diagnostics"]["bare-import"]["effects"])
        if any(not isinstance(value, list) for value in effects):
            raise ValueError("invalid external-effect observations")
        if any(effects):
            raise ValueError("external effect attempt invalidates replay")
    if _json(baseline["environment"]) != _json(candidate["environment"]):
        raise ValueError("replay environments or frozen inputs differ")
    if baseline["identity"]["effective_command"][:-1] != candidate["identity"]["effective_command"][:-1]:
        raise ValueError("Gunicorn arguments differ beyond the explicit selector")
    service = [key for key in baseline["service_cli"] if _json(baseline["service_cli"][key]) != _json(candidate["service_cli"][key])]
    direct = [key for key in baseline["direct_import_diagnostics"]
              if _json(baseline["direct_import_diagnostics"][key]) != _json(candidate["direct_import_diagnostics"][key])]
    initial = [key for key in baseline["service_cli"] if
               _json({k: v for k, v in baseline["service_cli"][key].items() if k != "repeated_failure"})
               != _json({k: v for k, v in candidate["service_cli"][key].items() if k != "repeated_failure"})]
    repeated = [key for key in baseline["service_cli"] if _json(baseline["service_cli"][key].get("repeated_failure"))
                != _json(candidate["service_cli"][key].get("repeated_failure"))]
    return {
        "supported_launch_transcripts_equal": not service,
        "all_recorded_observations_equal": not service and not direct,
        "initial_launch_and_http_equal": not initial,
        "initial_launch_differences": initial, "same_interpreter_retry_differences": repeated,
        "service_cli_differences": service, "direct_import_differences": direct,
        "baseline_source": baseline["identity"], "candidate_source": candidate["identity"],
        "production_equivalence": "not_established", "execution_authority_granted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--selector", choices=("main:app", "main:initialize_runtime()"))
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BASELINE", "CANDIDATE"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.compare:
        if args.source or args.selector:
            parser.error("choose capture or comparison, not both")
        result = compare(*(json.loads(path.read_text()) for path in args.compare))
        code = 0 if result["all_recorded_observations_equal"] else (2 if result["supported_launch_transcripts_equal"] else 1)
    else:
        if args.source is None or args.selector is None:
            parser.error("capture requires --source and --selector")
        result, code = replay(args.source, args.selector), 0
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
