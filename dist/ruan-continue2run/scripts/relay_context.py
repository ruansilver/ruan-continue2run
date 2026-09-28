"""RelayContext：接力载荷的运行时形式。

只做确定性的文本处理，不读写文件、不做任何状态管理：

  - 从第一条用户消息里识别并剥离触发标记与 Harness 标签，得到净化后的任务入口
  - 由任务入口确定性推导 slug（日志归类用）
  - 把 RelayContext 渲染回"新会话的第一条消息"（所有 Adapter 共用同一渲染，
    避免各 Adapter 各自拼装造成差异）

载荷只有三部分，不多不少：任务入口、Skill 自身、运行参数（含 Harness 标识）。
不复制历史上下文、不带链状态、不带轮次、不带日志路径。
"""
import hashlib
import re

import config


def _marker_pattern():
    # 触发写法由 config.py 统一定义；Skill 的 description 负责要求显式调用，
    # 解析器只在第一条消息中寻找这一明确标记。
    return re.compile(r"(?<![\w-])[/$@]?" + re.escape(config.INVOCATION_MARKER) + r"(?![\w-])")


def _tag_pattern():
    return re.compile(
        r"(?:^|\s)" + re.escape(config.HARNESS_TAG_PREFIX) + r"([A-Za-z0-9][A-Za-z0-9_.-]*)\s*$"
    )


def parse_first_message(text):
    """解析会话的第一条用户消息。

    返回 {"task_entry": str, "harness_tag": str | None}；
    若消息没有显式调用本 Skill，返回 None（调用方据此按普通会话处理）。
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
    """构建 RelayContext（同时也是 Adapter 的输入）。

    取不到的运行参数保持 None，不猜测、不从别处补齐。
    """
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
    """把 RelayContext 渲染为新会话的第一条消息。

    结果同时满足"显式调用本 Skill"（链条得以继续）与"携带同一任务入口"两件事。
    """
    parts = [config.INVOCATION_MARKER, "", context["task_entry"]]
    harness = context.get("harness") or {}
    tag = harness.get("tag") or harness.get("id")
    if tag:
        parts += ["", f"{config.HARNESS_TAG_PREFIX}{tag}"]
    return "\n".join(parts)
