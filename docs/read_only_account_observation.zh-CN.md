# 只读账户观察器的配置与边界

独立观察器支持 LongBridge 的 PAPER、HK、SG 协议范围。目标名称可以自定义；账户槽位、策略名称和券商原生账户身份分别处理，策略名称不能代替账户身份。

本实现复用普通配置对象、只读券商入口和现有账户事实接收接口。配置解析、服务身份核验、券商响应投影和发送分别由已有函数承担，不增加插件框架或第二套账户注册表。

## 配置来源

独立观察器从当前受保护任务环境读取部署信息，不从公开运行目标清单推导生产映射。

| 参数 | 用途 |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT`、`GOOGLE_CLOUD_PROJECT_NUMBER` | 核对服务所在项目；数字项目号在返回数字 namespace 时必填 |
| `SNAPSHOT_SERVICE`、`SNAPSHOT_REGION` | 定位并核对实际承载流量的服务版本 |
| `ACCOUNT_HISTORY_TARGET_ID`、`ACCOUNT_HISTORY_EXPECTED_SCOPE` | 自定义账户槽位与协议范围 |
| `ACCOUNT_HISTORY_SERVICE_URL` | 精确服务地址与 ID token audience |
| `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` | 核对该账户的预期来源绑定 |
| `ACCOUNT_HISTORY_ALLOWED_REF` | 允许执行的分支；工作流使用仓库默认分支 |
| `ACCOUNT_FACTS_SYNC_URL`、`ACCOUNT_FACTS_SYNC_TOKEN` | 既有账户事实接收接口及专用同步凭据 |

工作流的 `GCP_PROJECT_ID`、`GCP_PROJECT_NUMBER`、`GCP_WORKLOAD_IDENTITY_PROVIDER` 和 `GCP_WORKLOAD_IDENTITY_SERVICE_ACCOUNT` 使用 GitHub Secrets。每个环境的 `ACCOUNT_HISTORY_SERVICE_URL` 和 `ACCOUNT_HISTORY_EXPECTED_SOURCE_BINDING_ID` 同样只从 Secrets 读取。真实地址、来源绑定及可关联用户的映射应放在受保护配置中；源码、文档、fixture 和公开日志只用不可关联真实部署的示例。部署参数不得在应用代码中设生产默认值。

`GITHUB_REPOSITORY`、`GITHUB_REF`、`GITHUB_WORKFLOW_REF` 使用 GitHub 提供的执行上下文。保护环境、云端 WIF 条件及精确来源绑定共同限制调用者；仓库名称不写死在 Python 中。

## 访问与数据处理

观察器先核对服务、项目、区域、地址及实际承载 100% 流量版本的只读开关，再发送 audience token。失败的新版本不能代替现役版本，也不能阻断一个已核准的健康现役版本。

`independent_get` 适用于可由当前执行器访问的已鉴权 Cloud Run 服务。内部 ingress 返回固定原因 `internal_observer_required`，在调用券商或发送账户事实前停止；不得为采集开放 ingress。内部服务应复用受支持的内部执行入口，另行验证只读采集、私有归档与接收闭环。此文档不表示该内部闭环已经完成。

单次操作最多一次快照 GET 和一次事实 POST，不自动重试未知结果。投影保留券商实际币种、金额、观察时间及来源；账户来源不匹配时拒收，不补零、不改旧数据时间。交易启停、风险预算和通知不由此观察器控制。

本次配置迁移只覆盖独立观察器与该心跳工作流。历史 Scheduler 观察器及其他部署流程尚有旧配置约定，不能据此宣称全仓已完成配置与隐私整改。
