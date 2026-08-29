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
