"""Contract tests for the DeepSeek Harness (dsh) Adapter.

The Adapter is loaded from the maintenance source tree, which the Maintenance
sync keeps identical to the copy the Harness actually loads. A fake `dsh`
launcher stands in for the real CLI: it answers --dump-config and emulates the
one-shot headless surface by writing a durable Session log.
"""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src/ruan-continue2run/scripts"

FAKE_DSH = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path

MODE = os.environ.get("FAKE_DSH_MODE", "ok")
PROVIDER = os.environ.get("FAKE_DSH_PROVIDER", "fake-provider")
MODEL = os.environ.get("FAKE_DSH_MODEL", "fake-model")

DUMP = """- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      {provider}:
        displayName: {provider}
        api: openai-responses
        baseURL: https://example.invalid/v1
        models:
          - id: {model}
            contextWindow: 1000
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: {provider}
    model: {model}
""".format(provider=PROVIDER, model=MODEL)

argv = sys.argv[1:]
if "--dump-config" in argv:
    sys.stdout.write(DUMP)
    raise SystemExit(0)

def scalar(overlay, key):
    match = re.search(r'^\s*%s:\s*"([^"]*)"' % key, overlay, re.MULTILINE)
    return match.group(1) if match else ""

overlay_text = ""
payload = ""
index = 0
while index < len(argv):
    if argv[index] == "--patch":
        overlay_text = Path(argv[index + 1]).read_text(encoding="utf-8")
        index += 2
        continue
    index += 1
payload = sys.stdin.read()

if MODE == "nosession":
    sys.stderr.write("dsh: fake launcher failed before creating a session\n")
    raise SystemExit(2)

session_id = os.environ.get("FAKE_DSH_SESSION_ID", "session-fake-0001")
cwd = os.getcwd()
home = Path(os.environ["DSH_HOME"])
directory = home / "sessions" / "--fake--" / session_id
directory.mkdir(parents=True, exist_ok=True)

stored = payload
if MODE == "mutate":
    stored = payload.replace("MARKER", "MUTATED")
if MODE == "empty":
    stored = ""

provider = scalar(overlay_text, "provider") or PROVIDER
model = scalar(overlay_text, "model") or MODEL
if MODE == "drift":
    model = "drifted-model"
effort = scalar(overlay_text, "reasoningEffort")
preset = scalar(overlay_text, "defaultPreset")
sandbox = scalar(overlay_text, "mode")
approval = scalar(overlay_text, "policy")

events = [
    {"type": "session", "version": 4, "id": session_id, "cwd": cwd},
    {"type": "permission/preset", "seq": 0, "data": {"preset": preset}},
    {"type": "sandbox/mode", "seq": 1, "data": {"mode": sandbox}},
    {"type": "approval/policy", "seq": 2, "data": {"policy": approval}},
    {"type": "user/message", "seq": 3, "data": {"content": [{"type": "text", "text": stored}],
                                                "source": {"kind": "user"}, "role": "user"}},
    {"type": "turn/start", "seq": 4, "data": {"turn": 1}},
    {"type": "request/header", "seq": 5, "data": {"header": {"config": {
        "provider": provider, "model": model, "reasoningEffort": effort}}}},
]
if MODE == "blocked":
    events.append({"type": "approval/asked", "seq": 6, "data": {"id": "approval-1", "toolName": "bash"}})
