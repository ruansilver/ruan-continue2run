# ruan-continue2run —— Skill 源码目录

> **这个 README 只属于 Skill 的源码目录**：给人（和后续进来的 AI）读的，不参与 Skill 运行，
> 也不会被安装进技能目录（`build.py` 的产物与 `install.py` 都自动排除它）。安装目录里不该出现 README.md。
> 整个工程的目录布局见项目根 [`README.md`](../../README.md)。

---

## 1. ruan-continue2run 是什么

一个**会话接力编排 Skill**。被显式调用后，当前会话按"放养模式"执行任务（用户不在场，遇到原本要停下来问的节点就自行判断并继续），并在会话结束前，用**同一任务入口和同一套运行参数**创建下一个会话，让下一个会话接着做同一件事，如此接力。

它解决的问题：跨会话接力开发时，每一轮都要人工"开新会话 → 选模型 → 选参数 → 粘贴接力提示词"。本 Skill 把这套动作自动化，并且每次都是**全新会话、不带历史上下文**。

它**只**负责会话接力编排和记录：

- 不管任务本身做得好不好；
- 不管新会话是否真的跑起来了；
- 不做状态管理、不做完成判断、不做自检、不做自动恢复、不复制历史上下文。

设计依据见 [`design/ruan-continue2run-design.md`](../../design/ruan-continue2run-design.md)（项目根的设计输入，唯一出处，只读参考）。

---

## 2. 这个目录是什么（源码 / 产物 / 安装目录）

Skill 的工程化管理分三层，只有第一层允许改：

| | ① 源码目录（你正在看的这里） | ② 安装产物 | ③ 安装目录（DSH 实际加载的） |
|---|---|---|---|
| 路径 | `src/ruan-continue2run/` | `dist/ruan-continue2run/` | `$DSH_HOME/skills/ruan-continue2run/`（默认） |
| 内容 | `SKILL.md` + `scripts/` + `references/` + `README.md` | 只有 `SKILL.md` + `scripts/` + `references/` | 只有 `SKILL.md` + `scripts/` + `references/` |
| 怎么来 | **手改，唯一维护源** | `python build.py` 从源码生成（清空重建） | `python install.py` 从产物安装（清空重建） |
| 给谁看 | 维护者、后续进来的 AI | 分发/安装用 | DSH 运行时 |
| 能不能改 | **只能改这里** | 不要手改，会被下次构建覆盖 | 不要改，会被下次安装覆盖 |

一句话：**改源码 → `python build.py` → `python install.py`**。产物和安装目录里都没有 README，也不需要有。

分发到别的机器，只需要 `dist/ruan-continue2run/` 这一个目录（解压到对方的技能根即可）。

---

## 3. 目录职责一览

```
ruan-continue2run/                      # 项目根（工程目录）
├── README.md                           # 工程说明：布局 + 构建/安装流程。不安装
├── build.py                            # 构建：src/ → dist/。不安装
├── install.py                          # 安装：dist/ → 技能根。不安装
├── design/                             # 设计输入（唯一出处，只读参考）。不安装
│   └── ruan-continue2run-design.md
├── src/ruan-continue2run/              # ① 源码（本目录）：唯一维护源
│   ├── README.md                       #   本文件。不安装
│   ├── SKILL.md                        #   唯一入口：只写流程编排，不含任何 Harness 创建细节
│   ├── references/                     #   运行期按需读的规则文档
│   │   ├── adapter-contract.md         #     Adapter 输入输出契约（权威定义）
│   │   ├── log-format.md               #     日志位置、命名、记录类型、事实原则
│   │   └── relay-override.md           #     放养模式的固定外壳文本
│   └── scripts/                        #   确定性逻辑，只用 Python 标准库
│       ├── relay.py                    #     命令行入口：open / decision / finish
│       ├── relay_context.py            #     解析第一条消息、推导 slug、构建/渲染 RelayContext
│       ├── relay_log.py                #     会话日志读写（每会话一个文件）+ 子进程输出落盘位置
│       ├── dispatch.py                 #     按 Harness 加载 Adapter，并强制执行输出契约
│       ├── config.py                   #     待确认默认值（触发写法、标签语法、日志目录名）
│       └── adapters/                   #     **所有 Harness 差异只允许出现在这里**
│           ├── registry.json           #       Harness 标签 → Adapter 模块
│           ├── __init__.py
│           ├── base.py                 #       result / not_implemented / pending_confirmation
│           ├── deepseek.py             #       DeepSeek Harness（已实现，见 8.3）
│           ├── codex.py                #       Codex CLI Harness（已实现，见 8.4）
│           └── claude_code.py          #       扩展位置（占位，未实现）
└── dist/ruan-continue2run/             # ② 安装产物：只含 SKILL.md + scripts/ + references/
                                        #    （由 build.py 从上面三项生成，可整目录分发）
```

