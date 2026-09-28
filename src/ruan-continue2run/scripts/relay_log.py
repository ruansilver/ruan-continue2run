"""会话日志：每个会话一个独立的时间戳 Markdown 文件，只记录事实。

规则（设计文档第 8 节）：

  - 粒度：每个会话一个独立文件，不共享、不追加别的会话的文件
    （新旧会话交接重叠时，两边各写各的文件，不会互相踩）
  - 归类：靠 slug（由任务入口确定性推导），体现在文件名里；不靠共享文件、不传路径
  - 只记事实：RelayContext、关键决策、意图日志、创建调用结果
  - 不做：不判断新会话是否真正运行成功、不承担任何状态管理

与 Agent Checkpoint / 会话恢复机制保持独立，不合并存储格式。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import config


def utc_now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp(dt):
    return dt.strftime("%Y%m%dT%H%M%SZ")


def resolve_log_dir(context):
    """返回 (日志目录, 警告列表)。日志只写入 RelayContext 指定的工作目录。"""
    wd = (context.get("runtime_params") or {}).get("working_directory")
    if not wd:
        raise OSError("RelayContext 未提供 working_directory，无法确定项目级日志目录")
    root = Path(wd)
    if not root.is_dir():
        raise OSError(f"working_directory 不是有效目录：{wd}")
    return root / config.LOG_DIR_NAME, []


def create_session_log(context, slug):
    """创建本会话的日志文件并写入头部（含 RelayContext）。返回 (路径, 警告列表)。"""
    log_dir, warnings = resolve_log_dir(context)
    log_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    stamp = _stamp(now)
    n = 0
    while True:
        suffix = "" if n == 0 else f"-{n + 1}"
        path = log_dir / f"{stamp}-{slug}{suffix}.md"
        try:
            with open(path, "x", encoding="utf-8") as f:
                h = context.get("harness") or {}
                f.write(
                    f"# {config.SKILL_NAME} session log\n\n"
                    f"- slug: `{slug}`\n"
                    f"- opened_at: {_iso(now)}\n"
                    f"- harness: tag={h.get('tag')}, id={h.get('id')}\n\n"
                    f"## RelayContext\n\n```json\n"
                    f"{json.dumps(context, ensure_ascii=False, indent=2)}\n```\n"
                )
            return path, warnings
        except FileExistsError:
            n += 1


def child_output_paths(context, slug):
    """Adapter 派生子进程时，给它自己的原始输出找一个落盘位置。

    返回 (stdout 路径, stderr 路径)，目录是 <日志目录>/children/。这是原始事实，不是会话日志，
    只用于事后排查，不参与任何判断（见 references/log-format.md 末尾）。
    """
    log_dir, _ = resolve_log_dir(context)
    d = log_dir / "children"
    d.mkdir(parents=True, exist_ok=True)
    stamp = _stamp(utc_now())
    return d / f"{stamp}-{slug}.out", d / f"{stamp}-{slug}.err"


def append_entry(path, kind, fields):
    """追加一条事实记录。fields 是有序的 (键, 值) 列表或 dict。"""
    items = fields.items() if isinstance(fields, dict) else fields
    lines = [f"\n## [{_iso(utc_now())}] {kind}\n"]
    for k, v in items:
        lines.append(f"- {k}: {v if v not in (None, "") else '-'}")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def has_entry(path, kind):
    """判断本会话日志里是否已有某类记录（只读本会话自己的文件，不构成状态管理）。"""
    tail = f"] {kind}"
    with open(path, encoding="utf-8") as f:
        return any(line.startswith("## [") and line.rstrip().endswith(tail) for line in f)


def read_context(path):
    """从日志头部读回 RelayContext。

    收尾时从这里读回，保证任务入口逐字不变，不经过模型转述。
    """
    text = Path(path).read_text(encoding="utf-8")
    start_marker = "## RelayContext\n\n```json\n"
    i = text.index(start_marker) + len(start_marker)
    j = text.index("\n```", i)
    return json.loads(text[i:j])
