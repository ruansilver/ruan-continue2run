# DeepSeek Harness (dsh) Adapter

当前 dsh Adapter 使用官方 CLI 的 one-shot headless 应用创建下一个真实 Session。

## Launcher 选择

按以下顺序解析：

1. `RUAN_CONTINUE2RUN_DSH_BIN`（Maintenance 调试覆盖）；
2. PATH 中的 `dsh`。

## Runtime 参数

Adapter 只读取当前 Session 的 durable log：`DSH_SESSION_ID` 定位
`$DSH_HOME/sessions/<cwd-key>/<session-id>/session.v4.jsonl[.zstd]`，压缩日志用系统
`zstd -dc` 解压。不使用模型自述，也不把 profile 默认值当作 `observed`。

| Relay 字段 | dsh 观测来源 / 提交方式 |
|---|---|
| `working_directory` | `session` 事件的 `cwd` / 创建进程的 cwd |
| `model` | 最新 `request/header` 的 `header.config.model`，缺失时回退 `request/context.model` / overlay `agent-default-model.model` |
| `model_provider` | 同上 `config.provider` / overlay `agent-default-model.provider` |
| `thinking_depth` | `config.reasoningEffort` / overlay `agent-default-model.reasoningEffort` |
| `permission_mode` | 最新 `permission/preset` 的 `preset` / overlay `permission.defaultPreset` |
| `sandbox_mode` | 最新 `sandbox/mode` 的 `mode` / overlay `sandbox-policy.mode` |
| `approval_mode` | 最新 `approval/policy` 的 `policy` / overlay `approval.policy` |

`permission_mode` 为 `custom` / `auto` 这类保留名时不提交 `defaultPreset`，只提交
sandbox/approval 两个 knob，并验证派生出的 preset 与 expected 一致。

## 创建与确认

创建命令在 expected cwd 中运行，并脱离 Adapter 进程组：

```text
dsh --profile headless --patch <run-dir>/overlay.yml --json -
```

- Payload 通过私有 run dir 中的 stdin 文件传递，字节原样，不经过 shell 拼接；
- stdout/stderr 指向 run dir 文件，Adapter 增量读取 NDJSON 事件，因此 Adapter 退出后
  新 Session 继续运行，不会因为管道无人读取而阻塞；
- `session` 事件给出唯一 session id 与 cwd；`status.turn_start`/`step_start` 表示运行中；
  首个 `text`/`thinking`/`tool_call`/`tool_result` 表示真实开始执行；
- 随后读取新 Session 的 durable log，校验 effective 参数、Payload 逐字节一致，以及是否
  存在未决 `approval/asked`。

三者同时成立才返回 `confirmed`：稳定 session id、运行/执行事件、log 校验一致。
payload 校验不通过一律降级为 `unknown`，不用“看起来已经启动”替代证据。

## 环境前提

创建用的 `headless` profile 必须组合出与当前 Session 相同的 model route：
`$DSH_HOME/profiles/headless/cordis.patch.yml` 需要包含对应 provider 与 model 定义
（本机由主 profile 组合生成，见验收报告）。Preflight 用
`dsh --profile headless --dump-config` 验证该 route 存在，缺失时以
`DSH_MODEL_ROUTE_UNAVAILABLE` 失败，并给出要修改的文件路径。

## 错误码

- 创建前失败（确定没有创建 Session，`failed`，`retryable=false`）：
  `DSH_LAUNCHER_NOT_FOUND`、`DSH_CREATION_PROFILE_MISSING`、`DSH_SESSION_LOG_UNREADABLE`、
  `DSH_CWD_INVALID`、`DSH_RUNTIME_PARAMETER_UNAVAILABLE`、`DSH_SANDBOX_INVALID`、
  `DSH_APPROVAL_INVALID`、`DSH_MODEL_ROUTE_UNAVAILABLE`、`DSH_RUN_DIR_FAILED`、
  `DSH_LAUNCH_FAILED`；
- `DSH_SESSION_NOT_CREATED`（`failed`）：headless 进程已结束，且完整输出里没有任何
  `session` 事件，可以证明没有创建 Relay 会话；
- 其余一律 `unknown`：`DSH_STARTUP_TIMEOUT`、`DSH_CWD_MISMATCH`、`DSH_PAYLOAD_MISMATCH`、
  `DSH_EFFECTIVE_PARAMETER_MISMATCH`、`DSH_STARTUP_UNCONFIRMED`、
  `DSH_SESSION_LOG_UNREADABLE`（新 Session 日志在观察窗口内无法校验）、`APPROVAL_BLOCKED`。

## 当前验收边界

已在本机真实 DSH（web profile 当前会话 → headless 创建）上验证：

- 创建新的持久 Session，`session_reference` 稳定；
- Payload 逐字节到达新 Session（含中文、多行、引号、`$`、反引号、反斜杠），
  新 Session 再次进入本 Skill 并复算出相同的 `task_entry_sha256`；
- cwd/model/thinking/permission/sandbox/approval 提交值与新 Session 实际记录一致；
- Adapter 返回后新 Session 继续运行直至完成；
- 新 Session 出现在 Web GUI 的会话列表中并可打开查看内容（用户确认）。

仍未验证或属于环境条件：

- 非 Linux 平台、非 zstd 日志格式、无法读取 Session log 的部署；
- `permission_mode` 为 `custom`/`auto` 的会话（代码有分支，未做真实验收）；
- `approval_mode=ask` 的会话会阻塞在人工确认上，按契约返回 `unknown`，未做真实验收；
- 新 Session 是一次性 headless 运行，不是可继续对话的 Web 会话；它结束后由自身 Relay
  决定是否继续接力。
