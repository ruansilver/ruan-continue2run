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
| `sandbox_mode` | permission profile 映射 / `thread/start.sandbox` |
| `approval_mode` | thread settings approval policy / `thread/start.approvalPolicy` |
| `permission_mode` | Desktop permission profile，用于期望值与报告；实际沙箱能力由 `sandbox_mode` 提交 |

缺少可靠来源时返回 `unavailable`，不使用模型自述或配置默认值冒充 observed。

## 创建与确认

Adapter worker 使用：

```text
initialize
→ initialized
→ thread/start
→ turn/start
→ turn/started 或 item/started 或 item/agentMessage/delta
```

只有观察到真实启动事件后才返回 `confirmed`。worker 脱离 Adapter/Relay 进程组继续持有
app-server 连接，并在 turn 完成后退出。创建失败、approval 阻塞、超时或无法观察启动事件
按 2.0 三态契约返回 `failed` 或 `unknown`。

## 当前验收边界

已在本机 Codex Desktop bundled app-server 上真实验证：

- 创建新的 Desktop thread；
- 传递完整 `ruan-continue2run` Payload；
- 观察到 `turn/started`；
- 新 thread 读取到完整任务并完成测试输出；
- 安装位置加载的是同步后的 2.0 Skill。

账户权限、网络、模型可用性和 UI 面板显示仍属于真实环境条件；无法读取或验证时必须报告，
不能静态宣称支持。
