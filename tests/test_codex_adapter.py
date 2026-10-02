import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "ruan-continue2run" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from adapters import codex  # noqa: E402


FAKE = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if len(sys.argv) > 1 and sys.argv[1] == "app-server":
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except Exception:
            continue
        if message.get("id") == 1:
            print(json.dumps({"id": 1, "result": {"userAgent": "fake"}}), flush=True)
        elif message.get("method") == "thread/read":
            print(json.dumps({"id": 2, "result": {"thread": {
                "cwd": os.environ["FAKE_CWD"],
                "model": "gpt-6-astra",
                "modelProvider": "openai",
                "reasoningEffort": "high"
            }}}), flush=True)
        elif message.get("method") == "thread/start":
            capture = Path(os.environ["FAKE_CAPTURE"])
            capture.write_text(json.dumps({"start": message["params"]}), encoding="utf-8")
            print(json.dumps({"id": 2, "result": {"thread": {
                "id": "created-test-thread",
                "cwd": os.environ["FAKE_CWD"],
                "model": "gpt-6-astra",
                "modelProvider": "openai",
                "reasoningEffort": "high",
                "source": "vscode"
            }}}), flush=True)
        elif message.get("method") == "turn/start":
            capture = Path(os.environ["FAKE_CAPTURE"])
            payload = json.loads(capture.read_text(encoding="utf-8"))
            payload["turn"] = message["params"]
            capture.write_text(json.dumps(payload), encoding="utf-8")
            print(json.dumps({"id": 3, "result": {"turn": {"id": "turn-test"}}}), flush=True)
            print(json.dumps({"method": "turn/completed", "params": {"threadId": "created-test-thread"}}), flush=True)
'''


class CodexAdapterTests(unittest.TestCase):
    def test_reads_current_thread_config_and_uses_bundled_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(FAKE, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            capture = root / "capture"
            env = os.environ.copy()
            env.update({
                "CODEX_CLI_PATH": str(launcher),
                "CODEX_THREAD_ID": "thread-current",
                "FAKE_CWD": str(root),
                "FAKE_CAPTURE": str(capture),
            })
            old = os.environ.copy()
            os.environ.clear()
            os.environ.update(env)
            try:
                context = {
                    "task_entry": "原始任务入口",
                    "skill": "ruan-continue2run",
                    "runtime_params": {
                        "model": "display-label-that-must-not-be-used",
                        "thinking_depth": "low",
                        "working_directory": str(root),
                    },
                    "harness": {"tag": "codex", "id": "codex"},
                }
                result = codex.create(context)
                self.assertTrue(result["issued"], result)
                for _ in range(100):
                    if capture.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(capture.exists())
                payload = json.loads(capture.read_text(encoding="utf-8"))
                self.assertEqual(payload["start"]["cwd"], str(root))
                self.assertEqual(payload["start"]["model"], "gpt-6-astra")
                self.assertEqual(payload["start"]["modelProvider"], "openai")
                self.assertEqual(payload["turn"]["effort"], "high")
                prompt = payload["turn"]["input"][0]["text"]
                self.assertIn("ruan-continue2run", prompt)
                self.assertIn("原始任务入口", prompt)
                self.assertEqual(result["info"].count("created_thread_id=created-test-thread"), 1)
            finally:
                os.environ.clear()
                os.environ.update(old)

    def test_requires_current_thread_id(self):
        old = os.environ.copy()
        os.environ.pop("CODEX_THREAD_ID", None)
        os.environ.pop("CODEX_SESSION_ID", None)
        try:
            result = codex.create({"runtime_params": {}})
            self.assertFalse(result["issued"])
            self.assertIn("CODEX_THREAD_ID_MISSING", result["error"])
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
