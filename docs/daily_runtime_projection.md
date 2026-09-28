# LongBridge 每日运行投影

当前执行报告发布的是单次 run，不是一日一条的运行记录。`scripts/execution_report_heartbeat.py` 的扫描保持 alerts-only：正常心跳不发送，返回码、36 小时读取和 24 小时到期判断都不因本投影改变。

本入口只读已经拿到的报告，投影一个平台、一个目标、一个业务日。不新建账本、服务、数据库或调度，也不查询券商或 Secret。没有远端输入时，输出只是离线投影，不是已生成的真实日报。

## 调用

```python
from scripts.daily_runtime_projection import project_daily_runtime, project_listed_reports

projected = project_daily_runtime(
    targets=targets,
    reports=reports,
    observed_at=observed_at,
)
```

`targets` 使用 `load_runtime_targets` 已经规范化的目标。`reports` 是执行报告对象，或 `{payload, object_uri, object_updated_at}`。对象上传时间只作列表元数据，业务日和运行时间来自 `started_at` / `finished_at`。

已有云端调用方下一跳可以复用 heartbeat 的列表和读取，而不要改 `main`：

```python
projected = project_listed_reports(
    targets=targets,
    objects=[(uri, updated_at)],
    read_payload=lambda uri: heartbeat._cat_gcs_json(uri, project=project),
    observed_at=observed_at,
)
```

本地文件：

```bash
.venv/bin/python scripts/daily_runtime_projection.py \
  --reports reports.json \
  --targets targets.json \
  --observed-at 2026-09-28T16:40:00+08:00
```

退出码 0 表示投影已写出，记录不完整也是 0。输入无法解析时退出码 2。这不是 heartbeat 的告警退出码。

## 输出

顶层：`platform`、`observed_at`、`completeness`、`read_errors`、`records`、`unmatched_reports`。

每条 `records[]`：`target_key`、`target`（service / strategy_profile / account_scope）、`business_date`、`timezone`、`status`、`kind`、`completeness`、`execution_lane`、`schedule`、`runs`、`excluded_reports`、`conflicts`、`fills`。

`status` 只来自能区分的字段：

| 状态 | 依据 |
| --- | --- |
| `no_submission` / `no_signal` / `no_rebalance` | `summary.execution_status` 与 `broker_submission_done=false`、`action_done` 非真、`orders_pending_count=0`、无 errors 同时成立，并且该 run 覆盖本次 `latest_due_at`、结束时间不晚于 `observed_at`。同日更早的 run 只保留为日内事实，不能单独证明本次到期 |
| `submitted` | `broker_submission_done`、`action_done` 或 receipt `submitted`。`execution_status=no_action` 不能单独否定它 |
| `broker_acknowledged` / `partially_filled` / `filled` | 只接受 receipt 里写明的结果；`filled` 还要 `broker_confirmation=filled`。partial 不会升成 filled |
| `reconciliation_required` / `unknown` / `failed` / `blocked` | pending、`pending_reconciliation`、receipt、errors 或未知状态。这些优先于休市、未到期和窗口外。跨业务日的 `unknown` / `pending_reconciliation` 继续保留；次日休市、换日或当日普通成功不能解除 |
| `dry_run` / `shadow` / `validation` | `dry_run`、`dry_run_only`、`mode=shadow`、`validation_only`。不写成 paper/live |
| `not_due` / `market_closed` / `outside_window` / `within_grace` | cron、市场日历和时区。宽限由调用方传入，默认 30 分钟，与 heartbeat 默认值相同。这是调度观察，不是执行成功 |
| `missing_report` | 到期且宽限已结束，仍没有覆盖 `latest_due_at` 的已观察 run。未来时间、倒置时间和同日更早的 run 都不算这次到期证据 |
| `read_incomplete` / `insufficient` / `conflict` | 读取失败、字段不够区分，或同一 `run_id` 的证据互相矛盾。不默认健康 |

`fills` 固定为 `source=not_connected`、`records=[]`、`count=null`。不使用 `order_events_count`、`orders_previewed` 或报价。缺成交源不是 0 笔成交。

同一 `run_id` 且事实相同只保留一条。只有同一 `run_id` 的事实矛盾才记 `conflict`。同日不同 run 的无动作和已提交可以同时保留。不同 `target_key` 不合并。

## 字段来源

