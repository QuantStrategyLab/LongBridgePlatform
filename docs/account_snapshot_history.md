# 日频账户资产记录

这是关闭默认的工程记录，不是已启用的资产曲线。每日 `execution-report-heartbeat` 在现有 Google Cloud 认证后单独运行账户快照步骤，随后继续原 heartbeat 检查。采样失败会保留失败状态但不跳过原检查，最后再使整个 workflow 失败。不新增 Cloud Scheduler，也不改 `/run`、`/probe`、`/dry-run`。

## 旧 Scheduler/GCS 模式：何时会写

以下条件同时成立才调用 `scripts/record_daily_account_snapshot.py`：

- 当前 matrix 目标的 `id` 是 `paper`、`hk` 或 `sg`（目标 `label` 分别作为预期 scope）
- GitHub variable `ACCOUNT_HISTORY_RECORDING_ENABLED` 精确等于 `true`
- 同一步提供 `ACCOUNT_HISTORY_SERVICE_URL`、`ACCOUNT_HISTORY_GCS_PREFIX` 和可信的 `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID`（优先使用 GitHub protected Secret，旧 variable 仅作兼容回退）
- 目标 ID 来自 `matrix.target.id`，期望 scope 来自 `matrix.target.label`，项目来自已有 `GCP_PROJECT_ID`

`execution-report-heartbeat` 的每日定时运行始终选择完整 manifest matrix。手动 `workflow_dispatch` 可将 `target` 选为 `paper`、`hk` 或 `sg`，只检查该 manifest target；默认 `all` 保持完整 matrix。单目标选择只缩小这次 heartbeat 的账户范围，不启用账户记录或修改任何 target variable；对应 GitHub Environment 的 `ACCOUNT_HISTORY_RECORDING_ENABLED` 仍须单独精确为 `true`，否则采样步骤跳过。

仅需核验部署身份在项目级的 SG probe 权限时，手动选择 `target=sg` 并开启默认关闭的 `inspect_sg_probe_permissions`。其他 target 会在 matrix 解析阶段拒绝。检查只调用 Cloud Resource Manager `testIamPermissions`，输出 `context=project` 及 `cloudscheduler.jobs.get/run/enable/pause` 四项布尔值；该模式跳过采样、heartbeat 检查和发布。项目级结果不证明某个 job 上的条件角色生效，也不替代 SG probe 的实际 job 读回。

target label 只表示这份清单配置的 scope，不能据此推断券商账户身份。脚本从已校验的 runtime target manifest 读取准确 service/region，并先读回 `{service}-probe-scheduler` 的完整 job。默认路径仅接受 `ENABLED` job；PAPER 的 `PAUSED` job 仍在任何 mutation 或 GCS 读取前拒绝。SG 与 HK 在 `RUNTIME_TARGET_ENABLED` 精确为 `false`、`ACCOUNT_HISTORY_RECORDING_ENABLED` 精确为 `true`，且暂停任务完整身份、`POST {service_url}/probe`、空 body、Scheduler OIDC service account/audience、零重试、原控制字段、schedule `35 9,15 * * 1-5` 与对应时区（SG=`America/New_York`，HK=`Asia/Hong_Kong`）全部匹配时，才可走一次受限恢复：避开自然触发前后 10 分钟，resume 一次并读回，run 一次后立即 pause 一次并读回原控制配置。只有恢复确认后才读取归档；resume/run/pause 结果不明或恢复读回失败时不重触发、不读取或发布归档。旧 Scheduler/GCS 模式的任何路径都不直连 internal Cloud Run，也不使用 `/account-snapshot`；HK 与 SG 统一走 `scheduler_archive`，不以 `independent_get` 或放宽 ingress 作为日常路径。

`ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` 必须由部署后的可信读回提供，并与 QRS 的预期绑定完全相同；不能从第一个 GCS 对象反推信任。Workflow 对注入值发出 GitHub mask 命令，Secret 优先，旧 variable 兼容回退。GCS 前缀最后一段必须是 `account_snapshots`，不能落在 execution report 路径上。脚本只查本次触发 UTC 日期与当前 UTC 日期下准确的 target/source 前缀，限单页和 64KiB 对象。列举与固定 generation 读取都带超时、`retry=None`；若分页截断、对象变大或 generation 改变则失败，不把部分结果当完整结果。

接受 `jobs:run` 后，最多等待 180 秒，每 5 秒只读查询 GCS。候选必须是本次触发之后开始、15 分钟内的完整同源观察；取最新合法对象。若没有可信新鲜对象就失败，不追加 Scheduler 触发。QRS POST 前再次检查观察起始时间仍在 15 分钟窗口内。

