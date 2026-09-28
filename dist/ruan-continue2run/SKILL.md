---
name: ruan-continue2run
description: >-
  会话接力编排 Skill，仅限显式调用：只有当用户会话的第一条消息明确点名 ruan-continue2run 时才使用。
  它让当前会话按放养模式（用户不在场）执行任务，并在会话结束前，通过对应 Harness 的 Adapter，
  用同一任务入口与运行参数创建下一个会话，同时写入事实日志。
  不要因为用户提到“继续”“接力”“下一个会话”“自动续跑”“新开会话”“接着做”等字眼就触发；
  消息里没有显式出现 ruan-continue2run 时一律不要加载本 Skill——误触发会让会话在无人值守下
  自行决策并不断创建新会话。
metadata:
  version: "1.0.0"
  knowledge-doc: 源码目录的 README.md（只在源码目录里有，不随安装进技能目录）
---

# ruan-continue2run

让当前会话在结束前，用同一任务入口和运行参数创建下一个会话，形成接力。

本 Skill **只做会话接力编排与记录**：在某个具体 Harness 上"如何创建会话"由 Adapter 负责，本文件不包含任何 Harness 创建细节，只负责按顺序调用 `scripts/relay.py` 的 `open` / `decision` / `finish` 三个子命令。

命令里的占位符：

- `<skill_dir>`：本 `SKILL.md` 所在目录。
- `<python>`：当前环境可用的 Python 3 解释器（Windows 本机通常是 `python`，Linux/macOS 通常是 `python3`；不确定时先跑一次 `<python> --version` 确认再往下做）。
- `relay.py` 的 stdout 是 **ASCII 转义 JSON**：中文会以 `\uXXXX` 形式出现（stdout 编码不可信，这样才不会被终端/调用链改坏）。照转义还原即可，日志文件里是正常中文。

## 1. 开场：确认是否启用，并建立本会话日志

取**本会话第一条用户消息的原文**（不是最新一条、也不是你自己复述的版本），原样写入一个临时文件（放到系统临时目录即可，例如 Windows 的 `$env:TEMP\ruan-first-message.txt`），然后运行：

```bash
<python> <skill_dir>/scripts/relay.py open \
  --message-file "<临时文件路径>" \
  --model "<当前模型标识>" --thinking-depth "<当前思维深度>" --working-dir "<当前工作目录>"
```

- 运行参数只填**当前会话实际能确认的值**；确认不了就省略该参数，不要猜、不要用默认值凑。
- 用临时文件是为了让第一条消息**逐字**进入载荷，避免 shell 引号、换行被改写。`relay.py open` 也接受从 stdin 读入，二选一即可。

读它输出的 JSON：

- `relay` 为 `false`：第一条消息并没有显式调用本 Skill（例如用户是会话中途才提到它）。**按普通会话处理**：不套用第 2 步的放养模式、不要写日志、不要创建下一会话，本 Skill 到此结束。
- `relay` 为 `true`：记住 `log_path`、`slug`、`history_glob`，继续第 2 步。`warnings` 里的提示照常继续即可，不要自行补救。
- 出现 `error`（例如 `LOG_WRITE_FAILED`）：本会话连接力日志都建不起来（常见原因：会话跑在只读沙箱里，工作目录不可写）。**按普通会话处理任务**，把错误如实告诉用户，不要重试、不要换目录、不要自己想办法创建会话。

## 2. 正文：按放养模式执行任务

读 `references/relay-override.md`，并按它执行 `context.task_entry` 里的任务。要点：用户不在场，遇到原本要停下来问的节点就自行判断并继续；**只认第一条消息**作为任务入口，之后用户再说什么都不改变它，也不进入交接内容。

## 3. 记录关键决策

每次做出关键决策（尤其是本该问用户、改为自行判断的那些节点），立刻记一条：

```bash
<python> <skill_dir>/scripts/relay.py decision --log "<log_path>" \
  --node "遇到的问题/节点" --choice "最终选择的方向" \
  --basis "依据的文档或线索" --confidence "把握程度（high / medium / low）"
```

只记事实，不写自我评价。命令细节见 `references/relay-override.md`。

## 4. 收尾：创建下一会话（当前会话的最后一个动作）

任务告一段落时运行（不要先问自己"任务是不是已经完成、还要不要接力"——这不是本 Skill 的职责）：

```bash
<python> <skill_dir>/scripts/relay.py finish --log "<log_path>"
```

`finish` 会依次做三件事：写意图日志 → 通过 Adapter 创建下一会话 → 把创建调用的结果追加到本会话日志。它结束之后：

- 不要再调用任何工具，不要重试，不要自己想办法创建会话，也不要修改 Adapter 或注册表。
- 把输出 JSON 里的 `issued` / `info` / `error` 如实转述给用户，然后结束本会话。
- `issued` 为 `false` 时（例如 `HARNESS_UNRESOLVED`、`HARNESS_UNREGISTERED`、`NOT_IMPLEMENTED`、`PENDING_CONFIRMATION`）同样如实转述：这说明接力没有发出，需要新增或修复某个 Harness 的支持，由用户另行进入"Skill 修复阶段"（见源码目录 `README.md` 第 8.6 节）。

Adapter 的输入输出契约见 `references/adapter-contract.md`；日志格式与事实原则见 `references/log-format.md`。

## 不做的事

保持轻量。下面这些都不属于本 Skill，即使看起来"更完整"也不要加：

- 任务状态管理（进度、轮次、链状态）
- 自动完成判断（不判断"任务是否做完、还要不要接力"）
- 自检、自动恢复、重试、巡检
- 判断新会话是否真正运行成功（日志只写"创建调用已发出/未发出"）
- 复制或注入历史上下文（历史决策只通过 `history_glob` 去读日志作参考）

## 边界情况

| 情况 | 怎么做 |
|---|---|
| 第一条消息没有 Harness 标签 | `open` 会让已注册 Adapter 做无副作用的被动识别；只有唯一结果才会选中。无法唯一识别时，`finish` 返回 `HARNESS_UNRESOLVED`。如实记录、如实报告，**不要**自己补标签或改注册表。 |
| 标签不在注册表 | 同上，`finish` 返回 `HARNESS_UNREGISTERED`。这是"该 Harness 还没适配"，不是本会话要解决的问题。 |
| 用户在本会话中途发消息 | 一律视为噪声：不进交接内容，也不改变任务入口。 |
| 想终止链条 | 不需要本 Skill 做任何事：新会话开场时，如果它的第一条消息没有显式调用本 Skill，它就会按普通会话处理，链条自然结束。 |
| 本会话已经执行过一次 `finish` | 不要重复执行。`finish` 会因为日志里已有 `intent` 而拒绝第二次创建，如实报告即可。 |
| 日志写不进去（如只读沙箱） | `open` / `finish` 会返回 `LOG_WRITE_FAILED`：本会话按普通会话处理，如实报告，不要重试、不要换目录。 |

## 待确认默认值

触发写法（消息里出现 `ruan-continue2run`）、Harness 标签语法（消息末尾 `//HarnessName`）、日志目录名（`<工作目录>/.ruan-continue2run/`）都是**待确认默认值**，集中放在 `scripts/config.py` 并标有 PENDING。需要改动时只改那一处。
