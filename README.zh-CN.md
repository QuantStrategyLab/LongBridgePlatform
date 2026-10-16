# LongBridgePlatform

LongBridgePlatform 是 QuantStrategyLab 在美股/港股方向的 LongBridge 执行运行时。
它接收 `UsEquityStrategies`、`HkEquityStrategies` 产出的策略 profile，通过
LongBridge 兼容的券商连接执行，并在真实下单前经过区域化的 dry-run/live 控制。
它只是执行层：策略研究逻辑留在策略仓库，snapshot/验证产物在 profile 需要时来自
对应的 snapshot pipeline。

[English](README.md) | 简体中文

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## QSL 架构角色

- **层级**：`执行平台`。
- **职责**：LongBridge 美股/港股执行运行时。
- **事实源/归属**：LongBridge 连接、区域 runtime 设置、dry-run/live 控制。
- **消费对象**：UsEquityStrategies、HkEquityStrategies、snapshot artifacts、QuantPlatformKit、QuantRuntimeSettings。
- **禁止事项**：承载策略研究逻辑或把凭据写入 Git。

## 运行边界

- 只加载策略包暴露的 runtime-enabled profile。
- 负责券商/API 连接、dry-run 检查、通知和部署配置。
- 凭据必须放在 GitHub Secrets、云密钥系统或券商专用密钥系统中，不能提交到 Git。
- 任何 live 下单路径启用前，都应先从 dry-run 或 paper mode 开始。

- 账户新增风险门先消费显式账户快照，再消费当前组合状态。它不从共享研究用的
  PerformanceStore 推导生产漂移，也不补造缺失的可选漂移信息。缺少必需账户输入，
  或证据处于 review、critical、invalid 状态时，仍会拒绝新增买入。

`GET /account-snapshot` 是独立的只读诊断入口，仅在
`LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true` 精确设置时启用。它返回按币种区分的
现金、券商报告的余额行（`currency`、`net_assets`、`total_cash`）、仅含数量的持仓、
有界七日读取中已知且脱敏的未终结订单，以及已知近期执行数量。
响应始终是不完整的：SDK 读取不是原子操作，不能证明稳定券商账户 ID、唯一账户
写入者、完整挂单或执行记录、费用、市值，或经独立对账的权益。余额行来自同一次
已有 `account_balance` 读取，适用于 SG/HK/paper；不同币种不会求和，也不会将
余额行纳入稳定对账摘要。缺失或非有限余额会拒绝该快照，不影响独立的对账入口。
该入口不授予恢复、实盘交易、下单、token 刷新、通知或报告权限，沿用服务既有的
Cloud Run IAM 和内部入口保护；部署与启用是独立的运维操作。

## 运行通知策略

由 runtime composer 构建的周期会静默处理健康的无订单成功和成功的 dry-run 预览，
包括已有 `action_done=true` 的预览。真实 pending/submitted/filled 订单事实沿用
现有通知。显式 blocked/rejected/unknown 状态、对账或数据错误、插件加载错误，
在 live 和 dry-run 周期中都需要关注。单独的未知 `no_execute` 不算健康结果；
显式研究合同 `no_order=true` + `execution_authorized=false` 仍不执行交易。
只有已知排队命令与 `waiting_window` 同时存在，才表示正常的持久命令等待。
手动构建配置中保留显式 `notify_no_trade_cycles` 选项以兼容原有行为；runtime
composer 将它设为 false。这个选项不会恢复成功预览的通知。

分类器读取结构化事实和新增的通知原因元组；元组默认为空。已有的小账户、负现金
及等待卖出释放资金的阻断会提供该元组，不改变执行。账户新增风险告警继续使用
既有 transition-key 去重，不另增周期消息。订单钩子、issue 通知、执行标记和 claim、
策略与风险决策、数量和状态，以及 scanner 仅告警策略都保持原有语义。

`main.py` 中最终执行报告的持久化失败只记日志，没有专用 issue 通知。执行标记和
持久命令的持久化失败仍沿用已有 issue 通知。显式 `validation_only` 调用方使用静默
周期通知 sink，V7 validation 也静默 issue 通知。这些隔离验证路径的分类结果
不能证明 sink 实际送达错误通知。

## 普通 profile 与 snapshot-backed profile

普通 runtime profile 通常可以直接基于 market history 或 portfolio state 执行。Snapshot-backed profile 需要先从对应 snapshot pipeline 获取当前 artifact bundle，平台才应该执行。平台不应该自行判断策略资格，而应消费策略仓和 snapshot 仓发布的状态与产物。

## 安全部署顺序

1. 在 Git 之外配置 secrets 和 runtime variables。
2. 先以 dry-run 模式运行 workflow 或服务。
3. 检查生成订单、日志、通知和 reconciliation 输出。
4. 确认回滚步骤和 artifact 版本。
5. 上述检查清楚后，再启用定时任务或 live 执行。

## 仓库结构

- `config/`：公开非敏感 runtime-target manifest。
- `tests/`：单元测试、契约测试和回归测试。
- `docs/`：运行手册、设计说明、证据和集成契约。
- `.github/workflows/`：CI、定时任务、发布或部署 workflow。
- `scripts/`：运维脚本和本地辅助工具。
- `research/`：研究配置和非 live 候选产物。

## 快速开始

```bash
uv sync --frozen --extra test
uv run --no-sync ruff check --exclude external .
uv run --no-sync python scripts/check_qpk_pin_consistency.py
```

## 延伸文档

- [`docs/hk_equity_runtime.md`](docs/hk_equity_runtime.md)
- [`docs/runtime_target_manifest.md`](docs/runtime_target_manifest.md)：公开 runtime-target manifest 契约、动态 workflow matrix 渲染与 Environment 启停边界

## 社区和安全

- 贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，确认 PR 范围、本地校验和文档要求。
- 讨论、issue 和 review 请遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
- 涉及密钥、自动化、券商/交易所或云资源的漏洞请按 [SECURITY.md](SECURITY.md) 私密报告；不要为 secret 或实盘风险开公开 issue。

## 许可证

详见 [LICENSE](LICENSE)。

显示与通知名称配置：[显示名称](docs/display-names.md)。
