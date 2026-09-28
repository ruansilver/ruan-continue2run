#!/usr/bin/env python3
"""ruan-continue2run 命令行入口：只做会话接力编排与记录。

子命令：

  open       会话开场：解析第一条用户消息 -> 构建 RelayContext -> 创建本会话日志
  decision   记录一条关键决策（只记事实）
  finish     会话收尾：写意图日志 -> 通过 Adapter 创建下一会话 -> 追加"创建调用结果"

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
    """把结果以 JSON 打到 stdout。

    固定用 ASCII 转义（\\uXXXX）输出：调用方可能是各种终端/Harness，stdout 的编码不可信，
    实测非 ASCII 字节经某些调用链会被解码坏掉。日志文件里仍然是正常中文（直接按 UTF-8 落盘）。
    """
    print(json.dumps(obj, ensure_ascii=True, indent=2))


def _read_message(args):
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8-sig")
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
        if harness_id:
            warnings.append(f"第一条消息没有 Harness 标签；Adapter 被动识别为 {harness_id}")
        else:
            warnings.append("第一条消息没有 Harness 标签且无法唯一被动识别；收尾时将无法选择 Adapter")
    elif not harness_id:
        warnings.append(f"Harness 标签 {tag} 不在注册表中；收尾时会如实记录未创建")

    ctx = relay_context.build_context(
        parsed["task_entry"], tag, harness_id,
        model=args.model, thinking_depth=args.thinking_depth, working_directory=args.working_dir,
    )
    slug = relay_context.make_slug(ctx["task_entry"])
    try:
        path, log_warnings = relay_log.create_session_log(ctx, slug)
    except OSError as e:
        # 例如会话跑在只读沙箱里，工作目录不可写。如实上报，不重试、不换目录、不自建会话。
        _out({
            "error": f"LOG_WRITE_FAILED: 无法建立本会话日志（{e!r}）。本会话按普通会话处理；"
                     f"不要重试、不要换目录、不要自行创建下一会话。",
        })
        return 2
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
    try:
        relay_log.append_entry(args.log, "decision", [
            ("node", args.node),
            ("choice", args.choice),
            ("basis", args.basis),
            ("confidence", args.confidence),
        ])
    except OSError as e:
        _out({"ok": False, "error": f"LOG_WRITE_FAILED: 无法写入决策日志（{e!r}）"})
        return 2
    _out({"ok": True})
    return 0


def cmd_finish(args):
    log = Path(args.log)
    if not log.is_file():
        _out({"ok": False, "error": f"日志文件不存在：{args.log}"})
        return 2
    # 只读本会话自己的日志：同一个会话不给第二次创建机会，避免重复创建下一会话。
    if relay_log.has_entry(log, "intent"):
        _out({"ok": False, "error": "本会话已经执行过 finish，拒绝重复创建下一会话"})
        return 1

    ctx = relay_log.read_context(log)
    harness = ctx.get("harness") or {}

    # ① 意图日志：写在调用 Adapter 之前
    try:
        relay_log.append_entry(log, "intent", [
            ("action", "即将通过 Adapter 请求创建下一会话"),
            ("adapter", harness.get("id") or "(未解析)"),
            ("harness_tag", harness.get("tag")),
        ])
    except OSError as e:
        # 记不下意图就不创建：宁可接力没发出，也不留下无记录的动作。
        _out({
            "issued": False,
            "info": None,
            "error": f"LOG_WRITE_FAILED: 无法写入意图日志（{e!r}），未执行任何创建",
            "log_path": str(log),
        })
        return 2

    # ② 创建下一会话：本会话最后一个有副作用的动作
    result = dispatch.run_adapter(ctx)

    # ③ 创建调用结果：Adapter 返回后，只往本会话自己的日志追加这一条
    warning = None
    try:
        relay_log.append_entry(log, "creation-call-result", [
            ("issued", str(result["issued"]).lower()),
            ("info", result["info"]),
            ("error", result["error"]),
            ("note", RESULT_NOTE),
        ])
    except OSError as e:
        warning = f"LOG_WRITE_FAILED: 创建调用结果未能写入日志（{e!r}）"

    out = {"issued": result["issued"], "info": result["info"], "error": result["error"], "log_path": str(log)}
    if warning:
        out["warning"] = warning
    _out(out)
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="relay.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("open", help="会话开场：解析第一条用户消息并建立本会话日志")
    o.add_argument("--message-file", help="第一条用户消息原文所在文件；缺省从 stdin 读取")
    o.add_argument("--model", help="当前会话的模型标识（确认不了就不要传）")
    o.add_argument("--thinking-depth", help="当前会话的思维深度（确认不了就不要传）")
    o.add_argument("--working-dir", help="当前会话的工作目录")
    o.set_defaults(fn=cmd_open)

    d = sub.add_parser("decision", help="记录一条关键决策")
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
