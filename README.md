# LongBridgePlatform


## QSL architecture role

- **Layer**: `runtime-platform`.
- **Responsibility**: LongBridge US/HK execution runtime.
- **Owns**: LongBridge connectivity, regional runtime settings, dry-run/live controls.
- **Consumes**: UsEquityStrategies, HkEquityStrategies, snapshot artifacts, QuantPlatformKit, QuantRuntimeSettings.
- **Must not**: own strategy research logic or store credentials in Git.

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

LongBridgePlatform is a QuantStrategyLab LongBridge US/HK equity execution platform. It runs US and HK equity profiles through LongBridge-compatible runtime services and regional deployment settings.

It is an execution layer, not a strategy research repository. Strategy logic comes from `UsEquityStrategies / HkEquityStrategies`; snapshot and validation artifacts come from `UsEquitySnapshotPipelines / HkEquitySnapshotPipelines` when a profile requires them.

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
orders from the bounded seven-day read, and a known recent-execution count. The
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

## Runtime notification policy (NOTIFY-02, 2026-10-06)

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

实现/合并阶段：[#561](https://github.com/QuantStrategyLab/LongBridgePlatform/pull/561)
已于 2026-10-06 合并为 `9a6ed81457795480ea46e45416fd8cc2d1b48548`。
修正后的 [PR CI](https://github.com/QuantStrategyLab/LongBridgePlatform/actions/runs/37459367684)
与该合并提交的 [main CI](https://github.com/QuantStrategyLab/LongBridgePlatform/actions/runs/37459907792)
均成功：各 1473 tests、154 subtests 通过，3 warnings；Ruff、依赖/pin/lockfile
与有界 workflow 测试也通过。本地定向合成回归及业务 AST 核对是补充证据。

首版 [CI](https://github.com/QuantStrategyLab/LongBridgePlatform/actions/runs/37457591638)
曾有 2 failed、1471 passed：非 USD fixture 错把独立 Quote failed 告警当作应静默；
V7 fixture 未接生产已有的显式 validation-only 静音 sink。后续只修两项测试接线/断言，
保留精确订单数量/价格与零提交约束，并验证真实 composer 的 silent=True 零通知、
silent=False 同一 REJECT 仍通知；没有给通用异常分类增加策略例外。

采用/业务阶段仍未验收：本任务没有部署、修改运行配置、读取 secret、连接券商、
发 Telegram 测试消息或调用模型。合并与 CI 通过不证明运行版本已采用、自然周期
静默/异常真实送达、有效路由同一性或跨服务去重；这些证据仍需分别核验。

Known pre-existing limit: final execution-report persistence failures in `main.py`
are logged rather than sent through a dedicated issue notification. This bounded
classification change does not add that separate alert path. Existing marker and
durable-command persistence issue notifications remain in place. Explicit
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

- [`docs/hk_equity_runtime.md`](docs/hk_equity_runtime.md)
- [`docs/runtime_target_manifest.md`](docs/runtime_target_manifest.md): public runtime-target manifest contract, dynamic workflow matrix rendering, and Environment enablement boundary

## Community and security

- See [CONTRIBUTING.md](CONTRIBUTING.md) for pull request scope, local verification, and documentation expectations.
- Follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for maintainer and contributor conduct.
- Report credential, automation, broker, exchange, or cloud-resource vulnerabilities through [SECURITY.md](SECURITY.md); do not open public issues for secrets or live-execution risk.

## License

See [LICENSE](LICENSE).
