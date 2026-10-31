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

**Status:** entry point migration is pending offline replay equivalence. The
owner's decision is to retain `main:app` and the eager compatibility bootstrap
until replay demonstrates equivalent behavior for the supported service and CLI
launch contracts. The proposed `main:initialize_runtime()` migration remains
pending; successful preparatory characterization does not satisfy that condition.

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

## Pending migration and replay acceptance

Any future production migration must be reviewed as one coordinated change after
the replay condition is satisfied:

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

The current draft retains the selector and compatibility call. Offline replay
must bind its baseline and candidate to the exact source revisions, launcher
arguments, Python and Gunicorn versions, dependency identities, configuration
fixtures and input versions used. Keep the production launcher and dependency
configuration unchanged while preparing replay, and record each intended
candidate launch difference. An older draft's Dockerfile is not a substitute for
a current production baseline. Missing or mismatched identities leave acceptance
pending.

The existing `test_explicit_gunicorn_factory_reuses_import_bootstrap` test imports
`main` while the eager compatibility call is still present. It verifies reuse of
an initialized runtime. It does not exercise a cold module import followed by the
factory call after removal of that bootstrap.

Replay acceptance requires the following observations from both versions:

| Area | Required comparison |
| --- | --- |
| Startup | Dependency and startup event order, settings and adapter lifetimes, startup receipts, route-registration order, and application availability before the first request. Repeat initialization after success must retain its established behavior. |
| Failure paths | Configuration, dependency, adapter and route failures, including internal `AttributeError` and `TypeError`. Preserve propagated exception identity within each run; compare exception type, message, cause chain, failure stage and worker exit classification across runs using the exact Gunicorn resolver and worker failure contract. |
| Partial initialization | State retained after each injected startup failure, repeated initialization and fresh-worker restart. A cold factory failure can leave `main` imported; a failed eager import removes the module. That difference must be evaluated rather than assumed equivalent. |
| HTTP behavior | All 11 application route registrations, their methods, endpoint and handler identities, HEAD/OPTIONS/405 responses, callback arguments, health responses and headers, and unknown or paused outcomes. |
| CLI behavior | Initialization timing and the host/port arguments used by `python main.py`, without opening a listening socket. |
| Determinism and effects | Controlled clocks and fixed synthetic or approved frozen inputs. Compare captured output and effect intents. Broker, credential, notification, cloud, DNS and socket operations must be intercepted; any real external service I/O attempt invalidates the offline run. |

Cold-import isolation deliberately changes bare-import side effects. Record that
difference and the affected direct callers separately. Equivalence of supported
service and CLI launches must not be described as equivalence of every import or
direct-call behavior. Synthetic characterization establishes only its recorded
cases; it is not proof of historical production replay. A replay report must
identify its tested cases and unresolved coverage without converting missing
observations into successful outcomes.

Completing offline acceptance does not grant merge, deployment or operational
authority. Drafts remain subject to automated checks, independent AI review and
the applicable risk tier. The production entry point remains unchanged until
the replay condition and normal operational approval are satisfied. If an active
governance check requires `owner-approved`, only the repository owner may apply
that label; agents must stop advancing that PR, keep it as a draft and list it
for owner approval.
Rollback of an eventual isolation change must restore both the `main:app`
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
