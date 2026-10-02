"""Codex app-server Harness Adapter.

The desktop Codex process exposes the running thread's effective configuration
through the local app-server protocol. We read that metadata, then use the
same binary's ``thread/start`` and ``turn/start`` methods. This gives the new
thread the same Desktop-visible source as a manually created thread while
avoiding an older ``codex`` executable on PATH and display-label model ids.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import relay_context  # noqa: E402

from .base import result  # noqa: E402

LAUNCHER_ENV = "RUAN_CONTINUE2RUN_CODEX_BIN"
CODEX_PATH_ENV = "CODEX_CLI_PATH"
THREAD_ENV_NAMES = ("CODEX_THREAD_ID", "CODEX_SESSION_ID")
CONFIG_QUERY_TIMEOUT = 10.0

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def detect():
    """识别当前进程是否运行在 Codex 会话中；仅供无标签时被动选 Adapter。"""
    return bool(os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))


def _node_launcher_from_shim(shim):
    """把 Windows npm shim 解析为可直接交给 Popen 的 node argv。"""
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
    """返回 Codex 可执行文件 argv 前缀。

    ``CODEX_CLI_PATH`` is injected by Codex Desktop and points at the binary
    bundled with the running desktop version. It must win over PATH, where an
    unrelated older npm CLI commonly appears first.
    """
    candidates = []
    override = os.environ.get(LAUNCHER_ENV)
    if override:
        candidates.append(override)
    bundled = os.environ.get(CODEX_PATH_ENV)
    if bundled:
        candidates.append(bundled)
    candidates.extend(("codex", "codex.exe"))

    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate).expanduser()
        resolved = str(path) if path.is_file() else shutil.which(candidate)
        if resolved and Path(resolved).is_file():
            if Path(resolved).suffix.lower() in {".cmd", ".ps1", ".bat"}:
                return _node_launcher_from_shim(resolved)
            return [resolved]
    return None


def _write_json_line(stream, payload):
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _read_json_line(stream, deadline):
    """读取 app-server 的下一条 JSON 行，跳过异常诊断行。"""
    while time.monotonic() < deadline:
        line = stream.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _thread_config(launcher, thread_id):
    """从当前 Codex thread 读取 cwd/model/provider/reasoning effort。"""
    argv = list(launcher) + ["app-server", "--stdio"]
    proc = None
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=os.getcwd(),
            close_fds=os.name != "nt",
        )
        _write_json_line(
            proc.stdin,
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "ruan-continue2run", "version": "1.0"}},
            },
        )
        deadline = time.monotonic() + CONFIG_QUERY_TIMEOUT
        initialized = False
        while time.monotonic() < deadline:
            msg = _read_json_line(proc.stdout, deadline)
            if msg is None:
                break
            if msg.get("id") == 1:
                if "error" in msg:
                    return None, f"CODEX_CONFIG_QUERY_FAILED: initialize 失败：{msg['error']}"
                initialized = True
                break
        if not initialized:
            return None, "CODEX_CONFIG_QUERY_FAILED: app-server initialize 超时或已退出"

        _write_json_line(
            proc.stdin,
            {
                "id": 2,
                "method": "thread/read",
                "params": {"threadId": thread_id, "includeTurns": False},
            },
        )
        while time.monotonic() < deadline:
            msg = _read_json_line(proc.stdout, deadline)
            if msg is None:
                break
            if msg.get("id") != 2:
                continue
            if "error" in msg:
                return None, f"CODEX_CONFIG_QUERY_FAILED: thread/read 失败：{msg['error']}"
            thread = (msg.get("result") or {}).get("thread") or {}
            return thread, None
        return None, "CODEX_CONFIG_QUERY_FAILED: thread/read 超时或已退出"
    except OSError as exc:
        return None, f"CODEX_CONFIG_QUERY_FAILED: 无法启动 app-server：{exc!r}"
    finally:
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except OSError:
                    pass
                except subprocess.TimeoutExpired:
                    pass
            for stream in (proc.stdout,):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass


def _effective_params(context, thread):
    """合并 app-server 的 canonical 参数和 RelayContext 的已知值。"""
    supplied = context.get("runtime_params") or {}
    cwd = thread.get("cwd") or supplied.get("working_directory")
    model = thread.get("model") or supplied.get("model")
    effort = thread.get("reasoningEffort") or supplied.get("thinking_depth")
    provider = thread.get("modelProvider")
    if not cwd:
        return None, "CODEX_CWD_MISSING: 当前 Codex thread 没有可用 working directory"
    if not model:
        return None, "CODEX_MODEL_MISSING: 当前 Codex thread 没有可用 model"
    if not effort:
        return None, "CODEX_REASONING_EFFORT_MISSING: 当前 Codex thread 没有可用思维深度"
    path = Path(str(cwd)).expanduser()
    if not path.is_dir():
        return None, f"CODEX_CWD_INVALID: working directory 不是有效目录：{cwd}"
    settings = {}
    rollout_path = thread.get("path")
    if rollout_path:
        try:
            for line in Path(rollout_path).read_text(encoding="utf-8").splitlines():
                payload = json.loads(line).get("payload") or {}
                if payload.get("type") == "thread_settings_applied":
                    settings = payload.get("thread_settings") or {}
        except (OSError, json.JSONDecodeError):
            settings = {}
    active_profile = (settings.get("active_permission_profile") or {}).get("id")
    sandbox = None
    if active_profile == ":danger-full-access":
        sandbox = "danger-full-access"
    elif active_profile == ":workspace":
        sandbox = "workspace-write"
    elif active_profile == ":read-only":
        sandbox = "read-only"
    return {
        "cwd": path,
        "model": str(model),
        "effort": str(effort),
        "provider": provider,
        "approval_policy": settings.get("approval_policy"),
        "sandbox": sandbox,
    }, None


def _output_paths():
    """给 app-server worker 的原始输出找不依赖项目权限的临时位置。"""
    directory = Path(tempfile.mkdtemp(prefix="ruan-continue2run-codex-"))
    return directory, directory / "context.json", directory / "status.json", directory / "stdout.log", directory / "stderr.log"


def _read_status(path, deadline):
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.05)
    return None


def create(context):
    """创建下一轮 Codex Desktop-visible 会话并返回统一结果。"""
    launcher = _resolve_launcher()
    if not launcher:
        return result(False, error="CODEX_LAUNCHER_NOT_FOUND: 找不到 Codex CLI，未执行任何创建")

    thread_id = next((os.environ.get(name) for name in THREAD_ENV_NAMES if os.environ.get(name)), None)
    if not thread_id:
        return result(False, error="CODEX_THREAD_ID_MISSING: 无法读取当前 Codex thread id，未执行任何创建")

    thread, query_error = _thread_config(launcher, thread_id)
    if query_error:
        return result(False, error=query_error)
    params, config_error = _effective_params(context, thread)
    if config_error:
        return result(False, error=config_error)

    worker = Path(__file__).with_name("codex_worker.py")
    directory, context_path, status_path, out_path, err_path = _output_paths()
    context_path.write_text(json.dumps(context, ensure_ascii=False), encoding="utf-8")
    argv = [
        sys.executable,
        str(worker),
        "--launcher-json",
        json.dumps(launcher),
        "--context-path",
        str(context_path),
        "--status-path",
        str(status_path),
        "--stdout-path",
        str(out_path),
        "--stderr-path",
        str(err_path),
        "--cwd",
        str(params["cwd"]),
        "--model",
        params["model"],
        "--effort",
        params["effort"],
    ]
    if params.get("provider"):
        argv += ["--provider", str(params["provider"])]
    if params.get("approval_policy"):
        argv += ["--approval-policy", str(params["approval_policy"])]
    if params.get("sandbox"):
        argv += ["--sandbox", str(params["sandbox"])]
    try:
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
        else:
            flags["start_new_session"] = True
        proc = subprocess.Popen(
            argv,
            cwd=str(params["cwd"]),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            **flags,
        )
        pid = proc.pid
        proc.returncode = proc.poll()
        if proc.returncode is None:
            proc.returncode = 0
    except Exception as exc:  # noqa: BLE001 - Adapter 只如实报告，不重试
        return result(False, error=f"CODEX_SPAWN_FAILED: {exc!r}，未创建下一会话")

    status = _read_status(status_path, time.monotonic() + CONFIG_QUERY_TIMEOUT)
    if not status:
        try:
            proc.terminate()
        except OSError:
            pass
        return result(False, error="CODEX_APP_SERVER_TIMEOUT: 创建调用未在时限内返回 thread/start/turn/start 结果")
    if not status.get("issued"):
        return result(False, error=f"CODEX_APP_SERVER_CREATE_FAILED: {status.get('error')}")
    created_thread_id = status.get("thread_id")

    notes = [
        "创建调用已发出；这不表示新会话已启动或运行成功",
        f"pid={pid}",
        f"source_thread_id={thread_id}",
        f"created_thread_id={created_thread_id}",
        f"cwd={params['cwd']}",
        f"model={params['model']}",
        f"thinking_depth={params['effort']}",
        f"worker_command={' '.join(argv)}",
        f"子进程原始输出：{out_path} / {err_path}",
    ]
    if params.get("provider"):
        notes.append(f"model_provider={params['provider']}")
    return result(True, info="; ".join(notes))
