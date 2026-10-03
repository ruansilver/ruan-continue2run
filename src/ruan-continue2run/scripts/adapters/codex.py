"""Codex Desktop Adapter using the bundled codex app-server protocol.

This adapter is intentionally conservative: it reads the current thread through
app-server, keeps expected and observed values separate, and only returns
confirmed after a detached worker sees a real startup event for the first turn.
"""
from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
INVOCATION = "ruan-continue2run"
HANDOFF_DEADLINE_SECONDS = 60
STARTUP_TIMEOUT_SECONDS = 20
EXTRA_FIELDS = ["model_provider"]
PARAMETER_APPLICABILITY = {
    "working_directory": "required",
    "model": "applicable",
    "thinking_depth": "applicable",
    "permission_mode": "applicable",
    "sandbox_mode": "applicable",
    "approval_mode": "applicable",
    "model_provider": "applicable",
}

LAUNCHER_ENV = "RUAN_CONTINUE2RUN_CODEX_BIN"
BUNDLED_ENV = "CODEX_CLI_PATH"
THREAD_ENV_NAMES = ("CODEX_THREAD_ID", "CODEX_SESSION_ID")
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}


def _error(code: str, summary: str, status: str = "failed") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "retryable": False,
        "session_reference": "",
        "submitted_parameters": {},
        "effective_parameters": {},
        "startup_evidence": {},
        "error_code": code,
        "error_summary": summary,
    }


def _launcher_from_shim(shim: str) -> list[str] | None:
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


def _resolve_launcher() -> list[str] | None:
    candidates: list[str] = []
    for env_name in (LAUNCHER_ENV, BUNDLED_ENV):
        if os.environ.get(env_name):
            candidates.append(os.environ[env_name])
    candidates.extend(("codex", "codex.exe"))
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate).expanduser()
        resolved = str(path) if path.is_file() else shutil.which(candidate)
        if not resolved or not Path(resolved).is_file():
            continue
        if Path(resolved).suffix.lower() in {".cmd", ".ps1", ".bat"}:
            return _launcher_from_shim(resolved)
        return [resolved]
    return None


def _thread_id() -> str | None:
    return next((os.environ.get(name) for name in THREAD_ENV_NAMES if os.environ.get(name)), None)


def detect() -> bool:
    return bool(_thread_id() and _resolve_launcher())


def _spawn_app_server(launcher: list[str], cwd: str, capture: bool = True):
    flags: dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    return subprocess.Popen(
        launcher + ["app-server", "--stdio"],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        close_fds=os.name != "nt",
        **flags,
    )


