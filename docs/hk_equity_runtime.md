# LongBridge 港股运行时接入说明

## 结论

QuantStrategyLab 现有平台仓库里，能做港股股票交易运行时接入的平台是：

| 平台仓库 | 港股交易接入判断 | 当前处理 |
| --- | --- | --- |
| `LongBridgePlatform` | 可接入。LongBridge 支持港股账户、`.HK` 行情符号和 HKD 现金口径。 | 已加入 HK market scope 配置、通知和结构化日志字段。 |
| `InteractiveBrokersPlatform` | 可接入。IBKR 需要账户有港股/SEHK 交易与行情权限。 | 在对应仓库单独接入。 |
| `CharlesSchwabPlatform` | 不适合作为港股交易入口。 | 保持 US equity 边界，不改。 |
| `FirstradePlatform` | 不适合作为港股交易入口。 | 保持 US equity 边界，不改。 |
| `BinancePlatform` | 加密货币平台，不是港股股票交易入口。 | 不改。 |

## 运行时设计

平台运行时已具备港股市场维度，并接入 `HkEquityStrategies` 的港股 profile 元数据。当前平台可选港股 profile 只暴露 `runtime_enabled` 的 `hk_listed_global_etf_rotation`；`hk_blue_chip_leader_rotation` 是 snapshot 架构占位，`hk_index_mean_reversion`、`hk_etf_regime_rotation` 是 `market_history` 研究候选，均留在研究/快照仓库，不进入平台可选列表。Cloud Run 通过 `RUNTIME_TARGET_JSON` / `STRATEGY_PROFILE` 选择当前运行策略。整体仍沿用美股策略的分层方式：

