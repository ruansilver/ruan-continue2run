# Codex Desktop Adapter 真实验收记录

日期：2026-10-04（Asia/Shanghai）

## 环境

- PATH `@openai/codex`：升级前 `0.156.1`，升级后 `0.160.0`；
- Codex Desktop 注入的 `CODEX_CLI_PATH` bundled CLI：`0.159.0-alpha.12.1`；
- 当前 thread runtime：model `gpt-6-astra`、reasoning `high`、provider `custom`、approval `never`、permission/sandbox `:danger-full-access`。

## 根因与修复

本轮真实复现发现两个 Codex app-server 适配问题：`thread/start.permissions` 需要先在
`initialize` 声明 `capabilities.experimentalApi=true`；新 thread 的有效 permission profile
来自 `thread/start` 返回值和 rollout 的 `turn_context.active_permission_profile`，而不是
`turn_context.permission_profile.type=disabled`。旧代码丢弃了 `thread/read` 响应顶层的
`activePermissionProfile`、`sandbox`、`approvalPolicy`，导致下一棒把 permission 观测为
`unavailable`。

针对 Desktop 锁定问题，进一步确认 Desktop 自身的 app-server 身份是
`clientInfo.name=codex_desktop`、`title=Codex Desktop`。旧 Adapter 使用
`ruan-continue2run`，因此新 thread 被 Desktop 标成“在其他应用中打开”。现场还发现旧
worker 在部分版本只收到 `thread/status/changed` 的 `idle` 而没有 `turn/completed`，会一直
持有 app-server 连接；这会持续保持同一 interlock。

修复内容：

- worker 在 initialize 协商 experimental API，并以 named `permissions` profile 创建 thread；
- Adapter 和 worker 复用 Desktop 的 `codex_desktop` client identity；
- worker 把已启动 turn 后的 `thread/status/changed: idle` 作为无 `turn/completed` 版本的终态，
  原子写入终态并关闭 app-server 子进程组；
- `thread/read` 保留顶层 effective metadata；
- rollout 解析 `active_permission_profile`，继续把未知或环境 profile 当作 unavailable；
- authoritative source 整体同步到 `dist`、`/home/ruan/.codex/skills/ruan-continue2run` 和
  `/home/ruan/.agents/skills/ruan-continue2run`，并重新打包 `.skill`。

## 最终真实验收

- 新 thread：`01a100da-7633-7e52-be9f-631a804b41b8`；
- `thread/start` 返回稳定 thread id；
- 观察到 `turn/started`；
- 新 thread 最终输出：`CODEX_PERMISSION_PROFILE_ACCEPTED`，turn 状态为 `completed`；
- 新 thread 的 `activePermissionProfile` 为 `:danger-full-access`；最终安装副本通过
  `thread/read` 观测到 permission `:danger-full-access`、sandbox `danger-full-access`、
  approval `never`，以及 model、thinking、cwd、provider；
- task payload 的中文、多行、引号、`$HOME`、反引号、反斜杠保持不变，长度和 hash 校验通过；
- 验收期间由 Maintenance 创建的 cwd STOP 已在子会话完成后删除，原本不存在的 STOP 未被误删。

本轮 26 个本地测试和 Skill validator 均通过。

本次最终 bundled app-server 验收还创建了临时 thread `01a10669-7693-7511-a26b-52e72cb53fae`，
观察到 `turn/started`、`READY` 和 `turn/completed`，并确认退出码为 0；通过 Adapter 创建的
thread `01a1066b-e245-7842-9624-d7d27c9e01f4` 返回 `confirmed`，其 startup evidence 为
`turn/started`。临时验收 thread 随后已归档。

这证明了当前 bundled app-server 的创建、Payload 传递、真实启动、Skill 再触发和独立测试 cwd 路径。没有把静态检查或旧 CLI 版本当作真实支持证明。
