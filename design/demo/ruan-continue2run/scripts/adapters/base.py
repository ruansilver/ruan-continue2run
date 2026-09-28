"""Adapter 公共辅助：只提供统一的结果构造，不含任何 Harness 专属逻辑。"""


def result(issued, info=None, error=None):
    """构造契约规定的输出：{"issued": bool, "info": str | None, "error": str | None}。

    issued 只表示"创建调用是否已发出"，不表示新会话是否已经运行或运行成功。
    """
    return {"issued": bool(issued), "info": info, "error": error}


def not_implemented(harness_id):
    """占位 Adapter 使用：如实说明尚未实现，不假装创建。"""
    return result(False, error=f"NOT_IMPLEMENTED: {harness_id} 的 Adapter 尚未实现（扩展位置已保留）")


def pending_confirmation(harness_id, items):
    """Adapter 已有结构但创建方式尚未确认时使用：列出待确认项，不做任何假设性调用。"""
    listed = "；".join(items)
    return result(
        False,
        error=f"PENDING_CONFIRMATION: {harness_id} 的会话创建方式尚未确认，未执行任何创建。待确认项：{listed}",
    )
