# 日志格式

日志的作用：留下事实记录，方便事后追溯"这个会话做了哪些关键决策、有没有发出创建下一会话的调用"。日志**只记事实**，不判断新会话是否运行成功，也不承担任何状态管理。

## 位置与命名

- 目录：项目级点前缀目录，默认 `.ruan-continue2run/`，位于运行参数里的工作目录之下（目录名是待确认默认值，见 `scripts/config.py`）。
- 文件：**每个会话一个独立文件**，`<UTC时间戳>-<slug>.md`，例如 `20260928T030450Z-docs-plan-md-0318ce93.md`。
- 归类：`slug` 由任务入口确定性推导（同一任务入口永远得到同一个 slug），同一任务的多个会话日志靠 slug 聚合。Payload 不携带任何日志路径，会话之间不共享、不追加彼此的文件。

## 文件结构

```
# ruan-continue2run session log

- slug: `...`
- opened_at: <UTC 时间>
- harness: tag=..., id=...

## RelayContext
（json 代码块：本会话的 RelayContext，收尾时从这里读回，保证任务入口逐字不变）

## [<UTC 时间>] decision
- node / choice / basis / confidence

## [<UTC 时间>] intent
- action / adapter / harness_tag

## [<UTC 时间>] creation-call-result
- issued / info / error / note
```

## 记录类型

| 类型 | 写入时机 | 内容 |
|---|---|---|
| `decision` | 每次做出关键决策时 | 遇到的节点、选择的方向、依据的参考文档、把握程度 |
| `intent` | 收尾时，调用 Adapter 之前 | 即将请求创建下一会话，使用哪个 Adapter |
| `creation-call-result` | Adapter 返回之后 | 创建调用是否已发出、返回信息或错误 |

`intent` 与 `creation-call-result` 由 `relay.py finish` 自动写入，不需要手写。

## 事实原则

- 写"创建调用已发出 / 未发出"，不要写"接力完成"或"新会话运行成功"。
- 不记录对新会话的任何判断，不记录进度、轮次或链状态。
- 读取历史决策时，只读同一 slug 下最近几个文件，把它们当作参考，不要把内容整段搬进上下文。
