# LongPort Semiconductor Rotation & Income

[English](#english) | [中文](#中文)

---

<a id="english"></a>
## English

Quant system on LongPort OpenAPI and Google Cloud Run.

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

Beautiful emoji-formatted Telegram notifications with full i18n support.

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
| `TELEGRAM_TOKEN` | Yes | Bot token for alerts |
| `TELEGRAM_CHAT_ID` | Conditional | Per-service chat or user ID for alerts. Falls back to `GLOBAL_TELEGRAM_CHAT_ID` if unset. |
| `GLOBAL_TELEGRAM_CHAT_ID` | No | Optional shared Telegram chat ID for teams that route multiple quant services to the same destination. |
| `LONGPORT_APP_KEY` | Yes | LongPort OpenAPI app key (for token refresh) |
| `LONGPORT_APP_SECRET` | Yes | LongPort OpenAPI app secret (for token refresh) |
| `LONGPORT_SECRET_NAME` | No | Secret Manager secret name for LongPort token (default: `longport_token`) |
| `ACCOUNT_PREFIX` | No | Alert/log prefix for account/environment (default: `DEFAULT`) |
| `SERVICE_NAME` | No | Alert/log prefix for service identity (default: `longbridge-quant`) |
| `NOTIFY_LANG` | No | Notification language: `en` (English, default) or `zh` (Chinese) |
| `GOOGLE_CLOUD_PROJECT` | No | GCP project ID (defaults to ADC project when unset) |

Secret Manager must contain the secret named by `LONGPORT_SECRET_NAME` (default: `longport_token`), where the **latest version = active access token**. The app refreshes it when expiry is within 30 days.

### Multi-deployment isolation (HK/SG, etc.)

Deploy the same codebase as multiple Cloud Run services (e.g. `HK` and `SG`) by setting different values per service:

- `LONGPORT_SECRET_NAME`: point to different secrets (e.g. `longport_token_hk`, `longport_token_sg`)
- `ACCOUNT_PREFIX`: e.g. `HK`, `SG` (all Telegram/log alerts will include `[ACCOUNT_PREFIX/SERVICE_NAME]`)
- `SERVICE_NAME`: e.g. `longbridge-quant-hk`, `longbridge-quant-sg`
- `NOTIFY_LANG`: set `en` or `zh` per deployment

### GitHub-managed env sync for HK / SG

If code deployment still uses Google Cloud Trigger, but you want GitHub to be the single source of truth for runtime env vars, this repo includes `.github/workflows/sync-cloud-run-env.yml`.

Recommended setup:

- **Repository Variables (shared):**
  - `CLOUD_RUN_REGION`
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **Repository Secrets (shared):**
  - `GCP_SA_KEY`
  - `TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `SERVICE_NAME`, `LONGPORT_SECRET_NAME`
  - Secrets: `LONGPORT_APP_KEY`, `LONGPORT_APP_SECRET`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `SERVICE_NAME`, `LONGPORT_SECRET_NAME`
  - Secrets: `LONGPORT_APP_KEY`, `LONGPORT_APP_SECRET`

On every push to `main`, the workflow updates both Cloud Run services with the shared and per-environment values above. It does **not** remove legacy `TELEGRAM_CHAT_ID`, so existing deployments keep working. Once you have confirmed both services are reading `GLOBAL_TELEGRAM_CHAT_ID` as intended, you can remove `TELEGRAM_CHAT_ID` from each Cloud Run service manually.

### Quick deploy

1. Enable **Cloud Run** and **Secret Manager API** in GCP.
2. Create secret `longport_token` (or your custom `LONGPORT_SECRET_NAME`) in Secret Manager and add your LongPort access token as the first version.
3. Set the required env vars above on the Cloud Run service.
4. Deploy the app to Cloud Run (e.g. `gcloud run deploy` from repo root with Dockerfile or buildpack).
5. Create a Cloud Scheduler job that POSTs to the Cloud Run URL on a schedule (e.g. `45 15 * * 1-5` for ~15 min before US market close on weekdays).

IAM: the Cloud Run service account needs **Secret Manager Admin** (or Secret Accessor for `longport_token`) and **Logs Writer**. Build/deploy typically uses a separate account with Artifact Registry Writer, Cloud Run Admin, Service Account User.

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

精美的 Emoji 格式 Telegram 通知，支持中英文切换。

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
| `TELEGRAM_TOKEN` | 是 | Telegram 机器人 Token |
| `TELEGRAM_CHAT_ID` | 条件必需 | 当前服务自己的 Chat ID。不填时会回退到 `GLOBAL_TELEGRAM_CHAT_ID`。 |
| `GLOBAL_TELEGRAM_CHAT_ID` | 否 | 可选的共享 Telegram Chat ID。适合多个 quant 服务共用一个接收目标。 |
| `LONGPORT_APP_KEY` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token） |
| `LONGPORT_APP_SECRET` | 是 | LongPort OpenAPI 应用密钥（用于刷新 Token） |
| `LONGPORT_SECRET_NAME` | 否 | Secret Manager 中的密钥名称（默认: `longport_token`） |
| `ACCOUNT_PREFIX` | 否 | 通知/日志前缀，区分账户环境（默认: `DEFAULT`） |
| `SERVICE_NAME` | 否 | 通知/日志前缀，区分服务（默认: `longbridge-quant`） |
| `NOTIFY_LANG` | 否 | 通知语言: `en`（英文，默认）或 `zh`（中文） |
| `GOOGLE_CLOUD_PROJECT` | 否 | GCP 项目 ID（未设置时使用 ADC 默认项目） |

Secret Manager 中需存在 `LONGPORT_SECRET_NAME` 指定的密钥（默认: `longport_token`），**最新版本 = 当前有效的 access token**。Token 到期前 30 天会自动刷新。

### 多部署隔离（港区/新加坡等）

同一代码库可部署为多个 Cloud Run 服务（如 `HK` 和 `SG`），通过以下变量区分：

- `LONGPORT_SECRET_NAME`: 指向不同密钥（如 `longport_token_hk`、`longport_token_sg`）
- `ACCOUNT_PREFIX`: 如 `HK`、`SG`（所有通知/日志将包含 `[ACCOUNT_PREFIX/SERVICE_NAME]`）
- `SERVICE_NAME`: 如 `longbridge-quant-hk`、`longbridge-quant-sg`
- `NOTIFY_LANG`: 每个部署可独立设置 `en` 或 `zh`

### GitHub 统一管理 HK / SG 环境变量

如果代码部署继续走 Google Cloud Trigger，但你想把运行时环境变量统一放在 GitHub 管理，这个仓库已经提供 `.github/workflows/sync-cloud-run-env.yml`。

推荐配置方式：

- **仓库级 Variables（共享）：**
  - `CLOUD_RUN_REGION`
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **仓库级 Secrets（共享）：**
  - `GCP_SA_KEY`
  - `TELEGRAM_TOKEN`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`SERVICE_NAME`、`LONGPORT_SECRET_NAME`
  - Secrets: `LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `CLOUD_RUN_SERVICE`、`ACCOUNT_PREFIX`、`SERVICE_NAME`、`LONGPORT_SECRET_NAME`
  - Secrets: `LONGPORT_APP_KEY`、`LONGPORT_APP_SECRET`

每次 push 到 `main` 时，这个 workflow 会分别更新两个 Cloud Run 服务，把共享和各自隔离的变量同步进去。它**不会主动删除**旧的 `TELEGRAM_CHAT_ID`，这样现有部署不会被硬切断。等你确认两个服务都已经按预期读取 `GLOBAL_TELEGRAM_CHAT_ID` 后，再手动把各自 Cloud Run 上旧的 `TELEGRAM_CHAT_ID` 删掉即可。

### 快速部署

1. 在 GCP 中启用 **Cloud Run** 和 **Secret Manager API**。
2. 在 Secret Manager 中创建密钥 `longport_token`（或自定义名称），将 LongPort access token 作为第一个版本写入。
3. 在 Cloud Run 服务上配置上述环境变量。
4. 部署至 Cloud Run（如从仓库根目录执行 `gcloud run deploy`）。
5. 创建 Cloud Scheduler 定时任务，POST 到 Cloud Run URL（如 `45 15 * * 1-5`，工作日美股收盘前约 15 分钟）。

IAM: Cloud Run 服务账号需要 **Secret Manager Admin**（或 `longport_token` 的 Secret Accessor）和 **Logs Writer** 权限。

### 策略参数 (main.py)

- `TREND_MA_WINDOW`
- `SMALL_ACCOUNT_DEPLOY_RATIO` / `MID_ACCOUNT_DEPLOY_RATIO` / `LARGE_ACCOUNT_DEPLOY_RATIO`
- `TRADE_LAYER_DECAY_COEFF`
- `INCOME_LAYER_START_USD` / `INCOME_LAYER_MAX_RATIO`
- `INCOME_LAYER_QQQI_WEIGHT` / `INCOME_LAYER_SPYI_WEIGHT`
