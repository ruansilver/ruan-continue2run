"""Detached Codex app-server worker used by adapters.codex."""
from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

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


def read_messages(proc: subprocess.Popen, deadline: float, output_path: Path, on_message, stop_when=None):
    if proc.stdout is None:
        return
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        with output_path.open("a", encoding="utf-8") as output:
            while time.monotonic() < deadline:
                events = selector.select(max(0.01, deadline - time.monotonic()))
                if not events:
                    continue
                line = proc.stdout.readline()
                if not line:
                    return
                output.write(line)
                output.flush()
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                on_message(message)
                if stop_when is not None and stop_when():
                    return
    finally:
        selector.close()


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


def run(request_path: Path, status_path: Path) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    launcher = request["launcher"]
    cwd = str(Path(request["cwd"]).expanduser())
    logs = status_path.with_suffix(".events.log")
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
        state = {"initialized": False, "thread_started": False, "turn_started": False, "running": False, "completed": False}

        def handle(message: dict[str, Any]) -> None:
            nonlocal thread_id, effective, evidence
            if message.get("id") == 1 and "error" not in message:
                state["initialized"] = True
            if message.get("id") == 2:
                if "error" in message:
                    atomic_status(status_path, {"schema_version": 1, "phase": "failed", "error_code": "CODEX_THREAD_START_FAILED", "error_summary": str(message["error"])})
                    return
                thread = (message.get("result") or {}).get("thread") or {}
                thread_id = str(thread.get("id") or "")
                active_profile = thread.get("activePermissionProfile") or {}
                effective = {"working_directory": thread.get("cwd", cwd), "model": thread.get("model", request["model"]), "thinking_depth": thread.get("reasoningEffort", request["effort"]), "model_provider": thread.get("modelProvider", request["provider"])}
                if isinstance(active_profile, dict) and active_profile.get("id"):
                    effective["permission_mode"] = active_profile["id"]
                state["thread_started"] = bool(thread_id)
            if message.get("id") == 3:
                if "error" in message:
                    phase = "unknown" if thread_id else "failed"
                    atomic_status(status_path, {"schema_version": 1, "phase": phase, "thread_id": thread_id, "error_code": "CODEX_TURN_START_FAILED", "error_summary": str(message["error"])})
                    return
                state["turn_started"] = True
            method = message.get("method") or ""
            params = message.get("params") or {}
            if method in {"turn/started", "item/started", "item/agentMessage/delta"} and params.get("threadId", thread_id) in {None, "", thread_id}:
                state["running"] = True
                evidence = {"method": method, "params": params}
                atomic_status(status_path, {"schema_version": 1, "phase": "running", "thread_id": thread_id, "effective_parameters": effective, "startup_evidence": evidence})
            if method == "turn/completed" and params.get("threadId", thread_id) in {None, "", thread_id}:
                status = params.get("turn", {}).get("status") or params.get("status")
                if status == "completed" and thread_id:
                    state["running"] = True
                    state["completed"] = True
                    evidence = {"method": method, "params": params}
                    atomic_status(status_path, {"schema_version": 1, "phase": "running", "thread_id": thread_id, "effective_parameters": effective, "startup_evidence": evidence, "turn_completed": True})
            # Some bundled Desktop app-server builds emit the terminal idle
            # status without a matching ``turn/completed`` notification. Once
            # a turn has produced startup evidence, that transition is the
            # durable signal that the external owner can be released.
            if method == "thread/status/changed" and state["running"] and params.get("threadId", thread_id) in {None, "", thread_id}:
                status = params.get("status") or {}
                if status.get("type") == "idle":
                    state["completed"] = True
                    evidence = {"method": method, "params": params}
                    atomic_status(status_path, {"schema_version": 1, "phase": "running", "thread_id": thread_id, "effective_parameters": effective, "startup_evidence": evidence, "turn_completed": True})

        send(proc.stdin, {"id": 1, "method": "initialize", "params": {
            "clientInfo": CLIENT_INFO,
            "capabilities": {"experimentalApi": True},
        }})
        read_messages(proc, deadline, logs, handle, lambda: state["initialized"])
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
        read_messages(proc, deadline, logs, handle, lambda: state["thread_started"] or status_path.is_file())
        if not state["thread_started"]:
            return 3
        send(proc.stdin, {"id": 3, "method": "turn/start", "params": {"threadId": thread_id, "input": [{"type": "text", "text": request["payload"]}], "effort": request["effort"]}})
        read_messages(proc, deadline, logs, handle, lambda: state["running"] or status_path.is_file())
        if not state["turn_started"]:
            atomic_status(status_path, {"schema_version": 1, "phase": "unknown" if thread_id else "failed", "thread_id": thread_id, "error_code": "CODEX_TURN_NOT_ACCEPTED", "error_summary": "turn/start was not accepted"})
            return 4
        if not state["running"]:
            atomic_status(status_path, {"schema_version": 1, "phase": "unknown", "thread_id": thread_id, "effective_parameters": effective, "error_code": "CODEX_STARTUP_UNKNOWN", "error_summary": "turn accepted but no startup event observed"})
            return 5
        # Keep the app-server connection alive until the turn completes. The
        # worker is detached from Adapter/Relay, so the old session may exit.
        while proc.poll() is None and not state["completed"]:
            read_messages(proc, time.monotonic() + 1.0, logs, handle, lambda: state["completed"])
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
        atomic_status(status_path, {"schema_version": 1, "phase": "unknown" if thread_id else "failed", "thread_id": thread_id, "error_code": "CODEX_WORKER_EXCEPTION", "error_summary": f"{type(exc).__name__}: {exc}"})
        return 6
    finally:
        terminate(proc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--status", required=True)
    args = parser.parse_args()
    return run(Path(args.request), Path(args.status))


if __name__ == "__main__":
    raise SystemExit(main())
