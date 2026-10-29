# Runtime write isolation (image / env / traffic / Scheduler)

Short operator notes for LongBridge Cloud Run release writes. This is not a
new release platform and does not authorize production dispatch.

## Two dispatch paths

| Mode | Purpose | Default effect on serving traffic | Default effect on Scheduler enabled |
|---|---|---|---|
| `image-only-no-traffic` | Stage one approved image for PAPER/HK/SG | Unchanged (`--no-traffic`) | Unchanged (job does not touch Scheduler) |
| `legacy` | Deploy image and/or sync env/schedule for configured targets | Unchanged (`--no-traffic` on deploy and env update) | Unchanged (`scheduler_enabled_action=preserve`) |

Matrix `fail-fast: false` is retained: one target failure does not cancel sibling targets.

## Explicit actions (independent of ordinary sync)

Set these only when the operator intends the side effect:

- `shift_traffic_to_commit=true` — after admission checks, route 100% traffic to the Ready revision labeled with this commit, then read traffic back.
- `scheduler_enabled_action=pause|resume` — pause or resume the managed main/probe/precheck jobs, then read state back.

Readbacks:

- Traffic: `scripts/reconcile_cloud_runtime.py --read-traffic`
- Scheduler enabled: `--read-scheduler-enabled`

## What ordinary image/env sync may still do

- Create/update schedule, timezone, URI, and attempt deadlines (T11 `600s` on main).
- Delete known legacy Scheduler job names.
- Pause **newly created** Scheduler jobs under `preserve` so create cannot silently enable production triggers. Existing jobs keep their prior enabled state.

## What it must not do by default

- Move Cloud Run serving traffic.
- Pause/resume existing Scheduler jobs from `RUNTIME_TARGET_ENABLED` / desired config alone.
- Treat Guard success, CI green, or deploy success as a natural trading cycle.

## Runtime Guard evidence boundary

Runtime Guard checks configured Cloud Run and Scheduler logs for errors. Its
no-alert message only describes those log checks, not strategy-cycle completion.
By default, `RUNTIME_GUARD_REQUIRE_SUCCESS=false` also allows a window with no
HTTP responses. A zero exit code alone is not even an absence-of-alert guarantee:
`RUNTIME_GUARD_FAIL_WORKFLOW_ON_ALERT=false` allows it after an alert is emitted.

The HTTP count includes all 2xx/3xx responses, including health checks, login
redirects, and disabled no-op requests. Setting
`RUNTIME_GUARD_REQUIRE_SUCCESS=true` requires such a response for each configured
service, but still does not check business-cycle completion.

Assess business operation from a timely execution report for the actual target,
with account identity, order outcomes, and reconciliation evidence. If that
evidence is missing or stale, business-cycle status remains unknown even when
the log guard passes. These log checks do not authorize production recovery.

## Cycle heartbeat and account values

An authorized, market-open `/run` emits its no-trade heartbeat through the existing
`run_strategy` notification branch as the cycle finishes. `NOTIFY_LANG` selects
Chinese or English. Pending orders and completed actions retain their existing
separate notifications; disabled, market-closed, validation-only and dry-run paths
do not gain a success heartbeat. Existing execution deduplication and notification
delivery acknowledgement remain unchanged.

The scheduled execution-report scanner keeps its cadence and failure alerts, but
never sends success summaries, even if the legacy
`RUNTIME_HEARTBEAT_NOTIFY_ON_SUCCESS` variable is true. This prevents delayed or
duplicate success notifications and avoids describing a no-due window as a
successful business cycle.

The cycle carries a display-only `heartbeat_account_snapshot` into its notification
and `summary.heartbeat_account_snapshot` report field from its own existing initial
broker balance read: `available_cash`, `cash_currency`, `net_assets`,
`equity_currency`, and ISO `observed_at`. Each amount is validated separately.
Account total equity requires one unambiguous account balance row with finite
positive broker `net_assets`; it keeps the broker's own currency and never uses
the strategy subset's equity. Cash sums explicit finite rows in the trading
currency and labels that currency separately. Missing cash rows or fields are not converted
to zero; unavailable amounts display
`未核实` / `Unverified`. The timestamp labels the pre-rebalance observation.
Notification rendering never reads the broker or falls back to an older report.

## Safety gates kept

- V7 paused PAPER binding refusal
- Deployed runtime-target admission before traffic shift
- Exact SHA / approved-ref checks on `image-only-no-traffic`
- Per-environment service/region binding used by Runtime Guard
