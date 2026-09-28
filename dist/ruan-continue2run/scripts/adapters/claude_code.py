"""Claude Code Adapter —— 扩展位置（占位），尚未实现。

线索（设计文档第 7 节给出的探测起点，**尚未针对本设计验证**）：Claude Code 提供生命周期钩子
与会话恢复能力，可作为实现起点，不必从零造轮子；但实现前必须在 Skill 修复阶段实际探测确认，
不要直接假设它的调用方式。

输入输出契约见 references/adapter-contract.md；实现步骤见源目录 README.md 第 8 节。
"""
from .base import not_implemented


def create(context):
    """输入 RelayContext，返回 {"issued", "info", "error"}。"""
    return not_implemented("claude-code")
