import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "archive/v2.0/design/demo/ruan-continue2run/scripts"
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
    def test_desktop_launcher_and_startup_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(FAKE, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            capture = root / "capture.json"
            old = os.environ.copy()
            os.environ.update({
                "CODEX_CLI_PATH": str(launcher),
                "CODEX_THREAD_ID": "current-thread",
                "CODEX_PERMISSION_PROFILE": ":workspace-write",
                "FAKE_CWD": str(root),
                "FAKE_CAPTURE": str(capture),
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
                self.assertEqual(payload["start"]["model"], "gpt-test")
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
