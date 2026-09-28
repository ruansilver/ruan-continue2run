#!/usr/bin/env python3
"""ruan-continue2run 命令行入口：仅做会话接力编排与记录。

子命令：
  open      会话开场：解析第一条用户消息 -> 构建 RelayContext -> 创建本会话日志
  decision  记录一条关键决策（只记事实）
  finish    会话收尾：写意图日志 -> 调用 Adapter 创建下一会话 -> 追加"创建调用结果"

不做：任务状态管理、自动完成判断、自检、自动恢复、历史上下文复制。
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dispatch  # noqa: E402
import relay_context  # noqa: E402
import relay_log  # noqa: E402

RESULT_NOTE = "仅记录创建调用本身的结果；不代表新会话已启动或运行成功"


def _out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _read_message(args):
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""


def cmd_open(args):
    text = _read_message(args)
    if not text.strip():
        _out({"error": "没有收到第一条用户消息（用 --message-file 或 stdin 传入原文）"})
        return 2
    parsed = relay_context.parse_first_message(text)
    if parsed is None:
        _out({"relay": False, "reason": "第一条消息没有显式调用 ruan-continue2run，按普通会话处理"})
        return 0
    tag = parsed["harness_tag"]
    harness_id = dispatch.resolve_harness(tag)
    warnings = []
    if not tag:
        warnings.append("第一条消息没有 Harness 标签；收尾时将无法选择 Adapter（如实记录，不自行补救）")
    elif not harness_id:
        warnings.append(f"Harness 标签 //{tag} 不在注册表中；收尾时将如实记录未创建")
    ctx = relay_context.build_context(
        parsed["task_entry"], tag, harness_id,
        model=args.model, thinking_depth=args.thinking_depth, working_directory=args.working_dir,
    )
    slug = relay_context.make_slug(ctx["task_entry"])
    path, log_warnings = relay_log.create_session_log(ctx, slug)
    _out({
        "relay": True,
        "log_path": str(path),
        "slug": slug,
        "history_glob": str(path.parent / f"*-{slug}*.md"),
        "context": ctx,
        "warnings": warnings + log_warnings,
    })
    return 0


def cmd_decision(args):
    if not Path(args.log).is_file():
        _out({"ok": False, "error": f"日志文件不存在：{args.log}"})
        return 2
    relay_log.append_entry(args.log, "decision", [
        ("node", args.node), ("choice", args.choice),
        ("basis", args.basis), ("confidence", args.confidence),
    ])
    _out({"ok": True})
    return 0


def cmd_finish(args):
    log = Path(args.log)
    if not log.is_file():
        _out({"ok": False, "error": f"日志文件不存在：{args.log}"})
        return 2
    if relay_log.has_entry(log, "intent"):
        _out({"ok": False, "error": "本会话已经执行过 finish，拒绝重复创建下一会话"})
        return 1
    ctx = relay_log.read_context(log)
    h = ctx.get("harness") or {}
    relay_log.append_entry(log, "intent", [
        ("action", "即将通过 Adapter 请求创建下一会话"),
        ("adapter", h.get("id") or "(未解析)"),
        ("harness_tag", h.get("tag")),
    ])
    result = dispatch.run_adapter(ctx)
    relay_log.append_entry(log, "creation-call-result", [
        ("issued", str(result["issued"]).lower()),
        ("info", result["info"]), ("error", result["error"]),
        ("note", RESULT_NOTE),
    ])
    _out(result)
    return 0


def main():
    p = argparse.ArgumentParser(prog="relay.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="会话开场")
    o.add_argument("--message-file", help="第一条用户消息原文所在文件；缺省从 stdin 读取")
    o.add_argument("--model")
    o.add_argument("--thinking-depth")
    o.add_argument("--working-dir")
    o.set_defaults(fn=cmd_open)

    d = sub.add_parser("decision", help="记录关键决策")
    d.add_argument("--log", required=True)
    d.add_argument("--node", required=True, help="遇到的问题/节点")
    d.add_argument("--choice", required=True, help="最终选择的方向")
    d.add_argument("--basis", help="依据的参考文档/线索")
    d.add_argument("--confidence", help="把握程度")
    d.set_defaults(fn=cmd_decision)

    f = sub.add_parser("finish", help="会话收尾：写意图日志并通过 Adapter 创建下一会话")
    f.add_argument("--log", required=True)
    f.set_defaults(fn=cmd_finish)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
