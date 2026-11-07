# PAPER executor grant remediation (offline)

Status: Proposal only. Publishing or merging this note authorizes neither
Environment variable writes, IAM changes, WIF provisioning, grant dispatch,
Environment approval, deployment, scheduler mutation nor PAPER start.

Related definition: [paper-scheduler-get.md](paper-scheduler-get.md) and
[grant-paper-executor-permissions.yml](../../.github/workflows/grant-paper-executor-permissions.yml).
Lookup-channel fix lineage: PR #722 (Draft).

## Owner-provided installed configuration (t1393)

Source class: **OWNER-PROVIDED browser metadata**, not an independent cloud or
API verification by this engineering pass. Observation surfaces:

- Environment `longbridge-paper` variables table (40 items):
  `https://github.com/QuantStrategyLab/LongBridgePlatform/settings/environments/15143916325/edit`
- Repository Actions variables (18 items):
  `https://github.com/QuantStrategyLab/LongBridgePlatform/settings/variables/actions`

Hashes are SHA-256 over the full GitHub variable value encoded as UTF-8. Raw
values are not recorded here.

| Name | Installed? | Scope observed | Updated (UTC) | Value sha256 (UTF-8) |
| --- | --- | --- | --- | --- |
| `GCP_PROJECT_ID` | yes, nonempty | Environment | 2026-11-06T05:04:28Z | `5fd9761bc0f5e7a1b75fc007b946b554af81773faee45d940bbf52c04c52152e` |
| `PAPER_SCHEDULER_GET_PROJECT_ID` | yes, nonempty | Environment | 2026-11-06T09:11:15Z | **same hash as** `GCP_PROJECT_ID` |
| `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` | yes, nonempty | Environment | 2026-11-03T06:51:52Z | `382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22` |
| `PAPER_IAM_GRANT_RECIPIENT` | **absent** exact name | Environment then repository | — | — |
| `PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT` | **absent** exact name | Environment then repository | — | — |
| `PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` | **absent** exact name | Environment then repository | — | — |

Absence means the GitHub variable name is missing in the reviewed Environment
table and, for those three names, also in the repository Actions variables
table. Variable absence is **not** evidence that a GCP service account, WIF
pool or provider is absent in the cloud project.

## Intent versus installed config

| Mapping | Intent (contract) | Installed GitHub config (t1393) | Decision input |
| --- | --- | --- | --- |
| Recipient | `PAPER_IAM_GRANT_RECIPIENT` must equal the existing scheduler IAM executor `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` in the same PAPER project (`scripts/paper_executor_grant.py` `identity()`) | Executor mapping **exists** in Environment; recipient name **absent** | Fill Environment variable from the already-installed executor mapping; expected post-fill value sha256 must equal `382a7cbe…697cc22`. Do not invent a new email. |
| Project pair | `PAPER_SCHEDULER_GET_PROJECT_ID` must equal `GCP_PROJECT_ID` | Both exist; **hashes already match** | No project remapping required for this gate. |
| Bootstrap SA | Distinct authorized SA in the same project, not the recipient/executor | Name absent; no in-repo proved reusable IAM bootstrap | Requires design or bounded discovery (below). **PROPOSED_NOT_OBSERVED** if newly named. |
| Bootstrap WIF | Provider that actually trusts that bootstrap under repo/main/`longbridge-paper` limits | Name absent; no in-repo proved bootstrap provider | Same as bootstrap SA. |

### In-repo identities reviewed (not adopted as bootstrap)

These appear in workflows or docs and must **not** be treated as grant bootstrap
without separate proof that they hold exactly the four executor-grant
permissions and are distinct from the recipient:

| Identity channel | Role in repo | Bootstrap suitability |
| --- | --- | --- |
| `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` + `GCP_WORKLOAD_IDENTITY_PROVIDER` | Scheduler IAM / deploy executor for Environment `longbridge-paper` | **Rejected as bootstrap** — this is the deficient recipient; contract forbids equality with bootstrap. |
| `GCP_RUNTIME_SERVICE_ACCOUNT` | Cloud Run runtime service identity | Not an IAM role/policy writer in reviewed contracts. |
| `GCP_SCHEDULER_SERVICE_ACCOUNT` | Scheduler OIDC invoker | Invoker only; not project IAM admin. |
| `PAPER_READBACK_SERVICE_ACCOUNT` + `PAPER_READBACK_IDENTITY_PROVIDER` | Dedicated read-only PAPER readback Secrets | Documented read-only; wrong privilege class. |

No Terraform or provisioning module in this repository names a compatible
bootstrap. Prefer reuse only after an owner-authorized proof of an **existing**
independent SA that already holds the four permissions below and a WIF binding
that trusts only this repository, protected `main`, and Environment
`longbridge-paper`.

## Card A — recipient Environment fill (config only)

Preconditions:

1. PR #722 lookup-channel fix is merged to `main` (engineering merge only; still
   no grant). Until then, grant runs remain blocked on blank mappings even after
   fills.
2. Owner confirms Environment `longbridge-paper` still shows executor sha256
   `382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22`.
3. Exact authorization for GitHub Environment **variable create** on
   `longbridge-paper` (not repository-only, not org-only).

Action (human / approved operator; not this agent):

1. Create Environment variable `PAPER_IAM_GRANT_RECIPIENT`.
2. Set its value to the **same bytes** as Environment
   `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` (copy from the existing UI field).
3. Verify sha256(UTF-8(value)) ==
   `382a7cbea2ea0de0f455ed00cf29a3181766399a5cd28a794d00a79d8697cc22`.
4. Leave repository/org copies unset for this name.

Rollback: delete the Environment variable `PAPER_IAM_GRANT_RECIPIENT` if the
fill was wrong. No GCP IAM change is involved.

