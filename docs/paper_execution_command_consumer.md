# LongBridge 纸面命令消费者

这个消费者用于验证延迟执行命令的最后一道风险检查。它只读取 LongBridge 的账户快照和行情，写入纸面命令审计记录；它不会构造执行端口，也不会调用下单 API。

## 处理流程

1. 策略 dry-run 生成一条不可变的目标仓位命令，并把完整 `strategy_release` 身份写入命令内容。
2. 专用消费者只认领当日、仍处于 `queued` 的命令。
3. 它根据实时持仓、现金和行情逐标的生成模拟订单，并比较每个标的前后的绝对风险敞口；不能只根据 `buy` / `sell` 判断风险方向。
4. 每一笔模拟订单都经过共享的运行时命令门：发布身份、有效交易日、命令事件链、持仓对账任一不一致都会阻止命令完成。
5. 全部通过时，命令才按 `claimed → submitted → accepted → filled` 记录为纸面模拟完成；任何一笔本应被拦截时，整条命令记录为 `rejected`。消费者异常时转为 `reconciliation_required`，不自动重试或重新下单。

命令消费者固定使用 `enforce` 模式。任一模拟订单未通过准入门，整条命令都会记录为 `rejected`，不会模拟为已成交；消费者本身也不包含任何券商下单实现。

## 隔离要求

不要使用现有 `longbridge-quant-paper-service`：其当前运行目标并非这条验证链路。改用独立服务，例如 `longbridge-quant-paper-command-verify-service`，并满足以下全部条件：

- `RUNTIME_TARGET_JSON.execution_mode=paper` 且 `LONGBRIDGE_DRY_RUN_ONLY=true`。
- `RUNTIME_TARGET_ENABLED=false`，不会创建或恢复定时任务；只能显式调用 `/dry-run` 生成证据和 `/paper-command-consumer` 消费验证。
- `LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI` 是新的专用 GCS 前缀，不能等于或位于 `EXECUTION_REPORT_GCS_URI` 之下。
- `RUNTIME_TARGET_JSON.strategy_release` 必须是完整、已验证的发布身份。身份缺失、无效或与命令不一致时，消费者不认领命令或拒绝该命令。
- 使用纸面 LongBridge 凭据；即使凭据配置错误地指向其他账户，运行时仍强制 dry-run，消费者也没有下单实现。

## 手动部署

先准备独立的命令存储 URI 和完整的发布身份 JSON。随后手动发起 workflow；它只作用于新的专用服务，不会改 SG、现有 PAPER 服务或 Cloud Scheduler：

```bash
gh workflow run sync-cloud-run-env.yml \
  --repo QuantStrategyLab/LongBridgePlatform \
  -f target=paper-command-verify \
  -f paper_command_verify_execution_command_cloud_uri=gs://<dedicated-bucket>/longbridge-paper-command-verify \
  -f paper_command_verify_strategy_release_json='<complete-strategy-release-json>' \
  -f deploy_image=true \
  -f sync_env=true
```

部署后先手动调用 `/dry-run` 生成命令，再手动调用 `/paper-command-consumer`。检查执行报告中的 `paper_execution_command_consumer`：只有 `status=ok`、命令事件链完整、没有 `would_block` 或 `reconciliation_required`，才可作为后续强制执行阶段的纸面证据。