产物目录与安装目录里**只会**出现 `SKILL.md`、`scripts/`、`references/` 三项；其余都是开发维护文件。

各部分的边界（改动时请守住）：

- **`SKILL.md`**：只描述"什么时候调用 `relay.py` 的哪个子命令、拿到什么、然后做什么"。它**不应该**出现任何 Harness 专有细节（比如 `dsh --profile headless`）。
- **`scripts/relay*.py` / `dispatch.py`**：与 Harness 无关的编排与记录逻辑。
- **`scripts/adapters/*.py`**：Harness 差异的唯一容身处。
- **`references/`**：规则文档。`adapter-contract.md` 是契约的权威定义，改契约要同时改 `dispatch.py` 与 `adapters/base.py`。

---

## 4. 一次接力是怎么跑的

```
会话开场   relay.py open
   │       读第一条用户消息 → 识别触发标记与 Harness 标签 → 构建 RelayContext → 建本会话日志
   │       输出 relay=false 就到此为止（按普通会话处理，链条结束）
会话正文   按 references/relay-override.md 放养执行任务
   │       每次关键决策：relay.py decision
会话收尾   relay.py finish        ← 本会话最后一个有副作用的动作
           ① 写意图日志（intent）
           ② 通过 Adapter 创建下一会话
           ③ 把创建调用结果（creation-call-result）追加到本会话日志
```

新会话拿到的第一条消息由 `relay_context.render_first_message()` 统一渲染：

```
ruan-continue2run

<任务入口原文>

//<HarnessName>
```

所以新会话同样会显式调用本 Skill，链条得以继续。**没有内部终止条件**：用户被动中断后随手发的消息里没有触发标记，新会话就按普通会话处理，链条自然结束。

Relay Payload 只有三部分，不多不少：

1. 用户显式任务入口（第一条消息里剥离标记与标签后的原文，逐字保留）
2. `ruan-continue2run` Skill 自身
3. 当前运行参数：模型、思维深度、工作目录，以及 Harness 标识

不传：历史对话上下文、链状态、轮次、上一轮中间结果。历史决策只给**指针**（`history_glob`），不注入内容。

---

## 5. 用户怎么显式调用

在**会话的第一条消息**里点名 Skill，末尾带上 Harness 标签：

```
ruan-continue2run

<接力提示词 / 任务入口>

//DeepSeek
```

- 必须显式出现 `ruan-continue2run`；只说"继续""接力"这类词**不会**触发（description 写死了这一点，误触发代价高）。
- 末尾 `//HarnessName` 决定用哪个 Adapter。标签在解析时被剥离，不属于任务入口。
- 没有标签时，调度层让已注册 Adapter 做无副作用的被动识别；只有唯一结果才会继续，并在渲染载荷时补上规范化标签。
- **只认第一条消息**：会话中途或收尾时再发的消息不进交接内容。

---

## 6. 日志

