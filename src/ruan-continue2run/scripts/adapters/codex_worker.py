"""Detached Codex app-server worker used by adapters.codex."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

if __package__:
    from ._codex_protocol import read_message
else:
    from _codex_protocol import read_message

SCHEMA_VERSION = 1
CLIENT_INFO = {
    "name": "codex_desktop",
    "title": "Codex Desktop",
    "version": "2.0",
}


def atomic_status(path: Path, value: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def send(stream, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False) + "\n")
    stream.flush()


def read_messages(proc: subprocess.Popen, deadline: float, on_message, stop_when=None):
    while time.monotonic() < deadline:
        message = read_message(proc, deadline)
        if message is None:
            return
        on_message(message)
        if stop_when is not None and stop_when():
            return


def error_details(error: dict[str, Any], default: str) -> tuple[str, str]:
    """Return protocol error classifications without echoing provider secrets."""
    info = error.get("codexErrorInfo")
    if isinstance(info, dict):
        connection = info.get("httpConnectionFailed") or {}
        status = connection.get("httpStatusCode")
        if status in {401, 403}:
            return "CODEX_PROVIDER_AUTH_FAILED", f"provider rejected authentication (HTTP {status})"
        if isinstance(status, int):
            return f"CODEX_HTTP_{status}", f"provider request failed (HTTP {status})"
    if isinstance(info, str) and info.isidentifier():
        code = "CODEX_" + re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", info).upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", code):
            return code, f"Codex turn failed ({code})"
    return default, "app-server rejected the request or ended the turn"


def model_execution_event(method: str, params: dict[str, Any]) -> bool:
    if method == "item/agentMessage/delta":
        return bool(params.get("delta"))
    item = params.get("item") or {}
    if method == "item/completed" and item.get("type") == "agentMessage":
        return bool(item.get("text"))
    return method in {"item/started", "item/completed"} and item.get("type") in {
        "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch",
    }


def terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass


def completion_was_recorded(path: Path) -> bool:
    """Return true once the durable terminal event has been recorded."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    evidence = value.get("startup_evidence") or {}
    return bool(value.get("turn_completed") or evidence.get("method") == "turn/completed")


