# Codex 最终参数继承修订（2026-10-08）

## 问题与决策

用户进一步明确：中途切换模型/思维深度是正常选择，下一会话应该沿用 handoff 时的最终
状态。本次按这项明确授权修订公共参数契约，适用范围仍限定 Codex。

源码证实旧实现 `start` 调用 `read_runtime_context()` 并将参数保存到日志；`handoff`
直接用这个日志构造 Payload，没有再读取当前状态。上一轮供应商兼容补丁没有改变这个
时机，因此并未覆盖本次提出的核心设计问题；API Key 不是这项修订的原因。

## 最终行为

- Codex 声明 `PARAMETER_CAPTURE_PHASE = "handoff"`。start 保留任务正文、hash、cwd、
  Harness 和显式输入，不读取模型等运行参数，不执行 Codex 参数 Preflight。
- handoff 检查结果/claim/STOP/hash，取得 claim 后读取当前 model、thinking、provider、
  permission、sandbox、approval、cwd；用可靠当前值生成最终 expected 快照，重新 Preflight。
- 最终快照保存为 `<log>.handoff.context.json`，包括采样时间；原日志头保留。Payload
  控制区使用最终快照，任务正文与 hash 仍保持不变。
- 不可读时停止创建，不回退到初始 observed、inherited 或 supplied 值；权限继承最新实际
  profile，启动环境变量不覆盖较新的 session 记录。初始 session_meta provider 也不覆盖
  最新 thread metadata 或显式后续设置。
- 新会话提交值与最终快照不符仍不能 confirmed。安全重试保持同一快照，重复调用不重读
  参数、不重复创建。cwd 仍必须与日志所在项目一致，STOP 作用域不迁移。
- 旧 schema-1 Codex 日志也在首次 handoff 重新采样，无需重新 start。其他 Adapter 默认仍
  使用 `start`，dsh 创建与参数策略不变。

上一轮修复中已有确定性证据的 JSONL 消息漏读、过早 confirmed、错误分类及 worker 释放
修复保留。供应商只读检查现在跟随 handoff Preflight 执行；不修改认证或供应商配置。

## 验证

修正了 `tests/test_relay_contract.py` 原先误指向 archive Demo 的路径，现测试正式源码。
新增命令级回归覆盖 start 不读取、旧日志最后参数继承、不可读停止、Preflight 失败、
STOP、claim、重复调用、同快照重试、hash 损坏、cwd 改变及子会话 effective 漂移。

真实 bundled app-server 的隔离全流程：

1. 使用临时 CODEX_HOME、临时 cwd、合成凭据及 localhost Responses 端点。
2. 创建父会话，初始模型 `model-at-start`，执行真正的 `relay.py start`。
3. 父会话中先切为 `intermediate-model` / medium，再切为 `relay-local-custom-model` / low。
4. 执行真正的 `relay.py handoff`，经过实际 Adapter runner 和 detached worker 创建子会话。
5. endpoint 记录证明子会话请求与 Payload 都使用最后模型及 low；最终快照与实际 runtime
   读取一致，重复 handoff 的结果相同且没有第二个 worker。

该测试只暂时移除自身创建的 STOP 以运行 handoff，finally 恢复；本地固定输出模型不能
继续执行工具或形成接力链。用户原有 STOP 不被触碰。其他隔离用例继续覆盖 env_key、bearer、
null effort、401、503；官方路径有协议回归；完整 suite 同时覆盖 dsh 和公共契约。

最终 62 项测试全部通过，Skill validator 与 diff 格式检查通过。

```bash
RUAN_CONTINUE2RUN_CODEX_INTEGRATION=1 python3 -m unittest discover -s tests -v
```

测试没有切换当前 Desktop 模型或供应商、修改当前 config/auth、重新登录或重启 Codex。
它证明完整 handoff 的参数采样与提交链路，不等价于线上供应商/官方订阅的再次验收，也不
证明下一会话自主执行 Skill 的完整无限接力。最终状态是采样瞬间的状态，无法保证用户在
采样之后再修改 UI 设置时与创建操作原子同步。

工程 src、dist、两处已知安装 Skill 与 `.skill` 包同步并逐文件核对；现行设计位于
`references/runtime-inheritance.md`，历史 archive 保持不变。
