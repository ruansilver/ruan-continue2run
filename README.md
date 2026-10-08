# ruan-continue2run

`ruan-continue2run` 是一个仅显式触发的轻量会话接力 Skill。它负责把当前 session 的稳定 task entry、运行参数和显式 Skill 调用传给下一真实 session，并在有限观察窗口内确认下一 session 已开始执行。

2.0 版本的核心契约包括：

- expected / observed 运行参数分离；
- task entry 长度定界和 `task_entry_sha256` 保真校验；
- cwd 级 STOP；
- 当前日志的原子 handoff claim，防止重复创建；
- Adapter 独立进程和 Relay 外层 deadline；
- `confirmed` / `failed` / `unknown` 三态结果；
- Maintenance 真实 Harness 验收和 authoritative copy 整体同步。

Codex 现行参数策略：在 handoff 准备创建下一会话时读取当前最终模型、思维深度和运行参数。
start 只 Capture 任务与 cwd，不锁定运行参数；其他 Harness 保持原策略。设计见
`src/ruan-continue2run/references/runtime-inheritance.md`。

## 目录

- `src/ruan-continue2run/`：维护源；
- `dist/ruan-continue2run/`：可安装 Skill 目录；
- `dist/ruan-continue2run.skill`：打包文件；
- `archive/v2.0/design/`：2.0 设计和 Demo 对账材料；
- `tests/`：契约和 Adapter 测试；
- `reports/`：真实 Harness 探测与验收记录。

## 检查

```bash
python3 -m unittest discover -s tests -v
python3 /home/ruan/.agents/skills/skill-creator/scripts/quick_validate.py src/ruan-continue2run
```

未完成真实 Harness 验收的 Adapter 不得静态宣称支持。真实验收记录：

- Codex Desktop Adapter：`reports/codex-desktop-acceptance.md`；
- Codex 供应商兼容补丁与隔离验收边界：`reports/codex-provider-compatibility.md`；
- Codex 最终参数采样修订与完整 handoff 验证：`reports/codex-handoff-parameters.md`；
- DeepSeek Harness (dsh) Adapter：`reports/dsh-acceptance.md`，创建面与边界见
  `src/ruan-continue2run/references/dsh.md`。

Codex 隔离集成验证使用临时 CODEX_HOME 和 localhost 供应商，不改当前配置：

```bash
RUAN_CONTINUE2RUN_CODEX_INTEGRATION=1 python3 -m unittest discover -s tests -v
```
