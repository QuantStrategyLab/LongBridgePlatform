# LongBridgePlatform

LongBridgePlatform is QuantStrategyLab's LongBridge execution runtime for US
and HK equities. It takes strategy profiles produced by `UsEquityStrategies`
and `HkEquityStrategies`, runs them through LongBridge-compatible broker
connectivity, and applies regional dry-run/live controls before anything
reaches a real order. It is an execution layer only: strategy research lives
in the strategy repositories, and snapshot/validation artifacts come from the
matching snapshot pipeline when a profile needs one.

English | [简体中文](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

The pinned HK strategy policy has no selectable profiles. New selection of `hk_global_etf_tactical_rotation` and `hk_low_vol_dividend_quality_snapshot` is rejected, including dry-run switch plans. Historical catalog metadata and adapters remain available. US strategy pins and existing deployment configuration are unchanged.

## QSL architecture role

- **Layer**: `runtime-platform`.
- **Responsibility**: LongBridge US/HK execution runtime.
- **Owns**: LongBridge connectivity, regional runtime settings, dry-run/live controls.
- **Consumes**: UsEquityStrategies, HkEquityStrategies, snapshot artifacts, QuantPlatformKit, QuantRuntimeSettings.
- **Must not**: own strategy research logic or store credentials in Git.

## Runtime boundary

- Loads only runtime-enabled strategy profiles exposed by the strategy packages.
- Handles broker/API connectivity, dry-run checks, notifications, and deployment settings.
- Must keep credentials in GitHub Secrets, cloud secret stores, or the broker-specific secret system, never in Git.
- Should start with dry-run or paper mode before any live order path is enabled.
- The account new-risk gate consumes an explicit account snapshot first and the
  current portfolio status second. It does not derive production drift from a
  shared research PerformanceStore: absent optional drift is not fabricated,
  while missing required account inputs and review, critical, or invalid
  evidence remain fail-closed for new buys.

`GET /account-snapshot` is a separate, read-only diagnostic and stays disabled
unless `LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true` is set exactly. It returns
currency-specific cash, broker-reported balance rows (`currency`, `net_assets`,
`total_cash`), quantity-only positions, redacted known non-terminal
orders from the bounded seven-day read, and a known recent-execution count.
Order statuses are normalized from SDK enums to uppercase names. Only known
working states (including pending submission, replacement, cancellation and
partially filled orders) enter `known_non_terminal_orders_7d`; filled, canceled,
rejected, expired, partially withdrawn and unknown statuses are excluded. The
response is always partial: the SDK read is non-atomic and does not prove a
stable broker account ID, a unique account writer, complete open orders or
executions, fees, market value, or independently reconciled equity. Balance rows
come from the same existing `account_balance` read, apply to SG/HK/paper, and
are not summed across currencies or used in stable reconciliation digests.
Missing or non-finite balance values reject the snapshot without affecting the
separate reconciliation endpoint. The endpoint grants no recovery,
live-trading, order, token-refresh, notification, or reporting authority.
Access retains the service's existing Cloud Run IAM and internal ingress
protection; deploying and enabling this endpoint is a separate operational step.

## Runtime notification policy

Runtime-composed cycles keep healthy no-order success and successful dry-run
previews quiet, including previews whose existing `action_done` is true. Real
pending/submitted/filled order facts keep their current delivery. Explicit
blocked/rejected/unknown states, reconciliation or data errors, and plugin-load
errors remain attention-worthy in both live and dry-run cycles. A bare unknown
`no_execute` is not a healthy result; the explicit research
`no_order=true` + `execution_authorized=false` contract remains non-executing.
Only a known queued command plus `waiting_window` explains the normal durable
command wait. The existing explicit `notify_no_trade_cycles` opt-in on manually
constructed configs is retained for compatibility; the runtime composer sets it
false. It does not opt successful previews back into delivery.

The classifier reads structured facts and an appended, default-empty notification
reason tuple. Existing small-account, negative-cash, and pending-sell-release
blocks supply that tuple without changing execution. Account-new-risk attention
keeps its existing transition-key dedup path instead of gaining another cycle
message. Order hooks, issue notifications, execution markers/claims, strategy
and risk decisions, quantity/state, and scanner alerts-only policy are unchanged.

Final execution-report persistence failures in `main.py` are logged rather than
sent through a dedicated issue notification. Marker and durable-command
persistence failures have their existing issue notifications. Explicit
`validation_only` callers already request a silent cycle sink, and V7 validation
also mutes issue notifications. These isolated validation paths remain unchanged;
attention classification here is not proof that those sinks deliver errors.

## Direct vs snapshot-backed profiles

Direct runtime profiles can usually run from market history or portfolio state. Snapshot-backed profiles need a current artifact bundle from the matching snapshot pipeline before this platform should execute them. The platform should not invent strategy eligibility; it should consume the status and artifacts published by the strategy and snapshot repositories.

## Deploy safely

1. Configure secrets and runtime variables outside Git.
2. Run the workflow or service in dry-run mode.
3. Review generated orders, logs, notifications, and reconciliation output.
4. Confirm rollback steps and artifact versions.
5. Enable scheduled or live execution only after the above checks are clear.

## Repository layout

- `config/`: public non-sensitive runtime-target manifest.
- `tests/`: unit, contract, and regression tests.
- `docs/`: runbooks, design notes, evidence, and integration contracts.
- `.github/workflows/`: CI, scheduled jobs, release, or deployment workflows.
- `scripts/`: operator scripts and local helpers.
- `research/`: research configs and non-live candidate artifacts.

## Quick start

```bash
uv sync --frozen --extra test
uv run --no-sync ruff check --exclude external .
uv run --no-sync python scripts/check_qpk_pin_consistency.py
```

## Useful docs

- [`docs/hk-equity-runtime.md`](docs/hk-equity-runtime.md)
- [`docs/runtime-target-manifest.md`](docs/runtime-target-manifest.md): public runtime-target manifest contract, dynamic workflow matrix rendering, and Environment enablement boundary

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI assistants and maintainers follow [AGENTS.md](AGENTS.md).

## License

See [LICENSE](LICENSE).

Display/notification naming configuration: [display names](docs/display-names.md).

Telegram delivery uses QPK notification intents and redacted receipts. Runtime
entrypoints install shared log and stream redaction. Environment-backed bot
routes resolve credentials only in QPK delivery; existing injected or in-memory
credentials use QPK's compatibility sender, with no temporary environment mutation.
Provider errors and credential-bearing request data are excluded from reports.