## 保存什么

只接受 `longbridge_account_snapshot.v1`、`status=partial`、scope 与当前目标预期一致（PAPER/HK/SG）、持仓和现金读取完整、`no_order=true`、`live_authority_granted=false`、`snapshot_atomic=false`，且 `source_binding` 为 `bound` 的 64 位小写 hex。观察起止必须带时区、起点不晚于终点、都不晚于本次运行时刻，并且起点落在本次运行前 15 分钟内。观察日取起点的 UTC 日期，不是交易所收盘日。

对象只保留分币种 `broker_reported_balances`（`currency`、`net_assets`、`total_cash`）和 `cash`（`currency`、`available_cash`、`frozen_cash`、`settling_cash`）。金额必须是有限十进制字符串；负数保持原值，不改成零，也不把币种加总。不保存持仓、订单、token、secret 或完整响应。

producer 为每次观察写入 `{prefix}/{target_id}/{source_binding_id}/{observation_date}/{HHMMSSffffffZ.json}`，其中日期来自 `observed_started_at`，文件名使用 `observed_finished_at`。consumer 固定读取 listing 返回的 generation、保留原字节，并在 POST 前重新验证合同、路径、来源、UTC日期和时间，不改写时间或拼接来源。

只有从该受限 GCS listing 选出的同一对象才会被 POST 到 QRS，发送 body 是读取到的原始字节，不重新序列化。QRS 发布状态与观察读取状态分开输出；发布拒绝或结果未知不会改变原对象，未知 POST 不自动重试。QRS `ok=true` 且回读的目标、观察日、观察结束时间匹配，只表示接收端确认保存，不证明页面已经展示或数据完成物理账户身份核验。接收端按其可信配置绑定目标与来源，调用方不传账户 key 或身份结论。

这份记录不是 TWR，不是收益率，也不授予 live 权限。

## HK 独立只读观察模式（默认关闭，仅手动）

新增的 `ACCOUNT_HISTORY_OBSERVATION_MODE=independent_get` 是与旧归档路线分开的 HK-only 模式。缺省仍为 `scheduler_archive`，旧模式继续保留一次 Scheduler 触发、受限 GCS listing、固定 generation、原字节发布及原有 SG/HK 受限恢复保证。选择独立模式后，失败不会回退到 Scheduler、`/probe`、`/run`、`/dry-run` 或策略 canary。

独立模式仍要求当前 Environment 的 `ACCOUNT_HISTORY_RECORDING_ENABLED=true`，但这个开关在此模式仅允许观察，不表示创建 GCS 归档。它目前只接受：
- 同仓 main 的 `execution-report-heartbeat.yml`，`workflow_dispatch target=hk`
- manifest 的 HK service/region，`ACCOUNT_HISTORY_EXPECTED_SCOPE=HK`、`ACCOUNT_HISTORY_TARGET_ID=hk`、`RUNTIME_TARGET_ENABLED=false`
- 预先可信配置的 source binding ID，与 QRS 受保护绑定保持相同
- 既有 WIF 认证明确使用 `longbridge-platform-deploy@longbridgequant.iam.gserviceaccount.com`，Cloud Run ID token 的 audience 为配置的准确 service 根 URL
- 当前 Cloud Run service 只读 metadata 证明准确 service/project/region、URL与 token audience 相同、显式 ingress=all、Ready；正整数 `metadata.generation` 与 `status.observedGeneration` 完全一致，template revision、latest-created、latest-ready和唯一100%流量 revision 全部相同，才能把该模板的 `LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED=true` 视为实际接流量版本的开关。缺字段、未协调generation或新模板/旧流量组合均在GET前拒绝

Workflow 在认证前执行纯配置校验：原始 service URL 必须已经是完全一致的 canonical HTTPS根URL，尾斜线、大小写或空白等会改变 audience 的输入被拒绝，不自动规范化后继续。只有成功输出的同一个URL才传给两个原有主/重试认证步骤作为 ID-token audience，并传给后续GET配置；失败时不生成该ID token，也不回退使用原始URL。该输出不含凭据、账户数据或service环境。在把token发给service之前，脚本还必须验证上述已协调的准确 serving metadata。未通过验证不发送 token 或调用账户端点。认证步骤的重试不会重试账户 GET。Cloud metadata 从 `gcloud run services describe` 直接经管道进入脚本，不保存到文件或公开日志；ID token 只通过本次 step 环境传递，不写入 artifact。该路线不根据默认 gcloud identity 推定权限，凭据来源仍是上述显式 WIF 配置。

