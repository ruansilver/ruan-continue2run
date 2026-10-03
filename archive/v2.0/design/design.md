# ruan-continue2run 2.0 Final Skill 设计文档

## 0. 文档用途

本文档定义 `ruan-continue2run 2.0` 的最终设计，作为后续 Skills Creator 的实现依据。

本文档是权威来源。Demo、`adapter-contract.md`、`startup-confirmation.md`、
`log-format.md`、`maintenance.md` 以及最终 `src/` / `dist/` Skill 包都必须服从本文档。

后续流程：

1. Claude 对本文档做最后一次对账；
2. 重点检查是否存在遗漏、内部矛盾或无法实现的约束；
3. 若无新的实质问题，不再继续扩展设计；
4. 使用 Skills Creator 按本文档创建 Skill Demo；
5. 在用户真实 Harness 和真实运行环境中完成 Adapter 探测、实现和验收。

本文档定义行为和契约，不预先虚构 Codex、DeepSeek Harness、Claude Code 等当前版本的具体 CLI、API 或内部实现。

# 1. 定义

`ruan-continue2run` 是一个**仅显式触发的轻量会话接力 Skill**。

正常 Relay：

> 当前会话执行任务
> → 创建下一真实会话
> → 确认下一会话真正接过任务并开始运行
> → 当前会话结束职责。

Adapter 失效：

> 用户显式进入 Maintenance
> → 真实复现
> → 修改当前实际生效 Skill
> → 真实测试
> → 修复
> → 最终减法
> → 再次完整真实验收
> → 当前版本成为权威源
> → 整体同步其他已知安装位置。

核心思想：

> **平时极轻，坏了可修。**

# 2. 核心原则

## 2.1 不判断业务任务是否完成

Skill 不判断：

- 项目是不是已经完成；
- 当前是否“没有实质工作”；
- 是否应该自动停止整个长期任务。

AI 不得因为自己认为任务完成而终止 Relay。

Relay 的停止由用户控制。

## 2.2 不做重机制

明确不引入：

- 任务状态机；
- 接力轮次管理；
- 链状态管理；
- 自动完成判断；
- Adapter 版本系统；
- Maintenance Log；
- 并发锁；
- 自动冲突合并；
- 后台巡检；
- 定期健康检查；
- 自动 Adapter 升级；
- 动态 Payload 更新；
- 多副本版本比较；
- 自动回滚体系。

## 2.3 使用约定优先

假定：

- 一个长期 Relay 的核心任务入口保持稳定；
- Relay 中途不修改长期核心 Prompt；
- Maintenance 串行执行；
- 不同时维护同一个 Skill；
- 用户明确区分 Relay 与 Maintenance；
- 长期依赖的信息存在于下一轮仍可访问的文件、源码或持久文档中；
- 同一 `working directory` 下的 Relay 共用一个 STOP，需要独立刹车的 Relay 不在同一 cwd 并行运行。

Maintenance 默认串行执行，测试期间同一 cwd 不运行正式 Relay。能通过明确使用约定解决的问题，不额外增加机制。

## 2.4 真实运行优先

以下均不能单独证明 Adapter 可用：

- 静态代码正确；
- 命令返回 0；
- 成功取得 session id；
- 创建了空会话；
- AI 判断“理论上可以”。

Adapter 必须经过：

> **真实 Harness + 真实会话创建 + 真实任务启动。**

## 2.5 放养不取消安全边界

放养模式只取消：

> 因任务方向选择而停下来询问用户。

不取消：

- Harness 权限；
- sandbox；
- approval policy；
- 系统安全限制。

Relay 不得为了无人值守自动升级权限。

# 3. 两种模式

## 3.1 Relay Mode

仅通过显式调用进入。

用于正常无人值守接力。

## 3.2 Adapter Maintenance Mode

用户必须明确：

- 当前 Harness；
- 要检查、维护、创建或修复当前 Adapter。

Maintenance 可以持续接收用户后续提供的：

- 报错；
- 路径；
- 环境信息；
- 测试结果；
- 其他线索。

Relay 的“稳定第一条任务入口”规则不限制 Maintenance 会话。

# 4. Relay Payload 与 RelayContext

