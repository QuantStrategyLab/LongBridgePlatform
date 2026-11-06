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
