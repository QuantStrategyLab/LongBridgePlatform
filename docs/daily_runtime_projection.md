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

## 下一跳缺口

AAB 日报和网站还要在获准的云端身份上调用 `project_listed_reports`，读真实 GCS 执行报告。本批没有做这次读取，也没有改 heartbeat 通知或交易链。真实日投影和 bot 送达要另验。