## 4.1 Payload 组成

Relay Payload 只包含三部分。

### 稳定 task entry

即第一轮用户提供的长期任务入口。

### `ruan-continue2run` 显式调用

确保下一会话再次进入 Relay Mode。

### Control Metadata

至少包括：

- Harness；
- model；
- thinking depth；
- working directory；
- permission mode；
- sandbox mode；
- approval mode；
- 当前 Harness 必须继承的其他基础运行参数。

## 4.2 Control Metadata 与 task entry 分离

运行参数属于控制信息，不属于任务正文。

不得通过不断向 task entry 拼接：

```
model=...
cwd=...
thinking=...
```

来传递运行参数。

逻辑结构应始终是：

```
Control Metadata
+
Stable Task Entry
+
Explicit Skill Invocation
```

task entry 在整个 Relay 链中保持稳定。

Payload 的控制区使用 `param.<field>.state` / `param.<field>.value` 记录 expected 参数，
并使用长度定界的：

```text
<<<relay-task-entry bytes=N>>>
<正文原文，恰好 N 个 UTF-8 字节>
<<<relay-task-end>>>
```

区块外的换行不属于正文，保证正文可以以空白或任意字符结尾。

## 4.3 不传递

不进入 Payload：

- 历史聊天上下文；
- 当前任务进度；
- 接力轮次；
- 链状态；
- 上一轮中间结果；
- Maintenance 信息；
- 临时反馈。

## 4.4 运行参数来源

Adapter 优先从 Harness/session metadata 读取真实参数。

每个逻辑运行参数都要区分以下状态：

```
observed
supplied
inherited
unavailable
not_applicable
```

含义是：

- `observed`：从当前 Harness/session 实际读取；
- `supplied`：用户显式提供；
- `inherited`：Harness 有可靠的原生继承语义；
- `unavailable`：当前 Harness 应该支持，但无法可靠取得；
- `not_applicable`：该参数对当前 Harness 根本不适用。

`working_directory` 是本 Skill 的核心必需参数。其他参数由 Adapter 声明是否适用。
适用参数必须有可靠 expected 值，否则 Preflight 失败；`not_applicable` 不得与 `unavailable` 混用。当前 observed 为 `unavailable` 时，只有 expected 来自 `supplied` 且 Adapter 能证明会按该显式值提交，才可继续；否则 Preflight 失败。

Payload 中携带的是上一轮已经确定的 `expected` 参数。下一轮当前 Harness 读取到的是
`observed` 参数，只用于验证，不能覆盖 `expected`。如果某个适用参数的 `observed`
与 `expected` 不一致，Preflight 失败并报告参数漂移。

如果 Harness 无法读取当前值，但用户仍要显式提供：

> 由用户在 Relay 控制信息中显式提供。

既不适用参数也没有 `not_applicable` 声明，或适用参数最终为 `unavailable`：

> Preflight 失败，不猜。

模型自述不能作为 model、thinking 或权限参数的可靠来源。

# 5. Capture

Capture 必须由确定性代码实现，而不是完全依赖模型自由解析。

## 5.1 结构化解析

不得全文 replace：

```
ruan-continue2run
//Codex
```

等字符串。

只解析规定位置上的控制标记。

任务正文中出现相同文字时必须原样保留。

## 5.2 幂等要求

经过：

> Capture → 生成下一 Payload → 下一轮 Capture

以后：

- Skill 标记不能累积；
- Harness 标记不能累积；
- task entry 内容不得漂移。

经过任意轮次，稳定 task entry 应保持一致。

第一次 Capture 只做一次规范化：输入按 UTF-8、LF、无 BOM 处理；只删除结构上明确
属于 Skill / Harness Control Metadata 的区域，正文不 trim，不改动正文空白、中文、
引号、`$`、反引号或反斜杠。Capture 后计算 `task_entry_sha256`。

后续 Payload 同时携带规范化后的 task entry 与 hash。下一轮 Capture 重新计算 hash；
不一致时 Preflight 失败并报告，不允许模型自动修正任务正文。

为避免末尾换行改变 hash，Payload 使用长度定界的 task-entry 区块。区块外的分隔换行
不属于正文，正文可以以任意字符结尾。