def mark_worker_exited(path: Path) -> None:
    """Leave durable evidence that the external owner was released."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        value = {}
    value["worker_exited"] = True
    atomic_status(path, value)


def run(request_path: Path, status_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    launcher = request["launcher"]
    cwd = str(Path(request["cwd"]).expanduser())
    proc = None
    thread_id = ""
    evidence: dict[str, Any] = {}
    effective: dict[str, Any] = {}
    try:
        flags: dict[str, Any] = {}
        if os.name == "nt":
            flags["creationflags"] = 0x08000000 | 0x00000200
        else:
            flags["start_new_session"] = True
        proc = subprocess.Popen(
            list(launcher) + ["app-server", "--stdio"],
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            close_fds=os.name != "nt",
            **flags,
        )
        deadline = time.monotonic() + float(request.get("startup_timeout", 20))
        state = {"initialized": False, "thread_started": False, "turn_started": False, "running": False, "completed": False, "blocked": False, "failed": False}
        custom_provider = request["provider"] != "openai"

        def publish_running() -> None:
            atomic_status(status_path, {"schema_version": 1, "phase": "running", "thread_id": thread_id,
                                       "effective_parameters": effective, "startup_evidence": evidence})

        def handle(message: dict[str, Any]) -> None:
            nonlocal thread_id, effective, evidence
            if message.get("id") == 1 and "error" not in message:
                state["initialized"] = True
            if message.get("id") == 2:
                if "error" in message:
                    state["failed"] = True
                    code, summary = error_details(message["error"], "CODEX_THREAD_START_FAILED")
                    atomic_status(status_path, {"schema_version": 1, "phase": "failed", "error_code": code, "error_summary": summary})
                    return
                result = message.get("result") or {}
                thread = result.get("thread") or {}
                thread_id = str(thread.get("id") or "")
                active_profile = result.get("activePermissionProfile") or thread.get("activePermissionProfile") or {}
                effective = {}
                for source, target in (("cwd", "working_directory"), ("model", "model"),
                                       ("modelProvider", "model_provider")):
                    value = result.get(source, thread.get(source))
                    if value is not None:
                        effective[target] = value
                # thread/start effort describes initial defaults, not the
                # later turn/start override. Do not invent an effective value.
                if isinstance(active_profile, dict) and active_profile.get("id"):
                    effective["permission_mode"] = active_profile["id"]
                state["thread_started"] = bool(thread_id)
            if message.get("id") == 3:
                if "error" in message:
                    state["failed"] = True
                    phase = "unknown" if thread_id else "failed"
                    code, summary = error_details(message["error"], "CODEX_TURN_START_FAILED")
                    atomic_status(status_path, {"schema_version": 1, "phase": phase, "thread_id": thread_id, "error_code": code, "error_summary": summary})
                    return
                state["turn_started"] = True
                if state["running"] and not state["blocked"]:
                    publish_running()
            method = message.get("method") or ""
            params = message.get("params") or {}
            if not thread_id or params.get("threadId", thread_id) not in {None, "", thread_id}:
                return
            if method.endswith("/requestApproval"):
                state["blocked"] = True
                atomic_status(status_path, {"schema_version": 1, "phase": "unknown", "thread_id": thread_id,
                                           "error_code": "APPROVAL_BLOCKED", "error_summary": "Codex startup requires approval"})
                return
            startup = model_execution_event(method, params) if custom_provider else method in {
                "turn/started", "item/started", "item/agentMessage/delta",
            }
            if startup and not state["blocked"]:
                state["running"] = True
                evidence = {"method": method, "thread_id": thread_id}
                if state["turn_started"]:
                    publish_running()
            if method == "turn/completed" and params.get("threadId", thread_id) in {None, "", thread_id}:
                status = params.get("turn", {}).get("status") or params.get("status")
                if status in {"completed", "failed", "interrupted", "cancelled"} and thread_id:
                    # Every terminal turn status releases the external
                    # app-server owner.  Waiting only for `completed` leaves
                    # failed turns (for example model-capacity errors) alive
                    # forever and makes Desktop show “opened in another app”.
                    state["completed"] = True
                    evidence = {"method": method, "thread_id": thread_id}
                    terminal_phase = "running" if status == "completed" and state["running"] and not state["blocked"] else "unknown"
                    turn = params.get("turn") or {}
                    error = turn.get("error") or params.get("error") or {}
                    code, summary = error_details(error, "CODEX_TURN_FAILED" if status == "failed" else "CODEX_STARTUP_UNKNOWN")
                    atomic_status(status_path, {"schema_version": 1, "phase": terminal_phase, "thread_id": thread_id, "effective_parameters": effective, "startup_evidence": evidence, "turn_completed": True, "turn_status": status, "error_code": "" if terminal_phase == "running" else code, "error_summary": "" if terminal_phase == "running" else summary})
            # Some bundled Desktop app-server builds emit the terminal idle
            # status without a matching ``turn/completed`` notification. Once
            # a turn has produced startup evidence, that transition is the
            # durable signal that the external owner can be released.
            if method == "thread/status/changed" and state["running"] and params.get("threadId", thread_id) in {None, "", thread_id}:
                status = params.get("status") or {}
                if status.get("type") == "idle":
                    state["completed"] = True
                    evidence = {"method": method, "thread_id": thread_id}
                    atomic_status(status_path, {"schema_version": 1, "phase": "running", "thread_id": thread_id, "effective_parameters": effective, "startup_evidence": evidence, "turn_completed": True})

        send(proc.stdin, {"id": 1, "method": "initialize", "params": {
            "clientInfo": CLIENT_INFO,
            "capabilities": {"experimentalApi": True},
        }})
        read_messages(proc, deadline, handle, lambda: state["initialized"])
        if not state["initialized"]:
            atomic_status(status_path, {"schema_version": 1, "phase": "failed", "error_code": "CODEX_INITIALIZE_FAILED", "error_summary": "initialize did not succeed"})
            return 2
        send(proc.stdin, {"method": "initialized", "params": {}})
        params = {"cwd": cwd, "model": request["model"], "modelProvider": request["provider"], "threadSource": "user", "approvalPolicy": request["approval_policy"]}
        # `permissions` and legacy `sandbox` are mutually exclusive in the
        # app-server protocol. Named profiles preserve the Desktop permission
        # mode and make it observable in the new thread's metadata.
        if request.get("permission_profile"):
            params["permissions"] = request["permission_profile"]
        else:
            params["sandbox"] = request["sandbox"]
        send(proc.stdin, {"id": 2, "method": "thread/start", "params": params})
        read_messages(proc, deadline, handle, lambda: state["thread_started"] or status_path.is_file())
        if not state["thread_started"]:
            return 3
        # Give the Desktop sidebar a concise, recognizable title. Without
        # this, the first user message is the full relay control frame and the
        # new session is technically listed but practically hard to find.
        send(proc.stdin, {"id": 4, "method": "thread/name/set", "params": {
            "threadId": thread_id,
            "name": request.get("thread_name") or "接力：ruan-continue2run",
        }})
        send(proc.stdin, {"id": 3, "method": "turn/start", "params": {"threadId": thread_id, "input": [{"type": "text", "text": request["payload"]}], "effort": None if request["effort"] == "null" else request["effort"]}})
        read_messages(proc, deadline, handle, lambda: (state["running"] and state["turn_started"]) or state["completed"] or state["blocked"] or state["failed"])
        if not state["turn_started"]:
            if not status_path.is_file():
                atomic_status(status_path, {"schema_version": 1, "phase": "unknown" if thread_id else "failed", "thread_id": thread_id, "error_code": "CODEX_TURN_NOT_ACCEPTED", "error_summary": "turn/start was not accepted"})
            return 4
        if not state["running"]:
            if not status_path.is_file():
                atomic_status(status_path, {"schema_version": 1, "phase": "unknown", "thread_id": thread_id, "effective_parameters": effective, "error_code": "CODEX_STARTUP_UNKNOWN", "error_summary": "turn accepted but no model execution event observed"})
            return 5
        # Keep the app-server connection alive until the turn completes. The
        # worker is detached from Adapter/Relay, so the old session may exit.
        while proc.poll() is None and not state["completed"]:
            read_messages(proc, time.monotonic() + 1.0, handle, lambda: state["completed"])
            if completion_was_recorded(status_path):
                state["completed"] = True
            time.sleep(0.05)
        if proc.poll() is None and state["completed"]:
            try:
                proc.stdin.close()
            except OSError:
                pass
        return 0
    except Exception as exc:
        atomic_status(status_path, {"schema_version": 1, "phase": "unknown" if thread_id else "failed", "thread_id": thread_id, "error_code": "CODEX_WORKER_EXCEPTION", "error_summary": f"Codex worker stopped with {type(exc).__name__}"})
        return 6
    finally:
        terminate(proc)
        mark_worker_exited(status_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.status))


if __name__ == "__main__":
    raise SystemExit(main())
