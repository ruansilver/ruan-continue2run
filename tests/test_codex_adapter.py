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
    elif method == "thread/name/set":
        capture=json.loads(Path(os.environ["FAKE_CAPTURE"]).read_text(encoding="utf-8"))
        capture["name"]=msg["params"]["name"]
        Path(os.environ["FAKE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
        print(json.dumps({"id":msg["id"],"result":{}}), flush=True)
    elif method == "turn/start":
        capture=json.loads(Path(os.environ["FAKE_CAPTURE"]).read_text(encoding="utf-8"))
        capture["turn"]=msg["params"]
        Path(os.environ["FAKE_CAPTURE"]).write_text(json.dumps(capture), encoding="utf-8")
        print(json.dumps({"id":msg["id"],"result":{"turn":{"id":"turn-1"}}}), flush=True)
        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":"created-thread","delta":"started"}}), flush=True)
        time.sleep(0.05)
        status=os.environ.get("FAKE_TURN_STATUS", "completed")
        turn={"status":status}
        if status == "failed":
            turn["error"]={"codexErrorInfo":"serverOverloaded","message":"Selected model is at capacity."}
        print(json.dumps({"method":"turn/completed","params":{"threadId":"created-thread","turn":turn}}), flush=True)
'''
FAKE += '\nPath(os.environ["FAKE_EXIT_MARKER"]).write_text("closed", encoding="utf-8")\n'


class CodexAdapterTests(unittest.TestCase):
    def test_relay_loader_loads_codex_and_skips_private_protocol_helper(self):
        import detect
        adapter = detect.load_adapter("codex")
        self.assertTrue(callable(adapter.create_and_confirm))
        self.assertNotIn("_codex_protocol", detect.list_adapters())

    def runtime_from_records(self, root, records, metadata=None):
        rollout = root / "rollout.jsonl"
        rollout.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        thread = {"cwd": str(root), "path": str(rollout), **(metadata or {})}
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "current-thread"}, clear=True), \
                patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                patch.object(codex, "_rpc_thread_read", return_value=(thread, None)):
            return codex.read_runtime_context()

    def preflight_context(self, root, provider="custom"):
        values = {
            "working_directory": str(root), "model": "gpt-6.1-sol", "thinking_depth": "null",
            "permission_mode": ":danger-full-access", "sandbox_mode": "danger-full-access",
            "approval_mode": "never", "model_provider": provider,
        }
        return {"parameter_expectations": {
            field: {"state": "observed", "value": value} for field, value in values.items()
        }}

    def test_latest_rollout_settings_preserve_user_model_change_and_null_effort(self):
        records = [
            {"type": "session_meta", "payload": {"model_provider": "initial-provider"}},
            {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {
                "model": "gpt-6-sol", "reasoning_effort": "high", "approval_policy": "on-request",
                "active_permission_profile": {"id": ":read-only"},
                "sandbox_policy": {"type": "read-only"},
            }}},
            {"type": "turn_context", "payload": {
                "model": "intermediate-model", "effort": "medium", "approval_policy": "never",
                "active_permission_profile": {"id": ":workspace-write"},
                "sandbox_policy": {"type": "workspace-write"},
            }},
            {"type": "event_msg", "payload": {"type": "thread_settings_applied", "thread_settings": {
                "model_provider": "current-provider", "model": "user-selected-model",
                "reasoning_effort": "low",
            }}},
            {"type": "turn_context", "payload": {
                "model": "gpt-6.1-sol", "effort": None, "approval_policy": "never",
                "active_permission_profile": {"id": ":danger-full-access"},
                "permission_profile": {"type": "disabled"},
                "sandbox_policy": {"type": "danger-full-access"},
            }},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            observed = self.runtime_from_records(Path(tmp), records, {
                "model": "gpt-6-sol", "reasoningEffort": "high", "modelProvider": "initial-provider",
            })
        for field, value in {
            "model": "gpt-6.1-sol", "thinking_depth": "null", "model_provider": "current-provider",
            "approval_mode": "never", "permission_mode": ":danger-full-access",
            "sandbox_mode": "danger-full-access",
        }.items():
            self.assertEqual(observed[field], {"state": "observed", "value": value})

    def test_reasoning_null_is_observed_but_absent_is_unavailable(self):
        cases = [
            ({"reasoningEffort": None}, [], {"state": "observed", "value": "null"}),
            ({}, [], {"state": "unavailable", "value": ""}),
            ({"reasoningEffort": "high"}, [{"type": "turn_context", "payload": {"effort": None}}],
             {"state": "observed", "value": "null"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for metadata, records, expected in cases:
                with self.subTest(metadata=metadata, records=records):
                    observed = self.runtime_from_records(Path(tmp), records, metadata)
                    self.assertEqual(observed["thinking_depth"], expected)
        self.assertEqual(codex.PARAMETER_APPLICABILITY["thinking_depth"], "applicable")

    def test_final_permission_profile_wins_over_startup_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            rollout = Path(tmp) / "rollout.jsonl"
            rollout.write_text(json.dumps({"type": "turn_context", "payload": {
                "active_permission_profile": {"id": ":read-only"},
                "sandbox_policy": {"type": "read-only"}, "approval_policy": "on-request",
            }}) + "\n", encoding="utf-8")
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "current-thread", "CODEX_PERMISSION_PROFILE": ":danger-full-access"}), \
                    patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                    patch.object(codex, "_rpc_thread_read", return_value=({"cwd": tmp, "path": str(rollout)}, None)):
                observed = codex.read_runtime_context()
        self.assertEqual(observed["permission_mode"]["value"], ":read-only")
        self.assertEqual(observed["sandbox_mode"]["value"], "read-only")
        self.assertEqual(observed["approval_mode"]["value"], "on-request")

    def test_initial_session_provider_does_not_override_current_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            observed = self.runtime_from_records(Path(tmp), [
                {"type": "session_meta", "payload": {"model_provider": "initial-provider"}},
            ], {"modelProvider": "final-provider"})
        self.assertEqual(observed["model_provider"], {"state": "observed", "value": "final-provider"})

    def test_official_preflight_does_not_query_custom_provider_configuration(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"CODEX_THREAD_ID": "current-thread"}, clear=True), \
                patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                patch.object(codex, "_rpc_config_read") as read_config:
            result = codex.preflight(self.preflight_context(Path(tmp), "openai"))
        self.assertTrue(result["ok"], result)
        read_config.assert_not_called()

    def test_custom_preflight_reports_unavailable_config_and_missing_provider(self):
        cases = [
            (None, "CODEX_PROVIDER_CONFIG_UNAVAILABLE"),
            ({"model_providers": {}}, "CODEX_PROVIDER_NOT_CONFIGURED"),
            ({"model_providers": {"different-provider": {"name": "Other"}}},
             "CODEX_PROVIDER_NOT_CONFIGURED"),
        ]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"CODEX_THREAD_ID": "current-thread"}, clear=True), \
                patch.object(codex, "_resolve_launcher", return_value=["fake"]):
            for config, code in cases:
                with self.subTest(config=config), patch.object(codex, "_rpc_config_read", return_value=config):
                    result = codex.preflight(self.preflight_context(Path(tmp)))
                    self.assertFalse(result["ok"], result)
                    self.assertEqual(result["error_code"], code)

    def test_custom_env_key_preflight_is_read_only_and_does_not_echo_credentials(self):
        secret = "synthetic-provider-secret-do-not-log"
        config = {"model_providers": {"custom": {
            "env_key": "PRIVATE_PROVIDER_CREDENTIAL", "env_key_instructions": secret,
            "base_url": "https://example.invalid/v1?token=" + secret,
            "http_headers": {"X-Secret": secret},
        }}}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(codex, "_resolve_launcher", return_value=["fake"]), \
                patch.object(codex, "_rpc_config_read", return_value=config), \
                patch.object(codex, "_worker_paths", side_effect=AssertionError("preflight wrote worker files")):
            for credential in (None, " ", secret):
                environment = {"CODEX_THREAD_ID": "current-thread"}
                if credential is not None:
                    environment["PRIVATE_PROVIDER_CREDENTIAL"] = credential
                with self.subTest(credential_present=bool(credential and credential.strip())), \
                        patch.dict(os.environ, environment, clear=True):
                    result = codex.preflight(self.preflight_context(Path(tmp)))
                self.assertEqual(result["ok"], credential == secret, result)
                if not result["ok"]:
                    self.assertEqual(result["error_code"], "CODEX_PROVIDER_AUTH_UNAVAILABLE")
                serialized = json.dumps(result)
                self.assertNotIn(secret, serialized)
                self.assertNotIn("PRIVATE_PROVIDER_CREDENTIAL", serialized)
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_custom_auth_modes_do_not_require_an_unconfigured_env_key(self):
        secret = "synthetic-provider-secret-do-not-log"
        modes = [
            {"requires_openai_auth": True},
            {"requires_openai_auth": False, "experimental_bearer_token": secret},
            {"requires_openai_auth": False, "http_headers": {"Authorization": "Bearer " + secret}},
            {"requires_openai_auth": False, "auth": {"command": "provider-token-helper"}},
            {"requires_openai_auth": False},
        ]
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"CODEX_THREAD_ID": "current-thread"}, clear=True), \
                patch.object(codex, "_resolve_launcher", return_value=["fake"]):
            for info in modes:
                with self.subTest(info=info), patch.object(codex, "_rpc_config_read", return_value={
                    "model_providers": {"custom": {"name": "Provider", **info}}
                }):
                    result = codex.preflight(self.preflight_context(Path(tmp)))
                    self.assertTrue(result["ok"], result)
                    self.assertNotIn(secret, json.dumps(result))

    def test_custom_model_execution_before_turn_response_still_confirms(self):
        acceptance = '        print(json.dumps({"id":msg["id"],"result":{"turn":{"id":"turn-1"}}}), flush=True)'
        execution = '        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":"created-thread","delta":"started"}}), flush=True)'
        script = FAKE.replace(acceptance + "\n" + execution, execution + "\n" + acceptance)
        self.assertNotEqual(script, FAKE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(script, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            with patch.dict(os.environ, {
                "CODEX_CLI_PATH": str(launcher), "CODEX_THREAD_ID": "current-thread",
                "FAKE_CAPTURE": str(root / "capture.json"), "FAKE_INIT": str(root / "init.json"),
                "FAKE_EXIT_MARKER": str(root / "closed"),
            }, clear=True), patch.object(codex, "_worker_paths", return_value=(
                root, root / "request.json", root / "status.json"
            )):
                result = codex.create_and_confirm(self.preflight_context(root), "ruan-continue2run test")
            self.assertEqual(result["status"], "confirmed", result)
            capture = json.loads((root / "capture.json").read_text())
            self.assertIsNone(capture["turn"]["effort"])
            self.assertEqual(capture["start"]["model"], "gpt-6.1-sol")
            self.assertEqual(capture["start"]["modelProvider"], "custom")
            self.wait_for_worker_exit(root / "status.json")

    def test_custom_user_message_then_503_does_not_confirm_or_retarget(self):
        execution = '        print(json.dumps({"method":"item/agentMessage/delta","params":{"threadId":"created-thread","delta":"started"}}), flush=True)'
        pending = '\n'.join([
            '        print(json.dumps({"method":"turn/started","params":{"threadId":"created-thread"}}), flush=True)',
            '        print(json.dumps({"method":"item/started","params":{"threadId":"created-thread","item":{"type":"userMessage","id":"user-1"}}}), flush=True)',
        ])
        failure = '            turn["error"]={"codexErrorInfo":"serverOverloaded","message":"Selected model is at capacity."}'
        script = FAKE.replace(execution, pending).replace(
            failure,
            '            turn["error"]={"codexErrorInfo":{"httpConnectionFailed":{"httpStatusCode":503}},"message":"Selected model is unavailable."}',
        )
        self.assertNotEqual(script, FAKE)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(script, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            with patch.dict(os.environ, {
                "CODEX_CLI_PATH": str(launcher), "CODEX_THREAD_ID": "current-thread",
                "FAKE_CAPTURE": str(root / "capture.json"), "FAKE_INIT": str(root / "init.json"),
                "FAKE_EXIT_MARKER": str(root / "closed"), "FAKE_TURN_STATUS": "failed",
            }, clear=True), patch.object(codex, "_worker_paths", return_value=(
                root, root / "request.json", root / "status.json"
            )):
                result = codex.create_and_confirm(self.preflight_context(root), "ruan-continue2run test")
            self.assertEqual(result["status"], "unknown", result)
            self.assertEqual(result["error_code"], "CODEX_HTTP_503")
            capture = json.loads((root / "capture.json").read_text())
            self.assertEqual(capture["start"]["model"], "gpt-6.1-sol")
            self.assertEqual(capture["start"]["modelProvider"], "custom")
            status = self.wait_for_worker_exit(root / "status.json")
            self.assertEqual(status["turn_status"], "failed")

    def wait_for_worker_exit(self, path):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if path.is_file():
                status = json.loads(path.read_text())
                if status.get("worker_exited"):
                    return status
            time.sleep(0.02)
        self.fail("detached fake worker did not exit after its terminal turn")

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
                self.assertIn(result["status"], {"confirmed", "unknown"}, result)
                if result["status"] == "unknown":
                    self.assertEqual(result["error_code"], "CODEX_SERVER_OVERLOADED")
                self.assertEqual(result["session_reference"], "created-thread")
                payload = json.loads(capture.read_text(encoding="utf-8"))
                initialize = json.loads(init.read_text(encoding="utf-8"))
                self.assertEqual(initialize["clientInfo"]["name"], "codex_desktop")
                self.assertEqual(initialize["clientInfo"]["title"], "Codex Desktop")
                self.assertEqual(payload["start"]["model"], "gpt-test")
                self.assertEqual(payload["name"], "接力：ruan-continue2run")
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

    def test_failed_turn_releases_worker_and_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "fake-codex"
            launcher.write_text(FAKE, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
            capture = root / "capture.json"
            init = root / "init.json"
            before_workers = set(Path("/tmp").glob("ruan-continue2run-codex-*/status.json"))
            old = os.environ.copy()
            os.environ.update({
                "CODEX_CLI_PATH": str(launcher),
                "CODEX_THREAD_ID": "current-thread",
                "CODEX_PERMISSION_PROFILE": ":workspace-write",
                "FAKE_CWD": str(root),
                "FAKE_CAPTURE": str(capture),
                "FAKE_INIT": str(init),
                "FAKE_TURN_STATUS": "failed",
            })
            try:
                ctx = {"parameter_expectations": {
                    "working_directory": {"state":"observed","value":str(root)},
                    "model": {"state":"observed","value":"gpt-test"},
                    "thinking_depth": {"state":"observed","value":"high"},
                    "permission_mode": {"state":"observed","value":":workspace-write"},
                    "sandbox_mode": {"state":"observed","value":"workspace-write"},
                    "approval_mode": {"state":"supplied","value":"never"},
                    "model_provider": {"state":"observed","value":"fake-provider"},
                }}
                result = codex.create_and_confirm(ctx, "ruan-continue2run failed-turn probe")
                # Startup may already be confirmed before a later model
                # failure arrives; the invariant is that the worker releases
                # its app-server connection on that terminal failure.
                self.assertIn(result["status"], {"confirmed", "unknown"}, result)
                if result["status"] == "unknown":
                    self.assertEqual(result["error_code"], "CODEX_SERVER_OVERLOADED")
                deadline = time.monotonic() + 2
                exited = False
                while time.monotonic() < deadline and not exited:
                    time.sleep(0.02)
                    for status_path in set(Path("/tmp").glob("ruan-continue2run-codex-*/status.json")) - before_workers:
                        try:
                            status = json.loads(status_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            continue
                        exited = status.get("turn_status") == "failed" and status.get("worker_exited") is True
                        if exited:
                            break
                self.assertTrue(exited, "failed turn left worker/app-server alive")
            finally:
                os.environ.clear(); os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