## 5.3 Harness 选择

优先级：

> 用户显式指定 > 自动检测。

未显式指定时才执行 detect。

结果：

- 1 个匹配：使用；
- 0 个匹配：停止并报告；
- 多个匹配：停止并报告，不猜。

# 6. 确定性 Relay 编排

增加：

```
scripts/relay.*
```

具体语言由 Skills Creator 决定。

关键 Relay 控制逻辑尽量由 `relay.*` 执行，而不是依赖模型记忆。

它至少负责：

- Capture；
- Payload 幂等处理；
- RelayContext 落盘；
- STOP 检查；
- Preflight 调用；
- handoff 前重新读取 RelayContext；
- 调用 Adapter；
- `confirmed / failed / unknown` 分支；
- 安全重试；
- Adapter 结果写回当前日志；
- 在任何 Adapter 副作用之前，对当前 `current_log_path` 原子取得一次 handoff claim；
  同一日志已有最终结果时直接返回，只有 claim 而没有最终结果时保守返回 `unknown`，
  不得再次调用 Adapter；
- 对 `create_and_confirm` 施加 Relay 层 hard deadline。超时终止当前 Adapter 调用进程
  并记为 `unknown`，不得重试。

Harness 特有逻辑仍然属于 Adapter。

# 7. Relay 日志

## 7.1 运行目录

建议：

```
.ruan-continue2run/
├── STOP
└── relay/
```

位于当前 working directory。

## 7.2 每会话一个日志

每个 Relay 会话创建独立日志文件。

不同会话不共同追加同一文件。

## 7.3 RelayContext 落盘

Capture 完成后立即写入当前日志头。

白名单字段例如：

```
task_entry
task_entry_sha256
harness
model
thinking_depth
working_directory
permission_mode
sandbox_mode
approval_mode
slug
schema_version
parameter_expectations
parameter_observations
```

`parameter_expectations` 保存本轮要继承的期望参数及其状态；
`parameter_observations` 保存当前 Harness 的观测结果。两者不能互相覆盖。

RelayContext 使用轻量 `schema_version`。这不是 Adapter 版本系统，只用于识别日志数据结构。

收尾不得依赖模型记忆重新构造 RelayContext。

必须重新读取已落盘数据。

## 7.4 当前日志路径必须提前锁定

在创建下一会话**之前**确定本会话的具体日志路径。

例如：

```
current_log_path = ...
```

随后整个 handoff 都使用这个明确路径。

Adapter 创建新会话以后：

> **不得重新搜索“同 slug 最新日志”来决定写到哪里。**

因为此时新会话可能已经创建了自己的更新日志。

这是文件级确定性约束，不需要锁或共享链状态。

## 7.5 slug

使用：

> 短可读前缀 + 稳定 task entry 的短哈希。

避免中文、长 Prompt、相似任务和多轮漂移问题。

## 7.6 日志内容

除 RelayContext 外，仅记录：

- 关键决策；
- 必要依据；
- 接力意图；
- Adapter 状态；
- session reference；
- 是否发生唯一一次安全重试；
- 必要错误摘要。

不记录：

- 项目完整进度；
- 接力轮次状态；
- Maintenance 历史；
- 下一轮工作状态。

项目真实状态仍以：

> 当前源码 + 正式需求/设计文档

为主。

# 8. STOP：人工刹车

文件：

```
.ruan-continue2run/STOP
```

只表示：

> **用户要求禁止创建下一 Relay 会话。**

## 8.1 STOP 不停止当前已启动会话

STOP 只拦：

> 下一次 handoff。

如果一个 Relay 会话已经启动：

- 当前任务仍正常执行；
- 本轮结束时不创建下一会话。

启动时发现 STOP，应明确告诉用户：

> 已检测到 STOP，本轮继续执行，但结束后不会继续接力。

如果用户希望立刻停止当前正在运行的会话：

> 使用 Harness 自身的中断/停止功能。

## 8.2 检查时机

至少：

1. Capture 后检查一次；
2. handoff 前再次检查一次。

Skill 不得自行因为“觉得任务做完了”而创建 STOP。

