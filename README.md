# LongPort US Equity Strategy Runtime

> Risk warning: this project is not investment advice and is provided for study and engineering validation only.

Language: [English](README.md) | [中文](README.zh-CN.md)

---

Quant system on LongPort OpenAPI and Google Cloud Run.

This repository uses `QuantPlatformKit` for LongPort token handling, context bootstrap, account snapshot access, market data, and order submission. Cloud Run deploys this repository directly.
The runtime now carries a structured `RuntimeTarget` / `RUNTIME_TARGET_JSON` alongside the compatibility `STRATEGY_PROFILE` selector. Strategy-owned defaults come from `UsEquityStrategies` and `HkEquityStrategies`; platform variables are only explicit overrides.
The LongBridge runtime can execute the current `runtime_enabled` `us_equity` profiles from `UsEquityStrategies`. It also carries HK profiles from `HkEquityStrategies`: eligible-but-disabled `hk_blue_chip_leader_rotation`, `hk_index_mean_reversion`, `hk_etf_regime_rotation`, plus runtime-enabled `hk_listed_global_etf_rotation`; `LongBridgePlatform` keeps the LongPort runtime, token refresh, execution, and notification flow.
`STRATEGY_PROFILE` remains the compatibility selector for strategy routing, while `RuntimeTarget` describes the running service identity.

