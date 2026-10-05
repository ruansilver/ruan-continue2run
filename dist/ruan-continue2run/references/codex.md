# Codex Desktop Adapter

当前 Codex Desktop Adapter 使用本地 bundled CLI 的 app-server 协议。

## Launcher 选择

按以下顺序解析：

1. `RUAN_CONTINUE2RUN_CODEX_BIN`（Maintenance 调试覆盖）；
2. `CODEX_CLI_PATH`（Codex Desktop 注入的 bundled CLI）；
3. PATH 中的 `codex`。

不能默认使用 PATH 中的 CLI 覆盖当前 Desktop bundled CLI，因为两者可能属于不同版本。

## Runtime 参数

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

只有观察到真实启动事件后才返回 `confirmed`。worker 脱离 Adapter/Relay 进程组继续持有
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
