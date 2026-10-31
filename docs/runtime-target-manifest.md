# LongBridge runtime-target manifest

## 结论

`config/runtime_targets.manifest.json` 是 LongBridgePlatform 的公开、非敏感 runtime-target 清单。它用标准库 JSON 表达现有 PAPER / HK / SG 目标的必要字段，并提供严格校验。

Guard / Lifecycle / Heartbeat / Deploy（`sync-cloud-run-env.yml`）四个 workflow 通过 `scripts/render_runtime_target_matrix.py` 从**已校验**的 manifest 渲染动态 matrix。本文件**不是**生产启停真相源：GitHub Environment 变量（尤其 `RUNTIME_TARGET_ENABLED`）、Secret Manager 内容和 Cloud Run 实际配置保持不变。manifest 的 `enabled` **不会**过滤 matrix，也**不会**覆盖 Environment 启停。

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
| `mode` | 仅允许 `live` / `paper` / `shadow`（语义不得混淆） |
| `region` | Cloud Run region |
| `strategy_profile` | 预期策略 profile 名；必须与 Environment `RUNTIME_TARGET_JSON.strategy_profile` 一致，由 Runtime Guard 校验 |
| `service` | Cloud Run 服务名（全局唯一） |
| `environment` | GitHub Environment 名（全局唯一） |
| `secret_ref` | Secret Manager **名称**引用，不是密钥值 |

`secret_ref` 必填子字段：

- `longport_token`
- `longport_app_key`
- `longport_app_secret`

可选：

- `enabled`：缺省为 `false`；新增目标必须按 disabled 起步。该字段不进入 workflow matrix，也不授权交易。
- `label` / `account_scope`：与现有 workflow 标签对齐的可读字段

校验还会拒绝：

- 重复的 `id` / `service` / `environment`
- 把 token / password / 长串 opaque 值直接写进 manifest
- 未知顶层或目标字段
- 缺失或非法 manifest（fail-closed）

## 现有 PAPER / HK / SG 示例

仓库内示例已表达当前 workflow 使用的公开结构：

| id | environment | service | region | mode | strategy_profile（公开声明） |
| --- | --- | --- | --- | --- | --- |
| `paper` | `longbridge-paper` | `longbridge-quant-paper-service` | `asia-east1` | `paper` | `russell_top50_leader_rotation` |
| `hk` | `longbridge-hk` | `longbridge-quant-hk-service` | `asia-east2` | `live` | `tqqq_growth_income` |
| `sg` | `longbridge-sg` | `longbridge-quant-sg-service` | `asia-southeast1` | `live` | `soxl_soxx_trend_income` |

示例中三个目标的 `enabled` 均为 `false`。这表示公开清单的安全默认值，**不**覆盖 Environment 里现有的启停状态，也不授权交易。parity 测试锁定上述三个目标的 matrix 字段与历史硬编码一致。

## 漂移防护（manifest ↔ Environment）

结构校验只能证明 manifest 合法，不能证明它**真的**描述当前部署。`strategy_profile` 这类可变事实同时存在于公开 manifest 和 Environment `RUNTIME_TARGET_JSON` / `STRATEGY_PROFILE`，而且四个 matrix 都不消费它，手改一处、漏改另一处不会报错：HK 的 manifest 从 2026-09-20 引入起就写成 `hk_global_etf_tactical_rotation`，实际部署从 2026-07-30 起一直是 `tqqq_growth_income`。

`Runtime Guard` 的每个目标 job 在认证云账号前运行：

```bash
uv run --no-sync python scripts/check_runtime_target_manifest_drift.py --target-id "$MANIFEST_TARGET_ID"
```

它把 manifest 目标与当前 Environment 的 `RUNTIME_TARGET_JSON`（`platform_id` / `strategy_profile` / `account_scope` / `service_name`）以及 `STRATEGY_PROFILE` 对照，不一致时以固定原因码失败，例如 `strategy_profile_mismatch`、`strategy_profile_conflict`、`account_scope_mismatch`、`service_name_mismatch`。只读、无云调用、不打印整份 target JSON。

不纳入比对的字段：

- `enabled`：按设计只是新增目标的安全默认值，启停只看 Environment `RUNTIME_TARGET_ENABLED`。
- `mode`：账户类别（`live` 实盘账户 / `paper` 模拟账户），由部署准入和账户快照合同就地和 Cloud Run 配置核对；HK 是实盘账户但 `dry_run_only=true`，两者并不矛盾。
- `region`：由 Cloud Run 读回路径校验，不依赖 manifest。

本地复核：

```bash
RUNTIME_TARGET_JSON='{"platform_id":"longbridge","strategy_profile":"tqqq_growth_income","account_scope":"HK","service_name":"longbridge-quant-hk-service"}' \
STRATEGY_PROFILE=tqqq_growth_income \
uv run --no-sync python scripts/check_runtime_target_manifest_drift.py --target-id hk
```

发现漂移时先确认部署事实，再在 PR 里更新 manifest；manifest 永远不反向覆盖生产配置。

## 动态 matrix

```bash
uv run --no-sync python scripts/validate_runtime_target_manifest.py
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile guard
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile lifecycle
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile heartbeat
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile sync
```

CI / workflow 使用 `--github-output`，由 `resolve-matrix` job 写出 `matrix=...`，下游 job 使用 `fromJSON(needs.resolve-matrix.outputs.matrix)`。非法或缺失 manifest 会使 resolve 失败，从而 fail-closed。

保留边界：

- Cloud Run / Secret / Scheduler 读回与 no-traffic / image-only 路径不变
- traffic / scheduler 写入隔离（#502）不变
- 生产启停仍只看 Environment `RUNTIME_TARGET_ENABLED`

## 如何增减目标

新增目标：

1. 在 `config/runtime_targets.manifest.json` 增加一条目标；`enabled` 保持 `false`。
2. 创建对应受保护 GitHub Environment，只放变量名、Secret 名引用和启停开关；密钥值留在 Secret Manager / GitHub Secrets。
3. 本地运行：

```bash
uv run --no-sync python scripts/validate_runtime_target_manifest.py
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile guard
uv run --no-sync python -m pytest -q tests/test_runtime_target_manifest.py tests/test_runtime_target_matrix.py
```

4. 合并后，四个 workflow 会自动纳入新目标的 matrix 行；在 Environment 将 `RUNTIME_TARGET_ENABLED` 设为 true 之前，运行侧不得视为已启用。

减少目标：

1. 先确认对应 Environment 已停用、Scheduler / Cloud Run 不再需要该目标。
2. 从 manifest 删除该条目并保持校验通过。
3. Environment / Secret 的实际清理另授权，不由本文件自动执行。

## 仍未改变的边界

本批**不**做：

- 修改任何 GitHub Secret / Environment 内容、生产开关值
- 云端写入、交易、Scheduler pause/resume、流量切换
- 把 manifest `enabled` 提升为生产权威

## 本地校验

```bash
uv run --no-sync python scripts/validate_runtime_target_manifest.py
uv run --no-sync python scripts/validate_runtime_target_manifest.py --json
uv run --no-sync python scripts/render_runtime_target_matrix.py --profile guard
# 需要目标 Environment 的 RUNTIME_TARGET_JSON / STRATEGY_PROFILE，见上节「漂移防护」
uv run --no-sync python scripts/check_runtime_target_manifest_drift.py --target-id hk
uv run --no-sync python -m pytest -q tests/test_runtime_target_manifest.py tests/test_runtime_target_manifest_drift.py tests/test_runtime_target_matrix.py tests/test_runtime_monitor_workflows.py
bash tests/test_sync_cloud_run_env_workflow.sh
```
