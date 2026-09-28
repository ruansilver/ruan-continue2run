"""ruan-continue2run 集中配置。

本文件只放【全局约定】与【待确认默认值】(PENDING)。
设计文档第 10 节把这些列为需要现场确认的项：确认后只改这里，不需要动其他脚本。

注意：具体的 Harness 创建方式不属于本文件，也不属于任何核心脚本；
它们只允许出现在 scripts/adapters/<harness>.py 里（见 references/adapter-contract.md）。
"""

SKILL_NAME = "ruan-continue2run"

# PENDING（设计文档第 10 节）：显式触发的具体写法。
# 当前实现：第一条用户消息里出现 Skill 名（允许 / $ @ 前缀）。
INVOCATION_MARKER = "ruan-continue2run"

# PENDING（设计文档第 10 节）：Harness 标签的具体语法。
# 当前实现：消息末尾追加 //HarnessName，例如 "... //DeepSeek"；解析时被剥离，不进入任务入口。
HARNESS_TAG_PREFIX = "//"

# PENDING（设计文档第 10 节）：日志目录名。
# 当前实现：项目级点前缀目录，位于运行参数里的工作目录之下。
LOG_DIR_NAME = ".ruan-continue2run"
