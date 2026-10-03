---
name: ruan-continue2run
description: 仅显式触发的轻量会话接力 Skill。只有用户消息首行明确写出 `ruan-continue2run`（可带 `$`、`@` 或 `/` 前缀）时才使用；Relay Mode 在当前 session 即将结束时创建并确认下一个真实 session，Maintenance Mode 用于用户明确指定 Harness 后检查、创建或修复其 Adapter。不要因为“接着做”“继续”“接力”等词自行触发。
---

# ruan-continue2run

本 Skill 只负责“当前 session → 下一 session”的可靠接力，不判断业务任务是否完成，也不因为 AI 觉得任务做完而停止。用户通过 cwd 级 `.ruan-continue2run/STOP`、Harness 自身中断，或不再显式启动 Relay 来停止。

设计权威来源是同包 `references/` 文档和 `archive/v2.0/design/design.md`。不要发明新的轮次、链状态、任务状态机、后台巡检、并发协调、版本仲裁、自动恢复或动态 Payload 机制。

## 选择模式

读取用户消息首行：

- `ruan-continue2run`（可选 `$` / `@` / `/` 前缀）进入 Relay Mode；
- `ruan-continue2run maintenance <harness>`，或用户明确要求检查、创建、修复某个 Harness Adapter，进入 Maintenance Mode；先读 `references/maintenance.md`；
- 其他情况不使用本 Skill。

## Relay Mode

### Capture 与启动

把用户首条消息完整写入 UTF-8 临时文件，再运行：

```text
python3 <skill_dir>/scripts/relay.py start --message-file <临时文件>
```

如 Harness 无法自动检测，可以使用 `--harness <name>`。只有用户明确提供而 Harness 无法读取的运行参数，才使用 `--set key=value`。

脚本会：

1. 只解析规定位置的 Skill/Harness/Control Metadata；
2. 第一次 Capture 规范化为 UTF-8、LF、无 BOM，正文不 trim；
3. 计算并保存 `task_entry_sha256`；
4. 读取当前 Harness 观测值，构造 expected 参数；
5. 把 expected 与 observed 分开落盘；
6. 检查 cwd 级 STOP 和所有适用参数；
7. 执行纯读取 Preflight。

Preflight 失败时不要执行长期任务，原样报告 `error_code` 和 `error_summary`，让用户另开 Maintenance。适用参数没有可靠 expected 值、Adapter 不能证明 supplied 值会被提交、参数发生漂移或 task entry hash 不一致时失败；observed 为 `unavailable` 只有在 supplied 例外成立时才可继续。

如果启动时发现 STOP，告诉用户“已检测到 STOP，本轮继续执行，但结束后不会继续接力”，然后执行当前任务并跳过 handoff。STOP 是当前 cwd 所有 Relay 共用的人工刹车；Maintenance 不得删除原本就存在的 STOP。

### 放养执行

因任务方向选择原本要询问用户时，优先读取需求、设计、源码和持久资料，自行选择与既有设计一致且长期价值更高的方向继续。权限、sandbox、approval、真实工具故障仍按 Harness 边界处理，不升级权限。

重大决策可通过：

```text
python3 <skill_dir>/scripts/relay.py note --log <log_path> --text <一句话>
```

`note` 只记录短摘要，脚本会限制长度并脱敏。长期项目事实写入项目自己的正式文档。

### Handoff

在当前 session 即将返回最终结果或触发生命周期结束之前执行：

```text
python3 <skill_dir>/scripts/relay.py handoff --log <log_path>
```

必须使用 `start` 返回的精确 `log_path`，不能搜索“最新日志”。脚本会：

1. 再次检查 STOP；
2. 从当前日志重新读取 RelayContext；
3. 在任何 Adapter 副作用前原子创建一次 handoff claim；
4. 已有最终结果则直接返回；只有 claim 没有结果则返回 `unknown`，不得再次创建；
5. 在独立 Adapter 调用进程上施加 hard deadline；超时记为 `unknown`；
6. 处理 `confirmed`、`failed`、`unknown` 和唯一一次安全重试；
7. 将最终 AdapterResult 原子写回当前日志 sidecar。

结果处理：

- `confirmed`：只表示在 startup observation window 内已确认新 session 开始正常执行，不代表之后永不崩溃；当前 session 不再修改任务，直接结束。
- `stopped_by_stop`：正常结束，不创建下一 session。
- `failed` / `unknown`：不要现场维修或自行再次调用 handoff；如实报告 `error_code`、参数、hash、session reference 和尝试结果，建议另开 Maintenance。`unknown` 时新 session 可能已经存在。

## 文件

- `scripts/relay.py`：Capture、参数校验、日志、STOP、Preflight、handoff claim、deadline、三态结果和安全重试；
- `scripts/detect.py`：Harness 检测与 Adapter 加载；
- `scripts/adapter_runner.py`：在独立进程中执行 Adapter 创建调用；
- `scripts/adapters/`：每个 Harness 一个 Adapter；`_template.py` 是骨架，未真实验收的 Adapter 不得宣称支持；
- `scripts/sync.py`：仅 Maintenance 使用，dry-run/整体同步/路径安全检查/完整性校验；
- `references/adapter-contract.md`：参数状态、AdapterResult、错误码和生命周期契约；
- `references/startup-confirmation.md`：三态、确认窗口、claim、deadline 和重试；
- `references/log-format.md`：RelayContext、task hash、原子日志与 STOP 作用域；
- `references/maintenance.md`：真实复现、STOP 所有权、最终减法、真实复验和 authoritative copy 同步；
- `references/codex.md`：Codex Desktop bundled app-server Adapter 的参数映射、启动确认和验收边界。
