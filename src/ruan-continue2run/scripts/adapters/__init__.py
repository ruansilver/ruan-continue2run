"""Harness Adapter 包。

每个 Harness 一个模块，模块必须实现 create(context) -> dict；可选提供无副作用的 detect() -> bool
用于无标签时的被动识别（契约见 references/adapter-contract.md）。
新增 Harness 的做法见源目录 README.md 第 8 节；核心脚本与日志格式不需要改动。
"""
