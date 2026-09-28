"""DeepSeek Harness (DSH) Adapter。

创建方式（本机探测确认，不是在文档里看来的，证据见源目录 README.md 第 8 节）：

    dsh --profile headless [--patch <当前 profile 的 cordis.patch.yml>] -
        argv 里的 `-` 表示"从 stdin 读第一条消息"（`dsh --profile headless --help`）
        stdin = relay_context.render_first_message(context)
        cwd   = RelayContext.runtime_params.working_directory

    - 工作目录：用子进程的 cwd 表达。
    - 模型 / 思维深度：把**当前 profile 的用户补丁层**（`$DSH_PROFILE_DIR/cordis.patch.yml`）
      原样叠加成一层 `--patch`。该文件里的 `agent-default-model`
      （provider / model / reasoningEffort）就是当前会话的运行参数，叠加后子会话沿用同一套；
      实测 `dsh --profile headless --patch <web/cordis.patch.yml> --dump-config` 得到
      provider: ruansilver / model: deepseek-v4.1-flash / reasoningEffort: max。
    - 子进程与调用方的控制台分离（Windows: CREATE_NO_WINDOW；POSIX: start_new_session），
      发出后立即返回：本 Adapter 不等它、不看它、不判断它是否成功。

各 Harness 的差异只允许出现在这份文件里；契约见 references/adapter-contract.md。
"""
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

# 目标 profile：DSH 里"跑一个任务然后退出"的那个 profile（`dsh --profile headless --help`）。
TARGET_PROFILE = "headless"

# 排查/联调用的官方出口：指向一个自定义命令（例如打桩脚本）来替代解析出的 dsh。
LAUNCHER_ENV = "RUAN_CONTINUE2RUN_DSH_BIN"

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def detect():
    """识别当前进程是否运行在 DSH profile 中；仅供无标签时被动选 Adapter。"""
    return bool(
        os.environ.get("DSH_HOME")
        or os.environ.get("DSH_PROFILE")
        or os.environ.get("DSH_PROFILE_DIR")
    )


def _resolve_launcher():
    """返回可执行 argv 前缀，或 None。

    优先用 `node <npm 全局>/node_modules/@deepseek-ai/dsh/lib/bin.js`：真正的 JS 入口，
    argv 不经 .cmd/.ps1 的二次解析。退而求其次用 PATH 里的 dsh（.ps1 不能被直接执行）。
    """
    override = os.environ.get(LAUNCHER_ENV)
    if override:
        return [override]

    shim = shutil.which("dsh")
    if not shim:
        return None

    bases = {Path(shim).parent}
    try:
        bases.add(Path(shim).resolve().parent)
    except OSError:
        pass
    node = shutil.which("node")
    if node:
        for base in bases:
            for cand in (
                base / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js",
                base / ".." / "lib" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js",
            ):
                if cand.is_file():
                    return [node, str(cand)]

    if shim.lower().endswith(".ps1"):
        return None
    return [shim]


def _current_profile_patch():
    """当前 profile 的用户补丁层；不存在、为空（[]）或目标相同时返回 None。

    这是 Harness 自己的运行参数记录，不是猜测：Adapter 只做"原样叠加"。
    """
    profile = os.environ.get("DSH_PROFILE")
    if not profile or profile == TARGET_PROFILE:
        return None
    pdir = os.environ.get("DSH_PROFILE_DIR")
    if not pdir:
        return None
    path = Path(pdir) / "cordis.patch.yml"
    if not path.is_file():
        return None
    body = "\n".join(
        line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    return path if body.strip() not in ("", "[]") else None


def create(context):
    """输入 RelayContext，返回 {"issued", "info", "error"}。

    只负责"把创建调用发出去"；子会话是否启动、是否跑起来，本函数不看、不判断。
    """
    launcher = _resolve_launcher()
    if not launcher:
        return result(False, error=(
            "DSH_LAUNCHER_NOT_FOUND: 在 PATH 里找不到 dsh，也找不到 node + "
            "@deepseek-ai/dsh/lib/bin.js，未执行任何创建"
        ))

    params = context.get("runtime_params") or {}
    wd = params.get("working_directory")
    if not wd:
        return result(False, error="DSH_CWD_MISSING: RelayContext 未提供 working_directory，未执行任何创建")
    cwd = Path(wd)
    if not cwd.is_dir():
        return result(False, error=f"DSH_CWD_INVALID: working_directory 不是有效目录：{wd}，未执行任何创建")

    patch = _current_profile_patch()
    argv = list(launcher) + ["--profile", TARGET_PROFILE]
    if patch:
        argv += ["--patch", str(patch)]
    argv.append("-")  # 第一条消息从 stdin 读

    message = relay_context.render_first_message(context)
    slug = relay_context.make_slug(context.get("task_entry") or "")
    out_path, err_path = relay_log.child_output_paths(context, slug)

    try:
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            flags = {}
            if os.name == "nt":
                # CREATE_NO_WINDOW：子进程不继承调用方的控制台（调用方控制台关闭时不会连带杀掉它），
                #   但 stdin/stdout/stderr 仍按这里显式指定的句柄走。
                # 不要用 DETACHED_PROCESS：本机实测（Windows + Python 3.12）它会让子进程**收不到
                #   stdin 的数据与 EOF**，直接被挂死在读 stdin 上。
                flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
            else:
                flags["start_new_session"] = True
            proc = subprocess.Popen(
                argv, cwd=str(cwd), stdin=subprocess.PIPE,
                stdout=out_f, stderr=err_f, close_fds=True, **flags,
            )
            proc.stdin.write(message.encode("utf-8"))
            proc.stdin.close()
    except Exception as e:  # noqa: BLE001 - 如实上报，不重试
        return result(False, error=f"DSH_SPAWN_FAILED: {e!r}，未创建下一会话（子进程输出：{out_path} / {err_path}）")

    notes = [
        "创建调用已发出；这不表示新会话已启动或运行成功",
        f"pid={proc.pid}",
        f"cwd={cwd}",
        f"command={' '.join(argv)}",
    ]
    if patch:
        notes.append(f"已叠加当前 profile 的补丁层以沿用运行参数：{patch}")
    else:
        notes.append("未叠加补丁层，模型与思维深度取目标 profile 自身的默认配置")
    notes.append(f"子进程原始输出：{out_path} / {err_path}")
    return result(True, info="; ".join(notes))
