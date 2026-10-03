# Relay Log Format

这里只描述普通 Relay 日志，不保存 Maintenance 历史，也不保存任务轮次或 Chain State。

## 目录

```text
.ruan-continue2run/
├── STOP
└── relay/
    ├── <timestamp>-<slug>-<random>.log
    ├── <same-log>.handoff.claim
    └── <same-log>.handoff.result.json
```

目录位于当前 working directory。STOP 是 cwd 级共同刹车，当前 cwd 的所有 Relay 共用它。
需要独立刹车的 Relay 不在同一 cwd 并行运行。

## RelayContext

日志开头写入一次 JSON RelayContext，使用 `schema_version: 1`：

```json
{
  "schema_version": 1,
  "task_entry": "...",
  "task_entry_sha256": "...",
  "harness": "...",
  "slug": "...",
  "parameter_expectations": {
    "working_directory": {"state": "observed", "value": "..."},
    "model": {"state": "supplied", "value": "..."}
  },
  "parameter_observations": {
    "working_directory": {"state": "observed", "value": "..."}
  }
}
```

Capture 完成后立即原子写入。handoff 前必须从已锁定的 `current_log_path` 重新读取，不能
依赖模型记忆重构 RelayContext。

## task entry 保真

第一次 Capture 规范化为 UTF-8、LF、无 BOM；正文不 trim。Payload 用长度定界的
`<<<relay-task-entry bytes=N>>>` 区块传递正文，区块外分隔换行不属于正文。每轮重新计算
`task_entry_sha256`，hash 不一致时 Preflight 失败。

## handoff claim 与结果

在任何 Adapter 副作用之前，Relay 使用原子创建取得 `<log>.handoff.claim`。claim 内容只
记录 schema、时间和进程标识，不记录凭据。

Adapter 返回后，将最终 `AdapterResult` 以同目录临时文件 + `atomic replace` 写入
`<log>.handoff.result.json`，再在日志中追加摘要。第二次 handoff：

- 发现 result：直接返回已有结果；
- 只有 claim：返回 `unknown`，不再次调用 Adapter。

## 写入与脱敏

日志和 sidecar 文件尽量设置为当前用户可读写（例如 Unix `0600`）。`note`、错误摘要、
启动证据和 Adapter 输出都必须限制长度，并对明显 API key、token、密码、Bearer 值脱敏。
不整体 dump 环境变量、headers、credentials 或认证上下文。

## 日志内容

除 RelayContext 外，只记录关键决策、必要依据、接力意图、Adapter 状态、session reference、
唯一一次安全重试、错误码和必要错误摘要。项目真实状态仍以源码及正式需求/设计文档为准。
