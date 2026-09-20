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

## Safety gates kept

- V7 paused PAPER binding refusal
- Deployed runtime-target admission before traffic shift
- Exact SHA / approved-ref checks on `image-only-no-traffic`
- Per-environment service/region binding used by Runtime Guard
