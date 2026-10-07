# LB PAPER cycle-health projection v1 (local policy increment)

`scripts/runtime_cycle_health.py` is a pure source-policy module. It projects
already-read facts and validates recovery evidence. It has no CLI, network,
storage, environment, strategy, broker, workflow or notification operations.
It never grants execution authority. The reference incident reducer always
returns `execution_authority_granted: false`; reconciliation acceptance does
not enable a target or change an execution/continuity control.

## Small wire object

The proposed optional lifecycle target member `cycle_health` contains exactly:

1. `schema_version`: `qsl.runtime_cycle_health.v1`
2. `configuration_sha256`: current effective configuration digest
3. `schedule`: `state`, bounded `reason`, `timezone`, `latest_due_at`,
   `next_due_at`, `deadline_at`
4. `coverage`: `from`, `through`, `listed_count`, `read_count`,
   `terminal_page_seen`, `limit_hit`, `errors_present`
5. `cycles`: up to 20 tuples containing `cycle_id`, `scheduled_for`,
   `receipt_ref`, `completed_at`, `outcome`, `execution_state`, `correlation`
6. `resolutions`: up to 20 tuples containing `incident_cycle_id`, `kind`,
   `evidence_ref`, `resolved_by_cycle_id`, `verified_through`

`schedule.timezone` may be null only when the source explicitly reports
`unevaluable` with `missing_timezone` or `invalid_timezone`. This preserves an
honest unknown; it does not substitute UTC or a valid market zone. Evaluable
schedule states still require a valid timezone.

Existing outer lifecycle fields supply observation time, target/profile/lane
and source admission. The producer must derive the schedule from its actual
effective Scheduler/calendar/window policy. This module checks those supplied
facts; it does not invent cron, infer cadence from freshness, or duplicate a
strategy's decision logic. A successful no-action receipt proves an evaluation,
even when strategy action cadence is monthly. A no-due schedule alone does not
establish a missing historical baseline or clear an incident.

`CycleContext` is injected by the existing trusted source/target admission
boundary. Its source-binding digest is not independently authenticated by this
module. Missing provisioning or unverified actual binding must block integration;
passing a syntactically valid digest does not establish a broker identity.

## Raw evidence in, compact references out

`derive_coverage` consumes required prefixes, actual supplied provider pages
(including request and continuation tokens), and decoded report reads. It uses
the real QPK receipt/report validator for each candidate. It does not accept a
`verified` or `complete` shortcut. Every required prefix must have a positively
observed terminal page, every candidate must be successfully read/validated,
and all limits/errors must be clear. Exactly 20 candidates can be complete;
20 with continuation or a 21st candidate cannot. An aborted collector must
supply `aborted=True`, never fabricate an empty last page. Coverage remains an
authenticated source attestation, not a server-side independent storage audit.

`project_cycle` validates the actual canonical execution receipt against its
runtime report, release revision and lane, then exports its existing receipt
ID. It checks real errors, pending state and report timestamps. The original
LB failed fallback can attach its receipt after finalization and omit success
submission counters: this stays execution-uncertain. Headers themselves are
not authentication. Future `/run` integration must establish trusted Scheduler
invocation context before supplying the exact job/scheduled time. Legacy
window-matched evidence cannot prove a same-cycle retry.

`build_cycle_health` packages the six fields. A missing expected receipt is
projected only for a source-declared due slot, after its supplied deadline and
positively complete coverage;
it means unknown execution, not proof that no order occurred. Incomplete reads
remain explicit and must never be rendered healthy by a consumer.

## Recovery and retained incidents

Known no-submission failures are operational incidents. Missing, submitted,
partial, unknown and unreconciled outcomes remain execution uncertainty.

- `same_cycle_retry_succeeded` requires a raw successful report, validated
  again through QPK, and an explicit same scheduled occurrence. It can resolve
  a known-no-submission failure, never an uncertain earlier submission
- `current_state_reconciled` recomputes QPK recovery blockers using the existing
  `longbridge_reconciliation_candidate.v1` and separately supplied expected
  baseline/digests. An optimistic candidate wrapper cannot bypass failed
  identity, position, cash, order, execution, local-ledger, freshness or digest
  checks. The coverage interval must span the incident and reconciliation

`reduce_incidents` is a pure reference reducer for already-admitted projections,
not an HTTP ingress validator. Each incident retains its fault attempts by
stable receipt reference, the proof that resolved each attempt, and accepted
resolution history. Re-listing a known attempt is a no-op without requiring the
caller to resend its resolution. The same receipt with conflicting projection
data is rejected. A newly discovered fault remains unresolved; replaying an old
proof cannot expand the set of attempts that proof already resolved.

The historical highest-severity representative, latest-fault timestamp and
current active category are separate. New recovery must cover the latest
retained fault watermark and every active attempt. A known-no-submission retry
cannot clear another active uncertain attempt; previously reconciled uncertainty
does not itself prevent a later independent operational attempt from recovering.
Resolved incidents and attempts remain in history. The bounded reference state
fails closed rather than forgetting old attempts to fit a cap. The earlier,
unpublished prototype checkpoint without attempt history is not auto-migrated.

A heartbeat, later success, new configuration or acknowledgment cannot silently
resolve an incident. Binding changes are refused and the prior partition is
untouched. Same-binding configuration changes preserve previous incidents;
cross-configuration recovery needs a separately verified continuity contract.
The current LB binding is deployment/token-version scoped, not stable physical
identity. This increment performs no history migration or revision filtering.

## Explicit integration limits

This increment does not implement receiver/DO persistence, monotonic ingress,
baseline initialization, liveness/activation/freshness health labels, complete
cross-date schedule enumeration, workflow wiring, report invocation metadata,
or evidence delivery. Those remain the reviewed integration phases. The future
source must enumerate every expected slot over a required interval; the local
packager only synthesizes the supplied latest missing slot. It must not be used
as an all-missed-slots enumerator.

