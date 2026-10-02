"""Internal Codex app-server worker used by the Codex Adapter.

This process owns the app-server connection until the first turn completes. It
writes one handshake result to ``status_path`` for the parent Adapter and keeps
all protocol output in the supplied files for post-run inspection.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import relay_context  # noqa: E402

_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def _send(stream, payload):
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def _status(path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_response(stream, output, request_id, deadline):
    while time.monotonic() < deadline:
        line = stream.readline()
        if not line:
            return None
        output.write(line)
        output.flush()
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == request_id:
            return message
    return None


def _drain_until_done(stream, output, thread_id, deadline=None):
    while deadline is None or time.monotonic() < deadline:
        line = stream.readline()
        if not line:
            return
        output.write(line)
        output.flush()
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("method") in {"turn/completed", "turn/failed", "turn/cancelled"}:
            params = message.get("params") or {}
            if params.get("threadId") in (None, thread_id):
                return


def run(args):
    status_path = Path(args.status_path)
    stdout_path = Path(args.stdout_path)
    stderr_path = Path(args.stderr_path)
    context = json.loads(Path(args.context_path).read_text(encoding="utf-8"))
    launcher = json.loads(args.launcher_json)
    cwd = str(Path(args.cwd).expanduser())
    model = args.model
    effort = args.effort
    provider = args.provider or None
    approval_policy = args.approval_policy or None
    sandbox = args.sandbox or None
    proc = None
    try:
        with stdout_path.open("w", encoding="utf-8") as output, stderr_path.open("w", encoding="utf-8") as errors:
            flags = {}
            if os.name == "nt":
                flags["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
            else:
                flags["start_new_session"] = True
            proc = subprocess.Popen(
                list(launcher) + ["app-server", "--stdio"],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                close_fds=True,
                **flags,
            )
            deadline = time.monotonic() + 20
            _send(proc.stdin, {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "Codex Desktop", "version": "0.1"}},
            })
            message = _read_response(proc.stdout, output, 1, deadline)
            if not message or "error" in message:
                _status(status_path, {"issued": False, "error": f"initialize failed: {message!r}"})
                return 2

            start_params = {
                "cwd": cwd,
                "model": model,
                "modelProvider": provider,
                "threadSource": "user",
            }
            if approval_policy:
                start_params["approvalPolicy"] = approval_policy
            if sandbox:
                start_params["sandbox"] = sandbox
            _send(proc.stdin, {"id": 2, "method": "thread/start", "params": start_params})
            message = _read_response(proc.stdout, output, 2, deadline)
            if not message or "error" in message:
                _status(status_path, {"issued": False, "error": f"thread/start failed: {message!r}"})
                return 3
            thread = (message.get("result") or {}).get("thread") or {}
            thread_id = thread.get("id")
            if not thread_id:
                _status(status_path, {"issued": False, "error": "thread/start returned no thread id"})
                return 4

            _send(proc.stdin, {
                "id": 3,
                "method": "turn/start",
                "params": {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": relay_context.render_first_message(context)}],
                    "effort": effort,
                },
            })
            message = _read_response(proc.stdout, output, 3, deadline)
            if not message or "error" in message:
                _status(status_path, {"issued": False, "error": f"turn/start failed: {message!r}"})
                return 5
            _status(status_path, {"issued": True, "thread_id": thread_id})
            _drain_until_done(proc.stdout, output, thread_id)
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.wait(timeout=5)
            return 0
    except Exception as exc:  # worker reports to the parent; no retry
        _status(status_path, {"issued": False, "error": repr(exc)})
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
        return 6


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-json", required=True)
    parser.add_argument("--context-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--stdout-path", required=True)
    parser.add_argument("--stderr-path", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--effort", required=True)
    parser.add_argument("--provider")
    parser.add_argument("--approval-policy")
    parser.add_argument("--sandbox")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
