# DeepSeek Harness (dsh) Adapter 真实验收记录

日期：2026-10-03（Asia/Shanghai）

## 环境

- DSH CLI：`@deepseek-ai/dsh` 0.2.0-rc.1（`/home/ruan/.nvm/versions/node/v24.21.0/bin/dsh`）；
- 当前 Harness：profile `web`（`DSH_WEB_URL=http://127.0.0.1:3080`），当前 Session
  `session-e036cf0c-e557-4d78-ad78-141882770435`；
- 当前运行参数（从 Session durable log 读取，非模型自述）：cwd
  `/mnt/ext_drive/workspace/AI workspace/Promgram/ruanskills/ruan-continue2run`、
  model `deepseek-v4.1-flash`、provider `ruansilver`、thinking `max`、
  permission/sandbox `danger-full-access`、approval `never`；
- 创建用 profile：`headless`（`$DSH_HOME/profiles/headless`）。

## 创建面探测结论

- `dsh --profile headless --json -` 是官方文档化的一次性运行面：创建持久 Session、
  首个 stdout 帧给出 `session` 事件（sessionId + cwd），随后是 `status`/`text`/
  `thinking`/`tool_call`/`tool_result`/`final` 事件流，任务结束后自行退出；
- Web GUI 的 `/api` 通道受 browser-trust 栅栏保护（无 cookie 的请求一律 401），
  启动 token 只存在于 Web 进程内存中，因此不以 Web API 作为创建面；
- headless 创建出的 Session 落在标准 Session store（`$DSH_HOME/sessions/<cwd-key>/<id>/`），
  并出现在 Web GUI 的会话列表中且可打开查看内容（用户实测确认，见下）。

## 根因与环境修复

`headless` profile 的用户层原本是空数组，既没有 provider 表也没有默认 model route。
Adapter 第一次真实创建时只能用 `--patch` 自带 provider 表，而接力链的下一跳仍运行在
`headless` profile 中，第二跳就会因为 route 缺失而无法创建。

修复：把主（web）profile 组合出的 provider 表与默认 model route 生成进
`$DSH_HOME/profiles/headless/cordis.patch.yml`，使创建 profile 自身具备与当前 Session
相同的 model route。Adapter 的 Preflight 用 `dsh --profile headless --dump-config`
验证该 route 存在，缺失时以 `DSH_MODEL_ROUTE_UNAVAILABLE` 失败并给出要修改的文件。

## 最终真实验收（第三轮，最终代码）

路径：当前正式 Skill → dsh Adapter → 真实 DSH → 创建真实新会话。

- 测试 cwd：本仓库 cwd（原本没有 `.ruan-continue2run/STOP`）；
- `relay.py start`：`ok=true`、`stop=false`，task entry sha256
  `2ff2a5c6daf3fe1f5bdf5a1fb57f2258b811d9791905db4eeb4416a08c873822`；
- `relay.py handoff`：`outcome=confirmed`，用时约 33s，
  `session_reference=session-4bd44047-5e92-4620-a5d6-d1e172791a85`，
  payload sha256 `f2f92fcd5030219df652ad13c1eb73449974299a25d5d8904a7ce55dde3d6c08`；
- `submitted_parameters` 与 `effective_parameters` 的 7 个字段完全一致
  （cwd / model / thinking / permission / sandbox / approval / provider）；
- 新 Session 的 durable log 中第一条 `user/message`（`source.kind=user`）字节数 1800、
  sha256 等于上述 payload sha256：Payload 逐字节到达，含中文、多行、引号、`$`、
  反引号、反斜杠；
- Adapter 返回后新 Session 继续运行：它再次进入本 Skill，运行
  `relay.py start --message-file`（自身日志
  `.ruan-continue2run/relay/20261003T064602Z-dsh-adapter-1-2ff2a5c6-0eef.log`），
  得到同一 `task_entry_sha256`，并把 `log_path` / `task_entry_sha256` 写入
  `./RC2R_DSH_ACCEPT_PROOF.txt`；