The existing reconciliation lane supports PAPER in source, but its provisioned
state and delivery path are unverified. No new reconciliation/broker call is
authorized here. If usable recovery evidence is absent, keep the problem
explicit; local policy tests do not prove production health is restored.

Focused validation: `python -m pytest -q tests/test_runtime_cycle_health.py` and
`ruff check scripts/runtime_cycle_health.py tests/test_runtime_cycle_health.py`.
Use the repository's pinned QPK validators; all test accounts and evidence are
synthetic. Run the repository aggregate suite before publication/integration.

## A: offline source wiring candidate (2026-10-07)

The preceding pure helper remains byte-identical to reviewed revision2. This
additive candidate introduces one injected collector and shared exact Scheduler
selection / interval enumeration in the existing heartbeat policy module.
It does not wire a lifecycle workflow, receiver, frontend or live transport.

### Compatibility and adoption

`publish()` and its existing CLI remain on the original daily path. Their
historical route matcher is explicitly named `_legacy_confirmed_scheduler`;
retaining it does not make that matcher verified or confer new cycle-health
authority. `publish_verified(..., serving_context=...)` is a separate, unwired
opt-in. Missing proven context rejects this new path as `schedule_unevaluable`
before I/O. Merely merging these source files does not adopt it or interrupt the
currently publishing natural PAPER daily report. Neither daily path emits
`cycle_health`; calendar-day history and source cycle health remain separate.

Roll out receiver-first: accept the optional six-member envelope and persist
attempt-aware state transactionally in the existing DO, establish the protected
binding/checkpoint and serving-context inputs, then explicitly wire PAPER's
existing lifecycle producer. Verify coexistence and natural daily delivery
before any separate strict daily caller switch. Do not flip all platforms or
require new fields from producers that have not adopted them.

### Required injected facts

`collect_runtime_cycle_health` takes no credentials and has no CLI/default cloud
transport. `read_source()` must return the independently admitted current facts:

- `target`: the existing normalized PAPER target
- `serving_context.service` and `.revision`: actual service/serving-revision
  metadata, with one 100% serving revision, Ready condition, source commit and
  revision environment; `.route_contract`: independently established serving
  artifact service/source commit/exact path/POST method
- `jobs`: complete relevant Scheduler readback, retaining actual URI, method,
  state, name, cron and timezone; no template or name-guessed fallback
- `release_contract`: source commit and expected strategy revision
- `source_binding`: the already-provisioned protected binding ID and its admitted
  association to the service/serving revision
- `monitor_policy`: the producer's effective existing
  `RUNTIME_HEARTBEAT_PUBLICATION_GRACE_MINUTES` setting, explicitly supplied

The helper checks agreement, exact HTTPS origin/path/method and unique enabled
job; it cannot authenticate injected objects. Strategy calendar/profile/mode
come from serving revision values using existing normalization. Effective grace
is validated, applied and included in configuration identity. A digest is never
proof of account provisioning or identity. Source context, effective policy,
required report prefixes and the interval start must come from an admitted
producer/checkpoint boundary before live adoption; this candidate does not
invent that boundary. Neither current repository main nor the job URI proves
which route a serving artifact exposes. Unknown route/context stays unevaluable.

`list_page(prefix, token)` supplies actual provider pages with items and
continuation tokens. `read_report(name)` supplies bounded decoded raw reports.
The collector records requested tokens and requires every supplied prefix's
positive terminal page plus successful canonical receipt validation. Limits:
20 reports/events, 21 pages per prefix, 366-day expectation horizon, 20-second
default total budget (maximum 60), 1 MiB decoded report and 256 KiB cumulative
listing JSON. The future transport must independently enforce wire/download
limits and callback timeouts before decoding; Python cannot preempt an injected
callback. Post-callback budget overruns cannot return ready. Timeouts, caps,
malformed pages, byte failures and late-after-cutoff receipts never become
complete evidence. Paired source reads detect drift; they are not an atomic
cloud snapshot or an immutable listing guarantee.

### Expectations, correlation and recovery

The collector enumerates every source cron/calendar slot within the explicit
same-configuration interval, including multiple missed cycles and DST. It does
not derive cadence from a freshness TTL or require a report for today's date.
The optional reporting-window callback describes the current observation only;
it cannot erase expected invocations or past faults. Monthly strategy action
windows do not exempt a daily invocation: a valid `no_action` receipt satisfies
that cycle. Missing invocation receipts remain execution uncertainty.

Current LB source does not establish authenticated per-run Scheduler invocation
provenance. Consequently A removes untrusted `invocation` fields from report
copies and emits only `window_matched` correlation. Adding job/time fields to a
legacy report cannot grant recovery authority. A rejects
`same_cycle_retry_succeeded` with `invocation_provenance_unavailable`; the frozen
pure helper retains that capability for a later authenticated producer contract.
Existing `current_state_reconciled` remains available only through the actual
QPK/LB validators and independently admitted baseline/digests. Its delivery and
provisioning remain unverified; this candidate calls no broker or reconciliation
producer and does not promise full production recovery without usable evidence.

Outer `data_status=ready` means this bounded evidence collection completed. It
is not the website's healthy label, a first-baseline assertion or execution
permission. A returned checkpoint is an in-memory proposal, not durable ACK.
The receiver still owns adoption state, authenticated binding, source ordering,
idempotent transactions, freshness and the two-label user status. Incomplete
and changed-config/binding cases cannot clear prior incidents. Same physical
account history should eventually survive deployment changes, but present LB
binding has no independently established physical continuity: no cross-binding
migration or revision-wide history filtering is performed here.
