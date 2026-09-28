# 日频账户资产记录

这是关闭默认的工程记录，不是已启用的资产曲线。每日 `execution-report-heartbeat` 在原有检查之后可以多跑一步；前面的 heartbeat 步骤失败时，这一步按 `success()` 跳过。不新增 Cloud Scheduler，也不改 `/run`、`/probe`、`/dry-run`。

## 何时会写

四个条件同时成立才调用 `scripts/record_daily_account_snapshot.py`：

- 当前 matrix 目标的 `label` 是 `PAPER`
- GitHub variable `ACCOUNT_HISTORY_RECORDING_ENABLED` 精确等于 `true`
- 同一步提供 `ACCOUNT_HISTORY_SERVICE_URL`、`ACCOUNT_HISTORY_GCS_PREFIX`
- 目标 ID 来自 `matrix.target.id`，期望 scope 来自 `matrix.target.label`，项目来自已有 `GCP_PROJECT_ID`

`PAPER` 只表示这份清单配置的范围，不能据此推断券商账户身份。脚本再要求期望 scope 精确为 `PAPER`，服务根必须是没有 userinfo、query、fragment 和额外 path 的 HTTPS `*.run.app`，并且只 GET 该源站的 `/account-snapshot`。GCS 前缀最后一段必须是 `account_snapshots`，不能落在 execution report 路径上。缺任何一项就失败，不补默认值。

可选 QRS 发布仍默认关闭；只有 `ACCOUNT_FACTS_SYNC_ENABLED` 精确为 `true` 时才使用 `ACCOUNT_FACTS_SYNC_URL` 与专用 `ACCOUNT_FACTS_SYNC_TOKEN`。URL 必须是 HTTPS 的精确 `/api/account-facts/sync`，不接受 userinfo、端口、query、fragment 或其他路径；POST 不跟随重定向，设置超时并限制响应体大小。token 只作为该 workflow step 的环境变量和 Authorization header 使用，不打印。

OIDC 使用 heartbeat 里已经配置的 gcloud：`gcloud auth print-identity-token --audiences=<服务根> --quiet`。stdout 只留在内存，不打印，也不放进参数；失败只报短类别。HTTP 有超时、不跟随重定向、不重试。观察时钟在响应收齐之后读取。

## 保存什么

只接受 `longbridge_account_snapshot.v1`、`status=partial`、scope 为 `PAPER`、持仓和现金读取完整、`no_order=true`、`live_authority_granted=false`、`snapshot_atomic=false`，且 `source_binding` 为 `bound` 的 64 位小写 hex。观察起止必须带时区、起点不晚于终点、都不晚于本次运行时刻，并且起点落在本次运行前 15 分钟内。观察日取起点的 UTC 日期，不是交易所收盘日。

对象只保留分币种 `broker_reported_balances`（`currency`、`net_assets`、`total_cash`）和 `cash`（`currency`、`available_cash`、`frozen_cash`、`settling_cash`）。金额必须是有限十进制字符串；负数保持原值，不改成零，也不把币种加总。不保存持仓、订单、token、secret 或完整响应。

路径是 `{prefix}/{target_id}/{source_binding_id}/{YYYY-MM-DD}.json`。目标与来源绑定分成不同路径段，不把两段来源拼进同一个对象。`create_text` 只创建：已有对象时结果是 `already_recorded`，不覆盖、不重新拉取。存储结果不明则停止，不写占位点，也不重试。错误输出只有短类别。

仅当本次 `create_text` 明确返回新建成功时，脚本才把完全相同的历史 JSON body POST 到 QRS。`already_recorded` 明确跳过发布，不能把这次新读取的内容冒充为已保存对象；`store_unknown` 不 POST。QRS 发布状态与历史记录状态分开输出：发布拒绝或结果未知不会撤销已写入历史；未知 POST 不自动重试。QRS `ok=true` 且回读的目标、观察日、观察结束时间匹配，只表示接收端确认保存，不证明页面已经展示或数据完成物理账户身份核验。接收端按其可信配置绑定目标与来源，调用方不传账户 key 或身份结论。

这份记录不是 TWR，不是收益率，也不授予 live 权限。

## main 上的 PAPER 镜像暂存

`sync-cloud-run-env.yml` 的 image-only 入口仍由同仓 main workflow 控制。PAPER 可以额外把已审查候选 `0b939723c1db3ef59175535998b470cbcd4b8824` 做成无流量镜像，并只更新 image、commit、run 标签和四个 history 环境变量。HK/SG 不接受这个候选。准入读取候选的 `uv.lock`、`pyproject.toml` 和 `qsl.toml`，不执行候选脚本。本轮只改源码和离线测试，没有执行云端暂存，也没有切流。

该固定候选使用自然运行周期中已经读取的余额生成记录，与上文 main 的 HTTP heartbeat 快照入口不同。无流量暂存不会启用 HTTP 快照链，不设置 `ACCOUNT_HISTORY_SERVICE_URL`，也不调用 `/run` 或产生首个样本。上文 HTTP 入口条件不能作为该候选的启用步骤；正式采用及自然周期首样本需要分别核验。

## 尚未启用

本轮没有打开 GitHub variable，没有新增 secret 或 IAM，也没有真实采样。本次代码不配置生产 QRS URL/token，也不改变该关闭状态。以后若要启用，需要先确认 paper 服务上的部署版本、来源读取权限、bucket 保留策略和上述变量，并读回第一份对象。在那之前，仓库里的步骤保持关闭。
