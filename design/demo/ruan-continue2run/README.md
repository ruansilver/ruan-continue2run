# ruan-continue2run

> **本文件只属于 Skill 源目录，给维护者和使用者阅读，不参与 Skill 运行，也不应被安装到 Skill 运行目录。**
> 打包/安装时必须排除它，见第 9 节。

## 1. 这是什么，解决什么问题

`ruan-continue2run` 是一个会话接力编排 Skill。被显式调用后，当前会话会按"放养模式"执行任务（用户不在场，遇到原本要停下来问的节点就自行判断），并在会话结束前，用**同一任务入口和运行参数**创建下一个会话，让下一个会话继续同样的事，如此接力。

它解决的问题：逐会话开发时，每一轮都要人工"打开目录 → 选模型 → 选参数 → 粘贴接力提示词"。本 Skill 把这个动作自动化，并且每次都是全新会话，不带历史上下文。

它**只**负责会话接力编排和记录，不管任务本身做得好不好，也不管下一个会话是否真的跑起来。

## 2. 目录结构

```
ruan-continue2run/
├── README.md                    # 仅源目录，不安装
├── SKILL.md                     # 只做流程编排，不含任何 Harness 创建细节
├── scripts/
│   ├── relay.py                 # 命令行入口：open / decision / finish
│   ├── relay_context.py         # RelayContext 构建、解析、渲染，slug 推导
│   ├── relay_log.py             # 会话日志读写
│   ├── dispatch.py              # 按 Harness 加载 Adapter，并强制执行输出契约
│   ├── config.py                # 待确认默认值（触发写法、标签语法、日志目录名）
│   └── adapters/
│       ├── registry.json        # Harness 注册表
│       ├── base.py              # 结果构造辅助
│       ├── deepseek.py          # DeepSeek Harness Adapter（结构就位，创建方式待确认）
│       ├── codex.py             # 扩展位置（占位）
│       └── claude_code.py       # 扩展位置（占位）
└── references/
    ├── adapter-contract.md      # Adapter 输入输出契约（权威定义）
    ├── log-format.md            # 日志格式
    └── relay-override.md        # 放养模式固定文本
```

## 3. 用户如何显式调用

在会话的**第一条消息**里点名调用 Skill，并附上任务入口和 Harness 标签：

```
ruan-continue2run
<你的接力提示词 / 任务入口>
//DeepSeek
```

- 必须显式点名 `ruan-continue2run`。只提到"继续""接力"之类的词不会触发。
- 末尾的 `//HarnessName` 告诉 Skill 当前跑在哪个 Harness 上（决定用哪个 Adapter）。标签在解析时被剥离，不属于任务入口。
- 只有**第一条消息**有效。会话中途或收尾时你再发的消息，不会被带入下一会话。
- 以上写法是待确认默认值，见第 10 节。

## 4. 一次完整接力流程

```
会话开场   relay.py open      解析第一条消息 → 构建 RelayContext → 创建本会话日志
   │        （第一条消息没有显式调用 → relay=false → 按普通会话处理，到此为止）
会话正文   按放养模式执行任务（references/relay-override.md），关键决策用 relay.py decision 记录
   │
会话收尾   relay.py finish     ① 写意图日志
                              ② 通过 Adapter 创建下一会话（当前会话最后一个有副作用的动作）
                              ③ 追加"创建调用结果"到本会话日志
```

新会话拿到的第一条消息由 `render_first_message` 统一渲染（显式调用 + 任务入口 + Harness 标签），所以它同样会显式调用本 Skill，链条得以继续。

**链条如何结束：** 没有内部终止条件，也不做完成判断。新会话开场时如果第一条消息没有显式调用本 Skill（例如你被动中断后随手发了一句"继续"），它就按普通会话处理，链条就此结束。

## 5. Relay Payload 的组成规则

Payload 只有三部分，不多不少：

1. 用户显式任务入口（第一条消息剥离触发标记和 Harness 标签后的原文，逐字保留）
2. `ruan-continue2run` Skill 自身（新会话的第一条消息同样显式调用它）
3. 当前运行参数：模型、思维深度、工作目录，以及 Harness 标识

