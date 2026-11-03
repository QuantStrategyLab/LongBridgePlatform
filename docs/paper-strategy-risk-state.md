# Paper strategy risk-state persistence

LongBridge can persist a strategy-produced `StrategyRiskStateTransition` only
when `LONGBRIDGE_STRATEGY_RISK_STATE_PAPER_ENABLED=true` and
`LONGBRIDGE_DRY_RUN_ONLY=true`. The feature is disabled by default.

The strategy must place the already-calculated transition at
`execution.strategy_risk_state_transition`. LongBridge validates that the
transition has the runtime's strategy profile and account scope, then writes it
through the dedicated `LONGBRIDGE_STRATEGY_RISK_STATE_CLOUD_URI` (or local
development directory) store. It returns a redacted receipt in
`execution.strategy_risk_state`.

The store URI must be distinct from `LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI`.
It uses create-only root/successor objects, so repeated delivery is idempotent,
while a missing predecessor, concurrent writer, stale head, malformed state,
or identity mismatch fails closed. This module has no broker order import and
does not change allocations or authorize a paper/shadow/live command.

No existing LongBridge strategy emits this transition yet. Enabling the flag
before a qualified strategy supplies a frozen transition will stop the cycle
with an operator-visible error rather than silently forgetting state.

## Russell regime binding

The pinned UES Russell entrypoint requires a separately configured
`market_regime_control_expected` binding, even when position-control route
application is disabled. LongBridge reads the binding and the
`market_regime_control` signal from the existing `strategy_config_path` JSON
object on every feature-snapshot evaluation. The binding is passed as runtime
configuration; the signal is passed as a context artifact, not as a ranking
parameter. Other profiles keep their existing configuration path.

The platform never derives an expected digest from the supplied signal. The
configuration producer must provide the independent binding fields `plugin`,
`schema_version`, `as_of`, `valid_until` and `payload_sha256`. UES validates the
plugin, schema, signal date, evaluation date range and canonical JSON SHA-256
against the exact signal. Missing or invalid bindings and missing signals keep
ordinary decisions non-actionable; unreadable or malformed configuration stops
evaluation with a fixed diagnostic. Restarting a process does not grant an
exception, and the next evaluation re-reads configuration after revocation.

This consumer integration does not publish a signal, supply approval, enable a
runtime target or qualify a PAPER session. Authentic signal and independent
binding provisioning remain prerequisites for deployment under owner approval.
