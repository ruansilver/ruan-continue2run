"""ruan-continue2run 集中配置。

本文件里的值是【待确认默认值】(PENDING)：设计文档第 10 节把它们列为需要现场确认的项。
它们集中在这一个文件里，确认后只改这里，不需要动其他脚本。
"""

SKILL_NAME = "ruan-continue2run"

# PENDING: 显式触发的具体写法。
# 当前实现：第一条消息里出现 Skill 名即视为显式调用（允许带 / $ @ 前缀）。
INVOCATION_MARKER = "ruan-continue2run"

# PENDING: Harness 标签语法。
# 当前实现：消息末尾的 //HarnessName，例如 "//DeepSeek"。
HARNESS_TAG_PREFIX = "//"

# PENDING: 日志目录名。
# 当前实现：项目级点前缀目录，位于运行参数里的工作目录之下。
LOG_DIR_NAME = ".ruan-continue2run"
