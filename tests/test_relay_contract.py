import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "src/ruan-continue2run/scripts"
sys.path.insert(0, str(SCRIPTS))
import relay  # noqa: E402


FIELDS = relay.BASE_CONTROL


def fake_adapter(applicability=None):
    mod = SimpleNamespace(
        EXTRA_FIELDS=[],
        INVOCATION="ruan-continue2run",
        PARAMETER_APPLICABILITY=applicability or {f: "applicable" for f in FIELDS},
    )
    return mod


def context(body, expected_state="observed", observed_state="observed", value="x"):
    cwd = str(Path.cwd().resolve())
    values = {
        "model": "m",
        "thinking_depth": "medium",
        "working_directory": cwd,
        "permission_mode": "normal",
        "sandbox_mode": "workspace",
        "approval_mode": "never",
    }
    expectations = {k: {"state": expected_state, "value": values[k]} for k in FIELDS}
    observations = {k: {"state": observed_state, "value": values[k]} for k in FIELDS}
    return {
        "schema_version": 1,
        "task_entry": body,
        "task_entry_sha256": relay.task_hash(body),
        "expected_task_entry_sha256": "",
        "harness": "fake",
        "slug": "task-" + relay.task_hash(body)[:8],
        "parameter_expectations": expectations,
        "parameter_observations": observations,
    }


class RelayContractTests(unittest.TestCase):
    def test_task_frame_roundtrip_preserves_body(self):
        adapter = fake_adapter()
        body = "\n中文\nquotes ' \" $ ` \\\ntrailing spaces   "
        ctx = context(body)
        payload = relay.build_payload(adapter, ctx)
        parsed = relay.parse_message(payload)
        self.assertEqual(parsed["task_entry"], body)
        self.assertEqual(parsed["expected_hash"], relay.task_hash(body))

    def test_payload_hash_is_optional_adapter_evidence(self):
        adapter = fake_adapter()
        ctx = context("task")
        raw = {
            "schema_version": 1,
            "status": "confirmed",
            "retryable": False,
            "session_reference": "s1",
            "submitted_parameters": {k: v["value"] for k, v in ctx["parameter_expectations"].items()},
            "effective_parameters": {},
            "startup_evidence": {"event": "started"},
            "error_code": "",
            "error_summary": "",
        }
        result = relay.sanitize_result(raw, ctx, adapter, "sha")
        self.assertEqual(result["status"], "confirmed")

    def test_carried_hash_mismatch_is_detected(self):
        ctx = context("task")
        ctx["expected_task_entry_sha256"] = "0" * 64
        problems, code = relay.validate_context(fake_adapter(), ctx)
        self.assertEqual(code, "TASK_ENTRY_HASH_MISMATCH")
        self.assertTrue(problems)

    def test_observed_drift_fails(self):
        ctx = context("task")
        ctx["parameter_observations"]["model"]["value"] = "other"
        problems, code = relay.validate_context(fake_adapter(), ctx)
        self.assertEqual(code, "PARAMETER_DRIFT")
        self.assertTrue(problems)

    def test_unavailable_observation_only_allowed_for_supplied(self):
        ctx = context("task", expected_state="supplied", observed_state="unavailable")
        problems, code = relay.validate_context(fake_adapter(), ctx)
        self.assertEqual(code, "")
        self.assertFalse(problems)
        ctx = context("task", expected_state="inherited", observed_state="unavailable")
        problems, code = relay.validate_context(fake_adapter(), ctx)
        self.assertEqual(code, "PARAMETER_UNAVAILABLE")

    def test_result_and_claim_paths_are_per_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.log"
            path.write_text("x", encoding="utf-8")
            self.assertTrue(relay.acquire_claim(path))
            self.assertFalse(relay.acquire_claim(path))
            self.assertTrue(relay.claim_path(path).is_file())


if __name__ == "__main__":
    unittest.main()
