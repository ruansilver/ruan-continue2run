# Codex Desktop Adapter 真实验收记录

日期：2026-10-03（Asia/Shanghai）

## 环境

- PATH `@openai/codex`：升级前 `0.156.1`，升级后 `0.160.0`；
- Codex Desktop 注入的 `CODEX_CLI_PATH` bundled CLI：`0.159.0-alpha.12.1`；
- 当前 thread runtime：model `gpt-6-astra`、reasoning `high`、provider `custom`、approval `never`、permission/sandbox `:danger-full-access`。

## 根因与修复

第一次真实创建可以启动 thread，但新 thread 读取的是 `/home/ruan/.agents/skills/ruan-continue2run/SKILL.md` 的旧 1.0 安装副本。问题不是 app-server 创建失败，而是实际生效 Skill 未同步。

随后使用 2.0 authoritative copy 整体同步到：

```text
/home/ruan/.agents/skills/ruan-continue2run
```

同步后完成文件级校验。

## 最终真实验收

- 新 thread：`01a0ffd3-d4bb-7363-9520-84d174e32c5c`；
- `thread/start` 返回稳定 thread id；
- 观察到 `turn/started`；
- thread 状态最终为 `completed`；
- 测试输出：`CODEX_DESKTOP_V2_INSTALLED_PROBE` 和测试 cwd；
- 新 thread 读取到同步后的 2.0 Skill 内容；
- 测试 cwd 内创建 STOP，未继续创建下一 Relay。

这证明了当前 bundled app-server 的创建、Payload 传递、真实启动、Skill 再触发和独立测试 cwd 路径。没有把静态检查或旧 CLI 版本当作真实支持证明。
