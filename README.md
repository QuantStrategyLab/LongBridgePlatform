# LongPort US Equity Strategy Runtime

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Quant system on LongPort OpenAPI and Google Cloud Run.

This repository uses `QuantPlatformKit` for LongPort token handling, context bootstrap, account snapshot access, market data, and order submission. Cloud Run deploys this repository directly.
The runtime now carries a structured `RuntimeTarget` / `RUNTIME_TARGET_JSON` alongside the compatibility `STRATEGY_PROFILE` selector. Strategy-owned defaults come from `UsEquityStrategies`; platform variables are only explicit overrides.
The LongBridge runtime can execute the seven current `runtime_enabled` `us_equity` profiles from `UsEquityStrategies`; `LongBridgePlatform` keeps the LongPort runtime, token refresh, execution, and notification flow.
`STRATEGY_PROFILE` remains the compatibility selector for strategy routing, while `RuntimeTarget` describes the running service identity.

Full strategy documentation now lives in [`UsEquityStrategies`](https://github.com/QuantStrategyLab/UsEquityStrategies). The sections below focus on LongBridge runtime behavior, profile enablement, deployment, and credentials.
This runtime matrix is the authoritative enablement source for LongBridge. `UsEquityStrategies` carries strategy-layer logic, cadence, compatibility, and metadata.

### Execution boundary

The mainline runtime now follows one path only:

- `main.py` assembles `StrategyContext` plus platform overrides
- `strategy_runtime.py` loads the unified strategy entrypoint
- `entrypoint.evaluate(ctx)` returns a shared `StrategyDecision`
- `decision_mapper.py` converts that decision into LongBridge order and notification plans

Platform execution no longer depends on `strategy/allocation.py` or hard-coded strategy asset lists in the runtime mainline.


**LongBridge profile status**

| Canonical profile | Display name | Eligible | Enabled | Domain | Runtime note |
| --- | --- | --- | --- | --- | --- |
| `global_etf_confidence_vol_gate` | Global ETF Confidence Vol Gate | Yes | Yes | `us_equity` | enabled confidence-gated ETF rotation line |
| `global_etf_rotation` | Global ETF Rotation | Yes | Yes | `us_equity` | enabled weight-mode rotation line |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | Yes | Yes | `us_equity` | enabled feature-snapshot stock baseline |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Leader Rotation Top50 Balanced | Yes | Yes | `us_equity` | selectable balanced Top50 monthly leader rotation |
| `soxl_soxx_trend_income` | SOXL/SOXX Semiconductor Trend Income | Yes | Yes | `us_equity` | current SG deployment |
| `tqqq_growth_income` | TQQQ Growth Income | Yes | Yes | `us_equity` | selectable growth line |
| `tech_communication_pullback_enhancement` | Tech/Communication Pullback Enhancement | Yes | Yes | `us_equity` | current PAPER deployment |

Check the current matrix locally:

```bash
python3 scripts/print_strategy_profile_status.py
```

### Strategy documentation boundary

Strategy logic, cadence, asset universes, parameters, and research/backtest notes live in `UsEquityStrategies`. This platform README keeps only LongBridge profile enablement, env vars, deployment wiring, broker execution behavior, and notification transport.

### Notifications

Telegram notifications include structured execution and heartbeat messages, with English and Chinese variants. Strategy-specific signal/status fields come from the selected `UsEquityStrategies` profile; LongBridge-specific fields cover order submission, fill/reject/error reporting, account prefix, and region.

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes | Bot token for alerts; recommended to inject from Secret Manager secret `longbridge-telegram-token` |
| `GLOBAL_TELEGRAM_CHAT_ID` | Yes | Telegram chat or user ID used by this service. |
| `LONGPORT_APP_KEY` | Yes | LongPort OpenAPI app key (for token refresh); recommended to inject from the region-specific Secret Manager secret for this deployment, such as `longport-app-key-paper` / `longport-app-key-hk` / `longport-app-key-sg` |
| `LONGPORT_APP_SECRET` | Yes | LongPort OpenAPI app secret (for token refresh); recommended to inject from the region-specific Secret Manager secret for this deployment, such as `longport-app-secret-paper` / `longport-app-secret-hk` / `longport-app-secret-sg` |
| `LONGPORT_SECRET_NAME` | No | Secret Manager secret name for LongPort token (default: `longport_token_paper`) |
| `ACCOUNT_PREFIX` | No | Alert/log prefix for account/environment (default: `DEFAULT`) |
| `STRATEGY_PROFILE` | Yes | Strategy profile selector for compatibility and strategy routing. Set explicitly per deployment; enabled values include `global_etf_confidence_vol_gate`, `global_etf_rotation`, `mega_cap_leader_rotation_top50_balanced`, `russell_1000_multi_factor_defensive`, `soxl_soxx_trend_income`, `tech_communication_pullback_enhancement`, and `tqqq_growth_income`. The structured runtime target is carried separately as `RUNTIME_TARGET_JSON`. |
| `ACCOUNT_REGION` | No | Account region marker for platform-style deployment (e.g. `PAPER`, `HK`, `SG`; defaults to `ACCOUNT_PREFIX` / `DEFAULT`) |
| `LONGBRIDGE_DRY_RUN_ONLY` | No | Set to `true` to keep the selected deployment in dry-run mode. |
| `LONGBRIDGE_FRACTIONAL_LIMIT_BUY_FALLBACK_TO_MARKET` | No | Set to `true` to convert fractional `limit buy` orders to `market buy` orders instead of skipping them. |
| `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT` | No | Set to `true` to log raw LongBridge position quantity and available quantity for troubleshooting. |
| `LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON` | No | Optional LongBridge-side strategy plugin mount JSON. The plugin artifact controls mode; platform config must not set `mode`. |
| `INCOME_THRESHOLD_USD` | No | Optional strategy override for the `tqqq_growth_income` income-layer threshold. Leave unset to use the strategy package default. |
| `QQQI_INCOME_RATIO` | No | Optional strategy override for QQQI's share of the `tqqq_growth_income` income layer, 0–1. |
| `NOTIFY_LANG` | No | Notification language: `en` (English, default) or `zh` (Chinese) |
| `GOOGLE_CLOUD_PROJECT` | No | GCP project ID (defaults to ADC project when unset) |

Strategy allocation can still target fractional dollar values and fractional position weights. The LongBridge execution layer keeps the tested conservative rule: `limit buy` orders stay whole-share only by default, while `market buy` / `market sell` and `limit sell` can preserve fractional quantities when the target quantity is at least 1 share. If you set `LONGBRIDGE_FRACTIONAL_LIMIT_BUY_FALLBACK_TO_MARKET=true`, fractional `limit buy` orders are downgraded to market buys instead of being skipped. When a target value is zero, sell sizing uses the sellable position quantity instead of re-deriving shares from current price, so liquidation targets do not leave a residual share because of quote drift.

Secret Manager must contain the secret named by `LONGPORT_SECRET_NAME` (default: `longport_token_paper`), where the **latest version = active access token**. The app refreshes it when expiry is within 30 days.

Recommended runtime secrets in the `longbridgequant` project:

- `longbridge-telegram-token`
- `longport-app-key-paper`
- `longport-app-key-hk`
- `longport-app-key-sg`
- `longport-app-secret-paper`
- `longport-app-secret-hk`
- `longport-app-secret-sg`
- `longport_token_paper`
- `longport_token_hk`
- `longport_token_sg`

### Multi-deployment isolation (paper/HK/SG)

Deploy the same codebase as multiple Cloud Run services (e.g. `paper` and `SG` today, `HK` later) by setting different values per service:

- `LONGPORT_SECRET_NAME`: point to different secrets (e.g. `longport_token_paper`, `longport_token_hk`, `longport_token_sg`)
- `ACCOUNT_PREFIX`: e.g. `PAPER`, `HK`, `SG` (all Telegram/log alerts will include `[ACCOUNT_PREFIX]`)
- `STRATEGY_PROFILE`: set per service; current live examples are `mega_cap_leader_rotation_top50_balanced` on paper and `soxl_soxx_trend_income` on SG. `HK` will use the same pattern later. The deployment control plane now also carries `RUNTIME_TARGET_JSON`; treat `STRATEGY_PROFILE` as a compatibility input that still selects the strategy implementation, not the only identity key.
- Current strategy domain is `us_equity`. `STRATEGY_PROFILE` still goes through a platform capability matrix plus a rollout allowlist derived from `runtime_enabled` strategy metadata: `eligible` means the platform can run it in theory, `enabled` means the current rollout really allows it.
- `ACCOUNT_REGION`: explicitly mark the deployed account region (`PAPER` / `HK` / `SG`); if unset, the app falls back to `ACCOUNT_PREFIX` or `DEFAULT`
- `LONGBRIDGE_DRY_RUN_ONLY`: set per service when that deployment should stay dry-run
- `NOTIFY_LANG`: set `en` or `zh` per deployment

### GitHub-managed env sync for paper / HK / SG

If code deployment still uses Google Cloud Trigger, but you want GitHub to be the single source of truth for runtime env vars, this repo includes `.github/workflows/sync-cloud-run-env.yml`.

Recommended setup:

- **Repository Variables (shared):**
  - `ENABLE_GITHUB_ENV_SYNC` = `true`
  - `TELEGRAM_TOKEN_SECRET_NAME` (recommended: `longbridge-telegram-token`)
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **Repository Secrets (shared):**
  - Optional fallback only: `TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-paper`**
  - Variables: `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (strategy overrides only; leave unset to inherit `UsEquityStrategies`)
  - Current live example: `STRATEGY_PROFILE=mega_cap_leader_rotation_top50_balanced`
  - Recommended secret-name values: `longport-app-key-paper`, `longport-app-secret-paper`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (strategy overrides only; leave unset to inherit `UsEquityStrategies`)
  - Current live example: `STRATEGY_PROFILE=soxl_soxx_trend_income`
  - Recommended secret-name values: `longport-app-key-sg`, `longport-app-secret-sg`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (strategy overrides only; leave unset to inherit `UsEquityStrategies`)
  - Current live example: `STRATEGY_PROFILE=tech_communication_pullback_enhancement`
  - Recommended secret-name values: `longport-app-key-hk`, `longport-app-secret-hk`

On every push to `main`, the workflow updates the configured Cloud Run services with the shared and per-environment values above, and removes `TELEGRAM_CHAT_ID` from each Cloud Run service.

Important:

- `CLOUD_RUN_REGION` should be set on each GitHub Environment, not as one shared repository variable. This lets `paper`, `HK`, and `SG` live in different Cloud Run regions.
- `LONGPORT_APP_KEY_SECRET_NAME` and `LONGPORT_APP_SECRET_SECRET_NAME` should also be set on each GitHub Environment. Do not keep one repository-level default for them when `paper`, `HK`, and `SG` use different LongPort credentials.
- The workflow only becomes strict when `ENABLE_GITHUB_ENV_SYNC=true`. If this variable is unset, the sync job is skipped and the old Google Cloud Trigger-only setup keeps working. Once you set it to `true`, missing env-sync values become a hard failure so you do not get a false green deployment. The selected profile's snapshot/config requirements are resolved from `scripts/print_strategy_profile_status.py --json` instead of a hard-coded strategy-name list.
- The workflow now also emits `RUNTIME_TARGET_JSON` so Cloud Run receives a structured runtime target alongside the legacy `STRATEGY_PROFILE` input.
- GitHub now authenticates to Google Cloud with OIDC + Workload Identity Federation, so `GCP_SA_KEY` is no longer required for this workflow.
- If you deploy with `gcloud run deploy --source` or a Cloud Run source trigger, also grant `roles/storage.objectViewer` on `gs://run-sources-<project>-<region>` to the build service account, the deploy service account, and the default compute service account. Otherwise source deploy can fail before Cloud Build starts with `storage.objects.get` denied.
- Here "shared" only means **shared inside this repository** between the `paper`, `HK`, and `SG` Cloud Run services. The Telegram token can still be shared, but LongPort app credentials should live in Secret Manager and be referenced by per-environment secret-name variables; they are not meant to be a global secret set reused by unrelated quant repos.
- If you want one cross-project shared layer across multiple quant repos, keep it small: `GLOBAL_TELEGRAM_CHAT_ID` and `NOTIFY_LANG` are reasonable; account credentials and deployment keys are not.

### Deployment unit and naming

- `QuantPlatformKit` is only a shared dependency; Cloud Run still deploys `LongBridgePlatform` itself.
- Recommended Cloud Run service names: `longbridge-quant-paper-service`, `longbridge-quant-hk-service`, and `longbridge-quant-sg-service`.
- Keep using three triggers and three GitHub Environments today: `longbridge-paper`, `longbridge-hk`, and `longbridge-sg`. The split key is still `CLOUD_RUN_SERVICE + CLOUD_RUN_REGION`, and the runtime identity is now explicit through `RUNTIME_TARGET_JSON` with `STRATEGY_PROFILE + ACCOUNT_REGION` kept for compatibility.
- If you later rename or move this repository, rebuild the GitHub source binding in Google Cloud for all Cloud Build triggers instead of assuming the existing source binding will follow the rename.
- For the shared deployment model and trigger migration checklist, see [`QuantPlatformKit/docs/deployment_model.md`](../QuantPlatformKit/docs/deployment_model.md).

### Quick deploy

1. Enable **Cloud Run** and **Secret Manager API** in GCP.
2. Create secret `longport_token_paper` for paper / `longport_token_hk` for HK / `longport_token_sg` for SG (or your custom `LONGPORT_SECRET_NAME`) in Secret Manager and add your LongPort access token as the first version.
3. Set the required env vars above on the Cloud Run service.
4. Deploy the app to Cloud Run (e.g. `gcloud run deploy` from repo root with Dockerfile or buildpack).
5. Create two Cloud Scheduler jobs that POST to the Cloud Run URL. Use `"/precheck"` after the open window and `"/"` near the close window. Choose both crons from the strategy-layer cadence in `UsEquityStrategies`; this platform repo only owns the runtime trigger wiring.

IAM: the Cloud Run service account needs **Secret Manager Admin** (or Secret Accessor for the configured `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, and `LONGPORT_APP_SECRET_SECRET_NAME`, such as `longport_token_paper`, `longport-app-key-paper`, `longport-app-secret-paper`) and **Logs Writer**. Build/deploy typically uses a separate account with Artifact Registry Writer, Cloud Run Admin, Service Account User.


---

<a id="中文"></a>
## 中文

基于 LongPort OpenAPI 和 Google Cloud Run 的量化交易系统。

这个仓库通过 `QuantPlatformKit` 复用 LongPort token 处理、上下文初始化、账户快照、行情读取和下单逻辑。Cloud Run 直接部署这个仓库。
LongBridge 的账户身份按 `paper`、`HK`、`SG` 三个维度建模；当前线上运行的是 `paper` 和 `SG`，`HK` 以后按同样模式补齐。
`LongBridgePlatform` 现在可直接执行 `UsEquityStrategies` 里的 7 条 `runtime_enabled` `us_equity` 策略：`global_etf_confidence_vol_gate`、`global_etf_rotation`、`mega_cap_leader_rotation_top50_balanced`、`russell_1000_multi_factor_defensive`、`soxl_soxx_trend_income`、`tqqq_growth_income` 和 `tech_communication_pullback_enhancement`。较弱或重复的研究 profile 已从 LongBridge 可配置入口移除。仓库本身继续保留 LongPort 运行时、token 刷新、执行和通知流程。

完整策略说明现在放在 [`UsEquityStrategies`](https://github.com/QuantStrategyLab/UsEquityStrategies)。下面这些章节只保留 LongBridge 运行时、profile 启用状态、部署和凭据说明。

### 执行边界

当前主线运行路径已经统一为：

- `main.py` 负责组装 `StrategyContext` 和平台 override
- `strategy_runtime.py` 负责加载统一策略入口
- `entrypoint.evaluate(ctx)` 返回共享的 `StrategyDecision`
- `decision_mapper.py` 再把决策转换成 LongBridge 订单和通知计划

平台执行主线已经不再依赖 `strategy/allocation.py`，也不再在运行时主流程里硬编码策略资产列表。

**LongBridge profile status**

| Canonical profile | Display name | Eligible | Enabled | Domain | Runtime note |
| --- | --- | --- | --- | --- | --- |
| `global_etf_confidence_vol_gate` | Global ETF Confidence Vol Gate | Yes | Yes | `us_equity` | 已启用的置信度/波动门控 ETF 轮动线 |
| `global_etf_rotation` | Global ETF Rotation | Yes | Yes | `us_equity` | 已启用的 weight-mode 轮动线 |
| `russell_1000_multi_factor_defensive` | Russell 1000 Multi-Factor | Yes | Yes | `us_equity` | 已启用的 feature-snapshot 个股基线 |
| `mega_cap_leader_rotation_top50_balanced` | Mega Cap Leader Rotation Top50 Balanced | Yes | Yes | `us_equity` | 可选的 Top50 平衡月度龙头轮动线 |
| `soxl_soxx_trend_income` | SOXL/SOXX 半导体趋势收益 | Yes | Yes | `us_equity` | 当前 SG 部署线路 |
| `tqqq_growth_income` | TQQQ 增长收益 | Yes | Yes | `us_equity` | 可选增长线路 |
| `tech_communication_pullback_enhancement` | 科技通信回调增强 | Yes | Yes | `us_equity` | 当前 paper feature-snapshot 线路 |

本地可直接查看当前矩阵：

```bash
python3 scripts/print_strategy_profile_status.py
```

### 策略文档边界

策略逻辑、策略频率、标的池、参数和研究/回测说明都放在 `UsEquityStrategies`。这个平台 README 只保留 LongBridge profile 启用状态、环境变量、部署 wiring、券商执行行为和通知通道说明。

### 通知格式

Telegram 通知包含结构化的调仓和心跳消息，支持中英文切换。策略相关的信号/状态字段来自当前选择的 `UsEquityStrategies` profile；LongBridge 侧只负责下单、成交/拒单/异常、账户前缀和区域字段。

### 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TELEGRAM_TOKEN` | 是 | Telegram 机器人 Token；建议通过 Secret Manager 的 `longbridge-telegram-token` 注入 |
| `GLOBAL_TELEGRAM_CHAT_ID` | 是 | 这个服务使用的 Telegram Chat ID。 |
| `LONGPORT_APP_KEY` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token）；建议从当前部署对应区域的 Secret Manager 密钥注入，例如 `longport-app-key-paper` / `longport-app-key-hk` / `longport-app-key-sg` |
| `LONGPORT_APP_SECRET` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token）；建议从当前部署对应区域的 Secret Manager 密钥注入，例如 `longport-app-secret-paper` / `longport-app-secret-hk` / `longport-app-secret-sg` |
| `LONGPORT_SECRET_NAME` | 否 | Secret Manager 中的密钥名称（默认: `longport_token_paper`） |
| `ACCOUNT_PREFIX` | 否 | 通知/日志前缀，区分账户环境（默认: `DEFAULT`） |
| `STRATEGY_PROFILE` | 是 | 策略档位选择。每个部署都要显式设置；已启用值包括 `global_etf_confidence_vol_gate`、`global_etf_rotation`、`mega_cap_leader_rotation_top50_balanced`、`russell_1000_multi_factor_defensive`、`soxl_soxx_trend_income`、`tech_communication_pullback_enhancement` 和 `tqqq_growth_income` |
| `ACCOUNT_REGION` | 否 | 平台化部署时的账户区域标记（如 `PAPER`、`HK`、`SG`；默认按 `ACCOUNT_PREFIX` / `DEFAULT` 推断） |
| `LONGBRIDGE_DRY_RUN_ONLY` | 否 | 设为 `true` 时，该部署保持 dry-run。 |
| `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT` | 否 | 设为 `true` 时输出 LongBridge 原始持仓数量和可卖数量，便于排查。 |
| `LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON` | 否 | 可选的 LongBridge 侧策略插件挂载 JSON。插件 artifact 自带模式；平台配置不要设置 `mode`。 |
| `INCOME_THRESHOLD_USD` | 否 | 可选的 `tqqq_growth_income` 收入层启动阈值覆盖（策略 override）。不填时使用策略包默认值。 |
| `QQQI_INCOME_RATIO` | 否 | 可选的 QQQI 收入层占比覆盖，0–1（策略 override）。 |
| `NOTIFY_LANG` | 否 | 通知语言: `en`（英文，默认）或 `zh`（中文） |
| `GOOGLE_CLOUD_PROJECT` | 否 | GCP 项目 ID（未设置时使用 ADC 默认项目） |

策略分配层仍然可以按目标金额和目标比例计算出小数仓位；LongBridge 执行层只提交整数股订单，因为实测账户的 OpenAPI `submit_order` 会拒绝碎股委托数量。目标市值为 0 时，卖出数量直接按可卖整数股持仓计算，不再用当前报价反推股数，避免因报价漂移留下 1 股残仓。

Secret Manager 中需存在 `LONGPORT_SECRET_NAME` 指定的密钥（默认: `longport_token_paper`），**最新版本 = 当前有效的 access token**。Token 到期前 30 天会自动刷新。

建议在 `longbridgequant` 项目里维护这些运行时 secret：

- `longbridge-telegram-token`
- `longport-app-key-paper`
- `longport-app-key-hk`
- `longport-app-key-sg`
- `longport-app-secret-paper`
- `longport-app-secret-hk`
- `longport-app-secret-sg`
- `longport_token_paper`
- `longport_token_hk`
- `longport_token_sg`

### 多部署隔离（paper/HK/SG）

同一代码库可部署为多个 Cloud Run 服务（如 `paper` 和 `SG`，`HK` 以后按同样模式补齐），通过以下变量区分：

- `LONGPORT_SECRET_NAME`: 指向不同密钥（如 `longport_token_paper`、`longport_token_hk`、`longport_token_sg`）
- `ACCOUNT_PREFIX`: 如 `PAPER`、`HK`、`SG`（所有通知/日志将包含 `[ACCOUNT_PREFIX]`）
- `STRATEGY_PROFILE`: 按服务分别设置；当前线上 paper 用 `mega_cap_leader_rotation_top50_balanced`，SG 用 `soxl_soxx_trend_income`，HK 以后按同样模式补齐。控制面会另外携带 `RUNTIME_TARGET_JSON`，`STRATEGY_PROFILE` 继续只作为兼容选择器。
- 当前策略域是 `us_equity`。`STRATEGY_PROFILE` 现在会先经过平台能力矩阵，再经过从 `runtime_enabled` 策略元数据派生的 rollout allowlist：`eligible` 表示平台理论可跑，`enabled` 表示当前 rollout 真正放开。
- `ACCOUNT_REGION`: 显式标记部署账户区域（`PAPER` / `HK` / `SG`）；未设置时会回退到 `ACCOUNT_PREFIX` 或 `DEFAULT`
- `LONGBRIDGE_DRY_RUN_ONLY`: 需要保持模拟运行时按服务单独设置
- `NOTIFY_LANG`: 每个部署可独立设置 `en` 或 `zh`

### GitHub 统一管理 paper / HK / SG 环境变量

如果代码部署继续走 Google Cloud Trigger，但你想把运行时环境变量统一放在 GitHub 管理，这个仓库已经提供 `.github/workflows/sync-cloud-run-env.yml`。

推荐配置方式：

- **仓库级 Variables（共享）：**
  - `ENABLE_GITHUB_ENV_SYNC` = `true`
  - `TELEGRAM_TOKEN_SECRET_NAME`（建议：`longbridge-telegram-token`）
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **仓库级 Secrets（共享）：**
  - 仅保留为 fallback：`TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-paper`**
  - Variables: `CLOUD_RUN_REGION`、`CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`ACCOUNT_REGION`、`RUNTIME_TARGET_JSON`、`STRATEGY_PROFILE`、`LONGPORT_SECRET_NAME`、`LONGPORT_APP_KEY_SECRET_NAME`、`LONGPORT_APP_SECRET_SECRET_NAME`
  - 可选 Variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`、`LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`、`LONGBRIDGE_STRATEGY_CONFIG_PATH`、`LONGBRIDGE_DRY_RUN_ONLY`、`LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`、`INCOME_THRESHOLD_USD`、`QQQI_INCOME_RATIO`（仅策略 override；不填则继承 `UsEquityStrategies`）
  - 当前线上示例：`STRATEGY_PROFILE=mega_cap_leader_rotation_top50_balanced`
  - 建议的 secret-name 值：`longport-app-key-paper`、`longport-app-secret-paper`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_REGION`、`CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`ACCOUNT_REGION`、`RUNTIME_TARGET_JSON`、`STRATEGY_PROFILE`、`LONGPORT_SECRET_NAME`、`LONGPORT_APP_KEY_SECRET_NAME`、`LONGPORT_APP_SECRET_SECRET_NAME`
  - 可选 Variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`、`LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`、`LONGBRIDGE_STRATEGY_CONFIG_PATH`、`LONGBRIDGE_DRY_RUN_ONLY`、`LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`、`INCOME_THRESHOLD_USD`、`QQQI_INCOME_RATIO`（仅策略 override；不填则继承 `UsEquityStrategies`）
  - 当前线上示例：`STRATEGY_PROFILE=soxl_soxx_trend_income`
  - 建议的 secret-name 值：`longport-app-key-sg`、`longport-app-secret-sg`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_REGION`、`CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`ACCOUNT_REGION`、`RUNTIME_TARGET_JSON`、`STRATEGY_PROFILE`、`LONGPORT_SECRET_NAME`、`LONGPORT_APP_KEY_SECRET_NAME`、`LONGPORT_APP_SECRET_SECRET_NAME`
  - 可选 Variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`、`LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`、`LONGBRIDGE_STRATEGY_CONFIG_PATH`、`LONGBRIDGE_DRY_RUN_ONLY`、`LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`、`INCOME_THRESHOLD_USD`、`QQQI_INCOME_RATIO`（仅策略 override；不填则继承 `UsEquityStrategies`）
  - 当前线上示例：`STRATEGY_PROFILE=tech_communication_pullback_enhancement`
  - 建议的 secret-name 值：`longport-app-key-hk`、`longport-app-secret-hk`

每次 push 到 `main` 时，这个 workflow 会更新配置的 Cloud Run 服务，把共享和各自隔离的变量同步进去，并删除旧的 `TELEGRAM_CHAT_ID`。

注意：

- `CLOUD_RUN_REGION` 应该分别放在 `longbridge-paper`、`longbridge-hk` 和 `longbridge-sg` 这些 Environment 里，不要再当成一个仓库级共享变量。这样 paper、HK 和 SG 才能各自更新到自己的 region。
- `LONGPORT_APP_KEY_SECRET_NAME` 和 `LONGPORT_APP_SECRET_SECRET_NAME` 也应该分别放在各自的 GitHub Environment 里。既然 paper、HK 和 SG 用的是不同 LongPort 凭据，就不要再给它们保留一个仓库级默认值。
- 现在 workflow 只有在 `ENABLE_GITHUB_ENV_SYNC=true` 时才会严格检查配置。没打开这个开关时，它会直接跳过，不影响原来只靠 Google Cloud Trigger 的老流程；一旦打开，缺任何配置都会直接失败，避免你以为已经同步成功。目标策略需要的 snapshot/config 输入会通过 `scripts/print_strategy_profile_status.py --json` 动态解析，不再维护硬编码策略名列表。
- GitHub 现在通过 OIDC + Workload Identity Federation 登录 Google Cloud，这个 workflow 不再需要 `GCP_SA_KEY`。
- 如果你用 `gcloud run deploy --source` 或 Cloud Run source trigger 部署，还要给 `gs://run-sources-<project>-<region>` 这个 staging bucket 补 `roles/storage.objectViewer`，对象是 build service account、deploy service account、默认 compute service account。少了这层权限，部署会在 Cloud Build 启动前直接报 `storage.objects.get denied`。
- 这里的“共享”只是指 **同一个仓库里的 paper / HK / SG 服务共享**。Telegram token 可以继续共用，但 LongPort app 凭据建议放到 Secret Manager，并通过各自 Environment 里的 secret-name 变量引用，不建议把它们当成所有 quant 共用的全局 secrets。
- 如果你真的要在多个 quant 仓库之间保留一层全局共享，建议只保留 `GLOBAL_TELEGRAM_CHAT_ID` 和 `NOTIFY_LANG` 这种低耦合配置。

### 部署单元和命名建议

- `QuantPlatformKit` 只是共享依赖，不单独部署；Cloud Run 继续只部署 `LongBridgePlatform`。
- 推荐 Cloud Run 服务名：`longbridge-quant-paper-service`、`longbridge-quant-hk-service` 和 `longbridge-quant-sg-service`。
- 现在使用三个 trigger 和三个 GitHub Environment：`longbridge-paper`、`longbridge-hk`、`longbridge-sg`。区分键始终是 `CLOUD_RUN_SERVICE + CLOUD_RUN_REGION`，运行身份通过 `RUNTIME_TARGET_JSON` 明确，`STRATEGY_PROFILE + ACCOUNT_REGION` 保留为兼容输入。
- 如果后面改 GitHub 仓库名或再次迁组织，Google Cloud 里的所有 Cloud Build trigger 都要重新选择 GitHub 来源，不要假设旧绑定会自动跟过去。
- 统一部署模型和触发器迁移清单见 [`QuantPlatformKit/docs/deployment_model.md`](../QuantPlatformKit/docs/deployment_model.md)。

### 快速部署

1. 在 GCP 中启用 **Cloud Run** 和 **Secret Manager API**。
2. 在 Secret Manager 中为 paper 创建 `longport_token_paper`、为 HK 创建 `longport_token_hk`、为 SG 创建 `longport_token_sg`（或使用你自定义的 `LONGPORT_SECRET_NAME`），并将 LongPort access token 作为第一个版本写入。
3. 在 Cloud Run 服务上配置上述环境变量。
4. 部署至 Cloud Run（如从仓库根目录执行 `gcloud run deploy`）。
5. 创建两个 Cloud Scheduler 定时任务，POST 到 Cloud Run URL。开盘后窗口走 `"/precheck"`，临近收盘窗口走 `"/"`。cron 频率以 `UsEquityStrategies` 里的策略层 cadence 为准；这个平台仓只维护运行时触发 wiring。

IAM: Cloud Run 服务账号需要 **Secret Manager Admin**（或当前 `LONGPORT_SECRET_NAME`、`LONGPORT_APP_KEY_SECRET_NAME`、`LONGPORT_APP_SECRET_SECRET_NAME` 对应 secret 的 Secret Accessor，例如 `longport_token_paper`、`longport-app-key-paper`、`longport-app-secret-paper`）和 **Logs Writer** 权限。
