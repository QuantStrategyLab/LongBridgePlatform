# PAPER account snapshot history

The optional history producer runs only inside the existing `/probe` request when
`ACCOUNT_REGION=PAPER` and `ACCOUNT_HISTORY_RECORDING_ENABLED=true`. It does not
change `/run`, `/account-snapshot`, non-PAPER probes, or any order path. The
history step uses the existing one-response Secret Manager metadata context
builder, which does not refresh or reread the token. It reads the original
`account_balance(currency="USD")` response once, rejects an aggregate in any other currency,
then writes its complete currency-specific cash facts,
then lets the existing indicator and portfolio checks continue using that same
response. A later probe health failure remains a probe failure; it does not
invalidate a snapshot that was already created from a complete observation.

The producer requires the exact PAPER target and scope, an existing GCS prefix
ending in `account_snapshots`, a project ID, and a bound deployment/scope/token
version source. Missing or malformed source, balances, cash rows, or configuration
does not write an object. Values are normalized to finite decimal strings and
kept by currency; no strategy equity, market value, positions, orders, token, or
secret is substituted or copied into the history record.

Each object uses the `longbridge_account_snapshot_history.v1` format and names
`longbridge_account_snapshot.v1` as its source snapshot schema. Its path is
`{prefix}/paper/{source_binding_id}/{UTC observation date}/{UTC finish time}.json`.
The upload has a 20-second timeout, no retries, and
`if_generation_match=0`; only a confirmed create is success. A conflict or
unknown write result fails the probe and is not retried. The source observation
is captured before strategy indicator/portfolio checks so unrelated strategy
health cannot suppress a complete account record. The next ordered consumer
step must still be validated independently; GCS creation alone does not prove
QRS synchronization or website display.
