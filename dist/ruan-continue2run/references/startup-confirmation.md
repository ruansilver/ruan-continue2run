# Startup Confirmation

只有能够证明“新会话已经开始正常执行”的结果才能是 `confirmed`。

## confirmed 的三个条件

1. **唯一会话身份**：取得 session id、conversation id、thread id 或 Harness 等价稳定标识。
2. **参数提交正确**：实际提交或可靠继承的 Payload、cwd、model、thinking、permission、sandbox、approval 与 RelayContext 的 expected 参数一致。
3. **真实开始执行**：观察到 assistant execution event、runtime/tool event、running/started 状态或等价证据，并且没有立即出现 fatal startup error。

`confirmed` 只表示在该 Adapter 定义的 startup observation window 内已确认开始正常执行，
不承诺新会话之后永不崩溃，也不表示业务任务已完成。

## effective 参数

Harness 能读到新会话真实生效值时应记录到 `effective_parameters`。读不到某一项不自动
判定失败，但不能伪造为已验证。使用 `start` 策略时，当前会话 observed 与上一轮 expected
不一致仍须 Preflight 失败。Codex 使用 `handoff` 策略，当前用户调整过的参数在交接时形成
新的 expected 快照；子会话的提交和 effective 校验针对这份快照，不能沿用本轮开始值。

## approval 阻塞

如果会话停在 approval、permission、sandbox 或其他等待人工确认状态，不能返回
`confirmed`。如果请求可能已经产生副作用，返回 `unknown`，并说明阻塞原因。

## 三态

### `confirmed`

新会话已在有限观察窗口内可靠接管。当前会话可以结束 handoff 职责。

### `failed`

只有经过该 Adapter 真实验证、能够证明没有创建新 Relay 会话的错误才可返回。错误必须带
稳定 `error_code`。`retryable` 默认 `false`。

### `unknown`

请求已发出但信息不足、Adapter 超时、进程异常、approval 阻塞或无法证明失败时使用。
`unknown` 禁止自动重试，因为新会话可能已经存在。

## retryable

只有同时满足以下条件，Adapter 才能返回 `failed + retryable=true`：

1. 可以确定本次没有创建 session；
2. 错误具有偶发性；
3. 原样重试有现实意义。

Relay 只允许原样机械重试一次。第二次不是 `confirmed` 就停止，不存在第三次自动重试。

## handoff claim

Relay 在调用 Adapter 之前，对当前日志原子创建 handoff claim：

- 已有最终结果：返回已有结果；
- 只有 claim、没有最终结果：保守返回 `unknown`，不得再次调用 Adapter。

这防止 lifecycle hook、模型重复调用或进程中途退出后产生重复会话。它不管理轮次、链
状态或任务完成度。

## 外层 deadline

Adapter 自己定义 `HANDOFF_DEADLINE_SECONDS`，Relay 还会以独立进程调用 Adapter 并施加 hard
截止时间。超过截止时间时终止当前 Adapter 调用进程，写入 `ADAPTER_TIMEOUT`，状态为
`unknown`，不重试。
