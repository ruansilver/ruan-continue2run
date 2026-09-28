"""Claude Code Adapter —— 扩展位置（占位）。尚未实现。

线索（仅作为探测起点，尚未针对本设计验证）：Claude Code 提供生命周期钩子与会话恢复能力，
实现前仍需在 Skill 修复阶段实际探测确认，不要直接假设。
输入输出契约见 references/adapter-contract.md。
"""
from .base import not_implemented


def create(context):
    return not_implemented("claude-code")