之后只做一次 `GET /account-snapshot`：20秒连接/读取超时、禁重试和重定向、64KiB响应上限。已启用的 endpoint 使用同一次配置 token 元数据读取建立 order-port-free contexts，不刷新 token、不授权交易或恢复。它会读取余额、持仓与部分订单/成交资料，原始响应属于私有账户数据；它不是“只读一个余额字段”的 API，也没有写 GCS 归档。

publisher 严格核验 `partial`、`no_order=true`、`live_authority_granted=false`、`snapshot_atomic=false`、持仓/现金完整标记、HK scope、预期 source binding、带时区的观察起止、当前请求之后开始、无未来时间及原有15分钟窗口。只投影已有 history 合同的余额/现金和合法可选 financing，丢弃持仓、订单、成交计数和其他字段。金额保留原生币种、零值和负值；不做 FX、不把 HK 标签当 HKD、不把 GET 默认币种当 USD。balance 与 cash 币种集合可不同。超出 QRS 原有15位整数/8位小数或32币种行上限的主金额被拒绝，不舍入。

`ACCOUNT_FACTS_SYNC_ENABLED=true` 且目的端/token已正确配置时，纯投影后只 POST 一次到现有 QRS；关闭时只报告 `observation=observed account_facts_publish=disabled`，不声称任何保存。成功需要 `ok=true`、`stored=true` 和目标、UTC观察日、观察结束时间全部与本次原值相符。未知 POST 不重试。此模式由 QRS 持久化 latest 和 daily history，**不保存原始 GET 响应，也不新建 GCS 归档**；`observed` 与 `published` 不等于旧模式的 `recorded`。

source binding 是 deployment/scope/token-version 来源摘要，含 revision；不是券商稳定账户号或 Cash/Margin 身份证明。QRS 继续按可信绑定/当前唯一账户 option 核验归属，读模型仍为 `partial_identity`，不能合计全部账户或跨来源拼接历史。不要从第一份响应反推新的可信 binding。

### 上线前仍需满足的边界

源码实现不证明当前 HK 已具备以上状态或权限。部署准入默认只允许 PAPER 修改 `LONGBRIDGE_ACCOUNT_SNAPSHOT_ENABLED`。HK 在取得授权后，可通过 image-only 模式对已审 SGHK 候选设置 `account_snapshot_enabled=true`（与 history 四元组互斥），暂存读回成功后把该 revision 切到 100% 流量；不能手动绕过准入或直接改 IAM。若当前 HK 为 internal ingress，此 GitHub-hosted runner 模式会拒绝，不能放宽 ingress、增加 IAM、创建 Scheduler 或借用暂停的 probe。WIF/ID-token签发、service只读及 invoker 权限不足时，同样停止；不新建 token secret 或切换主体。

当前候选仅允许手动单次验收；即使 Environment 中设置 independent_get，定时运行也会拒绝采样，原 heartbeat 仍继续后再汇总失败。把它扩为每日独立观察必须在单次受限真实 GET、准确 QRS stored ACK 和登录页面 readback 验收后另行批准。回滚可将采样 master 开关设回 false；不可为回滚自动恢复 HK Scheduler。代码修改、合成测试通过和配置开关存在，都不表示真实账户读数已经同步。


## main 上的 PAPER 镜像暂存

`sync-cloud-run-env.yml` 的 image-only 入口仍由同仓 main workflow 控制。PAPER 可以额外把已审查候选 `0b939723c1db3ef59175535998b470cbcd4b8824` 做成无流量镜像，并只更新 image、commit、run 标签和四个 history 环境变量。HK/SG 不接受这个候选。准入读取候选的 `uv.lock`、`pyproject.toml` 和 `qsl.toml`，不执行候选脚本。本轮只改源码和离线测试，没有执行云端暂存，也没有切流。

已审固定候选 `a2921d157efb887e9210fad6734ca040ea6e5293` 在自然 PAPER `/probe` 周期中复用一次原始余额读数生成记录。无流量暂存不会切换流量或产生首个样本。日常 heartbeat 现在经已有内部 probe Scheduler 触发该路径，再读取可信 GCS 对象；源码接线本身不证明已产生对象或网站已显示。

## 尚未启用

本轮没有打开 GitHub variable，没有新增 secret 或 IAM，也没有真实采样。本次代码不配置生产 QRS URL/token，也不改变该关闭状态。以后若要启用，需要先确认 paper 服务上的部署版本、来源读取权限、bucket 保留策略和上述变量，并读回第一份对象。在那之前，仓库里的步骤保持关闭。
