"""RelayContext 的构建、解析与渲染。

只做确定性的文本处理：
  - 从第一条用户消息里剥离触发标记与 Harness 标签，得到净化后的任务入口
  - 由任务入口确定性推导 slug（用于日志归类）
  - 把 RelayContext 渲染回"新会话的第一条消息"（各 Adapter 共用，避免各自拼装造成差异）

不做任何状态管理，不读写文件。
"""
import hashlib
import re

import config


def _marker_pattern():
    return re.compile(r"(?<![\w-])[/$@]?" + re.escape(config.INVOCATION_MARKER) + r"(?![\w-])")


def _tag_pattern():
    return re.compile(r"(?:^|\s)" + re.escape(config.HARNESS_TAG_PREFIX) + r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$")


def parse_first_message(text):
    """解析第一条用户消息。

    返回 {"task_entry": str, "harness_tag": str | None}；
    若消息没有显式调用本 Skill（找不到触发标记），返回 None。
    """
    m = _marker_pattern().search(text)
    if not m:
        return None
    rest = text[: m.start()] + text[m.end():]  # 只剥离第一处触发标记
    rest = rest.strip()
    tag = None
    tm = _tag_pattern().search(rest)
    if tm:
        tag = tm.group(1)
        rest = rest[: tm.start()].strip()
    return {"task_entry": rest, "harness_tag": tag}


def make_slug(task_entry):
    """由任务入口确定性推导 slug：同一任务入口永远得到同一个 slug。"""
    norm = " ".join(task_entry.split())
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", norm.lower()).strip("-")[:24].strip("-")
    return f"{ascii_part}-{digest}" if ascii_part else digest


def build_context(task_entry, harness_tag, harness_id, model=None, thinking_depth=None, working_directory=None):
    """构建 RelayContext（同时也是 Adapter 的输入）。取不到的运行参数保持 None，不猜测。"""
    return {
        "task_entry": task_entry,
        "skill": config.SKILL_NAME,
        "runtime_params": {
            "model": model,
            "thinking_depth": thinking_depth,
            "working_directory": working_directory,
        },
        "harness": {"tag": harness_tag, "id": harness_id},
    }


def render_first_message(context):
    """把 RelayContext 渲染为新会话的第一条消息（显式调用本 Skill + 任务入口 + Harness 标签）。"""
    parts = [config.INVOCATION_MARKER, "", context["task_entry"]]
    tag = context.get("harness", {}).get("tag")
    if tag:
        parts += ["", f"{config.HARNESS_TAG_PREFIX}{tag}"]
    return "\n".join(parts)
