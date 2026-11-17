# Read-Only Account Observer Configuration and Boundaries

English | [简体中文](read-only-account-observation.zh-CN.md)

The standalone observer supports LongBridge `PAPER`, `HK`, and `SG` protocol scopes. Target names can be customized; account slots, strategy names, and broker-native account identities are handled separately, and strategy names never substitute for account identity.

This implementation reuses standard configuration objects, read-only broker endpoints, and existing account-facts ingestion interfaces. Existing functions handle config parsing, service identity verification, broker response projection, and transmission without introducing a plugin framework or second account registry.

## Configuration Sources

The standalone observer reads deployment parameters from the protected runtime environment rather than deriving production mappings from public runtime-target manifests.

| Parameter | Purpose |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_PROJECT_NUMBER` | Verify service project ownership; numeric project number is required when numeric namespaces are returned |
| `SNAPSHOT_SERVICE`, `SNAPSHOT_REGION` | Identify and validate the Cloud Run revision serving traffic |
| `ACCOUNT_HISTORY_TARGET_ID`, `ACCOUNT_HISTORY_EXPECTED_SCOPE` | Custom account slot identifier and protocol scope |
| `ACCOUNT_HISTORY_SERVICE_URL` | Exact service URL and ID-token audience |
| `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` | Verify expected source binding for the account |
| `ACCOUNT_HISTORY_ALLOWED_REF` | Allowed execution branch; workflows use the repository default branch |
| `ACCOUNT_FACTS_SYNC_URL`, `ACCOUNT_FACTS_SYNC_TOKEN` | Existing account-facts ingestion endpoint and dedicated sync credential |

Workflow settings `GCP_PROJECT_ID`, `GCP_PROJECT_NUMBER`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, and `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` come from GitHub Secrets. Per-environment `ACCOUNT_HISTORY_SERVICE_URL` and `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` are likewise read from Secrets only. Real endpoints, source bindings, and user-identifying mappings remain in protected configuration; source code, documentation, fixtures, and public logs use examples unlinked to real deployments, with no production defaults hardcoded in application code.

`GITHUB_REPOSITORY`, `GITHUB_REF`, and `GITHUB_WORKFLOW_REF` come from GitHub execution context. Protected environments, cloud WIF conditions, and exact source bindings jointly constrain callers; repository names are not hardcoded in Python.

## Access and Data Handling

The observer verifies service project, region, URL, and the read-only flag on the revision serving 100% traffic before sending an audience token. A failing new revision cannot replace or block an already approved healthy active revision.

`independent_get` applies to authenticated Cloud Run services reachable by the current runner. Internal ingress fails closed with reason `internal_observer_required` before calling the broker or sending account facts; ingress must never be opened for collection. Internal services should use supported internal execution entrypoints and validate read-only collection, private archiving, and ingestion end-to-end separately; this document does not mark that internal loop complete.

HK internal connectivity can be verified separately via `config/hk_snapshot_verification.workflow.yaml`, which calls fixed `GET /account-snapshot` over OIDC within the same GCP project using protected deployment configuration for service URL and expected binding. Verification returns only timestamp, field counts, and binding status, with Cloud Workflows call logging disabled, a dedicated runtime identity, and no caller-supplied URL, method, or request parameters; when archiving is unconfigured, only manual verification runs. Once read-only snapshots are enabled for SG, setting `SNAPSHOT_VERIFICATION_SCOPE=SG` uses SG's dedicated identity and source binding. HK and SG each write their own candidate objects matching their snapshot scope. Production archiving uses protected `HK_DIGEST_BUCKET`, `HK_DIGEST_PREFIX`, `HK_DIGEST_ACCOUNT_UID`, `HK_DIGEST_TARGET_ID`, and `HK_DIGEST_STRATEGY_PROFILE`, collecting before 06:00 SGT daily and writing one candidate object per 06:00 boundary containing only source-bound net equity and equity positions without inferring orders or fills. Archive permissions grant object creation only and preserve existing candidates.

Each execution issues at most one snapshot `GET` and one account-facts `POST` with no automatic retry on unknown outcomes. Projection preserves the broker's actual currency, amounts, observation time, and provenance; mismatched source identities are rejected without zero-filling or altering historical timestamps. Trading enablement, risk budgets, and notifications are not controlled by this observer.

This configuration migration covers only the standalone observer and its heartbeat workflow. Legacy Scheduler observers and other deployment paths retain earlier configuration conventions and are not yet covered by repo-wide configuration and privacy remediation.