- 链式 `relay.py start` 在该 headless Session 内 `preflight: ok`，说明第二跳的
  runtime 观测与 model route 校验同样成立；
- 新 Session 自己创建 `.ruan-continue2run/STOP`，接力链在该轮终止；
- 新 Session 把收到的消息原样写出的副本（1800 字节）sha256 同样等于 payload sha256。

前两轮记录（同一路径、同一脚本，用于确认修复与减法后行为一致）：

| 轮次 | 新 Session | task entry sha256 | 结果 |
|---|---|---|---|
| 第一轮（初版） | `session-c9788601-5280-45b3-af1b-4d5471e7375d` | `0f25b9b4…` | confirmed，链式 preflight ok |
| 第二轮（减法后） | `session-0aa67d65-0016-40d7-baea-d07af55a66b4` | `3923c67a…` | confirmed，链式 preflight ok |
| 第三轮（最终） | `session-4bd44047-5e92-4620-a5d6-d1e172791a85` | `2ff2a5c6…` | confirmed，链式 preflight ok |

## GUI 可见性

用户实测确认：headless 创建出的会话（探测会话
`session-5f0d943b-c9ca-4611-b445-fad6afe4d4c1`，任务为只回复
`RC2R_DSH_GUI_PROBE_OK`）出现在 Web GUI 该 workspace 的会话列表中，可以打开并看到内容。

## 减法

- 观察窗口 48s + 日志校验 6s，仍在 Adapter 声明的 60s deadline 内；
- `confirmed` 时删除 run dir 里的 payload 副本（Session log 已是权威副本），
  保留 `overlay.yml`、`events.jsonl`、`dsh-stderr.txt` 作为诊断材料；
- 修正事件文件增量读取：进程退出前最后一帧也会被解析，避免把已创建 Session 误判为
  `failed`；
- 去掉未使用的 `DSH_PROFILE`/`DSH_PROFILE_DIR` 读取分支与跨 profile provider 提取逻辑。

## 验收与 authoritative copy

- 通过最终验收的 Adapter 代码：`scripts/adapters/dsh.py`，sha256
  `fd28b73133830841df2b453f6a5fc9f8dadba5950500bd27d895720ae2459911`
  （mtime 14:43:13，早于最终验收 handoff 的 14:43:55，之后未再改动）；
- 最终验收后只补充了文档（`SKILL.md` 的文件清单一行、`references/dsh.md`、
  本报告、README），不涉及 Adapter 行为；
- authoritative copy：`/home/ruan/.dsh/skills/ruan-continue2run`（DSH 实际加载位置）；
- 使用 `scripts/sync.py` 整体同步到三个已知位置，全部 `status=synced`、文件级校验一致：
  - `/mnt/ext_drive/workspace/AI workspace/Promgram/ruanskills/ruan-continue2run/src/ruan-continue2run`（维护源）；
  - `…/ruan-continue2run/dist/ruan-continue2run`（可安装目录，并重打包 `dist/ruan-continue2run.skill`）；
  - `/home/ruan/.agents/skills/ruan-continue2run`（既有安装位置）。

## 未验证项

- 非 Linux 平台、非 zstd 日志格式；
- `permission_mode` 为 `custom`/`auto`（代码有分支，未真实验收）；
- `approval_mode=ask` 的阻塞路径（按契约返回 `unknown`）；
- 新 Session 的 GUI 交互式续聊（它是一次性 headless 运行）。

## 单元测试

`tests/test_dsh_adapter.py`（14 项）用假 `dsh` launcher 覆盖：契约声明、runtime 观测、
overlay 生成与 YAML 有效性、preflight 的 route 校验、payload 保真、payload 篡改、
未创建 Session、effective 参数漂移、approval 阻塞、缺少执行事件、Session log 不可读。
