# 自然周期每日账户记录

已批准 PAPER 目标的正常周期如果已经读过账户余额，可以在执行报告持久化之后写一条既有 `account_snapshots` 日对象。默认关闭。`ACCOUNT_HISTORY_RECORDING_ENABLED` 去掉空白后必须恰好是 `true`，同时前缀、目标、`PAPER` 范围和 `GOOGLE_CLOUD_PROJECT` 都要显式给出。验证、force 和 dry-run 不写这个日对象。

记录复用这一周期里的一次 token 读取和 `fetch_strategy_account_state` 已经拿到的 `account_balance`。不追加 broker 或 Secret 查询，也不调用 `/run` 或 HTTP `/account-snapshot` 来造样本。refresh 后的 token 与读到的值不同，或部署、范围、token 版本缺任何一段，这一周期不写。配置里的 target 只用于对象路径，不能代替这次已认证读取。

对象只保留逐币种净资产、现金、来源绑定和观察时间。总资产计价币和现金币各自校验，不相加，也不互相补行。不写入 token、持仓或原始订单，也不写 `no_order`。写入复用当前对象存储客户端，一次 `upload_from_string` 带 `if_generation_match=0`、明确 timeout 和 `retry=None`。客户端不支持这些参数时跳过，不退回无界 `create_text`。创建失败、超时或结果不明都不重试。结果只记 status 和 category。写入失败不改变周期结果、报告或订单数。

依赖声明仍是 QPK `03ab459d95c456e46315952932544cdd8e286510` 与 UES `4a3943883cd6b5bbfe32a559e56a91b40a81b7ce`。部署、运行身份和保留策略仍待主助手验收。

`image-only-no-traffic` 可以给已核对的 PAPER 服务准备一个不接流量的 revision，并可选写入上述四个变量。四个都缺省时保持服务原值。显式开启必须四个同时合法，目标只能是 PAPER。更新前要确认正在接流量的 revision 与当前模板一致，且该 revision 原提交的 `uv.lock` 里 UES 与本候选相同。一次 `--no-traffic` 更新后读回 revision、image、这四个字段和其余配置；流量必须与更新前相同。读回失败不重试。这份源码接线还不是 staging、生产或 T0 验收。
