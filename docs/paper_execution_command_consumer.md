# LongBridge 纸面命令消费者

这个消费者用于验证延迟执行命令的最后一道风险检查。它只读取 LongBridge 的账户快照和行情，写入纸面命令审计记录；它不会构造执行端口，也不会调用下单 API。

## 处理流程

1. 策略 dry-run 先计算不含准入回执的不可变决策摘要；经 QRS 控制面评估后，把完整 `strategy_release` 身份和纸面风险准入回执一同写入命令内容。
2. 专用消费者只认领当日、仍处于 `queued` 的命令。
3. 它根据实时持仓、现金和行情逐标的生成模拟订单，并比较每个标的前后的绝对风险敞口；不能只根据 `buy` / `sell` 判断风险方向。
4. 消费者先验证回执的内容哈希、决策摘要、策略档案、有效交易日、发布身份与风险策略哈希；再由共享的运行时命令门复核发布身份、有效交易日、命令事件链与持仓对账。任一不一致都会阻止命令完成。
5. 全部通过时，命令才按 `claimed → submitted → accepted → filled` 记录为纸面模拟完成；任何一笔本应被拦截时，整条命令记录为 `rejected`。消费者异常时转为 `reconciliation_required`，不自动重试或重新下单。

命令消费者固定使用 `enforce` 模式。任一模拟订单未通过准入门，整条命令都会记录为 `rejected`，不会模拟为已成交；消费者本身也不包含任何券商下单实现。

准入回执只能由已审计的控制面生成，且只包含发布、策略、策略风险版本、决策摘要与稳定原因码；不记录账户、仓位、下单数量或券商凭据。策略插件只能上报完整性问题，不能绕过或授予准入权限。回执缺失、格式错误、与命令/发布不一致时均为失败关闭。

## 隔离要求

不要使用现有 `longbridge-quant-paper-service`：其当前运行目标并非这条验证链路。改用独立服务，例如 `longbridge-quant-paper-command-verify-service`，并满足以下全部条件：

- `RUNTIME_TARGET_JSON.execution_mode=paper` 且 `LONGBRIDGE_DRY_RUN_ONLY=true`。
- `RUNTIME_TARGET_ENABLED=false`，不会创建或恢复定时任务；只能显式调用 `/dry-run` 生成证据和 `/paper-command-consumer` 消费验证。
- `LONGBRIDGE_EXECUTION_COMMAND_CLOUD_URI` 是新的专用 GCS 前缀，不能等于或位于 `EXECUTION_REPORT_GCS_URI` 之下。
- `RUNTIME_TARGET_JSON.strategy_release` 必须是完整、已验证的发布身份。身份缺失、无效或与命令不一致时，消费者不认领命令或拒绝该命令。
- 使用纸面 LongBridge 凭据；即使凭据配置错误地指向其他账户，运行时仍强制 dry-run，消费者也没有下单实现。

## 本次升级的运行范围

本次变更只补充默认关闭的准入代码与测试，不部署服务、不调用环境同步工作流、不恢复 Scheduler，也不改变现有 SG 或 PAPER 运行目标。后续若单独批准运行，应先在独立变更中验证控制面回执来源、专用命令存储、完整发布身份与 PAPER 账户隔离；在这些条件未全部满足前，缺少回执的命令会被拒绝。
