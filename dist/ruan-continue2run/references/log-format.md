# 日志格式

日志的作用：留下**事实记录**，方便事后追溯"这个会话做了哪些关键决策、有没有发出创建下一会话的调用"。

日志只记事实：

- 不判断新会话是否运行成功。写"创建调用已发出"，**不写**"接力完成"。
- 不承担状态管理：不记录进度、轮次、链状态，不判断"任务是否已完成"。
- 与 Agent Checkpoint / 会话恢复机制保持独立，不合并存储格式。

## 位置与命名

- 目录：项目级点前缀目录，默认 `.ruan-continue2run/`，位于运行参数里的工作目录之下（目录名是**待确认默认值**，集中在 `scripts/config.py`）。
- 文件：**每个会话一个独立文件**，命名 `<UTC时间戳>-<slug>.md`，例如 `20260928T030450Z-docs-plan-md-0318ce93.md`。
  - 同一秒内同 slug 再次开会话时，追加 `-2`、`-3` 序号，**不追加他人的文件**。
- 归类：`slug` 由任务入口确定性推导（同一任务入口永远得到同一个 slug），同一任务的多个会话日志靠 slug 聚合。载荷不携带任何日志信息，会话之间也不共享文件。

## 文件结构

```markdown
# ruan-continue2run session log

- slug: `...`
- opened_at: <UTC 时间>
- harness: tag=..., id=...

## RelayContext

（json 代码块：本会话的 RelayContext。收尾时从这里读回，保证任务入口逐字不变）

## [<UTC 时间>] decision
- node / choice / basis / confidence

## [<UTC 时间>] intent
- action / adapter / harness_tag

## [<UTC 时间>] creation-call-result
- issued / info / error / note
```

## 记录类型

| 类型 | 写入时机 | 内容 | 由谁写 |
|---|---|---|---|
| `decision` | 每次做出关键决策时 | 遇到的节点、选择的方向、依据的参考文档、把握程度 | 会话按 `references/relay-override.md` 调用 `relay.py decision` |
| `intent` | 收尾时、调用 Adapter 之前 | 即将请求创建下一会话，使用哪个 Adapter | `relay.py finish` 自动写 |
| `creation-call-result` | Adapter 返回之后 | 创建调用是否已发出、返回信息或错误 | `relay.py finish` 自动写 |

`intent` 与 `creation-call-result` 是收尾动作的两个半边：先记"要发了"，再记"发的结果"。中间不写任何判断性内容。

## 事实原则

- 写"创建调用已发出 / 未发出"，不要写"接力完成""新会话运行成功"。
- 不记录对新会话的任何判断；不记录进度、轮次或链状态。
- 读取历史决策时，只读同一 slug 下最近几个文件，把它们当参考；不要把内容整段搬进上下文。
- 会话日志是**本会话自己**的文件：新旧会话交接重叠时各写各的文件，不覆盖、不追加对方的记录。

## 附件文件（不属于会话日志语义）

Adapter 在创建调用中产生、需要事后排查的原始输出（例如派生子进程时它自己的 stdout/stderr），由 Adapter 落在 `<日志目录>/children/` 下，例如：

```
.ruan-continue2run/
├── 20260928T030450Z-docs-plan-md-0318ce93.md      # 会话日志
└── children/
    └── 20260928T030450Z-docs-plan-md-0318ce93.out # 子进程原始输出
```

它们是**原始事实**，不是会话日志，不参与上面的记录类型，也不改变"每会话一个日志文件"的规则。是否产生由各 Adapter 自己决定。