STOP 是 cwd 级共同刹车，不按 slug 区分 Relay。检查与 Adapter 创建之间仍存在正常文件竞态窗口；
使用约定负责避免并发 Relay，Skill 不增加锁或并发协调机制。

# 9. Preflight

如果启动时不存在 STOP、意味着本轮预计还需要继续 Relay，则在正式任务执行前做一次无副作用 Preflight。

至少检查：

- Adapter 是否存在；
- Harness executable/API 是否可访问；
- 必需运行参数是否已获得；
- Adapter 是否具备基本创建条件；
- task entry hash 是否与上一轮携带的 hash 一致；
- 所有适用参数是否有可靠期望值；
- 当前 Harness 的 observed 参数是否与 expected 参数一致。

Preflight：

- 不创建会话；
- 不维修 Adapter；
- 不属于后台巡检。

Preflight 明确失败：

> **本轮不进入放养任务执行。**

立即报告问题并结束，让用户进入 Maintenance。

避免先运行数小时，最后才发现下一跳必然无法创建。

# 10. Relay Override

正常执行时：

- 当前处于自动接力模式；
- 因“任务方向选择”原本需要询问用户时不暂停；
- 优先读取需求、设计、源码和相关资料；
- 结合当前阶段选择长期价值更高且与既有设计一致的方向；
- 基础设施、权限和真实工具故障仍可失败。

重大决策可以记录到 Relay 日志。

长期项目事实优先写入项目自己的正式文档。

# 11. Handoff 时机

不使用：

- 固定分钟数；
- 固定步骤数；
- 固定轮数；

切换会话。

优先使用 Harness 提供的：

- lifecycle hook；
- session end hook；
- 其他可靠收尾机制。

若 Harness 没有，则要求模型：

> 当前会话准备返回最终结果或结束生命周期之前执行 handoff。

这不是业务完成判断，只表示当前 session 即将结束。

# 12. Adapter Contract

Adapter 负责所有 Harness 差异。

建议稳定接口：

```
detect()
read_runtime_context()
preflight()
create_and_confirm(relay_context)
```

Adapter 必须声明每个逻辑运行参数是否适用，并提供有限的 handoff deadline。参数状态
和 AdapterResult 的结构见 `references/adapter-contract.md`。

## 12.1 `read_runtime_context`

负责读取当前 Harness/session 的观测值：

- model；
- thinking；
- cwd；
- permission；
- sandbox；
- approval；
- 其他 Harness 基础参数。

每个字段返回 `{state, value}`，其中 `state` 为 `observed`、`inherited`、`unavailable`
或 `not_applicable`。`supplied` 只用于期望值，不由本函数伪造。

无法读取的适用字段返回：

```
unavailable
```

## 12.2 `preflight`

纯读取、无创建副作用。

## 12.3 `create_and_confirm`

负责：

> 创建会话 + Startup Confirmation。

AdapterResult 建议结构：

```json
{
  "schema_version": 1,
  "status": "confirmed | failed | unknown",
  "retryable": false,
  "session_reference": "",
  "submitted_parameters": {},
  "effective_parameters": {},
  "startup_evidence": {},
  "error_code": "",
  "error_summary": ""
}
```

结果必须带轻量 `schema_version` 和结构化 `error_code`。`confirmed` 只表示在 startup
observation window 内已经确认新会话开始正常执行，不承诺其之后永不崩溃。

`retryable` 默认 `false`。

# 13. Payload 安全传递

真实任务可能包含：

- 中文；
- 多行；
- 单引号；
- 双引号；
- `$`；
- 反引号；
- 反斜杠；
- 其他 shell 特殊字符。

Adapter 应优先使用：

- Harness API；
- argv；
- stdin；
- 安全临时文件；
- 其他结构化输入。

不得使用未经可靠 escaping 的 shell 字符串直接拼接完整 Prompt。

# 14. Startup Confirmation

只有满足三个核心条件，才能返回：

```
confirmed
```

## 14.1 唯一会话身份

获得：

- session id；
- conversation id；
- thread id；
- 或 Harness 等价稳定标识。

## 14.2 提交参数正确

实际提交的：

- Relay Payload；
- cwd；
- model；
- thinking；
- permission/sandbox/approval；
- 其他要求继承的参数；

