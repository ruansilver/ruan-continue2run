"""Codex CLI Harness Adapter。

Codex 的会话创建细节全部封装在本模块：

    codex exec [--model <model>] [--config model_reasoning_effort=<effort>]
        --cd <working-directory> --skip-git-repo-check -
        stdin = relay_context.render_first_message(context)

``-`` 让 ``codex exec`` 从 stdin 读取新会话的首条提示。Adapter 只负责把调用发出，
不等待、不轮询、不判断子会话是否启动或运行成功。输出文件只用于保留子进程的原始事实，
不属于 Relay 状态。

实现依据：本机 ``codex-cli 0.155.1`` 的 ``codex exec --help`` 与实测 CLI 行为。模型和
工作目录分别由 ``--model`` / ``--cd`` 表达；Codex CLI 没有独立的 reasoning-effort
选项，因此用其配置键 ``model_reasoning_effort`` 传递 RelayContext 中已确认的思维深度。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import relay_context  # noqa: E402
import relay_log  # noqa: E402

from .base import result  # noqa: E402

# 允许联调/测试时显式指定启动器；正常运行优先使用 PATH 中的原生 codex 可执行文件。
LAUNCHER_ENV = "RUAN_CONTINUE2RUN_CODEX_BIN"

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def detect():
    """识别当前进程是否运行在 Codex 会话中；仅供无标签时被动选 Adapter。"""
    return bool(os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))


def _node_launcher_from_shim(shim):
    """把 npm 的 .cmd/.ps1 shim 解析为 ``node .../codex.js``。

    Windows 上直接把 .ps1 交给 ``subprocess.Popen`` 不可靠；解析到 JS 入口后，
    argv 不再经过 shell 的二次解析。解析失败时返回 None，由调用方如实报告。
    """
    node = shutil.which("node")
    if not node:
        return None
    parent = Path(shim).resolve().parent
    candidates = (
        parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
        parent / ".." / "lib" / "node_modules" / "@openai" / "codex" / "bin" / "codex.js",
    )
    for candidate in candidates:
        if candidate.is_file():
            return [node, str(candidate)]
    return None


def _resolve_launcher():
    """返回可执行 argv 前缀；找不到 Codex CLI 时返回 None。"""
    override = os.environ.get(LAUNCHER_ENV)
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            if path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
                return _node_launcher_from_shim(path) or None
            return [str(path)]
        # 允许测试/联调环境把一个可由 PATH 解析的命令名放进覆盖变量。
        resolved = shutil.which(override)
        if resolved:
            if Path(resolved).suffix.lower() in {".cmd", ".ps1", ".bat"}:
                return _node_launcher_from_shim(resolved) or None
            return [resolved]
        return None

    # 原生安装优先：避免把 npm shim（.cmd/.ps1）当作可执行文件交给 Popen。
    for name in ("codex.exe", "codex"):
        resolved = shutil.which(name)
        if resolved and Path(resolved).suffix.lower() not in {".cmd", ".ps1", ".bat"}:
            return [resolved]

    for name in ("codex", "codex.cmd", "codex.ps1"):
        resolved = shutil.which(name)
        if not resolved:
            continue
        launcher = _node_launcher_from_shim(resolved)
        if launcher:
            return launcher
    return None


def _append_config(argv, key, value):
    """追加一个安全的 TOML 字符串配置覆盖。"""
    argv += ["--config", f"{key}={json.dumps(str(value), ensure_ascii=True)}"]


def create(context):
    """输入 RelayContext，返回 ``{"issued", "info", "error"}``。"""
    launcher = _resolve_launcher()
    if not launcher:
        return result(
            False,
            error=(
                "CODEX_LAUNCHER_NOT_FOUND: 在 PATH 中找不到原生 codex，"
                "也无法从 npm shim 解析 codex.js，未执行任何创建"
            ),
        )

    params = context.get("runtime_params") or {}
    working_directory = params.get("working_directory")
    if not working_directory:
        return result(
            False,
            error="CODEX_CWD_MISSING: RelayContext 未提供 working_directory，未执行任何创建",
        )
    cwd = Path(working_directory).expanduser()
    if not cwd.is_dir():
        return result(
            False,
            error=f"CODEX_CWD_INVALID: working_directory 不是有效目录：{working_directory}，未执行任何创建",
        )

    argv = list(launcher) + ["exec"]
    if params.get("model") is not None:
        argv += ["--model", str(params["model"])]
    if params.get("thinking_depth") is not None:
        _append_config(argv, "model_reasoning_effort", params["thinking_depth"])
    argv += ["--cd", str(cwd), "--skip-git-repo-check", "-"]

    message = relay_context.render_first_message(context)
    slug = relay_context.make_slug(context.get("task_entry") or "")
    out_path, err_path = relay_log.child_output_paths(context, slug)

    try:
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            flags = {}
            if os.name == "nt":
                # 不使用 DETACHED_PROCESS：它会使子进程收不到 stdin 数据/EOF。
                flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
            else:
                flags["start_new_session"] = True
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                close_fds=True,
                **flags,
            )
            proc.stdin.write(message.encode("utf-8"))
            proc.stdin.close()
    except Exception as exc:  # noqa: BLE001 - Adapter 只如实报告，不重试
        return result(
            False,
            error=(
                f"CODEX_SPAWN_FAILED: {exc!r}，未创建下一会话 "
                f"（子进程输出：{out_path} / {err_path}）"
            ),
        )

    notes = [
        "创建调用已发出；这不表示新会话已启动或运行成功",
        f"pid={proc.pid}",
        f"cwd={cwd}",
        f"command={' '.join(argv)}",
        f"子进程原始输出：{out_path} / {err_path}",
    ]
    if params.get("model") is None:
        notes.append("RelayContext 未提供 model，未覆盖 Codex 配置")
    if params.get("thinking_depth") is None:
        notes.append("RelayContext 未提供 thinking_depth，未覆盖 Codex 配置")
    return result(True, info="; ".join(notes))