- 位置：`<工作目录>/.ruan-continue2run/`，每个会话一个独立文件 `<UTC时间戳>-<slug>.md`。
- 同一任务入口推导出同一个 slug，靠 slug 归类，会话之间不共享文件。
- 只记事实：`decision`（关键决策）/ `intent`（即将创建）/ `creation-call-result`（创建调用结果）。
- 不判断新会话是否运行成功——日志里只有"创建调用已发出 / 未发出"。
- Adapter 派生子进程的原始输出（stdout/stderr）落在 `<工作目录>/.ruan-continue2run/children/`，仅供事后排查，不属于会话日志语义。
- 日志写不进去时（例如会话跑在 `read-only` 沙箱里、工作目录不可写）：`open` 返回 `LOG_WRITE_FAILED`，
  会话按普通会话处理；`finish` 如果写不下 `intent` 就**不会**创建下一会话——宁可接力没发出，也不留下无记录的动作。
  这两种情况都只上报，不重试、不换目录（本 Skill 不做自我修复）。

怎么看：

```powershell
ls .ruan-continue2run/                                  # 本任务的历史会话日志
Select-String -Path .ruan-continue2run\*.md -Pattern 'creation-call-result' -Context 0,5
```

格式细节见 [`references/log-format.md`](references/log-format.md)。

---

## 7. 要改东西，改哪里

| 想改什么 | 改哪里 | 注意 |
|---|---|---|
| 触发的具体写法（是否允许 `/`、`$` 前缀等） | `scripts/config.py` 的 `INVOCATION_MARKER` + `relay_context._marker_pattern()` | 两者要一致 |
| Harness 标签语法（分隔符、位置） | `scripts/config.py` 的 `HARNESS_TAG_PREFIX` + `relay_context._tag_pattern()` | 改完要更新 `SKILL.md` 与 README 第 5 节的示例 |
| 日志目录名 / slug 规则 | `scripts/config.py` 的 `LOG_DIR_NAME` / `relay_context.make_slug()` | slug 变了会让历史日志分不到一起 |
| 放养模式的行为（自行判断的取向、记录决策的时机） | `references/relay-override.md` | 这是每轮固定叠加的外壳，不来自捕获 |
| 流程编排顺序、什么时候调用哪个子命令 | `SKILL.md` + `scripts/relay.py` | 不要把 Harness 细节写进这两个文件 |
| 日志格式、记录类型 | `references/log-format.md` + `scripts/relay_log.py` | 事实原则不要放宽 |
| Adapter 契约（输入输出结构） | `references/adapter-contract.md` + `scripts/dispatch.py` + `scripts/adapters/base.py` | 三处必须同步改 |
| **某个 Harness 的创建方式** | `scripts/adapters/<harness>.py`（必要时 `registry.json`） | 见第 8 节 |
| description 触发文案 | `SKILL.md` 的 frontmatter `description` | 改完建议重跑触发测试（第 9 节） |
| 构建/安装流程本身（哪些文件进产物、装到哪） | 项目根的 `build.py` / `install.py` | 只影响打包分发，不影响运行逻辑 |

---

## 8. Harness 支持：现状、新增、修复

### 8.1 状态表

| Harness | Adapter | 状态 |
|---|---|---|
| DeepSeek Harness (DSH) | `scripts/adapters/deepseek.py` | **已实现**（创建方式经本机探测确认，见 8.3） |
| Codex | `scripts/adapters/codex.py` | **已实现**（Codex CLI `exec`，见 8.4） |
| Claude Code | `scripts/adapters/claude_code.py` | 占位，未实现（返回 `NOT_IMPLEMENTED`） |

占位 Adapter 不会假装创建：它们如实返回 `issued=false` + `NOT_IMPLEMENTED`，`finish` 会把它记进日志。

### 8.2 新增一个 Harness

1. 复制 `scripts/adapters/codex.py` 为 `scripts/adapters/<module>.py`（模块名小写，多个词用下划线）。
2. 实现 `create(context) -> {"issued", "info", "error"}`，契约见 [`references/adapter-contract.md`](references/adapter-contract.md)：
   - 新会话第一条消息统一用 `relay_context.render_first_message(context)`；
   - 返回值用 `adapters.base.result / not_implemented / pending_confirmation` 构造；
   - 如需支持无标签场景，可提供无副作用的 `detect() -> bool`；只有唯一 Adapter 探测成功才会被选中；
   - 只做创建：不写日志、不重试、不自检、不判断新会话是否成功；
   - **没探明的事实不要假设**：写进 `PENDING_ITEMS` 并返回 `pending_confirmation`，不要写猜测性的调用。
