# Adapter 契约

本文件是 Adapter 接口的权威定义。核心 Skill（`SKILL.md`、`scripts/relay*.py`、`scripts/dispatch.py`）**不包含任何 Harness 的创建细节**；所有 Harness 差异都封装在 `scripts/adapters/<module>.py` 里，且都必须满足本契约。

契约是**设计和规则**层面的约定（输入、输出、允许做什么）。"某个 Harness 具体怎么创建会话"属于实现细节，写在对应 Adapter 里，不属于本文件。

## 接口

每个 Adapter 模块必须提供一个函数：

```python
def create(context: dict) -> dict:
    ...
```

可选地提供无副作用的 `detect() -> bool`。没有 `//HarnessName` 标签时，调度层会调用每个已注册
Adapter 的 `detect()`，只有**唯一一个**返回 `True` 时才选中它；没有或多于一个匹配时保持
`HARNESS_UNRESOLVED`。探测只能读取当前 Harness 的环境/进程事实，不能启动会话、写状态或修改载荷。

调用方是 `scripts/dispatch.py`（`run_adapter`）。核心流程不会直接 import 任何具体 Adapter，只通过注册表（`scripts/adapters/registry.json`）+ 本契约与它们交互。

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

- 这就是 Relay Payload 的运行时形式：任务入口、Skill 自身、运行参数（含 Harness 标识），**不多不少**。
- 取不到的运行参数是 `null`，Adapter **不得自行猜测填充**；确认不了的值就不填。无法处理的 `null` 要在返回的 `error` 或 `info` 里如实说明。
- 新会话的第一条消息用 `relay_context.render_first_message(context)` 统一渲染（触发标记 + 任务入口 + Harness 标签），避免各 Adapter 各自拼装造成差异。
- Adapter 可以读取**自己进程的环境**（例如 Harness 注入的环境变量、profile 配置）来把运行参数落到具体调用上——那是 Harness 适配层的事，不改变上面的载荷结构，也不往载荷里加东西。

## 输出：创建调用结果

```json
{ "issued": true, "info": "字符串或 null", "error": "字符串或 null" }
```

| 字段 | 含义 |
|---|---|
| `issued` | 布尔。创建调用是否**已发出**。 |
| `info` | 调用返回的信息（例如会话标识、命令、进程号）。没有则 `null`。 |
| `error` | 出错信息。`issued=false` 时必须给出；`issued=true` 时为 `null`。 |

**`issued` 只描述"创建调用本身"**：它不表示新会话已经运行，更不表示运行成功。Adapter 不得探测、等待、轮询或判断新会话之后的表现。

## Adapter 必须遵守

- 只做"创建下一会话"这一件事：不写会话日志（日志由核心流程写）、不读写任何任务状态、不复制历史上下文。
- 不重试、不自我修复、不做自检；失败就如实返回 `issued=false` 和 `error`。
- 对 Harness 行为无法确认时**不要假设**：返回 `base.pending_confirmation(...)` 并列出待确认项，不要执行猜测性的调用。
- 尚未实现的扩展位置返回 `base.not_implemented(...)`。
- 可以复用 `adapters/base.py` 的 `result / not_implemented / pending_confirmation` 构造返回值。

## 调度层如何保证契约

`dispatch.run_adapter` 会把下列情况统一转成 `issued=false` 的如实结果，**不会崩溃、不会重试、不会自动修复**：

| error 前缀 | 情况 |
|---|---|
| `HARNESS_UNRESOLVED` | 没有 Harness 标签且 Adapter 被动探测没有唯一结果 |
| `HARNESS_UNREGISTERED` | 标签不在注册表 |
| `ADAPTER_LOAD_FAILED` | Adapter 模块加载失败 |
| `ADAPTER_EXCEPTION` | `create()` 抛出异常 |
| `ADAPTER_CONTRACT_VIOLATION` | 返回值不符合上面的输出结构 |

## 注册表

`scripts/adapters/registry.json` 把 Harness 标识、别名映射到 Adapter 模块：

- 别名匹配不区分大小写，`_` 与空格视同 `-`（先归一化再比较）。
- `status` 字段仅供人阅读，不参与逻辑。
- 新增 Harness = 新增 `adapters/<module>.py` + 在注册表加一条；核心脚本与日志格式不动。

## 扩展方式（设计文档第 7 节）

- 新增某个 Harness 支持：新增一个对应的 Adapter，`SKILL.md` 与日志格式不动。
- 某个 Harness 更新后创建失效：只修该 Harness 对应的 Adapter。
- 以上都通过用户显式进入"Skill 修复阶段"完成（见源目录 `README.md` 第 8.6 节），不触碰其他部分。