与 RelayContext 中的 expected 参数一致。当前会话 observed 参数只能用来验证，不能覆盖 expected。

## 14.3 已真实开始执行

获得真实运行证据，例如：

- assistant execution event；
- runtime event；
- tool event；
- running/started 状态；
- Harness 等价证据。

且没有立即出现：

- permission failure；
- model unavailable；
- invalid parameter；
- connection failure；
- Harness startup failure；
- 其他 fatal error。

## 14.4 Effective 参数

如果 Harness 能读取新会话真实生效参数，应验证。

如果不能读取：

```
effective = unavailable
```

不得假装已经验证。

单纯无法读取某项 effective metadata，不自动等于 Relay 失败。

## 14.5 Approval 阻塞

如果会话停在：

- approval；
- permission confirmation；
- sandbox confirmation；
- 其他等待人工确认状态；

不能算 `confirmed`。

返回：

```
unknown
```

并说明原因。

## 14.6 有限观察窗口

每个 Adapter 根据实际 Harness 定义：

- startup timeout；
- observation window。

超过有限时间仍无法确认：

```
unknown
```

不能无限等待。

# 15. 新会话生命周期独立

新 Relay 会话必须脱离：

- Adapter 调用生命周期；
- 创建它的临时子进程；
- 旧会话进程组；
- 旧会话退出时会被统一回收的执行环境。

Adapter 返回、创建者退出以后：

> 新会话仍必须独立继续运行。

这是 Adapter 的硬契约。

# 16. 三态结果与安全重试

默认原则：

> **无法严格证明失败，就使用** `**unknown**`**。**

## 16.1 `confirmed`

在 startup observation window 内确认新会话已经开始正常执行。

当前旧会话完成 handoff 职责；这不承诺新会话之后永不崩溃，也不代表业务任务已完成。

## 16.2 `unknown`

例如：

- 请求已经发出但超时；
- session 可能已经创建；
- 返回信息不足；
- 卡在 approval；
- 无法确认副作用。

`unknown`：

> 禁止自动重试。

避免创建重复会话。

## 16.3 `failed`

只有能够证明：

> 本次没有成功创建新 Relay 会话

才能返回。

每个 Adapter 维护经过真实验证的 `failed` 白名单。

## 16.4 `retryable`

即使已经 `failed`，默认也：

```
retryable = false
```

只有某个错误经过真实验证同时满足：

1. 可以确定没有创建 session；
2. 错误具有偶发性；
3. 原样重试有现实意义；

才能：

```
failed + retryable=true
```

## 16.5 唯一一次机械重试

只有：

```
status = failed
retryable = true
```

才允许原样机械重试一次。

必须使用同一个 RelayContext。

不得修改：

- task entry；
- model；
- thinking；
- cwd；
- permission；
- sandbox；
- approval。

第二次只要不是 `confirmed`：

> 停止。

不存在第三次自动重试。

如果当前日志已经存在 handoff claim：

- 已有最终结果：直接返回已有结果；
- 只有 claim、没有最终结果：返回 `unknown`，不得再次调用 Adapter。

claim 必须在第一次调用 Adapter 之前原子创建。

# 17. Relay 失败

失败后旧会话不得现场进入复杂 Maintenance。

只输出必要信息，例如：

```
Harness
Adapter
stage
status
error_code
session_reference
model
thinking
cwd
permission
sandbox_mode
approval_mode
task_entry_sha256
payload_sha256
submitted_parameters
effective_parameters
error_summary
attempt result
```

用户之后另开 Maintenance 会话。

如果错误信息丢失：

> Maintenance 重新真实复现。

## 17.1 Secret 保护

日志使用字段白名单。

不得整体 dump：

- 环境变量；
- headers；
- credentials；
- 完整认证上下文。

`error_summary` 只保留排查所需片段，并对明显认证 Secret 脱敏。

至少不得直接保存：

- API key；
- access token；
- 其他认证密钥。

用户也不应把认证密钥直接写入长期 task entry。

日志写入优先采用同目录临时文件 + `atomic replace`，并尽量将权限限制为当前用户。
`note` 与 `error_summary` 都要做长度限制和明显 Secret 脱敏。

# 18. Adapter Maintenance

目标：