3. 在 `scripts/adapters/registry.json` 加一条（标识、模块名、别名）。
4. 更新本 README 的状态表（8.1）与对应 Harness 小节的"探测到的事实"。

**不需要改**（如果改不动，说明差异泄漏进了核心，应该回到 Adapter 里解决）：
`SKILL.md`、`scripts/relay*.py`、`scripts/dispatch.py`、`references/*.md`。
`scripts/config.py` 只在确认了第 10 节的待确认默认值时才改。

### 8.3 DeepSeek Harness：探测到的事实与实现方式

以下都是**本机实测确认**的事实（不是文档推测），Adapter 就按这些事实写：

| 事实 | 证据 |
|---|---|
| 非交互、可脚本、能指定第一条消息的入口是 `dsh --profile headless [options] [task...]`；`task` 传 `-` 表示从 stdin 读 | `dsh --profile headless --help` |
| headless 只有 `--json`、`--session-id`、`-h`；**没有** `--model` / `--cwd` / 思维深度参数 | 同上 |
| 工作目录 = 子进程 cwd（只能这样表达） | 同上 + 实测 |
| 模型与思维深度来自 profile 配置 `agent-default-model`（`provider` / `model` / `reasoningEffort`） | `dsh --dump-config --profile headless` 显示 `provider: deepseek-official, model: deepseek-flash` |
| `dsh <profile> --patch <文件>` 能把一层补丁叠加到 profile 之上 | `dsh --help`；`--patch` 的格式见 `$DSH_HOME/profiles/<name>/cordis.patch.yml` 头部注释 |
| 叠加当前 profile 的补丁层后，headless 的模型参数变成当前会话那一套 | 实测 `dsh --profile headless --patch C:\Users\17144\.dsh\profiles\web\cordis.patch.yml --dump-config` → `provider: ruansilver, model: deepseek-v4.1-flash, reasoningEffort: max` |
| headless 会加载 `$DSH_HOME/skills` 下的技能，会话持久化到 `$DSH_HOME/sessions` | `dsh --dump-config --profile headless` 里有 `skill-filesystem`、`session-persistence-jsonl`；实测跑通并把会话写进 sessions |
| 从 Python 里 `subprocess.run(['dsh', ...])` 在本机会 `WinError 2`（PATH 里只有 `dsh.CMD`/`dsh.ps1`） | 实测 |
| Windows 下 `subprocess` 用 `DETACHED_PROCESS` 时，**子进程收不到 stdin 的数据与 EOF**（会被挂死） | 实测对比：`none` / `group` 正常，`detached` / `both` 卡死；改用 `CREATE_NO_WINDOW \| CREATE_NEW_PROCESS_GROUP` 后正常 |

于是 Adapter 的实际做法是：

```
node <npm 全局>/node_modules/@deepseek-ai/dsh/lib/bin.js \
     --profile headless --patch <当前 profile 的 cordis.patch.yml> -
        stdin = render_first_message(context)      # 第一条消息
        cwd   = runtime_params.working_directory
        子进程不等待（CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP / POSIX start_new_session）
        子进程 stdout/stderr → <日志目录>/children/<时间戳>-<slug>.out|.err
```

模型与思维深度**不是**靠猜：直接叠加当前 profile 的补丁层（Harness 自己的运行参数记录）。
`runtime_params.working_directory` 缺失或无效时不发起调用；项目级日志也只写入 RelayContext 指定的工作目录。

顺带的一个副作用（是想要的）：补丁层里通常也含当前会话的 `permission` 预设。接力会话是**无人值守**的，
沿用同一套权限预设才不会让它卡在审批提示上；子会话因此也需要对工作目录有写权限，否则它自己的日志写不下去
（表现为 `LOG_WRITE_FAILED`，见第 6 节）。

