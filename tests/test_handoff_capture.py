"""The final session settings, not start-time settings, govern Codex handoff."""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "src/ruan-continue2run/scripts"
sys.path.insert(0, str(SCRIPTS))
import relay


class HandoffCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.original_cwd = os.getcwd()
        self.addCleanup(os.chdir, self.original_cwd)
        os.chdir(self.temporary.name)
        self.body = '任务中文\n"quotes" $ `backticks` \\slash\n'
        self.initial = self.runtime("model-at-start", "high", "openai", ":danger-full-access", "danger-full-access", "never")
        self.final = self.runtime("model-at-handoff", "low", "custom-final", ":read-only", "read-only", "on-request")
        self.adapter = SimpleNamespace(
            PARAMETER_CAPTURE_PHASE="handoff", EXTRA_FIELDS=["model_provider"],
            INVOCATION="ruan-continue2run", HANDOFF_DEADLINE_SECONDS=1,
            PARAMETER_APPLICABILITY={field: "applicable" for field in self.initial},
            read_runtime_context=Mock(return_value=self.final),
            preflight=Mock(return_value={"ok": True}),
        )
        self.ctx = {
            "schema_version": 1, "harness": "codex", "slug": "capture-test",
            "task_entry": self.body, "task_entry_sha256": relay.task_hash(self.body),
            "expected_task_entry_sha256": "",
            "parameter_expectations": copy.deepcopy(self.initial),
            "parameter_observations": copy.deepcopy(self.initial),
        }
        # No capture-phase marker: existing schema-1 logs also need final settings.
        self.log = relay.new_log(relay.run_root(), self.ctx)

    def runtime(self, model, effort, provider, permission, sandbox, approval):
        return {field: {"state": "observed", "value": value} for field, value in {
            "working_directory": str(Path.cwd()), "model": model,
            "thinking_depth": effort, "model_provider": provider,
            "permission_mode": permission, "sandbox_mode": sandbox, "approval_mode": approval,
        }.items()}

    def command(self, function, **kwargs):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), self.assertRaises(SystemExit) as raised:
            function(SimpleNamespace(**kwargs))
        return raised.exception.code, json.loads(stream.getvalue())

    def confirmed(self, harness, ctx, payload, deadline):
        return {
            "schema_version": 1, "status": "confirmed", "retryable": False,
            "session_reference": "child-thread",
            "submitted_parameters": {key: value["value"] for key, value in ctx["parameter_expectations"].items()},
            "effective_parameters": {}, "startup_evidence": {"event": "executing"},
            "error_code": "", "error_summary": "",
        }

    def test_start_captures_task_without_runtime_read_or_preflight(self):
        message = Path("message.txt")
        message.write_text(relay.build_payload(self.adapter, self.ctx), encoding="utf-8")
        with patch.object(relay.detect, "resolve_harness", return_value=("codex", None)), \
                patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "ensure_git_exclude", return_value=True):
            code, result = self.command(relay.cmd_start, message_file=str(message), harness="codex", set=[])
        self.assertEqual(code, 0, result)
        self.assertEqual(result["parameter_capture_phase"], "handoff")
        self.adapter.read_runtime_context.assert_not_called()
        self.adapter.preflight.assert_not_called()
        saved = relay.read_context(Path(result["log_path"]))
        self.assertEqual(saved["task_entry"], self.body)
        self.assertEqual(saved["task_entry_sha256"], self.ctx["task_entry_sha256"])

    def test_handoff_uses_final_settings_and_repeated_call_does_not_recapture(self):
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter", side_effect=self.confirmed) as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
            self.assertEqual(code, 0, result)
            call_ctx, payload = invoke.call_args.args[1:3]
            self.assertEqual(call_ctx["parameter_expectations"], self.final)
            self.assertEqual(relay.parse_message(payload)["parameters"], self.final)
            self.assertEqual(relay.parse_message(payload)["task_entry"], self.body)
            self.assertEqual(relay.read_context(self.log), self.ctx)
            snapshot = json.loads(relay.handoff_context_path(self.log).read_text())
            self.assertEqual(snapshot["parameter_expectations"], self.final)
            self.assertEqual(snapshot["task_entry_sha256"], self.ctx["task_entry_sha256"])
            self.adapter.read_runtime_context.return_value = self.initial
            code, repeated = self.command(relay.cmd_handoff, log=str(self.log))
            self.assertEqual(repeated["report"], result["report"])
            self.assertEqual(invoke.call_count, 1)
            self.adapter.read_runtime_context.assert_called_once()
            self.adapter.preflight.assert_called_once_with(call_ctx)

    def test_unavailable_final_model_never_falls_back_to_supplied_start_model(self):
        self.ctx["parameter_expectations"]["model"]["state"] = "supplied"
        self.log = relay.new_log(relay.run_root(), self.ctx)
        self.final["model"] = {"state": "unavailable", "value": ""}
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(code, 11)
        self.assertEqual(result["report"]["error_code"], "PARAMETER_UNAVAILABLE")
        invoke.assert_not_called()
        self.adapter.preflight.assert_not_called()

    def test_preflight_failure_is_final_without_creating_session(self):
        self.adapter.preflight.return_value = {"ok": False, "error_code": "PROVIDER_MISSING", "problems": ["unavailable"]}
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["report"]["error_code"], "PROVIDER_MISSING")
        self.assertEqual(result["report"]["attempts"], [])
        self.assertEqual(relay.read_result(self.log)["status"], "failed")
        invoke.assert_not_called()

    def test_changed_cwd_is_not_silently_inherited(self):
        other = Path("another-project")
        other.mkdir()
        self.final["working_directory"]["value"] = str(other.resolve())
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["report"]["error_code"], "WORKING_DIRECTORY_DRIFT")
        invoke.assert_not_called()

    def test_task_hash_corruption_still_blocks_handoff(self):
        self.log.write_text(self.log.read_text().replace('任务中文', '篡改正文'), encoding="utf-8")
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["error_code"], "INVALID_LOG")
        self.adapter.read_runtime_context.assert_not_called()
        invoke.assert_not_called()

    def test_child_effective_drift_is_checked_against_final_snapshot(self):
        def drift(*args):
            result = self.confirmed(*args)
            result["effective_parameters"] = {"model": "model-at-start"}
            return result
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter", side_effect=drift):
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(result["report"]["error_code"], "MISSING_CONFIRMATION_EVIDENCE")

    def test_stop_skips_runtime_capture_and_creation(self):
        (relay.run_root() / "STOP").touch()
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["outcome"], "stopped_by_stop")
        self.adapter.read_runtime_context.assert_not_called()
        invoke.assert_not_called()

    def test_stop_appearing_during_preflight_prevents_creation(self):
        def stop(ctx):
            (relay.run_root() / "STOP").touch()
            return {"ok": True}
        self.adapter.preflight.side_effect = stop
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["outcome"], "stopped_by_stop")
        invoke.assert_not_called()

    def test_retry_reuses_exact_snapshot_and_payload(self):
        calls = []
        def retry(*args):
            calls.append(args)
            if len(calls) == 1:
                self.adapter.read_runtime_context.return_value = self.initial
                return {**relay.error_result("BUSY", "not created", "failed"), "retryable": True}
            return self.confirmed(*args)
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter", side_effect=retry):
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(code, 0, result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.adapter.read_runtime_context.assert_called_once()

    def test_pending_claim_does_not_recapture_or_create(self):
        relay.acquire_claim(self.log)
        with patch.object(relay.detect, "load_adapter", return_value=self.adapter), \
                patch.object(relay, "invoke_adapter") as invoke:
            code, result = self.command(relay.cmd_handoff, log=str(self.log))
        self.assertEqual(result["outcome"], "unknown")
        self.adapter.read_runtime_context.assert_not_called()
        invoke.assert_not_called()

    def test_other_adapters_keep_start_time_parameter_capture(self):
        del self.adapter.PARAMETER_CAPTURE_PHASE
        captured = relay.parse_message("ruan-continue2run\n任务")
        ctx = relay.resolve_context(self.adapter, "legacy", captured, {})
        self.adapter.read_runtime_context.assert_called_once()
        self.assertEqual(ctx["parameter_capture_phase"], "start")
        self.assertEqual(ctx["parameter_expectations"], self.final)


if __name__ == "__main__":
    unittest.main()