> 把当前 Harness 的真实 Relay 链重新跑通。

不是：

> 把代码改到“看起来应该好了”。

## 18.1 直接修改当前实际生效 Skill

不使用：

- stable copy；
- candidate copy；
- 临时维修副本；
- 自动切换；
- 自动回滚。

Maintenance 直接修改：

> 当前真实 Harness 实际会加载的 Skill。

保证：

> **修改的就是测试的。**

## 18.2 Adapter 不存在

如果当前 Harness 没有 Adapter：

> 可以直接创建新的 Adapter。

如果发现必须修改：

- 公共 Adapter Contract；
- Relay 核心行为；
- 同时影响多个 Harness 的共享逻辑；

则停止 Maintenance。

这已经属于 Skill 设计修改。

## 18.3 修改边界

Maintenance 仅修改：

- 当前 Harness Adapter；
- 当前 Harness 私有的必要辅助实现。

Harness Adapter 应尽量自包含。

一次 Codex Maintenance 不应偷偷改变其他 Harness 行为。

# 19. Maintenance 真实验收

必须走生产路径：

```
当前正式 Skill
→ 当前 Adapter
→ 当前真实 Harness
→ 创建真实新会话
```

不能只 mock 或测试局部函数。

## 19.1 使用真实 Skill 调用

测试 Payload 必须真正包含：

```
ruan-continue2run
```

证明不仅能创建 session，还能让下一 session 再次进入本 Skill。

## 19.2 Maintenance 测试使用 STOP

测试前由 Maintenance 检查测试 cwd 是否已有 STOP。只有原本不存在时，Maintenance
才创建 STOP，并在验收结束后删除自己创建的 STOP；如果原本已存在，验收结束后不得
删除用户原来的 STOP。

测试会话：

1. 真正启动；
2. 真正进入 `ruan-continue2run`；
3. 识别 STOP；
4. 继续执行当前验收任务；
5. 收尾时不再创建下一 Relay。

验收完成后：

> Maintenance 删除自己创建的 STOP。

如果 Maintenance 意外中断导致 STOP 残留：

> 下一 Relay 必须明确提示检测到 STOP，不允许静默停链。

## 19.3 验收 Prompt

测试内容应简单、无副作用且容易辨认，例如：

> 输出当前工作目录，然后讲一个笑话。

同时故意包含：

