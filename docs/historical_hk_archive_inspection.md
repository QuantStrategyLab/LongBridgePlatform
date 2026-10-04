# Historical HK archive inspection

The library-only `scripts.inspect_historical_hk_account_archive` entry reads
existing HK account-snapshot archive objects. It does not acquire credentials,
construct a cloud or broker client, read environment settings, invoke Scheduler,
sample an account, publish to QRS, or change state. There is no CLI or workflow
wiring. The existing daily snapshot main and paper/SG inspection CLI are unchanged.

## Inputs and authority

A separately authorized caller provides an existing archive client, the exact
trusted expected deployment-scope/token-version source-binding ID, an explicit
timezone-aware window start/end, and a timezone-aware inspection time. The source
binding is a caller assertion. It does not establish the client's approved GCP
principal, native account identity, account class, or paper/live broker status.
The caller must establish that authority separately before any real read.

Only `qsl-runtime-logs-shared/longbridge/account_snapshots/hk/{expected binding}/`
is read. No caller-provided bucket, target, path, URL, account selector, token,
credential provider, redirect destination, or fallback endpoint is accepted.
The exact historical window is at most five minutes, cannot be in the future,
and may be older than 24 hours. At most its two UTC calendar dates are listed;
the current date is not added to a historical query. An arbitrary older source
binding is never discovered from an object or by listing sibling bindings.

## Bounded reads and contract

The same supplied client is used for listing and fixed-generation object reads.
Each date gets one page, at most 64 objects per date and 64 total. A page token,
missing pagination capability, duplicate or malformed metadata, unexpected
object path, or excess object/byte budget rejects the whole inspection. It never
returns partial coverage as success. Total declared data is bounded to 4 MiB;
each object is bounded to 64 KiB, read with generation precondition and
`retry=None`. Calls have at most 15-second timeouts within a shared 180-second
monotonic deadline, checked after listing, downloads, and before returning.
No object-generation race or unknown read is retried.

The unchanged archive validator checks schemas, HK target/scope, exact asserted
source binding, UTC observation-date/object-path consistency, finite decimal
money, optional financing, and the original 15-minute observation-duration rule.
The new reader additionally rejects unknown binding/money-row fields. A valid
object outside the requested window is a non-match. Any invalid object rejects
the inspection. Same-window matching is checked against the historical window
end, never by weakening the existing live freshness gate.

## What the result proves

Output contains only historical window/times, counts, object generation, raw
archive SHA256, and stale/match booleans. It contains no native IDs, amounts,
source-binding IDs, object paths, bucket names, tokens, or exception text.
`evidence_kind` is always `historical_hk_archive_inspection`. Current-health,
original-request-terminal, receiver-ACK, and native-identity confirmation are
always false, even when an observation remains recent at inspection time.
Multiple matching objects are reported as non-unique, without picking one.
Zero observed matches means zero validated matches in these fully listed fixed
prefixes; it does not diagnose a broker, Scheduler, or QRS failure.

The current archive schema has no original probe nonce/request trace, request
status, serving revision/image/source tuple, or QRS receiver receipt. Consequently
a unique same-window/source object establishes archive evidence only. Closing
a parked HK UNKNOWN still requires the exact original request terminal and its
independently bound receiver ACK, using the original authorized GCP identity and
existing records. This entry does not authorize a probe rerun, resume, cache
change, token operation, workflow dispatch, or activation.

## Verification

Run the new focused pytest file with a network-blocked, fake-client-only setup.
Existing dependency pins and all existing files must remain byte-identical.
Focused tests and lint establish local reader behavior, not cloud-client
authorization, real archive availability, original request success, or business
recovery. Remote CI and real reads remain separate checks.
