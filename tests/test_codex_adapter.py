import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src/ruan-continue2run/scripts"
sys.path.insert(0, str(SCRIPTS))
from adapters import codex  # noqa: E402


FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
for line in sys.stdin:
    try: msg=json.loads(line)
    except Exception: continue
    method=msg.get("method")
    if msg.get("id") == 1:
        Path(os.environ["FAKE_INIT"]).write_text(json.dumps(msg["params"]), encoding="utf-8")
        print(json.dumps({"id":1,"result":{"userAgent":"fake"}}), flush=True)
    elif method == "thread/read":
        print(json.dumps({"id":msg["id"],"result":{"thread":{
            "id":"current-thread","cwd":os.environ["FAKE_CWD"],"model":"gpt-test",
            "modelProvider":"fake-provider","reasoningEffort":"high"}}}), flush=True)
    elif method == "thread/start":
        Path(os.environ["FAKE_CAPTURE"]).write_text(json.dumps({"start":msg["params"]}), encoding="utf-8")
        print(json.dumps({"id":msg["id"],"result":{"thread":{
            "id":"created-thread","cwd":msg["params"]["cwd"],"model":msg["params"]["model"],
            "modelProvider":msg["params"]["modelProvider"],"reasoningEffort":"high"}}}), flush=True)
    elif method == "turn/start":
        capture=json.loads(Path(os.environ["FAKE_CAPTURE"]).read_text(encoding="utf-8"))
        capture["turn"]=msg["params"]
        Path(os.environ["FAKE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
        print(json.dumps({"id":msg["id"],"result":{"turn":{"id":"turn-1"}}}), flush=True)
        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":"created-thread","delta":"started"}}), flush=True)
        time.sleep(0.05)
        print(json.dumps({"method":"turn/completed","params":{"threadId":"created-thread","turn":{"status":"completed"}}}), flush=True)
'''


class CodexAdapterTests(unittest.TestCase):
    def test_spawned_thread_observes_permissions_from_turn_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({"type": "turn_context", "payload": {
                "approval_policy": "never",
                "sandbox_policy": {"type": "danger-full-access"},
                "permission_profile": {"id": ":danger-full-access"},
            }}) + "\n", encoding="utf-8")
            thread = {"cwd": tmp, "model": "gpt-test", "modelProvider": "fake-provider",
                      "reasoningEffort": "high", "path": str(rollout)}
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "child-thread"}, clear=True), \
                    patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                    patch.object(codex, "_rpc_thread_read", return_value=(thread, None)):
                observed = codex.read_runtime_context()
            for field, value in {"permission_mode": ":danger-full-access",
                                 "sandbox_mode": "danger-full-access", "approval_mode": "never"}.items():
                self.assertEqual(observed[field], {"state": "observed", "value": value})

    def test_unknown_permission_profile_is_not_fabricated(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({"type": "turn_context", "payload": {
                "approval_policy": "never", "sandbox_policy": {"type": "external-sandbox"},
                "permission_profile": {"type": "unknown-profile"},
            }}) + "\n", encoding="utf-8")
            self.assertIsNone(codex._profile_to_sandbox("unknown-profile"))
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "child-thread"}, clear=True), \
                    patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                    patch.object(codex, "_rpc_thread_read", return_value=({"path": str(rollout)}, None)):
                observed = codex.read_runtime_context()
            self.assertEqual(observed["permission_mode"]["state"], "unavailable")
            self.assertEqual(observed["sandbox_mode"]["state"], "unavailable")

    def test_thread_read_envelope_is_used_for_effective_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            thread = {"cwd": tmp, "model": "gpt-test", "modelProvider": "fake-provider",
                      "reasoningEffort": "high", "_activePermissionProfile": {"id": ":danger-full-access"},
                      "_activeSandbox": {"type": "dangerFullAccess"}, "_activeApprovalPolicy": "never"}
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "child-thread"}, clear=True), \
                    patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                    patch.object(codex, "_rpc_thread_read", return_value=(thread, None)):
                observed = codex.read_runtime_context()
            self.assertEqual(observed["permission_mode"]["value"], ":danger-full-access")
            self.assertEqual(observed["sandbox_mode"]["value"], "danger-full-access")
            self.assertEqual(observed["approval_mode"]["value"], "never")

    def test_turn_context_active_profile_wins_over_environment_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({"type": "turn_context", "payload": {
                "approval_policy": "never", "sandbox_policy": {"type": "danger-full-access"},
                "permission_profile": {"type": "disabled"},
                "active_permission_profile": {"id": ":danger-full-access"},
            }}) + "\n", encoding="utf-8")
            thread = {"cwd": tmp, "path": str(rollout)}
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "child-thread"}, clear=True), \
                    patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                    patch.object(codex, "_rpc_thread_read", return_value=(thread, None)):
                observed = codex.read_runtime_context()
            self.assertEqual(observed["permission_mode"], {"state": "observed", "value": ":danger-full-access"})

    def test_desktop_launcher_and_startup_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(FAKE, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            capture = root / "capture.json"
            init = root / "init.json"
            old = os.environ.copy()
            os.environ.update({
                "CODEX_CLI_PATH": str(launcher),
                "CODEX_THREAD_ID": "current-thread",
                "CODEX_PERMISSION_PROFILE": ":workspace-write",
                "FAKE_CWD": str(root),
                "FAKE_CAPTURE": str(capture),
                "FAKE_INIT": str(init),
            })
            try:
                observed = codex.read_runtime_context()
                self.assertEqual(observed["model"]["value"], "gpt-test")
                self.assertEqual(observed["sandbox_mode"]["value"], "workspace-write")
                ctx = {
                    "parameter_expectations": {
                        "working_directory": {"state":"observed","value":str(root)},
                        "model": {"state":"observed","value":"gpt-test"},
                        "thinking_depth": {"state":"observed","value":"high"},
                        "permission_mode": {"state":"observed","value":":workspace-write"},
                        "sandbox_mode": {"state":"observed","value":"workspace-write"},
                        "approval_mode": {"state":"supplied","value":"never"},
                        "model_provider": {"state":"observed","value":"fake-provider"},
                    }
                }
                result = codex.create_and_confirm(ctx, "ruan-continue2run\\nPAYLOAD")
                self.assertEqual(result["status"], "confirmed", result)
                self.assertEqual(result["session_reference"], "created-thread")
                payload = json.loads(capture.read_text(encoding="utf-8"))
                initialize = json.loads(init.read_text(encoding="utf-8"))
                self.assertEqual(initialize["clientInfo"]["name"], "codex_desktop")
                self.assertEqual(initialize["clientInfo"]["title"], "Codex Desktop")
                self.assertEqual(payload["start"]["model"], "gpt-test")
                self.assertEqual(payload["start"]["permissions"], ":workspace-write")
                self.assertNotIn("sandbox", payload["start"])
                self.assertEqual(payload["turn"]["effort"], "high")
                self.assertIn("ruan-continue2run", payload["turn"]["input"][0]["text"])
            finally:
                os.environ.clear(); os.environ.update(old)

    def test_detection_requires_current_desktop_thread(self):
        old = os.environ.copy()
        os.environ.pop("CODEX_THREAD_ID", None)
        os.environ.pop("CODEX_SESSION_ID", None)
        try:
            self.assertFalse(codex.detect())
        finally:
            os.environ.clear(); os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