Strategy documentation lives in [`UsEquityStrategies`](https://github.com/QuantStrategyLab/UsEquityStrategies) and [`HkEquityStrategies`](https://github.com/QuantStrategyLab/HkEquityStrategies). Snapshot artifact contracts for the HK profile are produced by [`HkEquitySnapshotPipelines`](https://github.com/QuantStrategyLab/HkEquitySnapshotPipelines). HK snapshot artifacts can be generated from a public yfinance source and then consumed by LongBridge for dry-run/execution, keeping broker permissions separate from data refresh. The sections below focus on LongBridge runtime behavior, profile enablement, deployment, and credentials.
This runtime matrix is the authoritative enablement source for LongBridge. Strategy packages carry strategy-layer logic, cadence, compatibility, and metadata.

### Execution boundary

The mainline runtime now follows one path only:

- `main.py` assembles `StrategyContext` plus platform overrides
- `strategy_runtime.py` loads the unified strategy entrypoint
- `entrypoint.evaluate(ctx)` returns a shared `StrategyDecision`
- `decision_mapper.py` converts that decision into LongBridge order and notification plans

Platform execution no longer depends on `strategy/allocation.py` or hard-coded strategy asset lists in the runtime mainline.

### Execution safety

LongBridge executes shared `StrategyDecision` objects through a value-mode
runtime plan. Weight-target strategies are translated to value targets using the
account snapshot total equity. If a new or empty account reports non-positive
total equity, the mapper returns a value-mode `no_execute` plan with zero target
values instead of attempting order translation.


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
| `hk_blue_chip_leader_rotation` | HK Blue Chip Leader Rotation | Yes | No | `hk_equity` | architecture scaffold only; not runtime-enabled |
| `hk_index_mean_reversion` | HK Index Mean Reversion | Yes | No | `hk_equity` | market-history research candidate; not runtime-enabled |
| `hk_etf_regime_rotation` | HK ETF Regime Rotation | Yes | No | `hk_equity` | market-history research candidate; not runtime-enabled |
| `hk_listed_global_etf_rotation` | HK-listed Global ETF Rotation | Yes | Yes | `hk_equity` | runtime-enabled; selectable by HK Cloud Run runtime config |

Check the current matrix locally:

```bash
python3 scripts/print_strategy_profile_status.py
```

### Strategy documentation boundary

Strategy logic, cadence, asset universes, parameters, and research/backtest notes live in the strategy repositories (`UsEquityStrategies` / `HkEquityStrategies`). This platform README keeps only LongBridge profile enablement, env vars, deployment wiring, broker execution behavior, and notification transport.

For the HK-equity runtime scope, platform matrix, and env defaults, see [`docs/hk_equity_runtime.md`](docs/hk_equity_runtime.md).

For HK Cloud Run deployment or env review, print the switch plan first. To deploy or resync an isolated HK dry-run service, manually trigger the `Deploy Cloud Run` workflow with `target=hk-verify`:

```bash
python scripts/print_strategy_switch_env_plan.py --profile hk_listed_global_etf_rotation --account-region hk --dry-run-only --deployment-selector hk-verify --account-scope hk-verify --service-name longbridge-quant-hk-verify-service --json
gh workflow run sync-cloud-run-env.yml --repo QuantStrategyLab/LongBridgePlatform -f target=hk-verify -f cloud_run_region=<gcp-region> -f cloud_run_service=longbridge-quant-hk-verify-service -f longport_secret_name=longport_token_hk -f longport_app_key_secret_name=longport-app-key-hk -f longport_app_secret_secret_name=longport-app-secret-hk -f deploy_image=true -f sync_env=true
```

### Notifications

Telegram notifications include structured execution and heartbeat messages, with English and Chinese variants. Strategy-specific signal/status fields come from the selected strategy package profile; LongBridge-specific fields cover order submission, fill/reject/error reporting, account prefix, region, and market scope.

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes | Bot token for alerts; recommended to inject from Secret Manager secret `longbridge-telegram-token` |
| `GLOBAL_TELEGRAM_CHAT_ID` | Yes | Telegram chat or user ID used by this service. |
| `LONGPORT_APP_KEY` | Yes | LongPort OpenAPI app key (for token refresh); recommended to inject from the region-specific Secret Manager secret for this deployment, such as `longport-app-key-paper` / `longport-app-key-hk` / `longport-app-key-sg` |
| `LONGPORT_APP_SECRET` | Yes | LongPort OpenAPI app secret (for token refresh); recommended to inject from the region-specific Secret Manager secret for this deployment, such as `longport-app-secret-paper` / `longport-app-secret-hk` / `longport-app-secret-sg` |
| `LONGPORT_SECRET_NAME` | No | Secret Manager secret name for LongPort token (default: `longport_token_paper`) |
| `ACCOUNT_PREFIX` | No | Alert/log prefix for account/environment (default: `DEFAULT`) |
| `STRATEGY_PROFILE` | Yes | Strategy profile selector for compatibility and strategy routing. Set explicitly per deployment; enabled values include `global_etf_confidence_vol_gate`, `global_etf_rotation`, `mega_cap_leader_rotation_top50_balanced`, `russell_1000_multi_factor_defensive`, `soxl_soxx_trend_income`, `tech_communication_pullback_enhancement`, `tqqq_growth_income`, and `hk_listed_global_etf_rotation`. The structured runtime target is carried separately as `RUNTIME_TARGET_JSON`; Cloud Run uses the values configured on the selected service. |
| `ACCOUNT_REGION` | No | Account region marker for platform-style deployment (e.g. `PAPER`, `HK`, `SG`; defaults to `ACCOUNT_PREFIX` / `DEFAULT`) |
| `LONGBRIDGE_MARKET` | No | Market scope. Defaults to `HK` when `ACCOUNT_REGION=HK`, otherwise `US`. |
| `LONGBRIDGE_MARKET_CALENDAR` | No | Market calendar for market-hours checks. Defaults to `XHKG` for HK and `NYSE` for US. |
| `LONGBRIDGE_MARKET_TIMEZONE` | No | Market timezone. Defaults to `Asia/Hong_Kong` for HK and `America/New_York` for US. |
| `LONGBRIDGE_SYMBOL_SUFFIX` | No | Market-data and order symbol suffix. Defaults to `.HK` for HK and `.US` for US. |
| `LONGBRIDGE_TRADING_CURRENCY` | No | Trading-currency cash/reporting scope. Defaults to `HKD` for HK and `USD` for US. |
| `LONGBRIDGE_DRY_RUN_ONLY` | No | Set to `true` to keep the selected deployment in dry-run mode. |
| `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT` | No | Set to `true` to log raw LongBridge position quantity and available quantity for troubleshooting. |
| `LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON` | No | Optional LongBridge-side strategy plugin mount JSON. The plugin artifact controls mode; platform config must not set `mode`. |
| `CRISIS_ALERT_CHANNELS` | No | Optional crisis alert channel list: `email`, `sms`, `push`, and/or `telegram`. |
| `CRISIS_ALERT_EMAIL_RECIPIENTS` | No | Comma/semicolon/newline-separated email-form recipients. Use a normal mailbox for email-only delivery, or a Google Voice-associated mailbox/address to also trigger Google Voice prompts. |
| `CRISIS_ALERT_EMAIL_SENDER_EMAIL` | No | Sender email address used for crisis alert email. Gmail is the default transport, but the sender naming is provider-neutral. |
| `CRISIS_ALERT_EMAIL_SENDER_PASSWORD` | No | Sender SMTP password or app password. For Cloud Run, prefer `CRISIS_ALERT_EMAIL_SENDER_PASSWORD_SECRET_NAME` in env sync. |
| `CRISIS_ALERT_EMAIL_SMTP_HOST` | No | Optional SMTP host override. Defaults to Gmail SMTP when unset. |
| `CRISIS_ALERT_EMAIL_SMTP_PORT` | No | Optional SMTP port override. Defaults to `465` when unset. |
| `CRISIS_ALERT_EMAIL_SMTP_SECURITY` | No | Optional SMTP security override: `ssl`, `starttls`, or `none`. Defaults to `ssl` when unset. |
| `CRISIS_ALERT_TELEGRAM_CHAT_IDS` | No | Dedicated crisis-alert Telegram chat IDs. Separate from the strategy-cycle Telegram chat. |
| `CRISIS_ALERT_TELEGRAM_BOT_TOKEN` | No | Dedicated crisis-alert Telegram bot token. For Cloud Run, prefer `CRISIS_ALERT_TELEGRAM_BOT_TOKEN_SECRET_NAME` in env sync. |
| `LONGBRIDGE_MIN_RESERVED_CASH_USD` | No | Platform-level minimum cash reserve in USD. Defaults to `0`; the effective reserve is the max of this floor, `LONGBRIDGE_RESERVED_CASH_RATIO * total equity`, and any strategy-provided reserve. |
| `LONGBRIDGE_RESERVED_CASH_RATIO` | No | Platform-level minimum cash reserve ratio in `[0,1]`. Defaults to `0`; it can raise but not lower a strategy-provided reserve. |
| `LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD` | No | Safe-haven/cash-sweep target values below this USD amount are kept as cash instead of buying BOXX/BIL. Default `1000`. |
| `INCOME_THRESHOLD_USD` | No | Optional strategy override for the `tqqq_growth_income` income-layer threshold. Leave unset to use the strategy package default. |
| `QQQI_INCOME_RATIO` | No | Optional strategy override for QQQI's share of the `tqqq_growth_income` income layer, 0–1. |
| `NOTIFY_LANG` | No | Notification language: `en` (English, default) or `zh` (Chinese) |
| `GOOGLE_CLOUD_PROJECT` | No | GCP project ID (defaults to ADC project when unset) |

Strategy allocation can still target fractional dollar values and fractional position weights. The LongBridge execution layer now keeps a whole-share-only rule for every broker order: sell sizing floors to whole shares, buy sizing floors to whole shares, and fractional orders are skipped rather than downgraded. When a target value is zero, sell sizing uses the sellable position quantity instead of re-deriving shares from current price, so liquidation targets do not leave a residual share because of quote drift.

When `LONGBRIDGE_STRATEGY_PLUGIN_MOUNTS_JSON` includes the `crisis_response_shadow` plugin, the normal Telegram cycle message still includes the compact plugin line. If the plugin signal escalates beyond `no_action` (for example `canonical_route=true_crisis`, `suggested_action=defend`/`blocked`, or `would_trade_if_enabled=true`), the service also sends independent crisis alerts through configured `CRISIS_ALERT_CHANNELS` channels.
Alert results are written into the runtime report. Duplicate suppression uses stable plugin alert keys and stores markers under `STRATEGY_PLUGIN_ALERT_STATE_GCS_URI` when set, otherwise `EXECUTION_REPORT_GCS_URI`, with a local `/tmp` marker fallback.

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

Deploy the same codebase as multiple Cloud Run services by setting different values per service:

- `LONGPORT_SECRET_NAME`: point to different secrets (e.g. `longport_token_paper`, `longport_token_hk`, `longport_token_sg`)
- `ACCOUNT_PREFIX`: e.g. `PAPER`, `HK`, `SG` (all Telegram/log alerts will include `[ACCOUNT_PREFIX]`)
- `STRATEGY_PROFILE`: set per service. The deployment control plane now also carries `RUNTIME_TARGET_JSON`; treat `STRATEGY_PROFILE` as a compatibility input that still selects the strategy implementation, not the only identity key.
- Current strategy domains are `us_equity` and `hk_equity`. `STRATEGY_PROFILE` still goes through a platform capability matrix plus a rollout allowlist derived from `runtime_enabled` strategy metadata: `eligible` means the platform can run it in theory, `enabled` means the current rollout really allows it.
- `ACCOUNT_REGION`: explicitly mark the deployed account region (`PAPER` / `HK` / `SG`); if unset, the app falls back to `ACCOUNT_PREFIX` or `DEFAULT`
- `LONGBRIDGE_DRY_RUN_ONLY`: set per service when that deployment should stay dry-run
- `NOTIFY_LANG`: set `en` or `zh` per deployment

### GitHub-managed deploy and env sync for paper / HK / SG

This repo includes `.github/workflows/sync-cloud-run-env.yml` for GitHub-managed Cloud Run automation. Set `ENABLE_GITHUB_CLOUD_RUN_DEPLOY=true` on each GitHub Environment when GitHub Actions should build and deploy the container image; set `ENABLE_GITHUB_ENV_SYNC=true` when GitHub Actions should sync runtime env vars. You can enable either flag independently while migrating away from Google Cloud Triggers.

Pushes to `main` use the `ENABLE_MAIN_PUSH_CLOUD_RUN_AUTOMATION` automation switch. Set it to `true` when main-branch pushes should also run Cloud Run automation; manual `workflow_dispatch` runs still follow the deploy/env-sync flags above.

Recommended setup:

- **Repository Variables (shared):**
  - `ENABLE_GITHUB_ENV_SYNC` = `true`
  - `TELEGRAM_TOKEN_SECRET_NAME` (recommended: `longbridge-telegram-token`)
  - `NOTIFY_LANG`
  - `GLOBAL_TELEGRAM_CHAT_ID`
- **Repository Secrets (shared):**
  - Optional fallback only: `TELEGRAM_TOKEN`
  - Optional fallback only: `CRISIS_ALERT_EMAIL_SENDER_PASSWORD`
- **GitHub Environment: `longbridge-paper`**
  - Variables: `ENABLE_GITHUB_CLOUD_RUN_DEPLOY`, `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_MARKET`, `LONGBRIDGE_MARKET_CALENDAR`, `LONGBRIDGE_MARKET_TIMEZONE`, `LONGBRIDGE_SYMBOL_SUFFIX`, `LONGBRIDGE_TRADING_CURRENCY`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `LONGBRIDGE_MIN_RESERVED_CASH_USD`, `LONGBRIDGE_RESERVED_CASH_RATIO`, `LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (leave unset to inherit platform and strategy defaults)
  - Recommended secret-name values: `longport-app-key-paper`, `longport-app-secret-paper`
- **GitHub Environment: `longbridge-sg`**
  - Variables: `ENABLE_GITHUB_CLOUD_RUN_DEPLOY`, `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_MARKET`, `LONGBRIDGE_MARKET_CALENDAR`, `LONGBRIDGE_MARKET_TIMEZONE`, `LONGBRIDGE_SYMBOL_SUFFIX`, `LONGBRIDGE_TRADING_CURRENCY`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `LONGBRIDGE_MIN_RESERVED_CASH_USD`, `LONGBRIDGE_RESERVED_CASH_RATIO`, `LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (leave unset to inherit platform and strategy defaults)
  - Recommended secret-name values: `longport-app-key-sg`, `longport-app-secret-sg`
- **GitHub Environment: `longbridge-hk`**
  - Variables: `ENABLE_GITHUB_CLOUD_RUN_DEPLOY`, `CLOUD_RUN_REGION`, `CLOUD_RUN_SERVICE`, `ACCOUNT_PREFIX`, `ACCOUNT_REGION`, `RUNTIME_TARGET_JSON`, `STRATEGY_PROFILE`, `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, `LONGPORT_APP_SECRET_SECRET_NAME`
  - Optional variables: `LONGBRIDGE_FEATURE_SNAPSHOT_PATH`, `LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH`, `LONGBRIDGE_STRATEGY_CONFIG_PATH`, `LONGBRIDGE_MARKET`, `LONGBRIDGE_MARKET_CALENDAR`, `LONGBRIDGE_MARKET_TIMEZONE`, `LONGBRIDGE_SYMBOL_SUFFIX`, `LONGBRIDGE_TRADING_CURRENCY`, `LONGBRIDGE_DRY_RUN_ONLY`, `LONGBRIDGE_DEBUG_POSITION_SNAPSHOT`, `LONGBRIDGE_MIN_RESERVED_CASH_USD`, `LONGBRIDGE_RESERVED_CASH_RATIO`, `LONGBRIDGE_SAFE_HAVEN_CASH_SUBSTITUTE_THRESHOLD_USD`, `INCOME_THRESHOLD_USD`, `QQQI_INCOME_RATIO` (leave unset to inherit platform and strategy defaults)
  - Recommended secret-name values: `longport-app-key-hk`, `longport-app-secret-hk`

On every push to `main`, the workflow can build and deploy the configured Cloud Run services, update their shared and per-environment values, and remove `TELEGRAM_CHAT_ID` from each Cloud Run service.

Important:

- `CLOUD_RUN_REGION` should be set on each GitHub Environment, not as one shared repository variable. This lets `paper`, `HK`, and `SG` live in different Cloud Run regions.
- The deploy workflow uses `<CLOUD_RUN_REGION>-docker.pkg.dev` by default. Set `GCP_ARTIFACT_REGISTRY_HOSTNAME` on an Environment only when its Artifact Registry repository is in a different region.
- `LONGPORT_APP_KEY_SECRET_NAME` and `LONGPORT_APP_SECRET_SECRET_NAME` should also be set on each GitHub Environment. Do not keep one repository-level default for them when `paper`, `HK`, and `SG` use different LongPort credentials.
- The workflow only becomes strict when `ENABLE_GITHUB_ENV_SYNC=true`. If this variable is unset, the sync job is skipped and the old Google Cloud Trigger-only setup keeps working. Once you set it to `true`, missing env-sync values become a hard failure so you do not get a false green deployment. The selected profile's snapshot/config requirements are resolved from `scripts/print_strategy_profile_status.py --json` instead of a hard-coded strategy-name list.
- The deploy path only becomes active when `ENABLE_GITHUB_CLOUD_RUN_DEPLOY=true`. If it is unset, an existing Cloud Build trigger can keep owning code deployment while this workflow only syncs env.
- The workflow now also emits `RUNTIME_TARGET_JSON` so Cloud Run receives a structured runtime target alongside the legacy `STRATEGY_PROFILE` input.
- GitHub now authenticates to Google Cloud with OIDC + Workload Identity Federation, so `GCP_SA_KEY` is no longer required for this workflow.
- GitHub deploy uses the repository Dockerfile and Artifact Registry. The deploy service account needs Artifact Registry writer, Cloud Run admin, and service-account user permissions for the runtime service account.
- Here "shared" only means **shared inside this repository** between the `paper`, `HK`, and `SG` Cloud Run services. The Telegram token can still be shared, but LongPort app credentials should live in Secret Manager and be referenced by per-environment secret-name variables; they are not meant to be a global secret set reused by unrelated quant repos.
- If you want one cross-project shared layer across multiple quant repos, keep it small: `GLOBAL_TELEGRAM_CHAT_ID`, `NOTIFY_LANG`, `CRISIS_ALERT_CHANNELS`, and shared crisis alert settings under `CRISIS_ALERT_EMAIL_*`/`CRISIS_ALERT_PUSH_*` are reasonable when the same alert policy applies; account credentials, deployment keys, and alert secrets are not.

### Runtime guard alerting

`.github/workflows/runtime-guard.yml` is a second notification layer for failures
outside the LongBridge Flask handler. It runs once per GitHub Environment
(`longbridge-paper`, `longbridge-hk`, and `longbridge-sg`), reads Cloud Logging
for recent Cloud Scheduler errors and Cloud Run request/runtime failures, then
sends Telegram directly through `CRISIS_ALERT_TELEGRAM_BOT_TOKEN` +
`CRISIS_ALERT_TELEGRAM_CHAT_IDS` or the fallback `TELEGRAM_TOKEN` +
`GLOBAL_TELEGRAM_CHAT_ID`.

The guard does not invoke Cloud Run trading routes. It is meant to catch cases
where Scheduler cannot reach the service, OIDC/IAM/audience is wrong, Cloud Run
returns 4xx/5xx, or the container fails before app-level Telegram fallback code
can run.

Required setup:

- keep each Environment's `CLOUD_RUN_SERVICE` set, or set
  `RUNTIME_GUARD_CLOUD_RUN_SERVICES`
- grant the GitHub deploy service account `roles/logging.viewer` on
  `longbridgequant`
- keep Telegram chat/token variables or secrets configured in GitHub
- optionally set `RUNTIME_GUARD_SCHEDULER_JOB_PATTERN` per Environment; by
  default the workflow filters Scheduler logs by that Environment's
  `CLOUD_RUN_SERVICE`

The scheduled guard runs every 30 minutes. For a missed-run heartbeat, set
`RUNTIME_GUARD_REQUIRE_SUCCESS=true` and choose
`RUNTIME_GUARD_LOOKBACK_MINUTES` so the window covers the expected Scheduler run
for that Environment. The default leaves the heartbeat check off to avoid false
alerts outside active market windows.

`Execution Report Heartbeat` (`.github/workflows/execution-report-heartbeat.yml`)
is the stricter completion check. It runs once per GitHub Environment on
weekdays after the expected market windows and verifies that a recent runtime
report exists under that Environment's `EXECUTION_REPORT_GCS_URI`. It reads the
latest report JSON and alerts if no recent report exists or the recent reports
have rejected statuses such as `error`. The deploy service account needs object
read/list access on the report bucket.
Each matrix job checks its own Environment service. Override
`RUNTIME_HEARTBEAT_REQUIRED_SERVICES` only when an Environment intentionally
monitors a different service list.

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
5. Create two Cloud Scheduler jobs that POST to the Cloud Run URL. Use `"/precheck"` after the open window and `"/"` near the close window. Choose both crons from the selected strategy-layer cadence; this platform repo only owns the runtime trigger wiring.

IAM: the Cloud Run service account needs **Secret Manager Admin** (or Secret Accessor for the configured `LONGPORT_SECRET_NAME`, `LONGPORT_APP_KEY_SECRET_NAME`, and `LONGPORT_APP_SECRET_SECRET_NAME`, such as `longport_token_paper`, `longport-app-key-paper`, `longport-app-secret-paper`) and **Logs Writer**. Build/deploy typically uses a separate account with Artifact Registry Writer, Cloud Run Admin, Service Account User.