- 中文；
- 换行；
- `'`；
- `"`；
- `$`；
- 反引号；
- `\`；

验证复杂 task entry 能够完整传递。

## 19.4 使用真实权限条件

不得通过更高权限 Maintenance 环境证明普通 Relay 可用。

尽量使用正常 Relay 对应的：

- cwd；
- model；
- thinking；
- permission；
- sandbox；
- approval。

## 19.5 生命周期验收

至少验证一次：

> Adapter 创建动作已经返回 / 创建者临时进程已经退出以后，新测试会话仍能继续执行并完成验收任务。

## 19.6 用户可见性确认

机器侧通过后，Maintenance 可以询问用户：

> 刚刚创建的测试会话应该输出当前目录并讲一个笑话，你是否能够看到？

用户确认后完成 UI 层验收。

如果用户发现异常：

> 继续维修。

# 20. 最终减法

首次跑通以后不能直接交付。

必须清理：

- debug 输出；
- 临时测试入口；
- 废弃分支；
- 无用兼容逻辑；
- 中间错误尝试；
- 临时定位机制；
- 已经没有价值的兜底。

最终版本只保留：

> 当前正确运行真正需要的实现。

# 21. 减法后再次真实验收

最终减法已经修改代码。

因此必须：

> 最终减法
> → 再走一次完整真实 Harness 验收。

最终版本至少重新确认：

- Payload 保真；
- Startup Confirmation；
- Skill 再触发；
- 权限条件；
- 生命周期独立；
- 用户能够看到测试会话。

只有这一次最终验收通过：

> Maintenance 才算成功。

# 22. Authoritative Copy 与同步

最终真实验收通过的当前 Skill：

> **成为本次唯一 authoritative copy。**

不比较：

- 文件时间；
- 谁更新；
- Adapter 版本；
- 历史副本状态。

## 22.1 整体覆盖

使用 authoritative copy 整体覆盖其他已知安装位置。

包括：

```
SKILL.md
scripts/
references/
其他正式 Skill 文件
```

不包括：

- `.ruan-continue2run/`；
- Relay 日志；
- STOP；
- Maintenance 会话内容；
- 缓存；
- Harness 运行数据。

## 22.2 安装位置发现

只检查明确已知位置：

- 当前 Harness Skill root；
- 全局/用户 Skill root；
- 当前项目 Skill root；
- Harness 配置暴露的位置；
- 用户补充路径。

不做全盘扫描。

## 22.3 路径去重

使用 realpath / symlink 解析去重。

避免：

- 同一个实际目录重复覆盖；
- authoritative copy 覆盖自己。

这只是路径处理，不引入软链接安装架构。

## 22.4 整体覆盖后的完整性校验

每个同步成功目标必须做文件级一致性校验。

例如使用：

- 文件列表；
- 内容哈希；
- 等价 recursive comparison。

确认正式 Skill 包与 authoritative copy 一致。

这不是版本比较。

只是验证：

> **刚刚执行的整体复制确实成功。**

覆盖前可以生成 dry-run manifest，明确哪些正式 Skill 文件将被替换或删除。source / target
必须是合法 `ruan-continue2run` Skill root，不能是同一 realpath，也不能存在危险的父子目录覆盖关系。

如果某个目录复制或校验失败：

> 明确报告该目录失败。

不自动回滚其他已经成功同步的位置。

## 22.5 正式 Skill 包不能保存安装位置私有配置

因为 Maintenance 成功后会整体覆盖安装副本，所以正式 Skill 包内部不得保存：

> 某一个安装位置独有且不能被覆盖的可变配置。

Harness/安装位置相关信息应：

- 动态探测；
- 从 Harness 配置读取；
- 或存放在 Skill 正式包之外。

否则整体覆盖策略本身无法成立。

# 23. Git 本地忽略

`.ruan-continue2run/` 属于本地运行数据，不应进入项目版本库。

若 cwd 属于 Git 仓库，应确保其被本地忽略。

优先使用：

```
.git/info/exclude
```

避免为了 Skill 运行数据修改项目正式 `.gitignore`。

环境不适用时可采用等价方式。

# 24. 意外断链恢复

如果因为：

- Harness 崩溃；
- 会话异常退出；
- 上下文极限；
- 模型没有执行最终 handoff；

导致链条中断，不设计自动恢复系统。

用户可以读取最近 Relay 日志头里的：

- task entry；
- Harness；
- 运行参数；

重新显式启动 `ruan-continue2run`。

# 25. 已知使用约定

## Relay 不会因任务“做完了”自动停止

这是刻意设计。

无人值守运行前，用户需要自己考虑 Harness 的：

- 使用额度；
- 成本；
- 长时间运行影响。

真正停止方式为：

- STOP；
- Harness 手动终止；
- 不再显式继续 Relay。

额度耗尽只是外部故障，不属于正常停止协议。

## STOP 不是立即 kill

STOP 只禁止下一跳。

需要立刻停止当前 session：

> 使用 Harness 自身停止能力。

## Maintenance 验收期间同 cwd 不运行正式 Relay

因为 Maintenance 会临时使用 cwd 级 STOP。

采用串行使用约定，不增加锁。

## 临时聊天附件不能作为长期唯一输入

需要长期使用的材料必须进入：

- 项目文件；
- 持久路径；
- 或 task entry 自包含文本。

## 同步失败必须显式处理

如果 Maintenance 最终报告某些副本没有成功同步：

> 用户应在下一次依赖这些安装位置之前处理。

Skill 不建立后台同步重试系统。

# 26. 推荐目录结构

```
ruan-continue2run/
├── SKILL.md
│
├── scripts/
│   ├── relay.*
│   ├── detect.*
│   └── adapters/
│       ├── codex.*
│       ├── deepseek-harness.*
│       └── ...
│
└── references/
    ├── adapter-contract.md
    ├── startup-confirmation.md
    ├── maintenance.md
    └── log-format.md
