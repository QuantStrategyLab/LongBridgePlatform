# 日频账户资产记录

这是关闭默认的工程记录，不是已启用的资产曲线。每日 `execution-report-heartbeat` 在现有 Google Cloud 认证后单独运行 PAPER 采样步骤，随后继续原 heartbeat 检查。采样失败会保留失败状态但不跳过原检查，最后再使整个 workflow 失败。不新增 Cloud Scheduler，也不改 `/run`、`/probe`、`/dry-run`。

## 何时会写

四个条件同时成立才调用 `scripts/record_daily_account_snapshot.py`：

- 当前 matrix 目标的 `label` 是 `PAPER`
- GitHub variable `ACCOUNT_HISTORY_RECORDING_ENABLED` 精确等于 `true`
- 同一步提供 `ACCOUNT_HISTORY_SERVICE_URL`、`ACCOUNT_HISTORY_GCS_PREFIX` 和非敏感 `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID`
- 目标 ID 来自 `matrix.target.id`，期望 scope 来自 `matrix.target.label`，项目来自已有 `GCP_PROJECT_ID`

`PAPER` 只表示这份清单配置的范围，不能据此推断券商账户身份。脚本从已校验的 runtime target manifest 读取准确 PAPER service/region，并先读回 `{service}-probe-scheduler` 的完整 job。只有 job 完整资源名、`ENABLED`、`POST {service_url}/probe`、空 body、Scheduler OIDC service account/audience 及零重试配置全部匹配时，才调用一次 Cloud Scheduler `jobs:run`。不直连 internal Cloud Run，也不使用 `/account-snapshot`。Scheduler 请求结果未知时不重触发。缺任何配置就失败，不补默认值。

`ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` 必须由部署后的可信读回提供，并与 QRS 的预期绑定完全相同；不能从第一个 GCS 对象反推信任。GCS 前缀最后一段必须是 `account_snapshots`，不能落在 execution report 路径上。脚本只查本次触发 UTC 日期与当前 UTC 日期下准确的 target/source 前缀，限单页和 64KiB 对象。列举与固定 generation 读取都带超时、`retry=None`；若分页截断、对象变大或 generation 改变则失败，不把部分结果当完整结果。

接受 `jobs:run` 后，最多等待 180 秒，每 5 秒只读查询 GCS。候选必须是本次触发之后开始、15 分钟内的完整同源观察；取最新合法对象。若没有可信新鲜对象就失败，不追加 Scheduler 触发。QRS POST 前再次检查观察起始时间仍在 15 分钟窗口内。

## 保存什么

只接受 `longbridge_account_snapshot.v1`、`status=partial`、scope 为 `PAPER`、持仓和现金读取完整、`no_order=true`、`live_authority_granted=false`、`snapshot_atomic=false`，且 `source_binding` 为 `bound` 的 64 位小写 hex。观察起止必须带时区、起点不晚于终点、都不晚于本次运行时刻，并且起点落在本次运行前 15 分钟内。观察日取起点的 UTC 日期，不是交易所收盘日。

对象只保留分币种 `broker_reported_balances`（`currency`、`net_assets`、`total_cash`）和 `cash`（`currency`、`available_cash`、`frozen_cash`、`settling_cash`）。金额必须是有限十进制字符串；负数保持原值，不改成零，也不把币种加总。不保存持仓、订单、token、secret 或完整响应。

producer 为每次观察写入 `{prefix}/paper/{source_binding_id}/{observation_date}/{HHMMSSffffffZ.json}`，其中日期来自 `observed_started_at`，文件名使用 `observed_finished_at`。consumer 固定读取 listing 返回的 generation、保留原字节，并在 POST 前重新验证合同、路径、来源、UTC日期和时间，不改写时间或拼接来源。

只有从该受限 GCS listing 选出的同一对象才会被 POST 到 QRS，发送 body 是读取到的原始字节，不重新序列化。QRS 发布状态与观察读取状态分开输出；发布拒绝或结果未知不会改变原对象，未知 POST 不自动重试。QRS `ok=true` 且回读的目标、观察日、观察结束时间匹配，只表示接收端确认保存，不证明页面已经展示或数据完成物理账户身份核验。接收端按其可信配置绑定目标与来源，调用方不传账户 key 或身份结论。

这份记录不是 TWR，不是收益率，也不授予 live 权限。

## main 上的 PAPER 镜像暂存

`sync-cloud-run-env.yml` 的 image-only 入口仍由同仓 main workflow 控制。PAPER 可以额外把已审查候选 `0b939723c1db3ef59175535998b470cbcd4b8824` 做成无流量镜像，并只更新 image、commit、run 标签和四个 history 环境变量。HK/SG 不接受这个候选。准入读取候选的 `uv.lock`、`pyproject.toml` 和 `qsl.toml`，不执行候选脚本。本轮只改源码和离线测试，没有执行云端暂存，也没有切流。

已审固定候选 `a2921d157efb887e9210fad6734ca040ea6e5293` 在自然 PAPER `/probe` 周期中复用一次原始余额读数生成记录。无流量暂存不会切换流量或产生首个样本。日常 heartbeat 现在经已有内部 probe Scheduler 触发该路径，再读取可信 GCS 对象；源码接线本身不证明已产生对象或网站已显示。

## 尚未启用

本轮没有打开 GitHub variable，没有新增 secret 或 IAM，也没有真实采样。本次代码不配置生产 QRS URL/token，也不改变该关闭状态。以后若要启用，需要先确认 paper 服务上的部署版本、来源读取权限、bucket 保留策略和上述变量，并读回第一份对象。在那之前，仓库里的步骤保持关闭。
