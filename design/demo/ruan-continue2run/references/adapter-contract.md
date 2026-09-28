# Adapter 契约

核心 Skill 不包含任何 Harness 的创建细节。所有 Harness 差异都封装在 `scripts/adapters/<module>.py` 里，并且都必须满足本契约。

## 接口

每个 Adapter 模块必须提供一个函数：

```python
def create(context: dict) -> dict:
    ...
```

调用方是 `scripts/dispatch.py`，核心流程不会直接 import 任何具体 Adapter。

## 输入：RelayContext

```json
{
  "task_entry": "用户显式任务入口（已剥离触发标记与 Harness 标签，逐字保留）",
  "skill": "ruan-continue2run",
  "runtime_params": {
    "model": "当前模型标识，取不到为 null",
    "thinking_depth": "当前思维深度，取不到为 null",
    "working_directory": "当前工作目录，取不到为 null"
  },
  "harness": {
    "tag": "用户写的原始 Harness 标签，可为 null",
    "id": "注册表里解析出的标识，可为 null"
  }
}
```

- 这就是 Relay Payload 的运行时形式：任务入口、Skill 自身、运行参数（含 Harness 信息），不多不少。
- 取不到的运行参数是 `null`，Adapter 不得自行猜测填充；无法处理 `null` 时，应在返回的 `error` 里如实说明。
- 新会话的第一条消息用 `relay_context.render_first_message(context)` 统一渲染，避免各 Adapter 各自拼装造成差异。

## 输出：创建调用结果

```json
{ "issued": true, "info": "字符串或 null", "error": "字符串或 null" }
```

| 字段 | 含义 |
|---|---|
| `issued` | 布尔。创建调用是否**已发出**。 |
| `info` | 调用返回的信息（例如会话标识、命令输出摘要）。没有则 `null`。 |
| `error` | 出错信息。`issued=false` 时必须给出；`issued=true` 时为 `null`。 |

**`issued` 只描述"创建调用本身"**：它不表示新会话已经运行，更不表示运行成功。Adapter 不得探测、等待或判断新会话之后的表现。

## Adapter 必须遵守

- 只做"创建下一会话"这一件事，不写日志（日志由核心流程写）、不读写任何任务状态、不复制历史上下文。
- 不重试、不自我修复、不做自检；失败就如实返回 `issued=false` 和 `error`。
- 对 Harness 行为无法确认时不要假设：返回 `base.pending_confirmation(...)`（列出待确认项），不要执行猜测性的调用。
- 尚未实现的扩展位置返回 `base.not_implemented(...)`。

## 调度层如何保证契约

`dispatch.run_adapter` 会把下列情况统一转成 `issued=false` 的如实结果，不会崩溃、不会重试：

| error 前缀 | 情况 |
|---|---|
| `HARNESS_UNRESOLVED` | 没有 Harness 标签，无法选 Adapter |
| `HARNESS_UNREGISTERED` | 标签不在注册表 |
| `ADAPTER_LOAD_FAILED` | Adapter 模块加载失败 |
| `ADAPTER_EXCEPTION` | `create()` 抛出异常 |
| `ADAPTER_CONTRACT_VIOLATION` | 返回值不符合上面的输出结构 |

## 注册表

`scripts/adapters/registry.json` 把 Harness 标识、别名映射到 Adapter 模块。别名匹配不区分大小写，`_` 与空格视同 `-`。`status` 字段仅供人阅读，不参与逻辑。
