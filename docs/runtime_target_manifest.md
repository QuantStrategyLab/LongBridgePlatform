# LongBridge runtime-target manifest

## 结论

`config/runtime_targets.manifest.json` 是 LongBridgePlatform 的公开、非敏感 runtime-target 清单。它用标准库 JSON 表达现有 PAPER / HK / SG 目标的必要字段，并提供严格校验。

本文件**不是**当前生产启停真相源。GitHub Environment 变量（尤其 `RUNTIME_TARGET_ENABLED`）、Secret Manager 内容和 Cloud Run 实际配置保持不变；本批也没有把 workflow matrix 改成动态读取该 manifest。

## 字段契约

顶层：

| 字段 | 要求 |
| --- | --- |
| `schema_version` | 固定为 `1` |
| `platform_id` | 固定为 `longbridge` |
| `targets` | 至少一个目标对象 |

每个目标必填：

| 字段 | 含义 |
| --- | --- |
| `id` | 目标唯一 id（如 `paper` / `hk` / `sg`） |
| `mode` | 仅允许 `live` / `paper` / `shadow` |
| `region` | Cloud Run region |
| `strategy_profile` | 预期策略 profile 名 |
| `service` | Cloud Run 服务名（全局唯一） |
| `environment` | GitHub Environment 名（全局唯一） |
| `secret_ref` | Secret Manager **名称**引用，不是密钥值 |

`secret_ref` 必填子字段：

- `longport_token`
- `longport_app_key`
- `longport_app_secret`

可选：

- `enabled`：缺省为 `false`；新增目标必须按 disabled 起步
- `label` / `account_scope`：与现有 workflow 标签对齐的可读字段

校验还会拒绝：

- 重复的 `id` / `service` / `environment`
- 把 token / password / 长串 opaque 值直接写进 manifest
- 未知顶层或目标字段

## 现有 PAPER / HK / SG 示例

仓库内示例已表达当前 workflow 使用的公开结构：

| id | environment | service | region | mode | strategy_profile（公开声明） |
| --- | --- | --- | --- | --- | --- |
| `paper` | `longbridge-paper` | `longbridge-quant-paper-service` | `asia-east1` | `paper` | `russell_top50_leader_rotation` |
| `hk` | `longbridge-hk` | `longbridge-quant-hk-service` | `asia-east2` | `live` | `hk_global_etf_tactical_rotation` |
| `sg` | `longbridge-sg` | `longbridge-quant-sg-service` | `asia-southeast1` | `live` | `soxl_soxx_trend_income` |

示例中三个目标的 `enabled` 均为 `false`。这表示公开清单的安全默认值，**不**覆盖 Environment 里现有的启停状态，也不授权交易。

## 如何增减目标（本批之后的操作顺序）

新增目标：

1. 在 `config/runtime_targets.manifest.json` 增加一条目标；`enabled` 保持 `false`。
2. 创建对应受保护 GitHub Environment，只放变量名、Secret 名引用和启停开关；密钥值留在 Secret Manager / GitHub Secrets。
3. 本地运行：

```bash
uv run --no-sync python scripts/validate_runtime_target_manifest.py
uv run --no-sync python -m pytest -q tests/test_runtime_target_manifest.py
```

4. 在**后续 wiring 批次**再考虑让 Guard / Lifecycle / Deploy workflow 读取该清单；在那之前不要假设改 manifest 就会改变运行矩阵。

减少目标：

1. 先确认对应 Environment 已停用、Scheduler / Cloud Run 不再需要该目标。
2. 从 manifest 删除该条目并保持校验通过。
3. Environment / Secret 的实际清理另授权，不由本文件自动执行。

## 下一阶段 wiring 边界（明确未做）

本批完成：schema、示例、校验、测试、文档。

本批**不**做：

- 把 `runtime-guard.yml`、`runtime-target-lifecycle.yml`、`execution-report-heartbeat.yml`、`sync-cloud-run-env.yml` 的硬编码 matrix 改成动态读取 manifest
- 修改任何 GitHub Secret / Environment 内容、生产开关、部署流程
- 云端写入、交易、Scheduler pause/resume、流量切换

后续若要接线，建议最小边界：

1. 先让只读 workflow（Guard / Lifecycle / Heartbeat）从 manifest 生成 matrix，但仍以 Environment 的 `RUNTIME_TARGET_ENABLED` 为启停真相。
2. Deploy / env sync 再单独迁移；新建目标默认 `enabled=false`，不会自动部署或启用。
3. 任何把 manifest `enabled` 提升为生产权威的改动，必须另开有授权的批次，并保留 fail-closed 读回。

## 本地校验

```bash
uv run --no-sync python scripts/validate_runtime_target_manifest.py
uv run --no-sync python scripts/validate_runtime_target_manifest.py --json
uv run --no-sync python -m pytest -q tests/test_runtime_target_manifest.py
```
