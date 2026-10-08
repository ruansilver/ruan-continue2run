# Codex 供应商兼容修复记录（2026-10-08）

后续用户明确要求将运行参数采样从 start 移到 handoff；该设计修订及完整流程验证见
`codex-handoff-parameters.md`。本文件保留此前维护事实，不代表最新的参数固定规则。

## 用户报错的实际来源

只读核对用户提供的 thread `01a119ea-c609-7750-b609-0dcb816517e3` 后确认：

- 2026-10-08 13:09:40（Asia/Shanghai）首次 turn 使用 `gpt-6-sol`、provider `custom`；
- 13:10:37 收到 HTTP 503，供应商报告该精确模型没有可用渠道；
- 首次失败之前未执行 Skill 或任何工具，没有 `relay.py start` / `handoff`；
- 13:12:19 的后续 turn 使用 `gpt-6.1-sol`，随后开始工具执行。

因此，这份记录不能证明接力脚本把模型改错，补丁也不能使供应商未提供渠道的精确模型可用。
当前会话实际可执行，是当前 provider/model 路径可用的直接证据；不要由此自动改模型名、
切换供应商、修改订阅配置或要求用户重启当前 Codex。

## 已确认并修复的 Adapter 缺陷

1. 旧 `selectors + TextIOWrapper.readline` 会把后续 JSONL 留在用户态缓冲中而不再触发
   selector；同一次 flush 的多条消息、分段 Unicode 和限时读取有确定性回归。
   新 Codex 私有 reader 使用 daemon 线程和逐行队列，持续保存后续通知。
2. 旧第三方路径收到 `turn/started` 或 `item/started:userMessage` 就可能 confirmed，
   即使紧接着模型请求返回 401/503。现在非 openai provider 等待模型消息或工具执行证据，
   已创建 thread 但模型失败时返回 unknown、保留 ID 并退出 worker，禁止重复创建。
3. Runtime 读取按顺序应用最新 rollout 设置，保留用户实际 model/effort/provider；
   显式空 effort 编码为 observed `null`，提交时使用 JSON null，仍为 applicable。
   字段缺失继续为 unavailable，不拿配置默认值代替 session 观测值。
4. 第三方 Preflight 只读查询独立 app-server 的有效配置并核对 provider 定义与声明的
   env_key 可见性。不拷贝供应商凭据、不统一要求官方登录、不做认证/模型自动降级。
5. 不写原始协议调试日志，错误分类保留 HTTP 状态和稳定错误码，避免响应回显凭据。
   终态均释放 app-server；启动通知先于 RPC 回应的顺序也有回归覆盖。

修改局限于 Codex Adapter、worker、私有协议 reader、Codex 文档与工程测试/记录；
公共 Relay 核心、Adapter Contract 和 dsh Adapter 未修改。

## 最终验证与边界

真实 launcher：`/usr/lib/chatgpt/resources/codex`，版本 `0.162.0-alpha.2`。
测试从当前安装的正式 Skill 加载 Adapter，为每个案例创建临时 CODEX_HOME 和 cwd，
仅请求 localhost Responses SSE 端点并使用合成凭据。测试 cwd 的 STOP 由测试创建，随临时
目录清理；用户已有 STOP 不被触碰。

真实 bundled app-server 的五条路径通过：env_key、experimental_bearer_token、null effort、
延迟 HTTP 401、延迟 HTTP 503（No available channel）。成功案例使用 `relay.build_payload`
生成完整控制帧，核对中文、多行、引号、美元符号、反引号、反斜杠、参数与 hash；本地端点
收到相同 Payload 和 model。创建者退出后仍继续流式执行，终态后 worker 正常退出。
失败案例均未提前 confirmed，保留 session reference，返回字符串错误码。

官方 openai 用独立假 CLI 验证原有启动语义、Desktop client 身份、model/provider、
permissions、effort 和 Payload 提交，不增加 provider config override。原有 dsh 和公共契约
测试同时回归。全部 47 项测试通过，Skill validator 通过。

```bash
RUAN_CONTINUE2RUN_CODEX_INTEGRATION=1 python3 -m unittest discover -s tests -v
python3 /home/ruan/.agents/skills/skill-creator/scripts/quick_validate.py src/ruan-continue2run
```

验收覆盖真实 Harness 的创建/执行/生命周期，但 localhost 模型仅返回固定输出，未证明
下一 session 实际读取并再次执行 Skill，也没有创建当前 Desktop 用户可见的生产测试会话。
未请求真实第三方端点或重新验证官方在线订阅，不能将本次维护宣称为完整生产 Relay 验收。
Desktop 仅内存生效的 provider override、会内切换 provider 与 session_meta 的优先级，
以及其他平台/版本，仍需各自真实环境验证。

当前 `/home/ruan/.codex/config.toml`、`auth.json` 的维护前后 SHA-256 一致。
未切换当前供应商/订阅、改配置、注销/重新登录或重启 Desktop。

正式修复副本整体同步到工程 src、dist、`/home/ruan/.codex/skills/ruan-continue2run`，
与 `/home/ruan/.agents/skills/ruan-continue2run` 逐文件核对，再生成 `.skill` 压缩包。
