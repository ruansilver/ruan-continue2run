# Adapter Contract

Adapter 负责所有 Harness 差异；`relay.py` 只做 Harness 无关的编排。一个 Adapter 是
`scripts/adapters/<harness>.py`，文件名就是小写 Harness 名。

## 模块级声明

```python
SCHEMA_VERSION = 1
INVOCATION = "ruan-continue2run"
HANDOFF_DEADLINE_SECONDS = 60
PARAMETER_CAPTURE_PHASE = "start"  # 缺省值；Codex 声明 "handoff"
PARAMETER_APPLICABILITY = {
    "working_directory": "required",
    "model": "applicable",
    "thinking_depth": "applicable",
    "permission_mode": "applicable",
    "sandbox_mode": "applicable",
    "approval_mode": "applicable",
}
EXTRA_FIELDS = []
```

`working_directory` 永远是本 Skill 的核心必需参数。其他字段必须显式声明为
`applicable` 或 `not_applicable`；`required` 只表示适用且缺失时必然失败。没有声明的
基础字段按 `applicable` 处理，避免静默跳过参数继承。

## 参数状态

`read_runtime_context()` 的每个字段返回：

```json
{"state": "observed|inherited|unavailable|not_applicable", "value": "..."}
```

- `observed`：当前 Harness/session 真实读到的值；
- `inherited`：Harness 具有可靠的原生继承语义，并给出可验证值；
- `unavailable`：字段适用，但无法可靠取得；
- `not_applicable`：字段对该 Harness 根本不适用。

`PARAMETER_CAPTURE_PHASE` 可为 `start`（兼容默认）或 `handoff`。后者在 handoff 中读取并
验证最终参数，在开始阶段只 Capture 任务、hash、cwd 与传入信息。详见
`runtime-inheritance.md`；该策略同时适用于该 Adapter 的官方与第三方 provider。

`supplied` 只出现在 RelayContext 的 expected 参数中，表示用户显式提供。适用字段
必须有可靠 expected 值；`unavailable` 不能被当作 `not_applicable`。当前 observed 为 `unavailable` 时，只有 expected 为 `supplied` 且 Adapter 能证明会按该显式值提交，才可继续。

上面的 supplied 例外仅适用于 `start` 策略。`handoff` 策略只使用交接时实际读取的参数；
不可读时不能回退到开始的 supplied/observed 值，也不能凭它们覆盖当前用户选择。

## 函数

### `detect() -> bool`

无副作用、快速判断当前进程是否确实运行在该 Harness 中。没有或多个 Adapter 匹配时
Relay 停止并报告，不猜。用户显式指定 Harness 时不调用 detect。

### `read_runtime_context() -> dict`

读取当前 Harness/session 的真实观测值，不使用模型自述。读取失败的适用字段返回
`unavailable`，不抛出未处理异常。

### `preflight(ctx) -> {"ok": bool, "problems": [str], "error_code": str}`

纯读取、无创建副作用。除 Harness 自身检查外，必须检查：

- `working_directory` 可用；
- 所有 `applicable` 参数都有 expected 值；
- 当前 observed 值没有偏离 expected；
- `task_entry_sha256` 与上一轮携带的 hash 一致；
- Harness 创建接口和所需运行参数可访问。

### `create_and_confirm(ctx, payload) -> AdapterResult`

负责创建会话并在有限 observation window 内确认启动。Adapter 必须在
`HANDOFF_DEADLINE_SECONDS` 内返回。

```json
{
  "schema_version": 1,
  "status": "confirmed|failed|unknown",
  "retryable": false,
  "session_reference": "stable-id-or-empty",
  "submitted_parameters": {},
  "effective_parameters": {},
  "startup_evidence": {},
  "error_code": "",
  "error_summary": ""
}
```

`submitted_parameters` 必须反映实际提交或可靠继承的参数；不能用模型猜测填充。
`effective_parameters` 读不到时留空。`confirmed` 必须同时具备稳定会话标识、参数提交
一致性和真实启动证据。

`relay.py` 会验证 `schema_version`、状态、会话标识、启动证据和参数一致性；Adapter 若返回
`payload_sha256`，Relay 也会验证该 hash。验证失败会把结果降级为 `unknown`。Adapter 抛异常、超时或进程被终止，也统一
按 `unknown` 处理，因为无法证明 Harness 没有收到创建请求。

## 错误码

错误码使用稳定的大写短名称，例如：

```text
HARNESS_UNAVAILABLE
PRECHECK_FAILED
PARAMETER_UNAVAILABLE
PARAMETER_DRIFT
TASK_ENTRY_HASH_MISMATCH
ADAPTER_TIMEOUT
MISSING_CONFIRMATION_EVIDENCE
SESSION_NOT_CREATED
APPROVAL_BLOCKED
UNKNOWN
```

`error_summary` 只用于人类排查，长度受限且必须脱敏；不要把凭据、环境变量或完整
headers 放入结果。Runtime parameter 字段名不得是 secret/token/password 等凭据字段；
明显像凭据的参数值直接拒绝写入 RelayContext。

## 硬契约

1. Payload 使用 API、argv、stdin 或安全临时文件传递，禁止拼接未可靠 escaping 的 shell 字符串。
2. 新会话必须脱离 Adapter 调用、创建它的临时进程和旧会话进程组。
3. Adapter 必须有限等待；Relay 还会施加外层 hard deadline。
4. 不为了无人值守提升 permission、sandbox 或 approval。
5. approval / permission / sandbox 阻塞不能算 `confirmed`。
6. 不把安装位置私有配置写入正式 Skill 包。
7. 不泄露 Secret。
8. `confirmed` 只表示在 startup observation window 内已确认开始正常执行，不保证之后永不崩溃。

## 结果幂等

`relay.py` 在调用 Adapter 前对当前日志原子创建 handoff claim。同一日志：

- 已存在最终结果：直接返回结果；
- 只有 claim、没有最终结果：返回 `unknown`，禁止再次调用 Adapter。

这只是当前会话的一次性副作用保护，不是任务状态机、轮次管理或 Chain State。