def _send(stream, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _read_until(proc: subprocess.Popen, request_id: int, deadline: float) -> dict[str, Any] | None:
    if proc.stdout is None:
        return None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            events = selector.select(max(0.01, deadline - time.monotonic()))
            if not events:
                continue
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
        return None
    finally:
        selector.close()


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                proc.terminate()
            proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
    finally:
        for stream in (getattr(proc, "stdin", None), getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _rpc_thread_read(launcher: list[str], thread_id: str, cwd: str) -> tuple[dict[str, Any] | None, str | None]:
    proc = None
    try:
        proc = _spawn_app_server(launcher, cwd)
        deadline = time.monotonic() + 10
        _send(proc.stdin, {"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "ruan-continue2run", "title": "ruan-continue2run", "version": "2.0"}
        }})
        initialized = _read_until(proc, 1, deadline)
        if not initialized or "error" in initialized:
            return None, "CODEX_CONFIG_QUERY_FAILED: app-server initialize failed"
        _send(proc.stdin, {"method": "initialized", "params": {}})
        _send(proc.stdin, {"id": 2, "method": "thread/read", "params": {"threadId": thread_id, "includeTurns": False}})
        response = _read_until(proc, 2, deadline)
        if not response:
            return None, "CODEX_CONFIG_QUERY_FAILED: thread/read timeout"
        if "error" in response:
            return None, f"CODEX_CONFIG_QUERY_FAILED: thread/read failed: {response['error']}"
        thread = (response.get("result") or {}).get("thread") or {}
        return thread, None
    except (OSError, ValueError) as exc:
        return None, f"CODEX_CONFIG_QUERY_FAILED: {type(exc).__name__}: {exc}"
    finally:
        _terminate(proc)


def _canonical(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "" if value is None else str(value)


def _settings_from_rollout(thread: dict[str, Any]) -> dict[str, Any]:
    path = thread.get("path")
    settings: dict[str, Any] = {}
    if not path:
        return settings
    try:
        for line in Path(str(path)).read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            payload = record.get("payload") or {}
            if payload.get("type") == "thread_settings_applied":
                settings.update(payload.get("thread_settings") or {})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return settings


def _profile_to_sandbox(profile: str | None) -> str | None:
    if profile in {":danger-full-access", "danger-full-access"}:
        return "danger-full-access"
    if profile in {":workspace", ":workspace-write", "workspace-write"}:
        return "workspace-write"
    if profile in {":read-only", "read-only"}:
        return "read-only"
    return None


def _runtime_value(state: str, value: Any = "") -> dict[str, str]:
    return {"state": state, "value": _canonical(value)}


def read_runtime_context() -> dict[str, dict[str, str]]:
    fields = ["working_directory", "model", "thinking_depth", "permission_mode", "sandbox_mode", "approval_mode", "model_provider"]
    unavailable = {field: _runtime_value("unavailable") for field in fields}
    launcher = _resolve_launcher()
    thread_id = _thread_id()
    if not launcher or not thread_id:
        return unavailable
    thread, error = _rpc_thread_read(launcher, thread_id, os.getcwd())
    if error or not thread:
        return unavailable
    settings = _settings_from_rollout(thread)
    profile = os.environ.get("CODEX_PERMISSION_PROFILE")
    profile = profile or settings.get("active_permission_profile") or settings.get("permission_profile")
    if isinstance(profile, dict):
        profile = profile.get("id") or profile.get("name")
    sandbox = _profile_to_sandbox(str(profile) if profile else None)
    approval = settings.get("approval_policy", settings.get("approvalPolicy"))
    cwd = thread.get("cwd")
    model = thread.get("model")
    effort = thread.get("reasoningEffort") or thread.get("reasoning_effort")
    provider = thread.get("modelProvider") or thread.get("model_provider")
    result = dict(unavailable)
    result["working_directory"] = _runtime_value("observed", cwd) if cwd else result["working_directory"]
    result["model"] = _runtime_value("observed", model) if model else result["model"]
    result["thinking_depth"] = _runtime_value("observed", effort) if effort else result["thinking_depth"]
    if profile:
        result["permission_mode"] = _runtime_value("observed", profile)
    if sandbox:
        result["sandbox_mode"] = _runtime_value("observed", sandbox)
    if approval is not None:
        result["approval_mode"] = _runtime_value("observed", approval)
    if provider:
        result["model_provider"] = _runtime_value("observed", provider)
    return result


def _expected(ctx: dict[str, Any], field: str) -> str:
    return str((ctx.get("parameter_expectations") or {}).get(field, {}).get("value") or "")


def _approval_value(value: str) -> Any:
    if value.startswith("{") or value.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def preflight(ctx: dict[str, Any]) -> dict[str, Any]:
    launcher = _resolve_launcher()
    if not launcher:
        return {"ok": False, "error_code": "CODEX_LAUNCHER_NOT_FOUND", "problems": ["bundled or PATH codex launcher not found"]}
    if not _thread_id():
        return {"ok": False, "error_code": "CODEX_THREAD_ID_MISSING", "problems": ["CODEX_THREAD_ID/CODEX_SESSION_ID unavailable"]}
    cwd = _expected(ctx, "working_directory")
    if not cwd or not Path(cwd).expanduser().is_dir():
        return {"ok": False, "error_code": "CODEX_CWD_INVALID", "problems": [f"invalid working directory: {cwd!r}"]}
    model = _expected(ctx, "model")
    effort = _expected(ctx, "thinking_depth")
    provider = _expected(ctx, "model_provider")
    sandbox = _expected(ctx, "sandbox_mode")
    approval = _expected(ctx, "approval_mode")
    missing = [name for name, value in (("model", model), ("thinking_depth", effort), ("model_provider", provider), ("sandbox_mode", sandbox), ("approval_mode", approval)) if not value]
    if missing:
        return {"ok": False, "error_code": "CODEX_RUNTIME_PARAMETER_UNAVAILABLE", "problems": [f"missing Codex parameters: {missing}"]}
    if sandbox not in _SANDBOXES:
        return {"ok": False, "error_code": "CODEX_SANDBOX_INVALID", "problems": [f"unsupported sandbox_mode: {sandbox!r}"]}
    return {"ok": True, "error_code": ""}


def _worker_paths() -> tuple[Path, Path, Path]:
    directory = Path(tempfile.mkdtemp(prefix="ruan-continue2run-codex-"))
    return directory, directory / "request.json", directory / "status.json"


def _read_status(path: Path, deadline: float) -> dict[str, Any] | None:
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.05)
    return None


def create_and_confirm(ctx: dict[str, Any], payload: str) -> dict[str, Any]:
    launcher = _resolve_launcher()
    if not launcher:
        return _error("CODEX_LAUNCHER_NOT_FOUND", "bundled or PATH codex launcher not found")
    if not _thread_id():
        return _error("CODEX_THREAD_ID_MISSING", "current Codex thread id unavailable")
    directory, request_path, status_path = _worker_paths()
    request = {
        "schema_version": SCHEMA_VERSION,
        "launcher": launcher,
        "ctx": ctx,
        "payload": payload,
        "cwd": _expected(ctx, "working_directory"),
        "model": _expected(ctx, "model"),
        "effort": _expected(ctx, "thinking_depth"),
        "provider": _expected(ctx, "model_provider"),
        "approval_policy": _approval_value(_expected(ctx, "approval_mode")),
        "sandbox": _expected(ctx, "sandbox_mode"),
    }
    request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    os.chmod(request_path, 0o600)
    worker = Path(__file__).with_name("codex_worker.py")
    flags: dict[str, Any] = {}
    if os.name == "nt":
        flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        flags["start_new_session"] = True
    try:
        proc = subprocess.Popen(
            [sys.executable, str(worker), "--request", str(request_path), "--status", str(status_path)],
            cwd=_expected(ctx, "working_directory") or os.getcwd(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            **flags,
        )
    except OSError as exc:
        return _error("CODEX_WORKER_START_FAILED", f"cannot start detached Codex worker: {exc}")
    # The worker owns the detached app-server lifecycle. Mark this Popen handle
    # as handed off so the short-lived Adapter process does not warn/reap it.
    proc.returncode = 0
    status = _read_status(status_path, time.monotonic() + STARTUP_TIMEOUT_SECONDS)
    if not status:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unknown",
            "retryable": False,
            "session_reference": "",
            "submitted_parameters": {field: _expected(ctx, field) for field in ["working_directory", "model", "thinking_depth", "permission_mode", "sandbox_mode", "approval_mode", "model_provider"]},
            "effective_parameters": {},
            "startup_evidence": {},
            "error_code": "CODEX_STARTUP_TIMEOUT",
            "error_summary": "detached Codex worker did not produce startup evidence within observation window",
        }
    thread_id = str(status.get("thread_id") or "")
    submitted = {field: _expected(ctx, field) for field in ["working_directory", "model", "thinking_depth", "permission_mode", "sandbox_mode", "approval_mode", "model_provider"]}
    submitted["payload_sha256"] = __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest()
    if status.get("phase") == "running" and thread_id:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "confirmed",
            "retryable": False,
            "session_reference": thread_id,
            "submitted_parameters": submitted,
            "effective_parameters": status.get("effective_parameters") or {},
            "startup_evidence": status.get("startup_evidence") or {"phase": "running"},
            "error_code": "",
            "error_summary": "",
        }
    if thread_id:
        return {**_error(status.get("error_code") or "CODEX_STARTUP_UNKNOWN", status.get("error_summary") or "Codex thread was created but startup could not be confirmed", "unknown"),
                "session_reference": thread_id, "submitted_parameters": submitted, "startup_evidence": status.get("startup_evidence") or {}}
    return _error(status.get("error_code") or "CODEX_START_FAILED", status.get("error_summary") or "Codex worker failed before creating a thread", "failed")
