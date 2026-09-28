---
name: ruan-continue2run
description: >-
  会话接力编排 Skill，仅限显式调用：只有当会话的第一条用户消息明确点名调用 ruan-continue2run 时才使用。
  它让当前会话按放养模式（用户不在场）执行任务，并在会话结束前，通过对应 Harness 的 Adapter，
  用同一任务入口与运行参数创建下一个会话，同时写入事实日志。
  不要因为用户提到“继续”“下一个会话”“接力”“自动续跑”“新开会话”等字眼就联想触发；
  消息里没有显式出现 ruan-continue2run 时一律不要启用，误触发会让会话在无人值守下自行决策并不断创建新会话。
compatibility: 需要能运行 Python 3（仅标准库）来执行 scripts/；DeepSeek Harness 是否满足待确认。
---

# ruan-continue2run

让当前会话在结束前，用同一任务入口和运行参数创建下一个会话，形成接力。

本 Skill **只做会话接力编排与记录**。在某个具体 Harness 上"如何创建会话"由 Adapter 负责，本文件不包含任何 Harness 创建细节，只通过 `scripts/relay.py` 调用 Adapter 接口。

## 1. 开场：确认是否启用，并建立本会话日志

把**本会话第一条用户消息的原文**原样传给 `open`：

```bash
python3 <skill_dir>/scripts/relay.py open \
  --model "<当前模型>" --thinking-depth "<当前思维深度>" --working-dir "<当前工作目录>" <<'EOF'
<第一条用户消息原文，逐字粘贴>
EOF
```

`<skill_dir>` 是本 SKILL.md 所在目录。运行参数只填**当前会话实际能确认的值**，确认不了就省略该参数，不要猜。

读取输出的 JSON：

- `relay` 为 `false`：第一条消息没有显式调用本 Skill。**按普通会话处理，忽略本 Skill 的其余内容。**
- `relay` 为 `true`：记住 `log_path`、`slug`、`history_glob`。`warnings` 里的提示照常继续，不要自行补救。

## 2. 正文：按放养模式执行任务

阅读 `references/relay-override.md`，并按它执行 `context.task_entry` 里的任务。要点：用户不在场，遇到原本要停下来问的节点就自行判断并继续；只认第一条消息作为任务入口，之后的用户消息不进入交接内容。

## 3. 记录关键决策

每次做出关键决策，用 `relay.py decision` 记一条（命令格式见 `references/relay-override.md`）。只记事实。

## 4. 收尾：创建下一会话（最后一步）

任务告一段落时，**作为当前会话的最后一个动作**运行：

```bash
python3 <skill_dir>/scripts/relay.py finish --log "<log_path>"
```

`finish` 会依次：写入意图日志 → 通过 Adapter 创建下一会话 → 把创建调用的结果追加到日志。之后：

- 不要再调用任何工具，也不要重试；直接把输出 JSON 里的 `issued` / `info` / `error` 如实告诉用户，然后结束。
- `issued` 为 `false`（例如 `PENDING_CONFIRMATION`、`HARNESS_UNRESOLVED`）时，**不要自己想办法创建会话**，也不要修改 Adapter。如实报告即可；需要新增或修复某个 Harness 的支持，由用户另行进入 Skill 修复阶段。

Adapter 契约与输出含义见 `references/adapter-contract.md`；日志格式见 `references/log-format.md`。

## 不做的事

保持轻量，下面这些都不属于本 Skill，不要自行加上：

- 任务状态管理（进度、轮次、链状态）
- 自动完成判断（不判断"任务是否做完、还要不要接力"）
- 自检、自动恢复
- 判断新会话是否真正运行成功
- 复制或注入历史上下文（历史决策只通过 `history_glob` 读取日志作参考）

## 待确认默认值

触发写法、Harness 标签语法、日志目录名是待确认的默认值，集中在 `scripts/config.py`（标有 PENDING）。DeepSeek Harness 的创建方式尚未确认，其 Adapter 目前会如实返回 `PENDING_CONFIRMATION`。
