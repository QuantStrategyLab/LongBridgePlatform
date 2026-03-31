# LongPort Semiconductor Rotation & Income

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Quant system on LongPort OpenAPI and Google Cloud Run.

This repository uses `QuantPlatformKit` for LongPort token handling, context bootstrap, account snapshot access, market data, and order submission. Cloud Run deploys this repository directly.
The `semiconductor_rotation_income` allocation logic is loaded from `UsEquityStrategies`. `LongBridgePlatform` keeps the LongPort runtime, token refresh, execution, and notification flow.

Full strategy documentation now lives in [`UsEquityStrategies`](https://github.com/QuantStrategyLab/UsEquityStrategies#semiconductor_rotation_income). The sections below focus on execution-side defaults and runtime behavior.

**Layers**

- **Trading:** SOXL / SOXX / BOXX
- **Income:** QQQI / SPYI

### Strategy

**Trading layer**

- SOXL 150-day MA for trend.
- SOXL > MA150 → hold SOXL; SOXL ≤ MA150 → hold SOXX.
- Rest of trading capital in BOXX.

**Income layer**

- Starts when total equity ≥ 150,000 USD.
- Target income allocation cap 15%.
- New income allocation: QQQI 70% / SPYI 30%.
- Income positions are buy-only (no automatic reduction).

**Execution**

- Only SOXL, SOXX, BOXX, QQQI, SPYI are used.
- Cash from account USD `available_cash`; no margin.
- `estimate_max_purchase_quantity` used before buys.
- Telegram alerts for submit, fill, reject, and errors.

### Notifications

Telegram notifications include structured execution and heartbeat messages, with English and Chinese variants.

**Trade execution:**
```
🔔 【Trade Execution Report】
📊 Market: 🚀 RISK-ON (SOXL)
💼 Risk Position: 57.8%
💰 Income Target: 0.0%
🏦 Income Locked: 38.8%
🎯 Signal: SOXL above 150d MA, hold SOXL, risk 57.8%
━━━━━━━━━━━━━━━━━━
  📈 [Market buy] BOXX: 190 shares @ $115.99 [order_id=xxx]
```

**Heartbeat (no trades):**
```
💓 【Heartbeat】
📊 Market: 🚀 RISK-ON (SOXL)
💰 Equity: $150,000.00
━━━━━━━━━━━━━━━━━━
SOXL: $85,000.00  SOXX: $0.00
QQQI: $15,000.00  SPYI: $6,000.00
BOXX: $34,000.00  Cash: $10,000.00
━━━━━━━━━━━━━━━━━━
💼 Risk Position: 57.0%
💰 Income Target: 5.0%
🏦 Income Locked: 14.0%
🎯 Signal: SOXL above 150d MA, hold SOXL, risk 57.0%
━━━━━━━━━━━━━━━━━━
✅ No trades needed
```

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes | Bot token for alerts; recommended to inject from Secret Manager secret `longbridge-telegram-token` |
| `GLOBAL_TELEGRAM_CHAT_ID` | Yes | Telegram chat or user ID used by this service. |
| `LONGPORT_APP_KEY` | Yes | LongPort OpenAPI app key (for token refresh); recommended to inject from Secret Manager secret `longport-app-key` |
| `LONGPORT_APP_SECRET` | Yes | LongPort OpenAPI app secret (for token refresh); recommended to inject from Secret Manager secret `longport-app-secret` |
| `LONGPORT_SECRET_NAME` | No | Secret Manager secret name for LongPort token (default: `longport_token_hk`) |
| `ACCOUNT_PREFIX` | No | Alert/log prefix for account/environment (default: `DEFAULT`) |
| `SERVICE_NAME` | No | Alert/log prefix for service identity (default: `longbridge-quant-semiconductor-rotation-income`) |
| `STRATEGY_PROFILE` | No | Strategy profile selector (default: `semiconductor_rotation_income`; supported value: `semiconductor_rotation_income`) |
| `ACCOUNT_REGION` | No | Account region marker for platform-style deployment (e.g. `HK`, `SG`; defaults to `ACCOUNT_PREFIX` / service-name suffix / `DEFAULT`) |
| `NOTIFY_LANG` | No | Notification language: `en` (English, default) or `zh` (Chinese) |
| `GOOGLE_CLOUD_PROJECT` | No | GCP project ID (defaults to ADC project when unset) |

Secret Manager must contain the secret named by `LONGPORT_SECRET_NAME` (default: `longport_token_hk`), where the **latest version = active access token**. The app refreshes it when expiry is within 30 days.

Recommended shared runtime secrets in the `longbridgequant` project:

- `longbridge-telegram-token`
- `longport-app-key`
- `longport-app-secret`
- `longport_token_hk`
- `longport_token_sg`

### Multi-deployment isolation (HK/SG, etc.)

Deploy the same codebase as multiple Cloud Run services (e.g. `HK` and `SG`) by setting different values per service:

- `LONGPORT_SECRET_NAME`: point to different secrets (e.g. `longport_token_hk`, `longport_token_sg`)
- `ACCOUNT_PREFIX`: e.g. `HK`, `SG` (all Telegram/log alerts will include `[ACCOUNT_PREFIX/SERVICE_NAME]`)
- `SERVICE_NAME`: e.g. `longbridge-quant-semiconductor-rotation-income-hk`, `longbridge-quant-semiconductor-rotation-income-sg`
- `STRATEGY_PROFILE`: use `semiconductor_rotation_income` for the current LongBridge strategy profile
- Current strategy domain is `us_equity`. The repo now keeps a small strategy registry so future strategy switching can grow by domain + profile, instead of mixing platform and strategy in one layer.
- `ACCOUNT_REGION`: explicitly mark the deployed account region (`HK` / `SG`); if unset, the app falls back to `ACCOUNT_PREFIX` or the `-hk` / `-sg` service-name suffix
- `NOTIFY_LANG`: set `en` or `zh` per deployment

### GitHub-managed env sync for HK / SG

If code deployment still uses Google Cloud Trigger, but you want GitHub to be the single source of truth for runtime env vars, this repo includes `.github/workflows/sync-cloud-run-env.yml`.

Recommended setup:

- **Repository Variables (shared):**
  - `ENABLE_GITHUB_ENV_SYNC` = `true`
  - `TELEGRAM_TOKEN_SECRET_NAME` (recommended: `longbridge-telegram-token`)
  - `LONGPORT_APP_KEY_SECRET_NAME` (recommended: `longport-app-key`)
  - `LONGPORT_APP_SECRET_SECRET_NAME` (recommended: `longport-app-secret`)
  - `STRATEGY_PROFILE` (recommended: `semiconductor_rotation_income`)
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **Repository Secrets (shared):**
  - Optional fallback only: `TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `SERVICE_NAME`, `ACCOUNT_REGION`, `LONGPORT_SECRET_NAME`
  - Secrets: optional fallback only `LONGPORT_APP_KEY`, `LONGPORT_APP_SECRET`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `SERVICE_NAME`, `ACCOUNT_REGION`, `LONGPORT_SECRET_NAME`
  - Secrets: optional fallback only `LONGPORT_APP_KEY`, `LONGPORT_APP_SECRET`

On every push to `main`, the workflow updates both Cloud Run services with the shared and per-environment values above, and removes `TELEGRAM_CHAT_ID` from each Cloud Run service.

Important:

- `CLOUD_RUN_REGION` should be set on each GitHub Environment, not as one shared repository variable. This lets `HK` and `SG` live in different Cloud Run regions.
- The workflow only becomes strict when `ENABLE_GITHUB_ENV_SYNC=true`. If this variable is unset, the sync job is skipped and the old Google Cloud Trigger-only setup keeps working. Once you set it to `true`, missing env-sync values become a hard failure so you do not get a false green deployment.
- GitHub now authenticates to Google Cloud with OIDC + Workload Identity Federation, so `GCP_SA_KEY` is no longer required for this workflow.
- Here "shared" only means **shared inside this repository** between the `HK` and `SG` Cloud Run services. The Telegram token and LongPort app credentials should live in Secret Manager and be referenced by the shared secret-name variables above; they are not meant to be a global secret set reused by unrelated quant repos.
- If you want one cross-project shared layer across multiple quant repos, keep it small: `GLOBAL_TELEGRAM_CHAT_ID` and `NOTIFY_LANG` are reasonable; account credentials and deployment keys are not.

### Deployment unit and naming

- `QuantPlatformKit` is only a shared dependency; Cloud Run still deploys `LongBridgePlatform` itself.
- Recommended Cloud Run service names: `longbridge-quant-semiconductor-rotation-income-hk-service` and `longbridge-quant-semiconductor-rotation-income-sg-service`.
- Keep using two triggers and two GitHub Environments. The split key is still `CLOUD_RUN_SERVICE + CLOUD_RUN_REGION`, and the runtime identity is now explicit through `STRATEGY_PROFILE + ACCOUNT_REGION`.
- If you later rename or move this repository, rebuild the GitHub source binding in Google Cloud for both triggers instead of assuming the existing source binding will follow the rename.
- For the shared deployment model and trigger migration checklist, see [`QuantPlatformKit/docs/deployment_model.md`](../QuantPlatformKit/docs/deployment_model.md).

### Quick deploy

1. Enable **Cloud Run** and **Secret Manager API** in GCP.
2. Create secret `longport_token_hk` for HK / `longport_token_sg` for SG (or your custom `LONGPORT_SECRET_NAME`) in Secret Manager and add your LongPort access token as the first version.
3. Set the required env vars above on the Cloud Run service.
4. Deploy the app to Cloud Run (e.g. `gcloud run deploy` from repo root with Dockerfile or buildpack).
5. Create a Cloud Scheduler job that POSTs to the Cloud Run URL on a schedule (e.g. `45 15 * * 1-5` for ~15 min before US market close on weekdays).

IAM: the Cloud Run service account needs **Secret Manager Admin** (or Secret Accessor for the configured `LONGPORT_SECRET_NAME`, such as `longport_token_hk` / `longport_token_sg`) and **Logs Writer**. Build/deploy typically uses a separate account with Artifact Registry Writer, Cloud Run Admin, Service Account User.

### Parameters (main.py)

- `TREND_MA_WINDOW`
- `SMALL_ACCOUNT_DEPLOY_RATIO` / `MID_ACCOUNT_DEPLOY_RATIO` / `LARGE_ACCOUNT_DEPLOY_RATIO`
- `TRADE_LAYER_DECAY_COEFF`
- `INCOME_LAYER_START_USD` / `INCOME_LAYER_MAX_RATIO`
- `INCOME_LAYER_QQQI_WEIGHT` / `INCOME_LAYER_SPYI_WEIGHT`

---

<a id="中文"></a>
## 中文

基于 LongPort OpenAPI 和 Google Cloud Run 的量化交易系统。

这个仓库通过 `QuantPlatformKit` 复用 LongPort token 处理、上下文初始化、账户快照、行情读取和下单逻辑。Cloud Run 直接部署这个仓库。
`semiconductor_rotation_income` 的仓位与调仓计算逻辑从 `UsEquityStrategies` 加载；`LongBridgePlatform` 继续保留 LongPort 运行时、token 刷新、执行和通知流程。

完整策略说明现在放在 [`UsEquityStrategies`](https://github.com/QuantStrategyLab/UsEquityStrategies#semiconductor_rotation_income)。下面这些章节主要保留执行侧默认值和运行时行为。

**层级**

- **交易层:** SOXL / SOXX / BOXX
- **收入层:** QQQI / SPYI

### 策略

**交易层**

- 使用 SOXL 150 日均线判断趋势。
- SOXL > MA150 → 持有 SOXL；SOXL ≤ MA150 → 切换至 SOXX。
- 交易层剩余资金放入 BOXX。

**收入层**

- 当总资产 ≥ 150,000 USD 时启动。
- 收入层目标配比上限 15%。
- 新增收入配置：QQQI 70% / SPYI 30%。
- 收入层仅买入，不自动减仓。

**执行**

- 仅使用 SOXL、SOXX、BOXX、QQQI、SPYI。
- 使用账户 USD 可用现金，不使用保证金。
- 买入前调用 `estimate_max_purchase_quantity` 校验。
- 通过 Telegram 推送下单、成交、拒绝及异常通知。

### 通知格式

Telegram 通知包含结构化的调仓和心跳消息，支持中英文切换。

**调仓通知:**
```
🔔 【调仓指令】
📊 市场状态: 🚀 RISK-ON (SOXL)
💼 交易层风险仓位: 57.8%
💰 收入层目标: 0.0%
🏦 收入层锁定占比: 38.8%
🎯 触发信号: SOXL 站上 150 日均线，持有 SOXL，交易层风险仓位 57.8%
━━━━━━━━━━━━━━━━━━
  📈 [市价买入] BOXX: 190股 @ $115.99 [order_id=xxx]
```

**心跳通知 (无需调仓):**
```
💓 【心跳检测】
📊 市场状态: 🚀 RISK-ON (SOXL)
💰 净值: $150,000.00
━━━━━━━━━━━━━━━━━━
SOXL: $85,000.00  SOXX: $0.00
QQQI: $15,000.00  SPYI: $6,000.00
BOXX: $34,000.00  现金: $10,000.00
━━━━━━━━━━━━━━━━━━
💼 交易层风险仓位: 57.0%
💰 收入层目标: 5.0%
🏦 收入层锁定占比: 14.0%
🎯 信号: SOXL 站上 150 日均线，持有 SOXL，交易层风险仓位 57.0%
━━━━━━━━━━━━━━━━━━
✅ 无需调仓
```

### 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TELEGRAM_TOKEN` | 是 | Telegram 机器人 Token；建议通过 Secret Manager 的 `longbridge-telegram-token` 注入 |
| `GLOBAL_TELEGRAM_CHAT_ID` | 是 | 这个服务使用的 Telegram Chat ID。 |
| `LONGPORT_APP_KEY` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token）；建议通过 Secret Manager 的 `longport-app-key` 注入 |
| `LONGPORT_APP_SECRET` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token）；建议通过 Secret Manager 的 `longport-app-secret` 注入 |
| `LONGPORT_SECRET_NAME` | 否 | Secret Manager 中的密钥名称（默认: `longport_token_hk`） |
| `ACCOUNT_PREFIX` | 否 | 通知/日志前缀，区分账户环境（默认: `DEFAULT`） |
| `SERVICE_NAME` | 否 | 通知/日志前缀，区分服务（默认: `longbridge-quant-semiconductor-rotation-income`） |
| `STRATEGY_PROFILE` | 否 | 策略档位选择（默认: `semiconductor_rotation_income`；当前支持值: `semiconductor_rotation_income`） |
| `ACCOUNT_REGION` | 否 | 平台化部署时的账户区域标记（如 `HK`、`SG`；默认按 `ACCOUNT_PREFIX` / 服务名后缀 / `DEFAULT` 推断） |
| `NOTIFY_LANG` | 否 | 通知语言: `en`（英文，默认）或 `zh`（中文） |
| `GOOGLE_CLOUD_PROJECT` | 否 | GCP 项目 ID（未设置时使用 ADC 默认项目） |

Secret Manager 中需存在 `LONGPORT_SECRET_NAME` 指定的密钥（默认: `longport_token_hk`），**最新版本 = 当前有效的 access token**。Token 到期前 30 天会自动刷新。

建议在 `longbridgequant` 项目里统一维护这些运行时 secret：

- `longbridge-telegram-token`
- `longport-app-key`
- `longport-app-secret`
- `longport_token_hk`
- `longport_token_sg`

### 多部署隔离（港区/新加坡等）

同一代码库可部署为多个 Cloud Run 服务（如 `HK` 和 `SG`），通过以下变量区分：

- `LONGPORT_SECRET_NAME`: 指向不同密钥（如 `longport_token_hk`、`longport_token_sg`）
- `ACCOUNT_PREFIX`: 如 `HK`、`SG`（所有通知/日志将包含 `[ACCOUNT_PREFIX/SERVICE_NAME]`）
- `SERVICE_NAME`: 如 `longbridge-quant-semiconductor-rotation-income-hk`、`longbridge-quant-semiconductor-rotation-income-sg`
- `STRATEGY_PROFILE`: 当前 LongBridge 策略档位使用 `semiconductor_rotation_income`
- 当前策略域是 `us_equity`。本地策略注册表只用于域和 profile 校验。
- `ACCOUNT_REGION`: 显式标记部署账户区域（`HK` / `SG`）；未设置时会回退到 `ACCOUNT_PREFIX` 或服务名里的 `-hk` / `-sg` 后缀
- `NOTIFY_LANG`: 每个部署可独立设置 `en` 或 `zh`

### GitHub 统一管理 HK / SG 环境变量

如果代码部署继续走 Google Cloud Trigger，但你想把运行时环境变量统一放在 GitHub 管理，这个仓库已经提供 `.github/workflows/sync-cloud-run-env.yml`。

推荐配置方式：

- **仓库级 Variables（共享）：**
  - `ENABLE_GITHUB_ENV_SYNC` = `true`
  - `TELEGRAM_TOKEN_SECRET_NAME`（建议：`longbridge-telegram-token`）
  - `LONGPORT_APP_KEY_SECRET_NAME`（建议：`longport-app-key`）
  - `LONGPORT_APP_SECRET_SECRET_NAME`（建议：`longport-app-secret`）
  - `STRATEGY_PROFILE`（建议设为 `semiconductor_rotation_income`）
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **仓库级 Secrets（共享）：**
  - 仅保留为 fallback：`TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_REGION`、`CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`SERVICE_NAME`、`ACCOUNT_REGION`、`LONGPORT_SECRET_NAME`
  - Secrets: 仅保留为 fallback：`LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_REGION`、`CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`SERVICE_NAME`、`ACCOUNT_REGION`、`LONGPORT_SECRET_NAME`
  - Secrets: 仅保留为 fallback：`LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`

每次 push 到 `main` 时，这个 workflow 会分别更新两个 Cloud Run 服务，把共享和各自隔离的变量同步进去，并删除旧的 `TELEGRAM_CHAT_ID`。

注意：

- `CLOUD_RUN_REGION` 应该分别放在 `longbridge-hk` 和 `longbridge-sg` 这两个 Environment 里，不要再当成一个仓库级共享变量。这样 HK 和 SG 才能各自更新到自己的 region。
- 现在 workflow 只有在 `ENABLE_GITHUB_ENV_SYNC=true` 时才会严格检查配置。没打开这个开关时，它会直接跳过，不影响原来只靠 Google Cloud Trigger 的老流程；一旦打开，缺任何配置都会直接失败，避免你以为已经同步成功。
- GitHub 现在通过 OIDC + Workload Identity Federation 登录 Google Cloud，这个 workflow 不再需要 `GCP_SA_KEY`。
- 这里的“共享”只是指 **同一个仓库里的 HK / SG 两个服务共享**。Telegram token 和 LongPort app 凭据建议放到 Secret Manager，并通过上面的 shared secret-name 变量引用，不建议把它们当成所有 quant 共用的全局 secrets。
- 如果你真的要在多个 quant 仓库之间保留一层全局共享，建议只保留 `GLOBAL_TELEGRAM_CHAT_ID` 和 `NOTIFY_LANG` 这种低耦合配置。

### 部署单元和命名建议

- `QuantPlatformKit` 只是共享依赖，不单独部署；Cloud Run 继续只部署 `LongBridgePlatform`。
- 推荐 Cloud Run 服务名：`longbridge-quant-semiconductor-rotation-income-hk-service` 和 `longbridge-quant-semiconductor-rotation-income-sg-service`。
- 继续保留两个 trigger 和两个 GitHub Environment，区分键始终是 `CLOUD_RUN_SERVICE + CLOUD_RUN_REGION`，运行身份再通过 `STRATEGY_PROFILE + ACCOUNT_REGION` 明确下来。
- 如果后面改 GitHub 仓库名或再次迁组织，Google Cloud 里的两个 trigger 都要重新选择 GitHub 来源，不要假设旧绑定会自动跟过去。
- 统一部署模型和触发器迁移清单见 [`QuantPlatformKit/docs/deployment_model.md`](../QuantPlatformKit/docs/deployment_model.md)。

### 快速部署

1. 在 GCP 中启用 **Cloud Run** 和 **Secret Manager API**。
2. 在 Secret Manager 中为 HK 创建 `longport_token_hk`、为 SG 创建 `longport_token_sg`（或使用你自定义的 `LONGPORT_SECRET_NAME`），并将 LongPort access token 作为第一个版本写入。
3. 在 Cloud Run 服务上配置上述环境变量。
4. 部署至 Cloud Run（如从仓库根目录执行 `gcloud run deploy`）。
5. 创建 Cloud Scheduler 定时任务，POST 到 Cloud Run URL（如 `45 15 * * 1-5`，工作日美股收盘前约 15 分钟）。

IAM: Cloud Run 服务账号需要 **Secret Manager Admin**（或当前 `LONGPORT_SECRET_NAME` 对应 secret 的 Secret Accessor，例如 `longport_token_hk` / `longport_token_sg`）和 **Logs Writer** 权限。

### 策略参数 (main.py)

- `TREND_MA_WINDOW`
- `SMALL_ACCOUNT_DEPLOY_RATIO` / `MID_ACCOUNT_DEPLOY_RATIO` / `LARGE_ACCOUNT_DEPLOY_RATIO`
- `TRADE_LAYER_DECAY_COEFF`
- `INCOME_LAYER_START_USD` / `INCOME_LAYER_MAX_RATIO`
- `INCOME_LAYER_QQQI_WEIGHT` / `INCOME_LAYER_SPYI_WEIGHT`