**不传：** 历史对话上下文、链状态、轮次、上一轮的中间结果。取不到的运行参数保持 `null`，不猜测。

历史决策不注入，只给读取指引：`open` 会输出 `history_glob`，放养外壳要求做重大决策前先读同一 slug 下最近几个日志。

## 6. 日志目录：作用与查看方式

- **作用：** 只记事实——本会话的 RelayContext、关键决策、意图日志、创建调用结果。用来事后追溯，以及确认"创建下一会话的调用有没有发出"。它不判断新会话是否运行成功，不是状态管理。
- **位置：** 项目级点前缀目录，默认 `<工作目录>/.ruan-continue2run/`。
- **粒度：** 每个会话一个独立文件 `<UTC时间戳>-<slug>.md`；同一任务入口得到同一个 slug，靠 slug 归类。
- **怎么看：**
  ```bash
  ls -1t .ruan-continue2run/                      # 最近的会话日志
  ls -1t .ruan-continue2run/*-<slug>*.md          # 某个任务的全部会话日志
  grep -A5 "creation-call-result" .ruan-continue2run/<文件>   # 创建调用发出了吗
  grep -A5 "] decision" .ruan-continue2run/<文件>             # 做过哪些决策，把握程度如何
  ```
- 没有自检机制：链条断了、创建失败，都靠你看日志或发现"没有新会话"来发现。

格式细节见 `references/log-format.md`。

## 7. 如何进入 Skill 修复阶段

新增某个 Harness 的支持，或某个 Harness 更新后创建会话失效，都通过"Skill 修复阶段"处理：**你开一个 Skill Creator 会话，用固定格式声明目标，Skill Creator 只在允许的范围内改。** 不需要重新理解整个设计。

把下面这段（填空后）作为 Skill Creator 会话的开场提示词：

```
【ruan-continue2run · Skill 修复阶段】
目标 Harness：<名称，例如 DeepSeek / Codex / Claude Code>
类型：<新增支持 | 创建失效修复>
背景/现象：<例如：Harness 更新后 finish 返回 ADAPTER_EXCEPTION：...>

请读取 ruan-continue2run 源目录，然后按以下固定要求工作：

1. 只允许修改：scripts/adapters/<harness 模块>.py、scripts/adapters/registry.json 中该 Harness
   的条目，以及本 README 第 8 节里该 Harness 的状态说明。
2. 不得修改：SKILL.md、scripts/relay.py、relay_context.py、relay_log.py、dispatch.py、
   references/ 下的任何文件。config.py 只有在我明确确认某个待确认默认值时才改。
3. Adapter 必须满足 references/adapter-contract.md：输入 RelayContext，输出
   {issued, info, error}；issued 只表示创建调用是否已发出。
4. 不要假设 Harness 的行为。先通过探测（查看它的 CLI/文档/配置，实际试运行）或向我提问确认：
   如何以编程方式创建会话、如何指定模型/思维深度/工作目录、如何投递第一条消息、
   如何在第一条消息里显式调用 Skill、无标签时如何被动识别。
   无法确认的项写进 PENDING_ITEMS，Adapter 对应处返回 pending_confirmation，不要写猜测性调用。
5. 不新增任务状态管理、自动完成判断、自动恢复、自检；不复制历史上下文；不改变 Relay Payload。
6. 完成后汇报：改了哪些文件、探测到的事实、仍待确认的项。
```

## 8. 支持新的 Harness

### Harness 当前状态

| Harness | Adapter | 状态 |
|---|---|---|
| DeepSeek | `scripts/adapters/deepseek.py` | 结构就位，**创建方式待确认**（`create()` 返回 `PENDING_CONFIRMATION`） |
| Codex | `scripts/adapters/codex.py` | 占位，未实现 |
| Claude Code | `scripts/adapters/claude_code.py` | 占位，未实现 |

### 新增一个 Harness，需要做什么