1. [`HkEquityStrategies`](https://github.com/QuantStrategyLab/HkEquityStrategies) 提供非 snapshot `hk_equity` 策略定义、运行入口和 LongBridge runtime adapter。
2. [`HkEquitySnapshotPipelines`](https://github.com/QuantStrategyLab/HkEquitySnapshotPipelines) 产出 snapshot-backed profile 的特征快照、manifest、ranking 和 release summary。
3. 非 snapshot profile 使用平台 market-data feed 提供的 `market_history`，不需要 snapshot artifact。
4. LongBridge 只读取 `RUNTIME_TARGET_JSON`、策略 profile、snapshot/config 路径和平台 market scope。
5. 平台根据 market scope 选择交易币种、行情后缀、市场日历和通知/日志字段。

这样可以避免在平台仓库里硬编码策略逻辑，也便于同一套港股策略接入 IBKR。

## 港股 profile 当前状态

| Profile | Domain | Inputs | Target mode | Snapshot manifest | Status |
| --- | --- | --- | --- | --- | --- |
| `hk_listed_global_etf_rotation` | `hk_equity` | `market_history` | `weight` | not required | runtime-enabled; platform-selectable |
| `hk_blue_chip_leader_rotation` | `hk_equity` | `feature_snapshot` | `weight` | required | snapshot scaffold; not platform-selectable |
| `hk_index_mean_reversion` | `hk_equity` | `market_history` | `weight` | not required | research/backtest only; not platform-selectable |
| `hk_etf_regime_rotation` | `hk_equity` | `market_history` | `weight` | not required | research/backtest only; not platform-selectable |

`scripts/print_strategy_profile_status.py` 只显示平台可选 profile，因此只会列出 `hk_listed_global_etf_rotation` 这一条港股 profile。其他港股候选继续保留在研究文档和 snapshot pipeline，不应该出现在 Cloud Run switch plan 里。

未来启用 snapshot-backed profile 后的最小策略配置示例；这些 profile 晋级为 `runtime_enabled` 前不会出现在平台可选列表：

```bash
STRATEGY_PROFILE=hk_blue_chip_leader_rotation
RUNTIME_TARGET_JSON={"platform_id":"longbridge","strategy_profile":"hk_blue_chip_leader_rotation","deployment_selector":"HK","account_scope":"HK","execution_mode":"live"}
LONGBRIDGE_FEATURE_SNAPSHOT_PATH=gs://<bucket>/hk_blue_chip_leader_rotation_feature_snapshot_latest.csv
LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH=gs://<bucket>/hk_blue_chip_leader_rotation_feature_snapshot_latest.csv.manifest.json
```

## 配置项

| 变量 | 默认值 | 港股建议值 | 说明 |
| --- | --- | --- | --- |
| `ACCOUNT_REGION` | `DEFAULT` | `HK` | 设置为 `HK` 时会推导港股默认 market scope。 |
| `LONGBRIDGE_MARKET` | 从 `ACCOUNT_REGION` 推导，默认 `US` | `HK` | 显式指定市场；优先级高于 `ACCOUNT_REGION`。 |
| `LONGBRIDGE_MARKET_CALENDAR` | `NYSE` / 港股为 `XHKG` | `XHKG` | 市场开闭市判断使用的 calendar 名称。 |
| `LONGBRIDGE_MARKET_TIMEZONE` | `America/New_York` / 港股为 `Asia/Hong_Kong` | `Asia/Hong_Kong` | 用于生成交易日日期。 |
| `LONGBRIDGE_SYMBOL_SUFFIX` | `.US` / 港股为 `.HK` | `.HK` | 平台行情和订单符号后缀。 |
| `LONGBRIDGE_TRADING_CURRENCY` | `USD` / 港股为 `HKD` | `HKD` | 账户现金、报价和通知口径。 |

最小港股配置：

```bash
ACCOUNT_REGION=HK
# 可选显式覆盖：
LONGBRIDGE_MARKET=HK
LONGBRIDGE_MARKET_CALENDAR=XHKG
LONGBRIDGE_MARKET_TIMEZONE=Asia/Hong_Kong
LONGBRIDGE_SYMBOL_SUFFIX=.HK
LONGBRIDGE_TRADING_CURRENCY=HKD
```

## Dry-run 切换计划

可用以下命令生成 HK dry-run 环境计划，复核当前 Cloud Run 配置或准备重新同步：

```bash
python scripts/print_strategy_switch_env_plan.py \
  --profile hk_listed_global_etf_rotation \
  --account-region hk \
  --dry-run-only \
  --deployment-selector hk-verify \
  --account-scope hk-verify \
  --service-name longbridge-quant-hk-verify-service \
  --json
```

这个命令只打印计划。输出会显式包含：

- `RUNTIME_TARGET_JSON`：`strategy_profile=hk_listed_global_etf_rotation`、`dry_run_only=true`、`execution_mode=paper`。
- `ACCOUNT_REGION=HK`、`ACCOUNT_PREFIX=HK`、`LONGBRIDGE_DRY_RUN_ONLY=true`。
- `LONGBRIDGE_MARKET=HK` / `XHKG` / `Asia/Hong_Kong` / `.HK` / `HKD`。
- `remove_if_present`：清理 snapshot/config 相关环境变量，因为该 profile 直接使用 `market_history`。
- `dry_run_plan`：检查 HK 行情权限、`.HK` / HKD 映射、整数股和 lot-size、HKD 现金口径、dry-run 订单预览、通知和 runtime report。

打印计划不会直接修改服务配置；只有执行 Cloud Run env 更新/部署命令才会改变服务。

## 生成 `hk_low_vol_dividend_quality` snapshot artifacts

`hk_low_vol_dividend_quality` 是 snapshot-backed 策略，Cloud Run 切到这个 profile 前必须先有经过校验的 feature snapshot 和 manifest。`LongBridgePlatform` 提供一个手动桥接 workflow，使用本仓库已经允许的 `longbridge-hk` WIF 发布 artifact。默认数据源是 `public_yfinance_staging`，不依赖 LongBridge 历史行情权限；如果账号已开通对应 HK market-data entitlement，也可以切到 `longbridge_openapi_staging`。

手动生成并只打印 GCS 发布计划：

```bash
gh workflow run build-hk-low-vol-snapshot-artifacts.yml \
  --repo QuantStrategyLab/LongBridgePlatform \
  -f snapshot_ref=main \
  -f profile=hk_low_vol_dividend_quality \
  -f data_source_mode=public_yfinance_staging \
  -f allow_research_defaults=false \
  -f execute_publish=false
```

如果生成结果通过校验，并确认目标 bucket 可由 HK Cloud Run runtime service account 读取，可以发布到 GCS：

```bash
gh workflow run build-hk-low-vol-snapshot-artifacts.yml \
  --repo QuantStrategyLab/LongBridgePlatform \
  -f snapshot_ref=main \
  -f profile=hk_low_vol_dividend_quality \
  -f data_source_mode=public_yfinance_staging \
  -f allow_research_defaults=false \
  -f gcs_prefix=gs://qsl-runtime-logs-interactivebrokersquant/strategy-artifacts/hk_equity/hk_low_vol_dividend_quality \
  -f execute_publish=true
```

发布后，HK dry-run runtime 需要至少配置：

```bash
STRATEGY_PROFILE=hk_low_vol_dividend_quality
LONGBRIDGE_DRY_RUN_ONLY=true
LONGBRIDGE_FEATURE_SNAPSHOT_PATH=gs://qsl-runtime-logs-interactivebrokersquant/strategy-artifacts/hk_equity/hk_low_vol_dividend_quality/hk_low_vol_dividend_quality_factor_snapshot_latest.csv
LONGBRIDGE_FEATURE_SNAPSHOT_MANIFEST_PATH=gs://qsl-runtime-logs-interactivebrokersquant/strategy-artifacts/hk_equity/hk_low_vol_dividend_quality/hk_low_vol_dividend_quality_factor_snapshot_latest.csv.manifest.json
```

注意：`allow_research_defaults=true` 只允许做研究 smoke，不允许发布到 GCS，也不能作为 live-enable 证据。public yfinance 数据源用于让 snapshot artifact 生成和券商执行解耦；它仍需要按策略证据包记录数据源、生成时间和 broker dry-run 结果。

## 部署或同步 HK Cloud Run

仓库的 `Deploy Cloud Run` workflow 支持手动 `workflow_dispatch` 目标 `hk-verify`。这个目标只启用 HK matrix deployment，PAPER / SG 会跳过，并设置或更新独立港股 dry-run 服务：

- `CLOUD_RUN_SERVICE=longbridge-quant-hk-verify-service`（可通过输入改名）
- `STRATEGY_PROFILE=hk_listed_global_etf_rotation`
- `ACCOUNT_REGION=HK`、`ACCOUNT_PREFIX=HK`
- `RUNTIME_TARGET_JSON.execution_mode=paper`、`dry_run_only=true`
- `LONGBRIDGE_DRY_RUN_ONLY=true`
- `LONGBRIDGE_MARKET=HK`、`LONGBRIDGE_SYMBOL_SUFFIX=.HK`、`LONGBRIDGE_TRADING_CURRENCY=HKD`

手动部署示例：

```bash
gh workflow run sync-cloud-run-env.yml \
  --repo QuantStrategyLab/LongBridgePlatform \
  -f target=hk-verify \
  -f cloud_run_region=<gcp-region> \
  -f cloud_run_service=longbridge-quant-hk-verify-service \
  -f longport_secret_name=longport_token_hk \
  -f longport_app_key_secret_name=longport-app-key-hk \
  -f longport_app_secret_secret_name=longport-app-secret-hk \
  -f deploy_image=true \
  -f sync_env=true
```

如果只想同步环境变量、不重新部署镜像，可以设置 `-f deploy_image=false -f sync_env=true`；workflow 会跳过 commit wait，避免等待一个并未部署的新 revision。

执行前确认：

- 目标 Cloud Run service 是独立 HK service；不要和 paper / SG 服务共用同一个 service 名。
- `longbridge-hk` GitHub Environment 或 workflow 输入里有 `CLOUD_RUN_REGION`。
- `GLOBAL_TELEGRAM_CHAT_ID`、`NOTIFY_LANG`、`TELEGRAM_TOKEN_SECRET_NAME` 或 `TELEGRAM_TOKEN` 已在 `longbridge-hk` Environment 配好。
- `longport_token_hk`、`longport-app-key-hk`、`longport-app-secret-hk` 已在 Secret Manager 存在，且 runtime service account 有读取权限。
- LongBridge HK 账号和行情权限已开通；如果 `LONGBRIDGE_DRY_RUN_ONLY=true`，服务只做订单预览，不提交真实订单。

## 通知和日志

- Telegram 中英文模板新增市场行：市场、交易币种、标的后缀。
- Runtime report / structured log context 新增：`market`、`market_calendar`、`market_timezone`、`symbol_suffix`、`trading_currency`。
- 市场开闭市跳过、market hours bypass 等事件会带上 market scope，便于区分 US/HK 服务。

## 风险和注意事项

- `XHKG` 是否可用取决于部署环境里的 `pandas_market_calendars` 版本；如不可用，可用 `LONGBRIDGE_MARKET_CALENDAR` 临时覆盖。
- `hk_listed_global_etf_rotation` 已在策略包 `runtime_enabled`，可由 LongBridge HK Cloud Run 通过运行时环境选择；`hk_blue_chip_leader_rotation`、`hk_index_mean_reversion`、`hk_etf_regime_rotation` 仍不进入平台可选列表。
- 港股 `market_history` profile 运行后，需要持续用 LongBridge HK 行情 feed 对 `02800`、`03033`、`02822`、`02840`、`03110`、`03188`、`02834`、`03175` 做行情、价差、lot-size 和订单预览/执行结果复核。
- LongBridge 下单仍保持整数股规则；如果未来港股策略涉及碎股或特殊交易单位，需要在策略层明确 lot-size 约束后再扩展。
