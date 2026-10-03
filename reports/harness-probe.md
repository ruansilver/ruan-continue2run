# Harness 只读探测报告（Codex，2026-10-03）

本报告只记录本机可验证的静态/只读证据。探测没有创建会话、发送 `thread/start` 或 `turn/start`，也没有修改 Codex 配置或任何正在生效的 Skill。

## 已确认的调用面

- PATH 中的 `codex` 为 `codex-cli 0.156.1`；当前 Codex Desktop 环境提供 `CODEX_CLI_PATH=/usr/lib/chatgpt/resources/codex`，该 bundled launcher 报告 `codex-cli 0.159.0-alpha.12.1`。因此 Adapter 必须优先使用 `CODEX_CLI_PATH`（或显式 launcher override），不能默认相信 PATH 中的 CLI。
- 当前进程确实带有 `CODEX_THREAD_ID` 和 `CODEX_SESSION_ID`。这证明在 Codex Desktop 会话内可以读取当前会话标识；不能推导出所有 Harness 都会提供同名环境变量。
- `codex app-server --stdio` 是可执行的 JSONL 协议入口。bundled CLI 的生成 schema（`codex app-server generate-json-schema --experimental`）确认：
  - `thread/read` 返回的 thread metadata 包含 `cwd`、`model`、`modelProvider`、`reasoningEffort`、`path` 等字段；其中 model/reasoning 的说明是“当前配置或最近持久化值”，不是逐 turn 执行遥测。
  - `thread/start` 接受 `cwd`、`model`、`modelProvider`、`approvalPolicy`、`sandbox`、`threadSource` 等字段。
  - `turn/start` 要求 `threadId` 和 `input`，并接受 `model`、`effort`、`approvalPolicy`、`sandboxPolicy`、`cwd` 等覆盖字段。
  - schema 中 `SandboxMode` 的枚举为 `read-only`、`workspace-write`、`danger-full-access`；approval policy 支持 `untrusted`、`on-request`、`never` 以及 granular 对象。

只读实测还启动了一个短生命周期的 `app-server --stdio` 进程，仅发送 `initialize` 后立即终止；收到正常 JSON 响应（`userAgent=Codex Desktop/0.159.0-alpha.12.1`）。没有发送任何会创建 thread/turn 的方法。

随后在同一只读连接上发送了当前 `CODEX_THREAD_ID` 的 `thread/read`（仍未创建或修改任何对象），成功读到 `cwd`、`model=gpt-6-astra`、`modelProvider=custom`、`reasoningEffort=high`、rollout `path`、`threadSource=subagent` 等字段。这验证了在本次 Codex Desktop 环境中，Python 子进程确实可以查询当前 thread 的基础 metadata；approval/sandbox 仍不在该 thread 对象的直接字段中。

只读解析该 thread 的 rollout 文件还看到 `thread_settings_applied` 记录包含 `approval_policy`、`active_permission_profile`、`permission_profile`、`reasoning_effort`、`model` 等键；本次记录的安全摘要为 approval `never`、active profile `:danger-full-access`。这说明 rollout settings 是本环境可用的参数来源，但 Adapter 必须把“记录存在且可解析”作为可靠性前提；缺失、解析失败或其他 Harness 没有等价记录时仍应报告 `unavailable`。

这些证据足以说明：Python Adapter 可以通过独立的 app-server 子进程发起“创建 thread + 启动首个 turn”的调用；v1 Demo 的 `codex_worker.py` 使用的协议方法和主要字段在当前 bundled schema 中仍有对应物。

## 不能由只读探测证明的事项

- 本轮没有真实调用 `thread/start`/`turn/start`，所以不能宣称当前账户、当前模型、权限或网络条件下真实新会话一定能创建并开始执行。2.0 的 `confirmed` 仍必须由真实 Harness 验收确认。
- `thread/read` 的 metadata 不直接给出一个稳定、逐会话生效的 approval/sandbox 观测值。旧 Demo 从 rollout 中寻找 `thread_settings_applied`，这属于启发式读取；字段缺失时必须报告 `unavailable`，不能用模型自述或 config 默认值冒充 `observed`。
- 当前 `codex doctor --json --summary` 显示 app-server daemon 未运行（ephemeral 模式）。这不妨碍 Adapter 直接启动 stdio app-server，但未验证 Desktop UI 是否会把该 stdio-created thread 显示为用户可见的独立会话。可见性必须在真实验收中检查。
- CLI help/schema 没有给出一个可由 Skill 普遍依赖的“session end/lifecycle hook 已触发”证明。虽然本机 feature 列表含 `hooks`，但具体 hook 注入环境、时序和失败语义仍需在真实 Harness 中探测；Adapter 不应假设 hook 一定存在。
- 当前环境的 `CODEX_THREAD_ID` 等变量只证明本次 Codex Desktop 运行注入了标识，不能扩展为 DeepSeek、Claude Code 或其他 Harness 的通用契约。

## 对 2.0 契约的直接影响

1. Codex Adapter 可声明 `working_directory`、`model`、`thinking`（reasoning effort）为适用参数，并从当前 thread metadata 读取候选 `observed` 值；approval/sandbox 必须以有明确来源的 thread settings/协议字段为准。适用字段无法可靠读取时通常为 `unavailable` 并触发 Preflight 失败；只有 RelayContext 的 expected 明确为用户 `supplied` 且 Adapter 能证明会按该值提交时，才适用设计中的例外。
2. Payload 中的 `expected` 必须与当前读取到的 `observed` 分开保存和比较。不能因为 `thread/read` 返回了当前配置值就覆盖上一轮 expected；schema 已明确这些字段不是逐 turn telemetry。
3. 外层 hard deadline 仍是必要的。旧 Demo 的 `create()` 只等待 status 文件约 10 秒，并在超时后尝试 terminate worker；这不是可靠的进程组终止、也没有把“请求可能已发出”统一映射为 `unknown`。2.0 实现需要在 Relay 层明确 deadline、进程组终止和 unknown 不重试。
4. 旧 Demo 在 status 写入 `issued=true` 后立即返回成功，notes 还写着“这不表示新会话已启动”。这与 2.0 的 `confirmed` 定义冲突；必须增加 startup observation window，并只在观察到新 turn 开始正常执行时返回 `confirmed`，否则按约定返回 `unknown`/`failed`。
5. app-server thread 是独立 ID，协议上支持独立创建；但“旧 session 结束后新 session 继续运行”和“UI 可见”不能靠静态 schema 证明，必须列为真实 Harness 验收项。

## 结论

当前 Codex app-server 协议为 2.0 Codex Adapter 提供了可实现的独立会话创建基础，未发现阻止实现的协议级硬阻碍。尚未证明的部分集中在真实账户调用、startup 观察、UI 可见性、hook 时序及权限元数据的可靠观测；这些不能以静态 CLI 探测替代，必须在 Maintenance 的真实 Harness 验收阶段验证。报告没有为未验证能力虚构“支持”。
