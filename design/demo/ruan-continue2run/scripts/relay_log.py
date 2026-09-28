"""会话日志：每个会话一个独立的时间戳 Markdown 文件，只记录事实。

- 不共享文件、不追加别的会话的文件（新旧会话交接重叠时不会互相写同一个文件）
- 用 slug 归类：文件名形如 <UTC时间戳>-<slug>.md
- 不判断新会话是否真正运行成功，不承担任何状态管理
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import config


def utc_now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_log_dir(context):
    """返回 (日志目录, 警告列表)。项目根 = 运行参数里的工作目录；缺失或无效时退回当前目录并给出警告。"""
    warnings = []
    wd = (context.get("runtime_params") or {}).get("working_directory")
    root = None
    if wd:
        if Path(wd).is_dir():
            root = Path(wd)
        else:
            warnings.append(f"working_directory 不是有效目录，日志退回写到当前目录：{wd}")
    else:
        warnings.append("未提供 working_directory，日志写到当前目录")
    if root is None:
        root = Path(os.getcwd())
    return root / config.LOG_DIR_NAME, warnings


def create_session_log(context, slug):
    """创建本会话的日志文件并写入头部（含 RelayContext）。返回 (路径, 警告列表)。"""
    log_dir, warnings = resolve_log_dir(context)
    log_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:
        suffix = "" if n == 0 else f"-{n + 1}"
        path = log_dir / f"{stamp}-{slug}{suffix}.md"
        try:
            with open(path, "x", encoding="utf-8") as f:
                h = context.get("harness", {})
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


def append_entry(path, kind, fields):
    """追加一条事实记录。fields 是 有序的 (键, 值) 列表或 dict。"""
    items = fields.items() if isinstance(fields, dict) else fields
    lines = [f"\n## [{_iso(utc_now())}] {kind}\n"]
    for k, v in items:
        lines.append(f"- {k}: {v if v not in (None, '') else '-'}")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def has_entry(path, kind):
    tail = f"] {kind}"
    with open(path, encoding="utf-8") as f:
        return any(line.startswith("## [") and line.rstrip().endswith(tail) for line in f)


def read_context(path):
    """从日志头部读回 RelayContext（保证任务入口逐字不变，不经过模型转述）。"""
    text = Path(path).read_text(encoding="utf-8")
    start_marker = "## RelayContext\n\n```json\n"
    i = text.index(start_marker) + len(start_marker)
    j = text.index("\n```", i)
    return json.loads(text[i:j])