```

## `SKILL.md`

只保留高层行为：

- 显式触发；
- Relay / Maintenance 选择；
- Capture；
- STOP；
- Preflight；
- Override；
- Handoff。

## `relay.*`

负责确定性 Relay 编排。

## `adapter-contract.md`

定义：

- runtime 参数读取；
- Preflight；
- create + confirm；
- 三态结果；
- retryable；
- 生命周期独立；
- Payload 安全传递。

## `startup-confirmation.md`

定义：

- `confirmed`；
- `failed`；
- `unknown`；
- `retryable`；
- timeout；
- observation window；
- failed 白名单原则。

## `maintenance.md`

定义完整维修与真实验收流程。

普通 Relay 不需要每轮加载完整维修规则。

## `log-format.md`

只定义普通 Relay 日志。

不存在 Maintenance Log。

# 27. Skills Creator 阶段必须完成的事项

Skills Creator 不能只生成静态 Demo 文件，还需要针对用户真实环境完成实现探测。

至少确认：

1. Relay 显式调用语法；
2. Maintenance 显式调用语法；
3. Harness 标签语法；
4. 当前 Harness 如何读取 runtime 参数；
5. 如何创建新 session；
6. 如何传递完整 Prompt；
7. 如何设置 cwd/model/thinking；
8. 如何继承 permission/sandbox/approval；
9. 如何取得唯一 session reference；
10. 如何观察真实 startup event；
11. 如何读取 effective metadata；
12. 如何识别 approval 阻塞；
13. 如何保证新 session 脱离创建者生命周期；
14. 当前 Harness 的 `failed` 白名单；
15. 哪些 `failed` 允许 `retryable=true`；
16. 合理的 startup timeout 和 observation window；
17. 当前机器上能够识别的 Skill 安装位置；
18. 适用参数及其状态转换；
19. 当前日志 handoff claim 与原子结果写入；
20. Relay 层 hard deadline 和超时后的 `unknown` 行为；
21. task entry 长度定界、hash 保真和多轮属性测试。

任何声称“已支持”的 Adapter：

> 必须完成真实 Harness 验收。

无法实际验证：

> 只能标记为未验证，不能静态宣称支持。

# 28. 最终流程

## 正常 Relay

```
显式触发
→ Capture
→ 生成并落盘 RelayContext
→ 检查 STOP

STOP 存在
→ 当前任务正常执行
→ 本轮不创建下一会话

STOP 不存在
→ Preflight

Preflight 失败
→ 不执行放养任务
→ 报告并结束

Preflight 成功
→ 放养执行
→ 当前 session 准备结束
→ 再次检查 STOP

STOP 存在
→ 正常结束

STOP 不存在
→ 锁定 current_log_path
→ 读取落盘 RelayContext
→ Adapter create_and_confirm

confirmed
→ 交接完成

unknown
→ 停止，不重试

failed + retryable=false
→ 停止

failed + retryable=true
→ 原样机械重试一次
→ 非 confirmed 则停止
```

## Adapter Maintenance

```
用户明确指定 Harness 并进入 Maintenance
→ 检查当前实际 Skill / Adapter
→ 真实复现
→ 直接修改当前生效实现
→ 真实测试
→ 根据错误继续修
→ 初步跑通
→ 最终减法
→ 再次完整真实验收
→ 用户确认测试会话可见
→ 当前 Skill 成为 authoritative copy
→ 整体覆盖其他已知安装位置
→ 文件级一致性校验
→ 报告 authoritative copy 与所有同步结果
```

# 29. 最终设计边界

`ruan-continue2run 2.0` 最终坚持六条核心规则：

> **1. Skill 不替用户判断任务是否完成。**

> **2. Relay 的关键控制逻辑尽量确定性脚本化，不依赖模型长期记忆。**

> **3. 无法证明创建失败，就不能自动重试。**

> **4. 新会话必须真正启动，并独立于旧会话生命周期。**

> **5. Adapter 修复必须经过真实运行；最终减法以后必须再次真实验收。**

> **6. 最终验收通过的当前 Skill 就是权威源，完整同步到其他已知安装位置。**

最终目标：

> **修改的就是运行的。**
> **验收过的才算修好。**
> **无法确定失败就不冒险重复创建。**
> **用户决定什么时候停止整个长期任务。**