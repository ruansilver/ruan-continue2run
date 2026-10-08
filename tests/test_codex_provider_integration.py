"""Opt-in bundled app-server tests against an isolated localhost provider.

Run with RUAN_CONTINUE2RUN_CODEX_INTEGRATION=1. The default adapter is the
installed Skill; RUAN_CONTINUE2RUN_TEST_SKILL_ROOT selects another Skill root.
Every real app-server receives a temporary CODEX_HOME with a localhost-only
provider and synthetic credentials. These tests never load the real auth.json,
change the running Desktop configuration, or contact an actual model provider.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


INSTALLED_ROOT = Path("/home/ruan/.agents/skills/ruan-continue2run")
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src/ruan-continue2run"
SKILL_ROOT = Path(os.environ.get(
    "RUAN_CONTINUE2RUN_TEST_SKILL_ROOT", str(INSTALLED_ROOT if INSTALLED_ROOT.is_dir() else SOURCE_ROOT)))
BINARY = Path(os.environ.get(
    "RUAN_CONTINUE2RUN_TEST_CODEX_BIN", "/usr/lib/chatgpt/resources/codex"))
ENABLED = os.environ.get("RUAN_CONTINUE2RUN_CODEX_INTEGRATION") == "1"
MODEL = "relay-local-custom-model"
PROVIDER = "relay_local"
TOKEN = "synthetic-local-provider-token"
OUTPUT = 'LOCAL_PROVIDER_OK 中文，多行\n"quotes" $ `backticks` \\slash'
PAYLOAD = 'ruan-continue2run\n仅输出 LOCAL_PROVIDER_OK\n中文\n"quotes" $ `backticks` \\slash'

DRIVER = r'''
import json, os, sys
from pathlib import Path
request = json.loads(sys.stdin.read())
sys.path.insert(0, str(Path(request["skill_root"]) / "scripts"))
from detect import load_adapter
codex = load_adapter("codex")
import relay
if request["mode"] == "runtime":
    result = codex.read_runtime_context()
else:
    directory = Path(request["worker_dir"])
    directory.mkdir()
    codex._worker_paths = lambda: (directory, directory / "request.json", directory / "status.json")
    context = request["ctx"]
    context.update(harness="codex", task_entry=request["payload"],
                   task_entry_sha256=relay.task_hash(request["payload"]))
    payload = relay.build_payload(codex, context)
    result = {"preflight": codex.preflight(request["ctx"]),
              "adapter": codex.create_and_confirm(context, payload),
              "payload": payload, "captured": relay.parse_message(payload)}
print(json.dumps(result, ensure_ascii=False))
'''

HANDOFF_DRIVER = r'''
import json, os, subprocess, sys, time
from pathlib import Path
request = json.loads(sys.stdin.read())
scripts = Path(request["skill_root"]) / "scripts"
sys.path.insert(0, str(scripts))
from detect import load_adapter
from adapters._codex_protocol import read_message
codex = load_adapter("codex")
import relay
proc = codex._spawn_app_server(codex._resolve_launcher(), os.getcwd())
def rpc(identifier, method, params):
    codex._send(proc.stdin, {"id": identifier, "method": method, "params": params})
    response = codex._read_until(proc, identifier, time.monotonic() + 10)
    assert response and "error" not in response, (method, response)
    return response["result"]
def command(*args):
    result = subprocess.run([sys.executable, str(scripts / "relay.py"), *args],
                            text=True, capture_output=True, timeout=50)
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    return json.loads(result.stdout)
stop = Path('.ruan-continue2run/STOP')
assert stop.read_text() == "owned by isolated provider integration test\n"
try:
    rpc(1, "initialize", {"clientInfo": codex.CLIENT_INFO, "capabilities": {"experimentalApi": True}})
    codex._send(proc.stdin, {"method": "initialized", "params": {}})
    parent = rpc(2, "thread/start", {"cwd": os.getcwd(), "model": "model-at-start",
        "modelProvider": request["provider"], "permissions": ":danger-full-access", "approvalPolicy": "never"})["thread"]["id"]
    os.environ["CODEX_THREAD_ID"] = parent
    message = Path('message.txt')
    message.write_text(request["payload"], encoding="utf-8")
    stop.unlink()  # Only this fixture-owned STOP, with a non-recursive local model.
    started = command("start", "--message-file", str(message), "--harness", "codex")
    assert started["runtime"]["model"]["state"] == "unavailable", started
    for index, (model, effort) in enumerate((("intermediate-model", "medium"), (request["model"], "low"))):
        rpc(10 + index, "turn/start", {"threadId": parent, "model": model, "effort": effort,
            "input": [{"type": "text", "text": "Local test: update session model settings."}]})
        deadline = time.monotonic() + 10
        while True:
            event = read_message(proc, deadline)
            assert event is not None, "missing terminal event"
            if event.get("method") == "turn/completed":
                assert event["params"]["turn"]["status"] == "completed", event
                break
    codex._terminate(proc)
    proc = None
    final_runtime = codex.read_runtime_context()
    result = command("handoff", "--log", started["log_path"])
    repeated = command("handoff", "--log", started["log_path"])
    snapshot = json.loads(relay.handoff_context_path(Path(started["log_path"])).read_text())
    print(json.dumps({"start": started, "handoff": result, "repeated": repeated,
                      "snapshot": snapshot, "final_runtime": final_runtime}))
finally:
    codex._terminate(proc)
    stop.write_text("owned by isolated provider integration test\n")
'''

OFFICIAL_FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
capture = {}
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    response = {"id": message.get("id"), "result": {}}
    if method == "initialize":
        capture["initialize"] = message["params"]
    elif method == "config/read":
        response["result"] = {"config": {"model_provider": "openai", "model_providers": {}}}
    elif method == "thread/start":
        capture["thread"] = message["params"]
        response["result"] = {"thread": {"id": "official-created-thread", "cwd": message["params"]["cwd"],
            "model": message["params"]["model"], "modelProvider": "openai", "reasoningEffort": "high"}}
    elif method == "turn/start":
        capture["turn"] = message["params"]
        Path(os.environ["LOCAL_FAKE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
        response["result"] = {"turn": {"id": "official-turn"}}
        print(json.dumps(response), flush=True)
        print(json.dumps({"method": "turn/started", "params": {
            "threadId": "official-created-thread", "turn": {"id": "official-turn", "status": "inProgress"}}}), flush=True)
        time.sleep(0.2)
        print(json.dumps({"method": "item/agentMessage/delta", "params": {
            "threadId": "official-created-thread", "delta": "official protocol response"}}), flush=True)
        print(json.dumps({"method": "turn/completed", "params": {
            "threadId": "official-created-thread", "turn": {"id": "official-turn", "status": "completed"}}}), flush=True)
        continue
    if message.get("id") is not None:
        print(json.dumps(response), flush=True)
'''


class LocalResponsesHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        self.server.requests.append({
            "path": self.path, "authorization": self.headers.get("Authorization"),
            "body": body,
        })
        time.sleep(0.15)
        if self.server.reject_status:
            data = json.dumps({"error": {"message": self.server.reject_message,
                                         "type": "isolated_provider_error"}}).encode()
            self.send_response(self.server.reject_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        item = {"id": "msg_local", "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": OUTPUT, "annotations": []}]}
        events = [
            {"type": "response.created", "response": {
                "id": "resp_local", "object": "response", "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0,
                "item": {**item, "status": "in_progress", "content": []}},
            {"type": "response.content_part.added", "item_id": "msg_local", "output_index": 0,
                "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "item_id": "msg_local", "output_index": 0,
                "content_index": 0, "delta": OUTPUT},
            {"type": "response.output_text.done", "item_id": "msg_local", "output_index": 0,
                "content_index": 0, "text": OUTPUT},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": {
                "id": "resp_local", "object": "response", "status": "completed", "output": [item],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}},
        ]
        try:
            for index, event in enumerate(events):
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.flush()
                if index == 3:
                    self.server.release_stream.wait(timeout=5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True


def isolated_environment(home: Path, binary: Path) -> dict[str, str]:
    keep = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "TMPDIR")
    environment = {key: os.environ[key] for key in keep if key in os.environ}
    environment.update({
        "CODEX_HOME": str(home), "CODEX_CLI_PATH": str(binary),
        "RUAN_CONTINUE2RUN_CODEX_BIN": str(binary),
        "CODEX_THREAD_ID": "isolated-parent-thread", "LOCAL_PROVIDER_KEY": TOKEN,
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    })
    return environment


@unittest.skipUnless(ENABLED, "set RUAN_CONTINUE2RUN_CODEX_INTEGRATION=1 for isolated bundled app-server tests")
class CodexProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not BINARY.is_file():
            self.skipTest(f"bundled codex binary unavailable: {BINARY}")
        if not (SKILL_ROOT / "scripts/adapters/codex.py").is_file():
            self.skipTest(f"Skill adapter unavailable: {SKILL_ROOT}")
        self.temporary = tempfile.TemporaryDirectory(prefix="ruan-codex-provider-integration-")
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex-home"
        self.home.mkdir()
        self.cwd = self.root / "cwd"
        self.cwd.mkdir()
        stop = self.cwd / ".ruan-continue2run/STOP"
        stop.parent.mkdir()
        stop.write_text("owned by isolated provider integration test\n", encoding="utf-8")
        self.worker = self.root / "worker"
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalResponsesHandler)
        self.server.requests = []
        self.server.reject_status = 0
        self.server.reject_message = "isolated provider rejected credentials"
        self.server.release_stream = threading.Event()
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.environment = isolated_environment(self.home, BINARY)

    def tearDown(self):
        self.server.release_stream.set()
        # In a regression, the detached worker may still be running. Restrict
        # cleanup to descendants whose command line references our unique dir.
        proc_root = Path("/proc")
        if proc_root.is_dir():
            for candidate in proc_root.iterdir():
                if not candidate.name.isdecimal():
                    continue
                try:
                    command = (candidate / "cmdline").read_bytes()
                    if str(self.worker).encode() in command and b"codex_worker.py" in command:
                        os.kill(int(candidate.name), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temporary.cleanup()

    def configure(self, auth: str = "env_key", effort: str | None = "medium"):
        auth_line = f'env_key = "LOCAL_PROVIDER_KEY"' if auth == "env_key" else \
            f'experimental_bearer_token = "{TOKEN}"'
        effort_line = f'model_reasoning_effort = "{effort}"\n' if effort is not None else ""
        config = (
            f'model = "{MODEL}"\nmodel_provider = "{PROVIDER}"\n' + effort_line +
            'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n' +
            f'[model_providers.{PROVIDER}]\nname = "Isolated local test"\n' +
            f'base_url = "http://127.0.0.1:{self.server.server_port}/v1"\n' +
            'wire_api = "responses"\nrequires_openai_auth = false\n' + auth_line + '\n' +
            'request_max_retries = 0\nstream_max_retries = 0\n')
        (self.home / "config.toml").write_text(config, encoding="utf-8")

    def context(self, effort: str | None = "medium"):
        values = {
            "working_directory": str(self.cwd), "model": MODEL, "thinking_depth": effort if effort is not None else "null",
            "permission_mode": ":danger-full-access", "sandbox_mode": "danger-full-access", "approval_mode": "never",
            "model_provider": PROVIDER,
        }
        return {"parameter_expectations": {
            field: {"state": "observed", "value": value}
            for field, value in values.items()
        }}

    def driver(self, mode: str, thread_id: str = "", effort: str | None = "medium"):
        request = {"skill_root": str(SKILL_ROOT), "mode": mode, "worker_dir": str(self.worker),
                   "ctx": self.context(effort), "payload": PAYLOAD}
        environment = dict(self.environment)
        if thread_id:
            environment["CODEX_THREAD_ID"] = thread_id
        result = subprocess.run([sys.executable, "-c", DRIVER], input=json.dumps(request),
                                text=True, capture_output=True, env=environment, cwd=self.cwd, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def worker_status(self):
        deadline = time.monotonic() + 8
        path = self.worker / "status.json"
        while time.monotonic() < deadline:
            if path.is_file():
                status = json.loads(path.read_text(encoding="utf-8"))
                if status.get("worker_exited"):
                    return status
            time.sleep(0.05)
        self.fail("detached worker did not release app-server after the terminal turn")

    def assert_success(self, effort: str | None = "medium"):
        result = self.driver("create", effort=effort)
        self.assertTrue(result["preflight"]["ok"], result)
        adapter = result["adapter"]
        self.assertEqual(adapter["status"], "confirmed", result)
        self.assertTrue(adapter["session_reference"], result)
        self.assertEqual(adapter["submitted_parameters"]["payload_sha256"],
                         hashlib.sha256(result["payload"].encode()).hexdigest())
        self.assertEqual(result["captured"]["task_entry"], PAYLOAD)
        self.assertEqual(result["captured"]["expected_hash"], hashlib.sha256(PAYLOAD.encode()).hexdigest())
        self.assertEqual(result["captured"]["parameters"], self.context(effort)["parameter_expectations"])
        # The creator has exited, while the detached worker keeps the streamed
        # turn alive until this test releases the final completion events.
        initial_status = json.loads((self.worker / "status.json").read_text())
        self.assertFalse(initial_status.get("worker_exited"), initial_status)
        self.server.release_stream.set()
        status = self.worker_status()
        self.assertTrue(status.get("turn_completed"), status)
        self.assertIn(status.get("turn_status"), {None, "completed"}, status)
        self.assertEqual(status["thread_id"], adapter["session_reference"])
        self.assertEqual(len(self.server.requests), 1, self.server.requests)
        request = self.server.requests[0]
        self.assertEqual(request["path"], "/v1/responses")
        self.assertEqual(request["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(request["body"]["model"], MODEL)
        self.assertTrue(any(part.get("text") == result["payload"] for item in request["body"]["input"]
                            for part in item.get("content", []) if isinstance(part, dict)))
        records = [json.loads(line) for rollout in self.home.glob("sessions/**/*.jsonl")
                   for line in rollout.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(record.get("type") == "response_item" and
                            any(part.get("text") == OUTPUT for part in record.get("payload", {}).get("content", [])
                                if isinstance(part, dict)) for record in records), "assistant output missing from durable rollout")
        self.assertTrue((self.cwd / ".ruan-continue2run/STOP").is_file())
        return adapter

    def test_env_key_custom_provider_creates_real_thread(self):
        self.configure()
        self.assert_success()

    def test_bearer_token_custom_provider_creates_real_thread(self):
        self.configure(auth="bearer")
        self.assert_success()

    def test_provider_401_is_not_confirmed_before_model_response(self):
        self.configure()
        self.server.reject_status = 401
        self.assert_rejected_provider()

    def test_provider_503_unavailable_channel_is_not_confirmed(self):
        self.configure()
        self.server.reject_status = 503
        self.server.reject_message = f"No available channel for model {MODEL}"
        self.assert_rejected_provider()

    def assert_rejected_provider(self):
        result = self.driver("create")
        self.assertNotEqual(result["adapter"]["status"], "confirmed", result)
        self.assertEqual(result["adapter"]["status"], "unknown", result)
        self.assertTrue(result["adapter"]["session_reference"], result)
        self.assertTrue(result["adapter"]["error_code"], result)
        self.assertIsInstance(result["adapter"]["error_code"], str)
        self.assertNotIn(TOKEN, json.dumps(result["adapter"]))
        status = self.worker_status()
        self.assertEqual(status.get("turn_status"), "failed", status)
        self.assertEqual(len(self.server.requests), 1)

    def test_custom_model_null_effort_is_observed_and_relayed(self):
        self.configure(effort=None)
        adapter = self.assert_success(effort=None)
        observed = self.driver("runtime", thread_id=adapter["session_reference"])
        self.assertEqual(observed["model"], {"state": "observed", "value": MODEL})
        self.assertEqual(observed["model_provider"], {"state": "observed", "value": PROVIDER})
        self.assertEqual(observed["thinking_depth"], {"state": "observed", "value": "null"})

    def test_full_handoff_captures_model_after_multiple_parent_turn_changes(self):
        self.configure()
        self.server.release_stream.set()
        worker_root = self.root / "handoff-workers"
        worker_root.mkdir()
        environment = {**self.environment, "TMPDIR": str(worker_root)}
        request = {"skill_root": str(SKILL_ROOT), "model": MODEL, "provider": PROVIDER, "payload": PAYLOAD}
        result = subprocess.run([sys.executable, "-c", HANDOFF_DRIVER], input=json.dumps(request),
                                text=True, capture_output=True, env=environment, cwd=self.cwd, timeout=75)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["handoff"]["outcome"], "confirmed", data)
        self.assertEqual(data["handoff"]["report"]["model"], MODEL)
        self.assertEqual(data["handoff"]["report"]["thinking"], "low")
        self.assertEqual(data["handoff"]["report"], data["repeated"]["report"])
        self.assertEqual(data["snapshot"]["parameter_expectations"], data["final_runtime"])
        self.assertEqual(data["snapshot"]["parameter_capture_phase"], "handoff")
        self.assertEqual([req["body"]["model"] for req in self.server.requests],
                         ["intermediate-model", MODEL, MODEL])
        self.assertEqual(self.server.requests[-1]["body"]["reasoning"]["effort"], "low")
        controls = [part["text"] for item in self.server.requests[-1]["body"]["input"]
                    for part in item.get("content", [])
                    if isinstance(part, dict) and "<<<relay-control" in part.get("text", "")]
        self.assertEqual(len(controls), 1)
        self.assertIn("param.model.value=" + MODEL, controls[0])
        self.assertIn("param.thinking_depth.value=low", controls[0])
        self.assertNotIn("model-at-start", controls[0])
        self.assertTrue((self.cwd / ".ruan-continue2run/STOP").is_file())
        workers = list(worker_root.glob("ruan-continue2run-codex-*"))
        self.assertEqual(len(workers), 1)
        self.worker = workers[0]
        self.assertTrue(self.worker_status()["worker_exited"])


class CodexOfficialProtocolRegressionTests(unittest.TestCase):
    def test_default_openai_route_preserves_official_startup_protocol(self):
        with tempfile.TemporaryDirectory(prefix="ruan-codex-official-protocol-") as temporary:
            root = Path(temporary)
            home = root / "codex-home"
            home.mkdir()
            launcher = root / "fake-codex"
            launcher.write_text(OFFICIAL_FAKE, encoding="utf-8")
            launcher.chmod(0o700)
            capture = root / "capture.json"
            environment = isolated_environment(home, launcher)
            environment["LOCAL_FAKE_CAPTURE"] = str(capture)
            values = {"working_directory": str(root), "model": "gpt-test", "thinking_depth": "high",
                      "permission_mode": ":workspace-write", "sandbox_mode": "workspace-write",
                      "approval_mode": "never", "model_provider": "openai"}
            request = {"skill_root": str(SKILL_ROOT), "mode": "create", "worker_dir": str(root / "worker"),
                       "ctx": {"parameter_expectations": {
                           key: {"state": "observed", "value": value} for key, value in values.items()}},
                       "payload": PAYLOAD}
            result = subprocess.run([sys.executable, "-c", DRIVER], input=json.dumps(request),
                                    text=True, capture_output=True, env=environment, cwd=root, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertTrue(response["preflight"]["ok"], response)
            self.assertEqual(response["adapter"]["status"], "confirmed", response)
            self.assertEqual(response["adapter"]["startup_evidence"]["method"], "turn/started", response)
            submitted = json.loads(capture.read_text(encoding="utf-8"))
            self.assertEqual(submitted["initialize"]["clientInfo"]["name"], "codex_desktop")
            self.assertEqual(submitted["thread"]["modelProvider"], "openai")
            self.assertEqual(submitted["thread"]["permissions"], ":workspace-write")
            self.assertNotIn("config", submitted["thread"])
            self.assertEqual(submitted["turn"]["effort"], "high")
            self.assertEqual(submitted["turn"]["input"][0]["text"], response["payload"])
            self.assertEqual(response["captured"]["task_entry"], PAYLOAD)
            status_path = root / "worker/status.json"
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = json.loads(status_path.read_text())
                if status.get("worker_exited"):
                    break
                time.sleep(0.05)
            self.assertTrue(status.get("worker_exited"), status)


if __name__ == "__main__":
    unittest.main()
