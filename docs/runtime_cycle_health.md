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

## A: bounded source adapter (2026-10-08)

The pure projection and injected collector remain free of credentials and
transport. A separate opt-in publisher now connects the LongBridge PAPER
lifecycle workflow to the QRS internal source GET/POST; it remains disabled by
default and emits no order authority or recovery proof.

### Compatibility and adoption

`publish()` and its original CLI remain on the original daily path. Their
historical route matcher does not confer cycle-health authority. The separate
`publish_runtime_cycle_health.py` path runs only when the protected
`RUNTIME_CYCLE_HEALTH_ENABLED` flag is exactly `true`; its missing or unadmitted
source configuration stops before report listing. The daily projection and
cycle-health observation remain separate records.

An uninitialized or blocked GET is accepted only with the authenticated
top-level configuration/binding, fixed `required_from`, revision zero and no
checkpoint or prior observation. Existing prior-partition history is preserved;
it does not establish continuity or clear blocked status. A checkpoint carrying
an old configuration digest is rejected rather than reused.

The workflow step is restricted to enabled LongBridge PAPER and requires
protected Environment configuration plus QRS source admission. The underlying
collector rejects a disabled serving target, so this source does not create a
disabled-day cycle-health record; keep the existing daily lifecycle/report path
for stopped targets and do not enable execution to make this source run. A merge,
local test or successful source POST is not a natural-cycle recovery result.

The outer `generated_at` and `computed_at` identify the actual current source
observation. The nested `coverage.from` and `coverage.through` bound the
historical report scan; `through` must not be later than `computed_at`, and
cycle timestamps must not be later than `through`. Freshness and observation
ordering use `computed_at`; only a complete, continuous ACK advances
`covered_through` to `coverage.through`. A gap or partial page is stored as
incomplete and leaves the cursor unchanged.

The publisher caps each observation at 20 scheduled slots and the collector at
20 candidate reports. A long interval can continue through later invocations:
each run reads the last ACK cursor (with the existing grace overlap), uses a
new current observation time, and posts only its bounded historical range. It
does not sweep repeatedly within one workflow run. If one chunk contains more
than 20 candidate reports, or any required prefix/page is incomplete, that
observation does not advance the cursor; retries or a corrected source listing
are required. This cap does not guarantee every unusually dense retry history
will fit a chunk.

The report root comes only from the verified 100%-traffic Ready serving
revision's unique plain `EXECUTION_REPORT_GCS_URI`. The root is included in the
configuration identity by its SHA-256; a changed root therefore requires a new
QRS admission and does not inherit the prior cursor. Secret references,
missing/duplicate roots and conflicting explicit workflow values stop before
report listing. The URI itself is not included in logs or the configuration
digest.

This path still does not backfill authenticated recovery proof. Scheduler
headers alone do not prove the exact invocation, and report provenance is not
independently tied to a verified `/run` invocation. `resolutions` remains empty;
the QRS durable archive is authoritative for faults and no source summary may
be treated as complete incident history.

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
continuation tokens. The workflow adapter uses `list_page_range` with exact
UTC-month object prefixes and inclusive/exclusive GCS name offsets derived from
the producer's report path and UTC run-id format. This keeps older objects in
the same month outside the bounded interval's report cap. `read_report(name)`
supplies bounded decoded raw reports.
The collector records requested tokens and requires every supplied prefix's
positive terminal page plus successful canonical receipt validation. Limits:
20 reports/events, 21 pages per prefix, 366-day expectation horizon, 20-second
default total budget (maximum 60), 1 MiB decoded report and 256 KiB cumulative
listing JSON. The GCS adapter enforces provider paging and per-report byte
limits before decoding; Python cannot preempt an injected
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

Current LB reports do not independently bind report origin to the authenticated
request or deployed revision. Consequently the collector removes untrusted `invocation` fields from report
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
permission. The collector's returned reducer checkpoint remains in-memory and
is not sent as authority; QRS's persisted attempt archive and `covered_through`
are the durable source state. GET's summary is never loaded into the local
reducer. A complete ACK must match the exact submitted normalized snapshot;
partial coverage can be stored but cannot advance the QRS cursor. Missing
protected configuration, changed source binding or unknown responses remain
blocked. Same physical
account history should eventually survive deployment changes, but present LB
binding has no independently established physical continuity: no cross-binding
migration or revision-wide history filtering is performed here.
