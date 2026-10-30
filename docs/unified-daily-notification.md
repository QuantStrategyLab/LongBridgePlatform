# Unified daily notification (code only)

Routine successful cycles, acknowledged actions and healthy heartbeats are
recorded for the next daily digest regardless of legacy notification flags.
Only a confirmed protected candidate write establishes successful deferral.
Failed or uncertain writes produce an operational coverage diagnostic;
they do not fall back to the normal trade or heartbeat message. Unknown order
outcomes, risk escalation, errors and authentication failures remain urgent.
An observed action is not proof of an order count or a fill; those counts stay
unknown unless independently established by the archive.

Routine plugin observations are counted from live signals using the allowlisted
routes and stored with the same source-run identity. Conflicting observations
are rejected as unconfirmed coverage, never summed or silently overwritten.
The scheduled archive exporter uses the existing protected bindings for every
flag setting. This source change alone does not establish runtime deployment,
candidate availability or notification delivery.

QPK source reference: `2b04882058666ebc81d1650d93fa2105ec4ba3d8` (notification modules only).

The notification-only QPK source snapshot in `notifications/digest_snapshot/` avoids an
unrelated trading dependency upgrade. Keep these four modules byte-identical
to the reviewed QPK notification modules. Existing QPK/strategy pins, order
execution, funding, risk, account enablement and durable report writes are unchanged.

Before enabling routing, validate the exact active target's private archived
report, candidate store and QRS receiver. Existing cloud monitor wiring uses
`RUNTIME_TARGET_ENABLED=true`, exact protected `RUNTIME_TARGET_JSON`,
`EXECUTION_REPORT_GCS_URI`, `DIGEST_CANDIDATES_GCS_URI`, `DIGEST_STORE_TARGET`,
`DIGEST_OPAQUE_ACCOUNT_UID` and `DIGEST_TARGET_ID`. Missing configuration stops
projection; disabled targets stay disabled. Do not infer private identities.
The producer aggregates every unique cycle in the completed 06:00 SGT window;
missing evidence is unknown, never zero. No new collector or scheduler is added.

No deployment, variable application, IAM, secret or live message is authorized
by this PR. Source adoption and delivery require separate approved release work.

For multiple active targets, protected `DIGEST_TARGET_BINDINGS_JSON` supplies a
list of per-target environment mappings. `DIGEST_NOTIFICATION_SCHEDULE_JSON`
can reuse the existing heartbeat scheduler/calendar declaration to prove a
window was not due. Missing due reports stay errors; closed configured sessions
produce an `actually_ran=false` marker without inventing zero trading counts.

Fill counts require individual execution identifiers. A completed order response
proves an order completed, not how many fills it received. Missing fill counts
remain unknown; confirmed routine orders still appear without a false diagnostic.

Runtime Guard notifications keep the localized summary and workflow link. Raw
provider audit entries remain in their original restricted logs and are not
passed as Telegram technical details, including when the deployed formatter is
pinned to an older shared package. Guard detection, deduplication and failure
exit codes are unchanged.