Out of scope for Card A: grant workflow_dispatch, IAM apply, PAPER start.

## Card B options — bootstrap identity

### B0 — bounded discovery of an existing independent identity (preferred if found)

Class: **discovery proposal only**. Variable absence does not imply cloud
absence. This engineering pass did **not** perform GCP GET (owner one-shot
budget already consumed elsewhere). Exact later read-only targets, once the
project id is taken from the privately known `GCP_PROJECT_ID` mapping:

1. Resolve `<PROJECT_ID>` from Environment `GCP_PROJECT_ID` (hash
   `5fd9761b…52152e`); do not publish the raw id in chat.
2. `serviceAccounts.get` / `getIamPolicy` only for candidate SA emails already
   known from protected config or prior approved inventory — **no org-wide or
   project-wide account listing**.
3. On each candidate, `projects.testIamPermissions` for exactly:
   `iam.roles.get`, `resourcemanager.projects.getIamPolicy`, `iam.roles.create`,
   `resourcemanager.projects.setIamPolicy`.
4. For a candidate that holds all four and whose email sha256 is **not**
   `382a7cbe…697cc22`, read only that SA's WIF principal bindings and the
   referenced `workloadIdentityPools.providers.get` for providers already named
   in protected config.
5. Accept only if trust is limited to this repository, protected `main`, and
   Environment `longbridge-paper` (or can be narrowed under a separate trust
   card).

If no candidate passes, do not widen search; proceed to B1.

### B1 — minimal new bootstrap design (**PROPOSED_NOT_OBSERVED**)

Proposed logical names (placeholders only; not created):

| Resource | Proposed id / pattern | Notes |
| --- | --- | --- |
| Service account | `paper-iam-bootstrap@<PROJECT_ID>.iam.gserviceaccount.com` (display name e.g. `paper-iam-bootstrap`) | Same PAPER project as `GCP_PROJECT_ID`. Must remain distinct from recipient. |
| WIF pool | `github-paper-iam` (or reuse an existing pool **only** if a separate trust card proves isolation) | Prefer a dedicated pool so trading/deploy providers are untouched. |
| WIF provider | `github-paper-iam-bootstrap` | Attribute conditions: repository `QuantStrategyLab/LongBridgePlatform`, ref `refs/heads/main`, Environment `longbridge-paper`. |
| Custom role on project (bootstrap itself) | `paperIamBootstrap` | Exact permissions listed below — not `roles/owner` / `roles/resourcemanager.projectIamAdmin` unbounded. |
| GitHub Environment variables | `PAPER_IAM_BOOTSTRAP_SERVICE_ACCOUNT`, `PAPER_IAM_BOOTSTRAP_WIF_PROVIDER` | Set only after resources exist and trust is verified. |

Bootstrap custom role permissions (complete set):

1. `iam.roles.get`
2. `resourcemanager.projects.getIamPolicy`
3. `iam.roles.create`
4. `resourcemanager.projects.setIamPolicy`

Affected resources:

- Project `<PROJECT_ID>` IAM policy (etag CAS via the grant script only).
- Project custom role `paperSchedulerIamExecutor` created/bound by the grant
  workflow (recipient-facing), not by this provisioning card alone.
- WIF pool/provider and SA `roles/iam.workloadIdentityUser` binding for the
  GitHub principal matching the provider condition.

Trust principals:

- GitHub OIDC subject for this repository + `main` + Environment
  `longbridge-paper` only.
- Required Environment reviewers remain mandatory before any grant job auth
  step.

Explicitly forbidden:

- Granting the four permissions (or broader project IAM admin) to
  `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT`, `GCP_RUNTIME_SERVICE_ACCOUNT`,
  `GCP_SCHEDULER_SERVICE_ACCOUNT`, or PAPER readback identities as a shortcut.
- Reusing the known deficient executor as bootstrap.
- Org-wide admin, key creation, or unbound `setIamPolicy` without Environment
  gates.

Preconditions for B1 apply:

1. Separate owner card naming exact `<PROJECT_ID>`, SA id, pool/provider ids.
2. Single-writer window for project IAM and WIF admin.
3. Card A complete (recipient mapping installed) before any grant dry-run that
   needs all six names.
4. PR #722 on `main` so the grant workflow can read mappings via `vars`.

Rollback for B1:

1. Remove Environment variables `PAPER_IAM_BOOTSTRAP_*` first (fail-closed).
2. Remove SA WIF user binding; disable/delete provider; delete SA only if unused.
3. Remove bootstrap custom role binding/role if created.
4. Never roll back by widening another identity's permissions.

## Card C — grant dry-run / apply (future; not authorized here)

After Cards A and B, a **separate** exact-revision owner run card may authorize
`workflow_dispatch` of `grant-paper-executor-permissions.yml` with
`mode=dry-run` first, then a new card for `apply`. Confirm string remains
`GRANT_PAPER_EXECUTOR_FOUR_PERMISSIONS`. This remediation note is not that card.

Grant script effect if later authorized: create project role
`paperSchedulerIamExecutor` with the same four permissions and bind it only to
`serviceAccount:<PAPER_IAM_GRANT_RECIPIENT>`. No PAPER runtime enablement.

## Remaining unknowns (do not fabricate)

- Live GCP presence/permissions of any SA behind the executor hash (no GCP GET
  in this pass).
- Whether any non-executor SA already holds the four permissions.
- Effective WIF attribute conditions on existing providers.
- Whether Environment protection reviewers remain configured at grant time
  (workflow re-checks; not re-verified here).
- Codex `ai-review` / clearing-label state for PR #722 (merge policy; no labels
  added by this pass).

## Verification posture

Offline: contract tests in `tests/test_paper_executor_grant.py` and documentation
checks. Online grant, IAM, or PAPER verification requires future exact cards.