1. **新增 Adapter：** 复制 `scripts/adapters/codex.py` 为 `scripts/adapters/<module>.py`（模块名用小写加下划线），实现 `create(context)`。
2. **满足输入输出契约**（权威定义在 `references/adapter-contract.md`）：
   - 输入：`RelayContext`——任务入口、Skill、运行参数（模型、思维深度、工作目录）、Harness 信息。
   - 输出：`{"issued": bool, "info": str | None, "error": str | None}`，`issued` 只表示创建调用已发出；`issued=false` 必须带 `error`。
   - 新会话第一条消息用 `relay_context.render_first_message(context)` 渲染；用 `adapters.base.result / pending_confirmation / not_implemented` 构造返回值。
   - 只做创建；不写日志、不重试、不自检、不判断新会话是否成功；对 Harness 行为不确定时不要假设，返回 `pending_confirmation`。
3. **注册：** 在 `scripts/adapters/registry.json` 加一条（标识、模块名、别名）。用户之后用 `//<别名>` 指定它。
4. **更新本 README** 第 8 节的状态表。

### 哪些文件需要修改

- `scripts/adapters/<module>.py`（新增）
- `scripts/adapters/registry.json`（加一条）
- `README.md`（状态表）

### 哪些核心文件不应该修改

`SKILL.md`、`scripts/relay.py`、`scripts/relay_context.py`、`scripts/relay_log.py`、`scripts/dispatch.py`、`references/*.md`。它们只依赖 Adapter 契约，与具体 Harness 无关；如果新增 Harness 需要改动它们，说明差异泄漏进了核心，应该回到 Adapter 里解决。`scripts/config.py` 只放全局待确认默认值，仅在确认这些默认值时才改。

## 9. 打包与安装：README 不进运行目录

`package_skill.py` 不会自动排除 `README.md`，所以打包前先复制一份并去掉它：

```bash
rm -rf /tmp/ruan-pkg && mkdir -p /tmp/ruan-pkg
cp -r ruan-continue2run /tmp/ruan-pkg/
rm -f /tmp/ruan-pkg/ruan-continue2run/README.md
find /tmp/ruan-pkg -name __pycache__ -type d -exec rm -rf {} +
# 在 skill-creator 目录下运行：
python -m scripts.package_skill /tmp/ruan-pkg/ruan-continue2run ./dist
```

安装位置取决于 Harness 的 Skill 加载方式（DeepSeek Harness 的具体方式待确认）。无论怎么装，装进去的目录里都不应该有 `README.md`。

## 10. 待确认项

以下内容设计文档没有覆盖，**没有被假设**，需要通过 Skill Creator 的探测/访谈流程补全：

| 待确认项 | 现在的处理 |
|---|---|
| DeepSeek Harness 如何以编程方式创建会话（CLI / 配置 / API）；如何指定模型、思维深度、工作目录；如何投递第一条消息 | `deepseek.py` 的 `create()` 返回 `PENDING_CONFIRMATION`，不执行任何创建 |
| 在第一条消息里如何显式调用 Skill | `config.py` 默认：消息里出现 Skill 名 `ruan-continue2run`（可带 `/` `$` `@` 前缀） |
| Harness 标签的具体语法 | `config.py` 默认：消息末尾 `//HarnessName` |
| 日志目录名、slug 规则 | 默认 `.ruan-continue2run/`；slug = 任务入口的 ASCII 前缀 + SHA-1 前 8 位 |
| 运行环境是否提供 Python 3 | 脚本只用标准库，按 Python 3 编写 |
| 无 Harness 标签时的被动识别 | 不做；此时 `finish` 返回 `HARNESS_UNRESOLVED` 并如实记录 |
| 各 Harness 的生命周期能力（会话结束事件等） | 未使用 |

## 11. 设计边界

不做，也不要加回来：任务状态管理、自动完成判断、自检、自动恢复、判断新会话是否运行成功、复制历史上下文、改变 Relay Payload。

一处实现层面的保护：`finish` 在同一个会话日志里只会执行一次（日志里已有意图记录就拒绝再次执行），避免同一会话重复创建下一会话。它只读取本会话自己的日志，不构成额外的状态管理。
