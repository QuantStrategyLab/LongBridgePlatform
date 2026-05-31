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

平台运行时已具备港股市场维度，并正式接入 `HkEquityStrategies` 的 `hk_blue_chip_leader_rotation` profile。整体仍沿用美股 snapshot 策略的分层方式：

1. [`HkEquityStrategies`](https://github.com/QuantStrategyLab/HkEquityStrategies) 提供 `hk_equity` 策略定义、运行入口和 LongBridge runtime adapter。
2. [`HkEquitySnapshotPipelines`](https://github.com/QuantStrategyLab/HkEquitySnapshotPipelines) 产出最新特征快照、manifest、ranking 和 release summary。
3. LongBridge 只读取 `RUNTIME_TARGET_JSON`、策略 profile、snapshot/config 路径和平台 market scope。
4. 平台根据 market scope 选择交易币种、行情后缀、市场日历和通知/日志字段。

这样可以避免在平台仓库里硬编码策略逻辑，也便于同一套港股策略接入 IBKR。

## 已启用港股 profile

| Profile | Domain | Inputs | Target mode | Snapshot manifest |
| --- | --- | --- | --- | --- |
| `hk_blue_chip_leader_rotation` | `hk_equity` | `feature_snapshot` | `weight` | required |

最小策略配置示例：

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
| `LONGBRIDGE_SYMBOL_SUFFIX` | `.US` / 港股为 `.HK` | `.HK` | 平台行情符号后缀。 |
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

## 通知和日志

- Telegram 中英文模板新增市场行：市场、交易币种、标的后缀。
- Runtime report / structured log context 新增：`market`、`market_calendar`、`market_timezone`、`symbol_suffix`、`trading_currency`。
- 市场开闭市跳过、market hours bypass 等事件会带上 market scope，便于区分 US/HK 服务。

## 风险和注意事项

- `XHKG` 是否可用取决于部署环境里的 `pandas_market_calendars` 版本；如不可用，可用 `LONGBRIDGE_MARKET_CALENDAR` 临时覆盖。
- `hk_blue_chip_leader_rotation` 已接入平台，但实盘前仍需要用最新 snapshot artifact、dry-run 和小范围账户做连接验证。
- LongBridge 下单仍保持整数股规则；如果未来港股策略涉及碎股或特殊交易单位，需要在策略层明确 lot-size 约束后再扩展。