### 8.4 Codex Harness：探测到的事实与实现方式

以下事实由本机 `codex-cli 0.155.1` 的 CLI 帮助、安装布局检查和 Adapter 启动参数探针确认：

| 事实 | 证据 |
|---|---|
| 非交互创建入口是 `codex exec [OPTIONS] [PROMPT]`；PROMPT 使用 `-` 时从 stdin 读取 | `codex exec --help` |
| 模型由 `--model <MODEL>` 指定 | `codex exec --help` |
| 工作目录由 `--cd <DIR>` 指定 | `codex exec --help` |
| Codex CLI 没有独立的 reasoning-effort 选项；配置键为 `model_reasoning_effort` | `codex exec --help`、本机 `~/.codex/config.toml` |
| Windows 下优先使用原生 `codex.exe`；npm shim 无法直接作为 `Popen` 入口时解析到 `node .../codex.js` | 本机 `where.exe codex`、npm 包布局 |

Adapter 的调用形态是：

```text
codex exec [--model <model>] [--config model_reasoning_effort="<effort>"] \
  --cd <working-directory> --skip-git-repo-check -
stdin = relay_context.render_first_message(context)
```

它将 stdout/stderr 交给 `.ruan-continue2run/children/` 下的原始输出文件，启动后立即返回，
不等待、不读取、不判断子会话是否运行。`working_directory` 缺失或无效时不发起调用；模型和思维深度缺失时不覆盖 Codex 配置，
由调用方按 RelayContext 的 `null` 语义处理。

### 8.5 已知但**未采用**的其它入口

留档，避免以后重复探测：

- `dsh --profile sdk`：stdio JSON-RPC（`initialize {cwd, provider, model, reasoningEffort}` + `session/prompt`），参数表达最直接。**未采用**：它是常驻服务，stdin 关闭即退出，需要一个长期持有 stdin 的守护进程才不至于让新会话随父进程一起结束；本 Adapter 选择"一个会话就是一个进程"的 headless。
- `dsh web` 的 `POST /api/session/create` + `/api/session/prompt`：能给正在运行的 Web GUI 建会话。**未采用**：`/api` 有浏览器会话 cookie 鉴权（裸请求实测 401），要自签 cookie 才能调，不适合当作 Skill 的常规路径。
- `dsh acp`（`@deepseek-ai/dsh-acp-app`）：能否指定首条用户消息**无依据**，未探测。

### 8.6 某个 Harness 失效了怎么办（Skill 修复阶段）

开一个 Skill Creator 会话，用下面这段（填空后）作为开场提示词，让它在受控范围内改：

```
【ruan-continue2run · Skill 修复阶段】
目标 Harness：<名称>
类型：<新增支持 | 创建失效修复>
背景/现象：<例如：Harness 更新后 finish 返回 ADAPTER_EXCEPTION: ...>

请读取 ruan-continue2run 源码目录 src/ruan-continue2run/（含其中的 README.md），然后按以下固定要求工作：

1. 只允许修改：src/ruan-continue2run/scripts/adapters/<harness 模块>.py、
   src/ruan-continue2run/scripts/adapters/registry.json 中该 Harness 的条目，
   以及 src/ruan-continue2run/README.md 第 8 节里该 Harness 的说明。
2. 不得修改：SKILL.md、scripts/relay.py、relay_context.py、relay_log.py、dispatch.py、
   references/ 下的任何文件。config.py 只有在我明确确认某个待确认默认值时才能改。
3. Adapter 必须满足 references/adapter-contract.md：输入 RelayContext，输出
   {issued, info, error}；issued 只表示创建调用是否已发出。
4. 不要假设 Harness 的行为：先探测（读它的 help / 配置 / 代码，实际试运行）或问我，
   确认后再写。无法确认的项写进 PENDING_ITEMS 并返回 pending_confirmation，不要写猜测性调用。
5. 不新增任务状态管理、自动完成判断、自动恢复、自检；不复制历史上下文；不改变 Relay Payload。
6. 完成后汇报：改了哪些文件、探测到的事实（含证据）、仍待确认的项。改完记得在项目根跑
   python build.py 与 python install.py 让安装目录生效。
```

