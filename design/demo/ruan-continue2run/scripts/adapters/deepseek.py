"""DeepSeek Harness Adapter。

状态：结构已就位，【创建方式待确认】——设计文档没有覆盖 DeepSeek Harness 如何以编程方式创建会话，
因此这里不做任何假设，create() 如实返回 PENDING_CONFIRMATION，不执行任何创建调用。

补全方式：用户显式进入 Skill 修复阶段（见源目录 README.md），由 Skill Creator 探测/访谈确认下列各项后，
只在本文件内实现 create()，并保持 references/adapter-contract.md 的输入输出契约不变。

实现时可用的共享辅助（避免各 Adapter 各自拼装造成差异）：
    from relay_context import render_first_message   # 新会话第一条消息的统一渲染
    from .base import result                          # 统一的输出构造
"""
from .base import pending_confirmation

# 待确认项（确认后从这里删除，并把结论写进 README 的 Harness 状态表）
PENDING_ITEMS = [
    "以编程方式创建新会话的入口（CLI / 配置 / API）及其调用方式",
    "如何指定新会话的模型",
    "如何指定新会话的思维深度（对应哪个参数）",
    "如何指定新会话的工作目录",
    "如何把第一条消息投递给新会话",
    "第一条消息里如何显式调用 Skill（是否就是写 Skill 名）",
    "运行环境是否提供 Python 3 以运行 scripts/",
    "无 Harness 标签时的被动识别信号（设计文档留给各 Harness 适配层补全）",
    "生命周期能力（如会话结束事件、创建后是否立即开始运行）",
]


def create(context):
    """输入 RelayContext，返回 {"issued", "info", "error"}。"""
    return pending_confirmation("deepseek", PENDING_ITEMS)
