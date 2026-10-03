"""Adapter skeleton. Files beginning with '_' are ignored by detect.py.

Maintenance must fill this against the real Harness and complete real end-to-end
acceptance before renaming it to adapters/<harness>.py.
"""

SCHEMA_VERSION = 1
INVOCATION = "ruan-continue2run"
HANDOFF_DEADLINE_SECONDS = 60
EXTRA_FIELDS = []
PARAMETER_APPLICABILITY = {
    "working_directory": "required",
    "model": "applicable",
    "thinking_depth": "applicable",
    "permission_mode": "applicable",
    "sandbox_mode": "applicable",
    "approval_mode": "applicable",
}


def detect():
    return False


def read_runtime_context():
    """Return {field: {state, value}} from real Harness metadata."""
    return {}


def preflight(ctx):
    return {"ok": False, "error_code": "ADAPTER_NOT_IMPLEMENTED",
            "problems": ["template is not a usable Adapter"]}


def create_and_confirm(ctx, payload):
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "retryable": False,
        "session_reference": "",
        "submitted_parameters": {},
        "effective_parameters": {},
        "startup_evidence": {},
        "error_code": "ADAPTER_NOT_IMPLEMENTED",
        "error_summary": "template is not a usable Adapter",
    }