---

## 9. 修改之后：重新构建 / 安装

改完源码（本目录）之后，回到**项目根**依次跑两个脚本：

```powershell
cd <项目根>

python build.py                  # ① 源码 → 产物（清空重建 dist/ruan-continue2run/）
python build.py --check          #    只检查产物是否最新

python install.py                # ② 产物 → 技能根（清空重建目标目录）
python install.py --dest "D:\repo\.dsh\skills\ruan-continue2run"   # 装到项目级技能根
python install.py --product "D:\other\ruan-continue2run"          # 从别处的产物目录装
```

- `build.py` 只把运行必需的三项（`SKILL.md`、`scripts/`、`references/`）复制进产物；
  `README.md`、`design/`、`install.py`、`build.py`、`__pycache__` 一律不进。
- `install.py` **只从产物目录安装、不读源码**；如果检测到源码比产物新，它会提醒你先跑 `build.py`。
- 「构建 → 安装」中间那层产物目录，就是可独立分发的 Skill 目录：打包/复制 `dist/ruan-continue2run/` 即可。
- （两个脚本都用 Python 写，是为了不踩 Windows PowerShell 5.1 读 UTF-8 无 BOM 脚本会乱码的坑；本 Skill 本来就只依赖 Python 3。）

装完的验证：

```powershell
# 1) 静态校验（DSH 技能契约：name/description/目录是否在被扫描的技能根下…）
python "$env:DSH_HOME\skills\skill-creator\scripts\dsh_validate.py" "$env:DSH_HOME\skills\ruan-continue2run"

# 2) 真实触发探测（用 skill-creator 的探测脚本，看会话日志里有没有 skill 调用）
python "$env:DSH_HOME\skills\skill-creator\scripts\dsh_trigger_probe.py" "$env:DSH_HOME\skills\ruan-continue2run" -q "ruan-continue2run 把这个任务做完"
```

DSH 监视技能根：装完**不需要重启**，下一个模型步的会话目录就会刷新（只改 `references/` 内容不刷新目录，但调用技能时正文是现读的）。

---

## 10. 待确认默认值（设计文档第 10 节）

设计文档把这几项标为"需要现场确认"，当前实现取了保守默认值，全部集中在 `scripts/config.py`：

| 待确认项 | 现在的默认做法 |
|---|---|
| 显式触发的具体写法 | 第一条消息里出现 `ruan-continue2run`（允许 `/`、`$`、`@` 前缀） |
| Harness 标签语法 | 消息末尾 `//HarnessName`（解析时剥离） |
| 日志目录名 | `<工作目录>/.ruan-continue2run/` |
| slug 规则 | 任务入口的 ASCII 前缀（≤24 字符）+ SHA-1 前 8 位 |
| 运行环境是否提供 Python 3 | 只用标准库，按 Python 3 写（本机 `python` = 3.12） |
| 无 Harness 标签时的被动识别 | 由已注册 Adapter 的无副作用 `detect()` 竞争；没有唯一结果时 `finish` 返回 `HARNESS_UNRESOLVED` |
| DeepSeek 的创建方式 | **已确认并实现**（见 8.3） |
| Codex 的创建方式 | **已确认并实现**（见 8.4） |

---

## 11. 设计边界：不要加回来的东西

以下都是设计文档明确排除的，别因为"看起来更完善"而加进来：

- 任务状态管理（进度、轮次、链状态）
- 自动完成判断（判断任务是否做完、是否还要接力）
- 自检机制（检测创建是否成功、是否需要新增适配、定期巡检）
- 判断新会话是否真正运行成功
- 自动恢复 / 重试
- 复制、注入历史上下文

一处实现层面的保护：`finish` 在同一个会话日志里只会执行一次（日志里已有 `intent` 就拒绝再次执行），避免同一会话重复创建下一会话。它只读本会话自己的日志，不构成额外的状态管理。