- 平台、目标、`run_id`、`started_at`、`finished_at`、`status`、`summary`：QPK runtime report
- `execution_receipt.outcome` / `broker_confirmation`：已附在报告上的 receipt；没有就不编造
- 调度：`runtime_heartbeat_policy.classify_business_date_schedule`，复用现有 cron、市场日历、时区和 publication grace
- `observed_at`：调用方传入的观察时间

## 发布到私有 GCS

`scripts/publish_daily_runtime_projection.py` 可以在 22:20 UTC heartbeat 之后，把一个 PAPER 目标的原始投影写到已有私有桶。默认关闭。只有 `RUNTIME_DAILY_PROJECTION_ENABLED` 精确等于 `true`，并且同时给出末段为 `runtime_daily` 的 `gs://` 前缀、`RUNTIME_DAILY_PROJECTION_TARGET_ID=paper`、`RUNTIME_HEARTBEAT_ACCOUNT_SCOPE=PAPER` 时才读取和写入。配置不全、scope 不是 `PAPER`，或 PAPER 目标不是恰好一个，都不写对象。服务名或环境名不能代替账户范围。

发布前用同一次 Cloud Run 部署读回核对实际 service、account scope 和 `runtime_target_enabled`。profile hydrate 只带回策略名，不能代替这次核对；部署里没有显式 enabled 时也不当成 true。输入候选与部署 JSON 的 service 或 scope 不一致，或部署 `runtime_target_enabled` 为 false，都不写对象，因此不会把 SG 部署或已停用部署写成 PAPER 的 `market_closed` / `complete`。

业务日以该服务实际 Cloud Scheduler 的 enabled、cron 和 timezone 为准，并核对 URI 指向该服务的策略 run。模板 cron 不能单独把一天写成 `not_due` / `complete`。调度暂停记 `scheduler_paused`，缺失或冲突记 `scheduler_missing` / `scheduler_conflict`，这些都不会标成完整的正常日。

报告列表和单份读取走已安装的存储客户端，带 20 秒时限、字节上限，并且 `retry=None`。列表扫描上限是 256 条，和最终保留的 20 条分开；预算内按 `updated` 取最新 20 条。扫描达到上限，或已发现超过 20 条因而只保留最新 20 条时，都记 `listing truncated`，`completeness` 为 incomplete。调度身份沿用 heartbeat 对服务主机和策略 run URI 的既有判断，读取入口是现有的 scheduler job list。

对象路径是 `{prefix}/longbridge/paper/{business_date}.json`。`business_date` 和时区来自该目标的有效调度。文件内容就是 `project_listed_reports` 的原 JSON，不是资产曲线，也不是成交账本。`fills` 仍是未接通。上传使用现有存储客户端，`if_generation_match=0`、`timeout=20`、`retry=None`；同日对象已存在则不覆盖。列表或读取不完整时，对象里的 `read_errors` 和 `completeness` 保持不完整，不把这一天写成正常休市。

可选的 QRS 日报同步另由 `RUNTIME_DAILY_SYNC_ENABLED` 精确等于 `true` 才开启，并要求 HTTPS 精确路径 `/api/runtime-daily/sync`。它复用既有 `EXECUTION_EVIDENCE_SYNC_TOKEN`，不增加 token 类型；workflow 仅在该开关开启时把 secret 送入当前 step。只允许固定 QRS PAPER 目标 `longbridge-quant-paper-service|russell_top50_leader_rotation|paper`，不由调用者指定或推导账户 key。只有本次 GCS 明确新建后才 POST 与 GCS 完全相同的 JSON；`already_recorded` 与写入结果未知均不 POST。POST 不跟随重定向、不重试，限制超时和响应大小。同步状态独立输出为 `qrs_sync=disabled|recorded|rejected|unknown`；网络结果未知或 QRS 拒绝不会撤销已保存的 GCS 记录，也不表示网页已经展示。成功响应必须回显同一 `business_date` 和 `target_key`，且包含服务端解析出的非空 `account_key` 才报告 `recorded`，但不会记录或输出该账户 key。

heartbeat 的告警、返回码和交易链不变。这个步骤不调用 heartbeat 的 `main`。workflow 只在 PAPER、变量显式为 `true`、任务未取消且 Google 认证成功时运行；heartbeat 业务失败后仍可以写出异常投影。

该同步仍默认关闭；本地只读核验发现 LongBridge PAPER 环境尚无 `RUNTIME_DAILY_SYNC_ENABLED` 或 `RUNTIME_DAILY_SYNC_URL` variable。它复用已有 execution-evidence secret 名称，不新增 secret。真实报告前缀、桶权限及第一份对象仍需在获准身份上验收。bot 送达不在这一步。
