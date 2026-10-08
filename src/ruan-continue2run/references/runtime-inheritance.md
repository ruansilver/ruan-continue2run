# Codex 运行参数继承时机（2026-10-08 修订）

用户可以在当前 session 中切换模型、思维深度及其他运行设置。下一 session 应继承当前
session 准备 handoff 时的实际状态，不能继承本轮开头固定的旧值。

本修订覆盖 archive/v2.0/design/design.md 中 Codex 的参数固定与漂移规则；历史档案不改写。
本次仅 Codex 声明 `PARAMETER_CAPTURE_PHASE = "handoff"`；未声明的 Adapter 默认为 `start`，
原 Preflight、supplied 例外和参数漂移检查保持原样。

## 开始时

`start` 解析显式调用、控制区和任务正文，规范化后保存正文、hash、Harness、cwd、精确日志
路径以及传入控制信息；校验 task hash、cwd 并检查 STOP。不调用 Codex runtime reader，
也不执行 Codex 参数 Preflight。日志里的传入参数是 Capture 记录，不是对下一会话的锁定。
传入 Payload 仍校验格式、字段集合、正文长度与 hash，schema_version 继续为 1。

## 接力时

`handoff --log <精确路径>` 检查结果、claim、STOP 和日志 task hash；取得一次性 claim 后：

1. 确认仍处于该日志的 cwd，保持项目与 STOP 的作用域。
2. 调用 Adapter 的 `read_runtime_context()`，读取这一刻实际运行的 model、thinking_depth、
   model_provider、permission_mode、sandbox_mode、approval_mode、working_directory。
3. 将可靠 observed / inherited 值构成新的 expected 快照；不从开头参数补齐，不让旧
   `supplied` 覆盖当前用户选择。读取失败则禁止创建，报告 unavailable。
4. 原子写入 `<log>.handoff.context.json`，包括采样时间、expected、observed 与原任务 hash；
   原日志头保留，便于对比开始记录和最终提交。
5. 校验该快照并执行 Adapter 只读 Preflight，再检查 STOP。
6. 使用快照生成下一会话控制区并提交创建；任务正文逐字保持不变。

权限等字段只继承 Harness 已观测到的用户选择，不由 Skill 自行升级。cwd 改变时明确失败，
避免将原项目日志及 STOP 静默搬到其他目录。

快照采样是一次 handoff 的明确边界。原生接口无法保证用户在采样之后再次改变设置时仍能
与创建操作原子同步，Skill 不宣称这种保证；也不在重试或重复调用时重新采样、发起另一份
Payload。`failed + retryable` 的一次安全重试保持原快照；已有结果直接返回，只有 claim
没有结果返回 unknown。旧 schema-1 Codex 日志同样在首次 handoff 时重新采样。

## 判断范围

当前会话中的合法模型/参数调整不再是 Codex 的 PARAMETER_DRIFT。新会话实际提交值仍须
与本次 handoff 快照一致；Adapter 读到的子会话 effective 值与快照不符仍不能 confirmed。
task hash、STOP、claim、三态及有限等待契约不变。

仅记录于开始阶段的 supplied 值不能证明 handoff 时仍是用户最终选择；Codex 读取失败时
不使用这类旧值兜底。官方 provider 和第三方 provider 使用相同的 handoff 采样时机。
