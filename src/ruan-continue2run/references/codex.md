# Codex Desktop Adapter

当前 Codex Desktop Adapter 使用本地 bundled CLI 的 app-server 协议。

## Launcher 选择

按以下顺序解析：

1. `RUAN_CONTINUE2RUN_CODEX_BIN`（Maintenance 调试覆盖）；
2. `CODEX_CLI_PATH`（Codex Desktop 注入的 bundled CLI）；
3. PATH 中的 `codex`。

不能默认使用 PATH 中的 CLI 覆盖当前 Desktop bundled CLI，因为两者可能属于不同版本。

## Runtime 参数

Codex 声明 `PARAMETER_CAPTURE_PHASE = "handoff"`。start 不读取模型参数；handoff 取得一次性
claim 后读取最终状态、Preflight、保存快照并提交创建，覆盖本轮开始时或上一轮携带的值。
官方和第三方 provider 相同；详见 `runtime-inheritance.md`。

Adapter 通过当前 `CODEX_THREAD_ID` / `CODEX_SESSION_ID` 对 app-server 执行只读 `thread/read`，
并从 thread metadata 与可解析的 `thread_settings_applied` 读取：

| Relay 字段 | Codex 来源 / 提交方式 |
|---|---|
| `working_directory` | `thread.cwd` / `thread/start.cwd` |
| `model` | `thread.model` / `thread/start.model` |
| `thinking_depth` | `thread.reasoningEffort` / `turn/start.effort` |
| `model_provider` | `thread.modelProvider` / `thread/start.modelProvider` |
| `sandbox_mode` | `thread/read` 顶层 `sandbox`、rollout `sandbox_policy` / `thread/start.sandbox` |
| `approval_mode` | thread settings approval policy / `thread/start.approvalPolicy` |
| `permission_mode` | `thread/read` 顶层 `activePermissionProfile`、rollout `active_permission_profile` / `thread/start.permissions` |

缺少可靠来源时返回 `unavailable`，不使用模型自述或配置默认值冒充 observed。

rollout 按记录顺序读取，较新的 `thread_settings_applied` / `turn_context` 覆盖旧设置；
其中后续持久化的 model、reasoning effort、provider 优先于 `thread/read` 的默认值或回退值；
初始 session_meta 的 provider 只在没有当前 metadata 和后续设置时作为回退来源。
可读取的显式空 reasoning effort 用字符串 `null` 保存在 RelayContext 中，仍为 applicable、
observed；提交 `turn/start` 时转回 JSON null。字段根本不存在时仍为 unavailable。
最新 rollout 的权限 profile 优先于启动环境变量，避免用户收紧权限后继承旧 profile。

## 第三方供应商

保留当前会话的 provider ID 和模型原名，不切回 openai、不猜模型别名，也不修改 config、
auth、订阅或登录状态。独立 app-server 原生读取相同 CODEX_HOME、环境和 cwd 的配置；
不复制供应商密钥或完整配置到 RelayContext、worker 请求、日志或正式 Skill 包。

非 openai provider 的 Preflight 使用只读 `config/read` 检查该进程能否解析供应商定义。
配置要求 `env_key` 时检查当前进程是否可获得该变量；不统一要求第三方供应商登录 ChatGPT，
也不统一要求所有供应商提供 API Key。静态 bearer、现有官方认证、无认证本地服务等仍交给
Codex 原生认证实现。缺少定义或凭据时报告具体错误，不改动用户配置。

这项检查不证明供应商当前为每个模型提供可用渠道。当前会话可执行，只证明它实际使用的
provider/model 请求可执行；应将报错中的精确模型与会话记录核对。首次模型请求在 Skill
执行任何工具之前失败时，Skill 尚无机会修复该请求，不能把它宣称为接力失败。

## 创建与确认

Adapter 的 `initialize.params.clientInfo` 必须复用 Codex Desktop 的身份：
`{"name":"codex_desktop","title":"Codex Desktop",...}`。`clientInfo.name` 会参与线程
归属标识；使用 `ruan-continue2run` 等自定义名称会让 Desktop 把新线程显示为“在其他应用中
打开”，从而阻止用户继续交互。

Adapter worker 使用：

```text
initialize
→ initialized
→ thread/start
→ thread/name/set（`接力：ruan-continue2run`）
→ turn/start
→ turn/started 或 item/started 或 item/agentMessage/delta
```

`initialize` 声明 `capabilities.experimentalApi=true`，以便使用 named `permissions`。
`thread/start` 提交 `permissions` 时不同时提交 legacy `sandbox`；没有可用 named profile
时才提交 `sandbox`。rollout 中的 `permission_profile.type=disabled` 描述本地环境，不能
覆盖同一记录中的 `active_permission_profile.id`。

官方 openai 路径保留原有启动事件确认语义；其他 provider 等待非空 agentMessage 输出或
模型产生的工具执行事件，不能仅凭 `turn/started` 或 userMessage 创建报告 confirmed。
认证失败、HTTP 错误、approval 阻塞以及只有启动通知而没有模型执行证据均不能确认为成功。
创建请求已产生 thread 时保留 session_reference 并返回 unknown，避免重复创建。

协议读取使用持久 JSONL reader，逐行队列保存同一次 flush 中的后续消息，避免文本缓冲与
selector 组合漏读。worker 不写原始协议调试日志；错误只保留协议分类与 HTTP 状态，防止
供应商响应回显认证信息。worker 脱离 Adapter/Relay 进程组继续持有
app-server 连接，并在任意终态 `turn/completed`（completed/failed/interrupted/cancelled）后关闭该连接和其子进程组；对部分 Desktop 版本只发送
`thread/status/changed` 的 `idle`，worker 在已观察到启动事件后将该状态作为等价终态。终端事件
同时写入原子状态文件，防止 selector/pipe 缓冲导致 worker 残留。创建失败、approval 阻塞、超时或无法观察启动事件
按 2.0 三态契约返回 `failed` 或 `unknown`。

Codex confirmed 结果中的 `session_reference` 可拼成 `codex://threads/<session_reference>`。
向用户报告 handoff 时同时给出这个链接，便于 Desktop 直接打开新线程；标题同步设置为
`接力：ruan-continue2run`。由于 Adapter 使用独立 app-server，不能假设 Desktop 当前侧栏
会立即刷新或自动切换到该线程。

## 当前验收边界

已在本机 Codex Desktop bundled app-server 上真实验证：

- 创建新的 Desktop thread；
- 传递完整 `ruan-continue2run` Payload；
- 观察到 `turn/started`；
- 新 thread 读取到完整任务并完成测试输出；
- 安装位置加载的是同步后的 2.0 Skill。

账户权限、网络、模型可用性和 UI 面板显示仍属于真实环境条件；无法读取或验证时必须报告，
不能静态宣称支持。

2026-10-08 补丁另使用临时 CODEX_HOME、测试 cwd 的自有 STOP 和 localhost Responses
端点，验证真实 bundled app-server 下 env_key / bearer、空 effort、401/503 与 worker 退出；
官方路径用协议回归验证。隔离测试未切换当前会话订阅、修改当前配置或重启 Desktop。
该证据不等价于对所有第三方服务或当前官方订阅的在线验收，详细记录见工程
`reports/codex-provider-compatibility.md`。
