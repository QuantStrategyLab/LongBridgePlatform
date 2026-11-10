# PAPER Scheduler metadata access proposal

Status: Draft proposal only. Neither merge nor these files apply IAM.

## Scope and identity

This proposal adds `cloudscheduler.jobs.get` only to the existing Box read
identity, `grok-gcp-ro`, in the logical `longbridge-paper` project only.
The custom project role ID is `paperSchedulerJobsGet`. Its complete permission
set is in [the role definition](paper-scheduler-get-role.json); the single
proposed additive binding is in [the binding template](paper-scheduler-get-binding.template.json).
The binding template is one binding, not a complete IAM policy: never submit
it as a replacement project policy.

`<PROJECT_ID>` denotes only the privately verified project ID for
`longbridge-paper`. `<EXISTING_BOX_READ_SA_EMAIL>` denotes the existing paper
reader selected by the restricted `grok-gcp-ro` authority and its existing
Box read configuration. It never denotes a new service account or the mixed
deployment identity. Resolve both through approved protected configuration
at the separately authorized apply boundary; no real ID or email belongs here.
Missing or ambiguous mappings block application. These templates are not
connected to any workflow, provisioning module or runtime loader.

The binding is project-level and covers **all Cloud Scheduler jobs in the
PAPER project**, including jobs beyond the three currently known PAPER jobs.
[Cloud Scheduler access control](https://cloud.google.com/scheduler/docs/access-control)
documents that job-level IAM policies and IAM Conditions resource attributes
on individual jobs are unsupported. There is no `resource.name` condition or
claim of three-job enforcement. The project-wide scope was explicitly chosen
for this Draft under owner decision `t1293s7520=A`.

No `cloudscheduler.jobs.list`, `run`, `update`, `pause`, `resume`, `delete`,
`create`, broader viewer/editor role, other project binding or identity trust
change is proposed. This is an additive permission proposal; it does not prove
or reduce any permissions the identity might already hold.

## Image metadata remains deferred

Image metadata permission classification remains `UNKNOWN_PERMISSION_CLASS` /
`OWNER_NEEDED_FOLLOWUP`: historical `PERMISSION_DENIED` evidence reported
`missing_permissions=[]`. Image grants are explicitly outside this proposal.
No Artifact Registry, Storage, Container Analysis or other image role is added.
The Scheduler permission alone cannot resolve the remaining M03 safety gaps.

## Separate owner merge/apply decision

The prior three-job-scoped Draft attempt concluded
`BLOCKED_MINIMAL_GRANT_UNSUPPORTED`. Its authority `t1293s7513=A` was consumed;
this Draft uses only `t1293s7520=A`. It does not reuse identity repair, inventory,
Gap B or earlier read-SA apply approvals.

After review, a separate owner card must bind the exact candidate revision,
PAPER project, existing reader and one-permission delta before merge or apply.
Any later authorized apply runs through protected GitHub Actions with GitHub
Environment credentials, a dry-run plan first and before/after readback.
It must resolve private mappings, verify current role contents and project
policy/etag, preserve unrelated bindings and stop on collisions, drift,
concurrent changes or ambiguous results. An existing role with different
permissions must not be adopted or overwritten. The later operation needs its
own single-writer control; this Draft supplies no executable apply entry point.
Do not replay a stale whole policy or automatically retry an uncertain write.

No cloud read or write, IAM apply, service-account/key/WIF creation, Secret
access, deployment, Scheduler enablement or PAPER start is performed by this
proposal. PAPER remains stopped; `READY_FOR_START_CARD=false`. Live, Kelly and
all stop-loss switches must remain OFF. A future successful IAM readback would
prove only that IAM delta, not runtime safety or permission to start PAPER.

## Restricted Actions entrypoint

[Apply PAPER Scheduler jobs.get IAM](../../.github/workflows/apply-paper-scheduler-jobs-get.yml)
provides the bounded entrypoint using
[the single-purpose adapter](../../scripts/paper_scheduler_get_iam.py).
This workflow proposal uses authority `t1293s7532=A` for Draft publication only.
The earlier merge/apply authority `t1293s7526=B` ended at
`MERGED_APPLY_BLOCKED_NO_ENTRYPOINT`; it is not reused. A separate owner card
must pin the merged workflow revision and authorize its dry-run and ONE apply.
No dispatch, IAM operation or PAPER start is authorized by this document.

The only trigger is `workflow_dispatch` on this repository's `main`, first run
attempt only, protected by Environment `longbridge-paper`. Enter exactly
`APPLY_PAPER_SCHEDULER_JOBS_GET`; `mode` defaults to `dry-run`. The concurrency
group `paper-scheduler-jobs-get-iam` queues writers without cancellation.
The single-writer window also requires other project IAM/role writers to stay
out; Actions concurrency cannot lock external clients.

Existing PAPER Environment variables `GCP_PROJECT_ID`,
`GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT`
supply existing WIF authentication. Two additional protected mapping variables
must already be verified at the later authorized boundary:
`PAPER_SCHEDULER_GET_PROJECT_ID` must equal `GCP_PROJECT_ID` for the approved
PAPER project, and `PAPER_SCHEDULER_GET_READER_EMAIL` must identify the existing
`grok-gcp-ro` reader. Neither accepts dispatch input. Missing or inconsistent
mappings fail before authentication. This PR neither configures these variables
nor verifies their deployed presence, Environment protection or the executor's
current IAM capabilities. Missing authority or permissions must block the later
operation, never prompt an automatic admin grant or new trust configuration.

Dry-run reads the role and version-3 project policy; it prints fixed aliases
and the permission list only. Apply repeats those reads, refuses snapshot drift,
creates the role only when absent, and never updates or undeletes an existing
role. An existing role must have exactly `cloudscheduler.jobs.get`, GA stage
and an etag. Conditional, duplicate or extra-member bindings for this role are
collisions and stop the operation. An already-correct role/binding is a no-op.

Project bindings use the Resource Manager `setIamPolicy` API with the explicit
field mask `bindings,etag` and the freshly read etag. This is a conditional
read-modify-write of the binding field, adding one member/role binding while
preserving every unrelated binding; it is not a whole-policy replacement or
an unconditional policy-file replay. Audit configuration is excluded from the
write. A concurrent policy change produces an etag conflict and stops. Before
and after reads validate exact role permissions, the single existing-reader
binding and unchanged unrelated policy fields. This API cannot atomically
lock a separate custom role: an external role writer remains a reason to
stop and investigate, and the owner must reserve a single-writer window.

Every HTTP mutation is attempted once, without redirects or automatic retry.
Transport failures, collisions, unreadable results and drift exit non-zero.
If role creation succeeds but the binding fails, the partial result remains;
there is no rollback, automatic second apply or recovery dispatch. Independently
read back remote state under a new owner decision before further action.
A process interruption has the same uncertain-result treatment; re-running an
Actions attempt is gated out. Logs emit fixed categories only, with no remote
payload, project ID, email, token or exception message.

The adapter has no permission, member, role or command input and exposes no
image, Artifact Registry, Storage, Container Analysis, Secret IAM, Scheduler
mutation, deployment, traffic or PAPER-start operation. Live, Kelly and all
stop-loss switches stay OFF. Image metadata remains
`UNKNOWN_PERMISSION_CLASS` / `OWNER_NEEDED_FOLLOWUP`.


### Executor permission observations

After authentication, a single read-only `projects.testIamPermissions` request
checks only `iam.roles.get`, `resourcemanager.projects.getIamPolicy`,
`iam.roles.create`, and `resourcemanager.projects.setIamPolicy`. The response is
limited to 4096 bytes. Output contains only `executor_preflight=held_all|missing`
and `executor_missing`, in the fixed allowlist order. An omitted permissions
field means none held; invalid, duplicate or unexpected values stop with a fixed
unknown category. HTTP, transport and JSON failures stop without retry or raw
response output, with phase `test_iam_permissions`.

These observations do not grant authority. A missing result still reaches the
existing role/policy validation; `held_all` proves neither API availability nor
successful later operations. The original role GET, accepted 404, policy checks,
CAS, dry-run default and apply gates remain required. A later executor grant,
especially project `setIamPolicy`, needs a separate owner decision. Publishing
this diagnostic does not authorize its execution or any IAM change.


## Protected executor bootstrap definition

The [fixed executor grant workflow](../../.github/workflows/grant-paper-executor-permissions.yml)
addresses the executor-permission blocker in the scheduler IAM route. It creates
one project custom role containing exactly `iam.roles.get`,
`resourcemanager.projects.getIamPolicy`, `iam.roles.create`, and
`resourcemanager.projects.setIamPolicy`, and binds it only to the existing
`apply-paper-scheduler-jobs-get` executor. These are project-level IAM powers;
this grant does not restrict what a compromised executor could do with those
permissions. The fixed script restricts the intended operation, while the
Environment approval and WIF trust remain essential access controls.

Publishing or merging this definition does not execute or authorize a grant.
The definition retains the four-permission scope of approval t1319s7706 and the
scheduler workflow lineage from PR #719; t1320s7720 authorizes this Draft only.
After merge, a separate owner run card must bind the merged main revision,
PAPER project, existing recipient, bootstrap identity, and grant scope. The
Environment reviewer must verify that card and a successful dry-run plan before
approving apply. The workflow also runs a read-only plan immediately before an
apply attempt. No scheduler apply, PAPER start, secret creation, deployment or
trading-switch change is part of this workflow.

Use the existing `longbridge-paper` Environment. It must have at least one
required reviewer and protect main; the workflow rejects a missing or unreadable
reviewer rule before cloud authentication. Current Environment policy is an
operational prerequisite, not established by committing this file. Maintainers
must ensure WIF trust limits the bootstrap to this repository, protected main
and Environment, and prevent unreviewed callers from assuming it.

Logical mappings exported by the grant ingestion step, never literal identifiers
in Git (the grant Secret transport names are listed below):

| Mapping | Required meaning |
| --- | --- |
| `GCP_PROJECT_ID` | Approved PAPER project |
| `PAPER_SCHEDULER_GET_PROJECT_ID` | Same approved project |
| `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` | Existing scheduler IAM executor |
| `PAPER_IAM_GRANT_RECIPIENT` | Independent approved mapping to that same executor |
| `PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT` | Existing authorized bootstrap service account in the same project, distinct from recipient |
| `PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` | Existing authorized WIF provider for that bootstrap |

No new production principal is proposed or created. A compatible bootstrap
identity, its effective authority and these additional protected mappings have
not been proved by offline tests. Missing mappings block execution; configuration
and WIF changes need their own authorization. Never substitute a broad new
administrator grant or reuse the known deficient executor as bootstrap.

Dispatch accepts only `dry-run` or `apply`, an exact merged-main revision,
`owner_run_card` reference and `GRANT_PAPER_EXECUTOR_FOUR_PERMISSIONS`
confirmation. Text input is an audit reference, not proof of owner authorization;
the required Environment reviewer validates it. Workflow reruns are rejected.
No grant dispatch is part of the Draft preparation job.

The script checks bootstrap permissions, reads role and policy, rejects role or
binding collisions, and re-reads before writes. It shares the scheduler IAM
workflow concurrency group, uses policy etag compare-and-set, changes only
bindings, and verifies the exact additive delta after writing. Transport errors,
unknown state and concurrent changes exit nonzero without write retries. Role
creation and policy binding are separate operations: partial or ambiguous
outcomes require readback and a new scoped run card before another attempt.
An existing exact role and binding are idempotent across process restarts.
Only fixed status messages are emitted; raw cloud responses stay in memory.


### Grant mapping redaction prerequisite

The fixed executor grant requires six grant-specific Secrets containing compact,
single-line JSON envelopes in Environment `longbridge-paper`:

| Grant Environment Secret | Envelope `name` / exported mapping |
| --- | --- |
| `PAPER_GRANT_ENVELOPE_GCP_PROJECT_ID` | `GCP_PROJECT_ID` |
| `PAPER_GRANT_ENVELOPE_PAPER_SCHEDULER_GET_PROJECT_ID` | `PAPER_SCHEDULER_GET_PROJECT_ID` |
| `PAPER_GRANT_ENVELOPE_GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` | `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` |
| `PAPER_GRANT_ENVELOPE_PAPER_IAM_GRANT_RECIPIENT` | `PAPER_IAM_GRANT_RECIPIENT` |
| `PAPER_GRANT_ENVELOPE_PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT` | `PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT` |
| `PAPER_GRANT_ENVELOPE_PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` | `PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` |

Existing scheduler/deploy raw-value Secrets retain their existing names and
values. In particular, `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` remains a raw
service-account email; do not replace it with JSON. The grant reads only the
Secret names in the first column, validates each envelope, and exports the
original mapping names in the second column after all six validate.

Each envelope contains `version` (integer `1`), `name` (the second-column mapping
name), `repository` (`QuantStrategyLab/LongBridgePlatform`), `environment`
(`longbridge-paper`), `scope` (`environment`), `revision` (the exact approved
source SHA), `owner_run_card` (the exact-generation run-card reference), and
`value` (the original mapping bytes). For each separately authorized run, trusted
provisioning must bind all six envelopes to the same `approved_revision` and
`owner_run_card` inputs. Refresh the generation under separate provisioning
authority whenever either input changes, including a later apply card. Raw
values in these grant Secrets are invalid; configuring `vars` alone cannot
satisfy this prerequisite. The grant has no Variables API or `vars` fallback.
See the [protected mapping ingestion contract](paper-executor-grant-remediation.md#protected-mapping-ingestion-contract-source-only-l2)
for the complete contract and separate provisioning prerequisites.

The runner must register each complete Secret for masking before printing the
first action's evaluated environment. The action then masks extracted values
before exporting any mapping or authenticating. A mapping-read failure reports
only the fixed mapping name, scope (`environment_secret`) and an allowlisted
category: `blank_value`, `malformed_response` or `provenance_mismatch`. Empty or
whitespace-only transports are blank; malformed JSON, invalid values and wrong
generation bindings stop before authentication. No mapping is exported if any
read fails, and no raw exception or mapping value is logged.

Trusted provisioning must independently establish physical Environment-only
storage, generation binding and absence of same-named repository/org Secrets.
The merged `secrets` context cannot technically detect or reject a falsely
labelled inherited Secret with matching metadata. Envelope assertions and
local tests do not establish physical scope or hosted runner behavior. These
source changes neither provision Secrets nor authorize configuration changes,
grant dispatch, retries or PAPER start.

### Mapping provenance (reviewed Git scope + owner t1393)

Authoritative in-repo sources for the three grant-specific mappings, plus
owner-provided GitHub variable presence. Offline remediation and approval-ready
cards live in
[paper-executor-grant-remediation.md](paper-executor-grant-remediation.md).
This table records historical variable observations, not installation of the
grant-specific Secret envelopes above. It does not invent cloud identities.
Variable absence is not cloud identity absence.

| Variable | Role | Intent state | Installed GitHub config (owner t1393) | Source path + commit + scope |
| --- | --- | --- | --- | --- |
| `PAPER_IAM_GRANT_RECIPIENT` | Independent approved mapping that must equal the existing scheduler IAM executor (`GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT`) | `EXISTS_SOURCE_RESOLVED` — intended value is the Environment executor mapping | **Absent** exact name in Environment then repository. Post-fill UTF-8 digests must privately match the installed executor mapping; digests are not published here. | Contract: this section / `#720`; dual-check `scripts/paper_executor_grant.py` `identity()`; executor installed source: Environment `longbridge-paper` / `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` via `.github/workflows/apply-paper-scheduler-jobs-get.yml` |
| `PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT` | Existing authorized bootstrap SA in the same PAPER project, distinct from recipient; never the deficient executor | `REQUIRES_DESIGN` — no proved reusable bootstrap in Git/IaC | **Absent** exact name in Environment then repository. Any concrete SA id remains `PROPOSED_NOT_OBSERVED` until a discovery or provisioning card proves it. | Required by `.github/workflows/grant-paper-executor-permissions.yml` and `scripts/paper_executor_grant.py`; see remediation Card B |
| `PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` | Existing authorized WIF provider that actually trusts that bootstrap | `REQUIRES_DESIGN` — same gap as bootstrap SA | **Absent** exact name in Environment then repository. Provider resource `PROPOSED_NOT_OBSERVED` until proved. | Required by grant workflow auth step; see remediation Card B |

Sibling scheduler mappings observed nonempty in Environment `longbridge-paper`
(owner t1393): `GCP_PROJECT_ID` and `PAPER_SCHEDULER_GET_PROJECT_ID` privately
verified equal; digests not published in Git. `GCP_WORKLOAD_IDENTITY_PROVIDER`
remains the scheduler executor auth provider and is not a bootstrap substitute
without separate proof.

Role permission responses are checked for exact membership and cardinality,
independent of order. Fresh role snapshots normalize permission ordering while
retaining etag and every other field for concurrent-drift detection.
