# Runtime startup

## Current contract

`main.initialize_runtime()` contains the runtime dependency imports,
configuration loading, startup receipts, adapter construction and route
registration. It returns `main.app`. A successful initialization is recorded
only after all routes have been registered; subsequent calls return the same
application without loading configuration or rebuilding adapters.

The module still calls `initialize_runtime()` during import. This compatibility
bootstrap preserves the existing lifecycle for both supported launch paths:

- The container starts Gunicorn with the `main:app` selector. Initialization
  completes before the WSGI application is returned to the worker.
- Running `python main.py` initializes the same runtime before calling
  `app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))`.

Startup retains the original dependency import order and the ordering of
Flask construction, configuration validation, strategy loading, startup
receipts, notification formatting, broker adapters, strategy adapters and
routes. Errors propagate from the same import boundary. Runtime initialization
does not submit an order or send a notification.

The runtime remains module scoped. Existing handlers and callbacks continue to
read the module globals, including values replaced by offline test fixtures.
The initializer is not a configuration reload API. Its initial invocation
remains inside Python's normal module import lifecycle.

`main.create_app()` creates a separate HTTP application over the initialized
runtime. It does not repeat runtime initialization. For HTTP-only assembly,
callers can supply handlers to
`application.http_app.create_http_app(import_name, handlers)` without importing
`main`.

## Decision: retain the compatibility bootstrap

**Status:** preparatory extraction. Plain `main` imports still initialize the
runtime. Full import isolation is pending an owner-approved entry point change.

The locked Gunicorn version is 26.0.0. Its
[`import_app` resolver](https://github.com/benoitc/gunicorn/blob/26.0.0/gunicorn/util.py)
imports the module, then resolves the named attribute in a separate
`AttributeError` handler. A startup `AttributeError` raised during module
import propagates unchanged. Raising that same error from a lazy
`__getattr__("app")` export instead causes Gunicorn to replace it with an
`AppImportError` reporting a missing application attribute.

The distinction also affects the
[`Arbiter.spawn_worker` failure path](https://github.com/benoitc/gunicorn/blob/26.0.0/gunicorn/arbiter.py):
an application import error exits with status 4, while an exception before
worker boot completes exits with status 3. Both stop the worker, but they have
different diagnostics and failure classifications. A callable WSGI proxy would
defer initialization until the first request, which would also change the
startup contract.

For these reasons, this extraction keeps the existing `main:app` selector and
the eager compatibility call. It does not add a lazy application export,
change exception handling, or change the container command.

## Owner decision needed for full import isolation

The next change must be reviewed as one coordinated change:

1. Change only the final application selector in the Docker command from
   `main:app` to `main:initialize_runtime()`; preserve all Gunicorn options.
2. Remove the unconditional compatibility call from `main`, and explicitly
   invoke `initialize_runtime()` in the `__main__` branch before `app.run`.
3. Update offline fixtures and any direct runtime callers to initialize
   explicitly. Add cold-import sentinels and repeat the startup and HTTP
   characterization against the updated launch paths.

Gunicorn invokes an explicit factory outside the attribute-lookup handler, so
an internal startup `AttributeError` retains its original classification. Its
factory-call handling also distinguishes an internal `TypeError` from an
invalid factory argument list. The exact locked resolver has been exercised
offline for these cases.

The owner must authorize the production entry point change before this next
step. No deployment or dependency update is part of the preparatory extraction.
Rollback of the eventual isolation change must restore both the `main:app`
selector and the eager compatibility call together.

## Verification boundary

`tests/test_http_composition.py` preserves the existing startup order, route
registration, handler identity, HTTP responses and failure behavior.
`tests/test_runtime_initialization.py` exercises the actual Gunicorn resolver,
CLI binding arguments, repeated initializer calls and dynamic module-global
lookup in broker callbacks. Broker, credential and notification operations are
replaced with offline test doubles. No test starts a server or submits an order.

These checks establish the preparatory extraction's behavior. They do not
assert that importing `main` is side-effect free.
