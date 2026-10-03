# Adapter Maintenance

目标是把当前 Harness 的真实 Relay 链重新跑通，而不是把代码改到“看起来应该好了”。

## 进入条件与边界

用户必须明确当前 Harness 以及要检查、创建或修复它的 Adapter。Maintenance 可以接收用户
后续提供的报错、路径、环境信息和测试结果；Relay 的稳定 task entry 规则不限制 Maintenance。

直接修改当前真实 Harness 实际加载的 Skill。不要使用 stable/candidate 副本、临时切换或
自动回滚。一次 Maintenance 只修改当前 Harness Adapter 及其私有必要辅助实现；发现必须
修改公共 Adapter Contract、`relay.py` 核心行为或多个 Harness 的共享逻辑时，停止并交回
Skill 设计修订。

Maintenance 默认串行执行；同一 cwd 不同时运行正式 Relay。

## 先探测，不假设

必须在真实环境确认：显式调用与 Harness 标签语法、runtime 参数读取、会话创建和安全
Payload 传递、cwd/model/thinking/permission/sandbox/approval 的继承、唯一 session reference、
startup event、effective metadata、approval 阻塞、生命周期独立、failed 白名单、retryable
错误、timeout/observation window 和已知安装位置。无法实际验证的项标记为未验证，不能静态
宣称支持。

## 真实验收

验收路径必须是：

```text
当前正式 Skill → 当前 Adapter → 真实 Harness → 创建真实新会话
```

测试前检查测试 cwd 是否已有 `.ruan-continue2run/STOP`：

- 原本不存在：Maintenance 创建，并在验收结束后只删除自己创建的 STOP；
- 原本已存在：验收结束后不得删除用户原来的 STOP。

测试必须确认：

1. Payload 真正包含 `ruan-continue2run`，下一 session 再次进入本 Skill；
2. task entry 含中文、多行、引号、`$`、反引号、反斜杠等内容，经过长度定界和 hash 校验后不变；
3. 使用正常 Relay 对应的 cwd/model/thinking/permission/sandbox/approval；
4. Adapter 返回、创建者临时进程退出后，新会话仍继续运行；
5. 机器侧通过后，用户能够看到测试会话并确认其输出。

## 最终减法与复验

初步跑通后清理 debug 输出、临时入口、废弃分支、无用兼容逻辑和没有价值的兜底。减法
修改代码，因此必须再次完整真实验收。只有最后一次真实验收通过，Maintenance 才成功。

## authoritative copy 与同步

最后一次真实验收通过的当前完整 Skill 是唯一 authoritative copy。只检查明确已知的安装
位置，不做全盘扫描，不比较版本、mtime 或副本新旧。

同步前可生成 dry-run manifest；source / target 必须是合法 `ruan-continue2run` Skill root，
不能是同一 realpath，也不能存在危险父子目录关系。同步整体覆盖正式 Skill 文件，不碰
`.ruan-continue2run/`、日志、STOP、缓存和 Harness 运行数据。每个目标做完整文件级一致性校验；
某个目标失败就明确报告，不自动回滚其他已成功目标。
