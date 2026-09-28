# ruan-continue2run —— 工程目录

`ruan-continue2run` 是一个**会话接力编排 Skill**：被显式调用后，当前会话按"放养模式"执行任务，并在结束前用同一任务入口与运行参数创建下一个会话，形成接力。

本仓库按 Skill 工程化管理组织：**一份源码维护入口 + 一份可独立安装分发的产物目录**，开发维护文件不进安装产物。

> Skill 本身的说明（它做什么、一次接力怎么跑、Adapter 契约、日志规则、怎么新增 Harness）在
> [`src/ruan-continue2run/README.md`](src/ruan-continue2run/README.md)。

---

## 目录布局

```
ruan-continue2run/                      # 项目根（本目录）
├── README.md                           # 本文件：工程说明
├── build.py                            # 构建：src/ → dist/
├── install.py                          # 安装：dist/ → DSH 技能根
├── design/                             # 设计输入（唯一出处，只读参考）
│   ├── ruan-continue2run-design.md
│   └── demo/ruan-continue2run/         # 早期参考实现（历史留档）
├── src/ruan-continue2run/              # 源码（维护入口）
│   ├── README.md                       #   源码说明：结构职责 / 修改地图 / Harness 适配
│   ├── SKILL.md                        #   Skill 入口：只做流程编排
│   ├── scripts/                        #   编排与 Adapter 实现（Python 标准库）
│   └── references/                     #   运行期按需读的规则文档
└── dist/ruan-continue2run/             # 安装产物（只含运行必需文件）
    ├── SKILL.md
    ├── scripts/
    └── references/
```

| 路径 | 是什么 | 进安装产物吗 |
|---|---|---|
| `src/ruan-continue2run/` | **源码，唯一维护源**：改 Skill 只改这里 | 只有 `SKILL.md`/`scripts/`/`references/` 进 |
| `dist/ruan-continue2run/` | 由 `build.py` 生成的**可独立安装分发目录**（清空重建，不要手改） | 它就是产物本身 |
| `design/` | 设计文档与早期参考实现 | 不进 |
| `src/ruan-continue2run/README.md` | 源码说明 | 不进 |
| `build.py` / `install.py` | 构建 / 安装脚本 | 不进 |

---

## 工作流：改源码 → 构建 → 安装

```powershell
# 1) 改源码
#    src/ruan-continue2run/ 下的 SKILL.md、scripts/、references/

# 2) 构建：把源码同步成产物（清空重建 dist/ruan-continue2run/）
python build.py
python build.py --check          # 只检查产物是否最新

# 3) 安装：把产物装到技能根（同名目录会被清空重建）
python install.py
python install.py --dest "D:\repo\.dsh\skills\ruan-continue2run"   # 项目级技能根
```

- `build.py` 只把**运行必需内容**（`SKILL.md`、`scripts/`、`references/`）复制进产物，
  `README.md`、`design/`、`install.py`、`build.py`、`__pycache__` 一律不进。
- `install.py` **从产物目录安装**，不读源码；如果产物比源码旧，它会提醒你先跑 `build.py`。
- 分发（给别人 / 换机器）：直接打包或复制 `dist/ruan-continue2run/`，解压到目标机器的技能根即可。
- DSH 监视技能根：装完**不需要重启**，下一个模型步的会话目录就会刷新。

---

## 校验

```powershell
# 静态校验（DSH 技能契约）
python "$env:DSH_HOME\skills\skill-creator\scripts\dsh_validate.py" "$env:DSH_HOME\skills\ruan-continue2run"

# 触发探测（真实 headless 会话，看日志里有没有 skill 调用）
python "$env:DSH_HOME\skills\skill-creator\scripts\dsh_trigger_probe.py" "$env:DSH_HOME\skills\ruan-continue2run" `
  -q "ruan-continue2run 把 docs/plan.md 里的待办做完" `
  -q "接着把刚才没做完的活干完" `
  --keep
```

`trigger-probe-result.json` 是上一次触发探测的真实结果（正例命中、反例不触发）。

---

## 边界（不要越界）

本仓库只调整**目录组织**，不改变 Relay 架构、Adapter 契约与功能逻辑：

- Payload 仍然只有三部分：任务入口、Skill 自身、运行参数（含 Harness 标识）；不复制历史上下文。
- 不做任务状态管理、自动完成判断、自检、自动恢复；不判断新会话是否运行成功。
- 新增/修复 Harness 只改 `src/ruan-continue2run/scripts/adapters/<harness>.py`（详见源码 README 第 8 节）。