(directory / "session.v4.jsonl").write_text(
    "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

stream = [
    {"type": "session", "sessionId": session_id, "cwd": cwd},
    {"type": "status", "phase": "turn_start", "turn": 1},
    {"type": "status", "phase": "step_start", "turn": 1, "step": 1},
]
if MODE not in {"noexec"}:
    stream.append({"type": "text", "text": "fake execution"})
sys.stdout.write("\n".join(json.dumps(event) for event in stream) + "\n")
sys.stdout.flush()
'''


def load_adapter():
    path = SCRIPTS / "adapters" / "dsh.py"
    spec = importlib.util.spec_from_file_location("dsh_adapter_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DshAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = load_adapter()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        (self.home / "profiles" / "headless").mkdir(parents=True)
        launcher = self.root / "fake-dsh"
        launcher.write_text(FAKE_DSH, encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        self.launcher = launcher
        self.saved = os.environ.copy()
        os.environ.update({
            "DSH_HOME": str(self.home),
            "DSH_SESSION_ID": "session-fake-current",
            "DSH_PROFILE": "fake-web",
            "RUAN_CONTINUE2RUN_DSH_BIN": str(launcher),
            "FAKE_DSH_SESSION_ID": "session-fake-created",
        })
        os.environ.pop("DSH_PROFILE_DIR", None)
        self.cwd = str(Path.cwd().resolve())
        self.write_current_session()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.saved)

    def write_current_session(self):
        directory = self.home / "sessions" / "--fake--" / "session-fake-current"
        directory.mkdir(parents=True, exist_ok=True)
        events = [
            {"type": "session", "version": 4, "id": "session-fake-current", "cwd": self.cwd},
            {"type": "permission/preset", "data": {"preset": "danger-full-access"}},
            {"type": "sandbox/mode", "data": {"mode": "danger-full-access"}},
            {"type": "approval/policy", "data": {"policy": "never"}},
            {"type": "request/header", "data": {"header": {"config": {
                "provider": "fake-provider", "model": "fake-model", "reasoningEffort": "max"}}}},
        ]
        (directory / "session.v4.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    def context(self):
        expectations = {
            "working_directory": {"state": "observed", "value": self.cwd},
            "model": {"state": "observed", "value": "fake-model"},
            "thinking_depth": {"state": "observed", "value": "max"},
            "permission_mode": {"state": "observed", "value": "danger-full-access"},
            "sandbox_mode": {"state": "observed", "value": "danger-full-access"},
            "approval_mode": {"state": "observed", "value": "never"},
            "model_provider": {"state": "observed", "value": "fake-provider"},
        }
        return {
            "schema_version": 1,
            "harness": "dsh",
            "task_entry": "task",
            "task_entry_sha256": "0" * 64,
            "parameter_expectations": expectations,
            "parameter_observations": expectations,
        }

    def test_declared_contract(self):
        self.assertEqual(self.adapter.INVOCATION, "ruan-continue2run")
        self.assertEqual(self.adapter.SCHEMA_VERSION, 1)
        self.assertLess(self.adapter.STARTUP_WINDOW_SECONDS + self.adapter.LOG_VERIFY_SECONDS,
                        self.adapter.HANDOFF_DEADLINE_SECONDS)
        for field in self.adapter.FIELDS:
            self.assertIn(field, self.adapter.PARAMETER_APPLICABILITY)
        self.assertEqual(self.adapter.PARAMETER_APPLICABILITY["working_directory"], "required")
        self.assertTrue(self.adapter.detect())

    def test_runtime_observation_uses_session_log(self):
        observed = self.adapter.read_runtime_context()
        self.assertEqual(observed["working_directory"]["state"], "observed")
        self.assertEqual(observed["model"]["value"], "fake-model")
        self.assertEqual(observed["model_provider"]["value"], "fake-provider")
        self.assertEqual(observed["thinking_depth"]["value"], "max")
        self.assertEqual(observed["permission_mode"]["value"], "danger-full-access")
        self.assertEqual(observed["sandbox_mode"]["value"], "danger-full-access")
        self.assertEqual(observed["approval_mode"]["value"], "never")

    def test_runtime_observation_is_unavailable_without_log(self):
        os.environ["DSH_SESSION_ID"] = "session-missing"
        observed = self.adapter.read_runtime_context()
        for field in self.adapter.FIELDS:
            self.assertEqual(observed[field]["state"], "unavailable", field)

    def test_overlay_is_valid_yaml_and_submits_expected_values(self):
        values = {field: self.context()["parameter_expectations"][field]["value"]
                  for field in self.adapter.FIELDS}
        overlay = self.adapter._build_overlay(values)
        parsed = yaml.safe_load(overlay.replace("!!js process.cwd()", '"/tmp"'))
        rows = {row["id"]: row for row in parsed}
        self.assertEqual(rows["agent-default-model"]["config"]["model"], "fake-model")
        self.assertEqual(rows["agent-default-model"]["config"]["provider"], "fake-provider")
        self.assertEqual(rows["agent-default-model"]["config"]["reasoningEffort"], "max")
        self.assertEqual(rows["sandbox-policy"]["config"]["mode"], "danger-full-access")
        self.assertEqual(rows["approval"]["config"]["policy"], "never")
        presets = rows["permission"]["config"]["presets"]
        self.assertEqual(presets["danger-full-access"], {"sandbox": "danger-full-access", "approval": "never"})
        self.assertEqual(rows["permission"]["config"]["defaultPreset"], "danger-full-access")

    def test_overlay_omits_default_preset_for_custom(self):
        values = {field: self.context()["parameter_expectations"][field]["value"]
                  for field in self.adapter.FIELDS}
        values["permission_mode"] = "custom"
        overlay = self.adapter._build_overlay(values)
        parsed = yaml.safe_load(overlay.replace("!!js process.cwd()", '"/tmp"'))
        rows = {row["id"]: row for row in parsed}
        config = rows["permission"]["config"]
        self.assertNotIn("defaultPreset", config)
        self.assertNotIn("custom", config["presets"])

    def test_preflight_requires_model_route(self):
        os.environ["FAKE_DSH_MODEL"] = "other-model"
        result = self.adapter.preflight(self.context())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "DSH_MODEL_ROUTE_UNAVAILABLE")

    def test_preflight_ok_for_configured_route(self):
        self.assertTrue(self.adapter.preflight(self.context())["ok"])

    def test_create_and_confirm_verifies_payload_and_parameters(self):
        payload = "ruan-continue2run\n//dsh\n中文 \"引号\" $VAR `cmd` 反斜杠\\\nMARKER\n"
        result = self.adapter.create_and_confirm(self.context(), payload)
        self.assertEqual(result["status"], "confirmed", result)
        self.assertEqual(result["session_reference"], "session-fake-created")
        self.assertEqual(result["submitted_parameters"]["model"], "fake-model")
        self.assertEqual(result["submitted_parameters"]["payload_sha256"],
                         __import__("hashlib").sha256(payload.encode("utf-8")).hexdigest())
        self.assertEqual(result["effective_parameters"]["working_directory"], self.cwd)
        self.assertEqual(result["effective_parameters"]["permission_mode"], "danger-full-access")
        self.assertIn("dsh --profile headless", result["startup_evidence"]["surface"])

    def test_payload_mutation_is_not_confirmed(self):
        os.environ["FAKE_DSH_MODE"] = "mutate"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\nMARKER\n")
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "DSH_PAYLOAD_MISMATCH")
        self.assertEqual(result["session_reference"], "session-fake-created")

    def test_missing_session_event_is_failed(self):
        os.environ["FAKE_DSH_MODE"] = "nosession"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\n")
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["error_code"], "DSH_SESSION_NOT_CREATED")
        self.assertFalse(result["retryable"])

    def test_effective_parameter_drift_is_unknown(self):
        os.environ["FAKE_DSH_MODE"] = "drift"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\n")
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "DSH_EFFECTIVE_PARAMETER_MISMATCH")

    def test_approval_blocking_is_not_confirmed(self):
        os.environ["FAKE_DSH_MODE"] = "blocked"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\n")
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "APPROVAL_BLOCKED")

    def test_missing_execution_event_is_unknown(self):
        os.environ["FAKE_DSH_MODE"] = "noexec"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\n")
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "DSH_STARTUP_UNCONFIRMED")

    def test_unreadable_session_log_is_unknown(self):
        os.environ["FAKE_DSH_MODE"] = "empty"
        result = self.adapter.create_and_confirm(self.context(), "ruan-continue2run\n")
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(result["error_code"], "DSH_SESSION_LOG_UNREADABLE")


if __name__ == "__main__":
    unittest.main()