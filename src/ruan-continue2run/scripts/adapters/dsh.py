"""DeepSeek Harness (dsh) Adapter.

Creation surface: the documented one-shot app ``dsh --profile headless --json``,
launched in the expected working directory, detached from this Adapter, and fed
the Payload on stdin. A generated ``--patch`` overlay submits the RelayContext
parameters explicitly, so the new Session records the same model route and
permission knobs as the current one.

Runtime observation and startup verification read the durable Session log under
``$DSH_HOME/sessions/<cwd-key>/<session-id>/`` (zstd-compressed JSONL v4). The
Adapter never trusts model self-report and never treats profile defaults as
observed values.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
INVOCATION = "ruan-continue2run"
HANDOFF_DEADLINE_SECONDS = 60
STARTUP_WINDOW_SECONDS = 48
LOG_VERIFY_SECONDS = 6
DUMP_TIMEOUT_SECONDS = 20
READ_TIMEOUT_SECONDS = 20
DEFAULT_PRESETS = {
    "read-only": {"sandbox": "read-only", "approval": "ask"},
    "workspace-write": {"sandbox": "workspace-write", "approval": "ask"},
    "danger-full-access": {"sandbox": "danger-full-access", "approval": "never"},
}
RESERVED_PRESETS = {"custom", "auto"}
SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
APPROVAL_POLICIES = {"ask", "never"}
EXECUTION_EVENTS = {"tool_call", "tool_result", "text", "thinking"}
SESSION_BYTES_CAP = 8 * 1024 * 1024

EXTRA_FIELDS = ["model_provider"]
FIELDS = [
    "working_directory",
    "model",
    "thinking_depth",
    "permission_mode",
    "sandbox_mode",
    "approval_mode",
    "model_provider",
]
PARAMETER_APPLICABILITY = {
    "working_directory": "required",
    "model": "applicable",
    "thinking_depth": "applicable",
    "permission_mode": "applicable",
    "sandbox_mode": "applicable",
    "approval_mode": "applicable",
    "model_provider": "applicable",
}

CREATION_PROFILE = "headless"
LAUNCHER_ENV = "RUAN_CONTINUE2RUN_DSH_BIN"
SESSION_ID_ENV = "DSH_SESSION_ID"
HOME_ENV = "DSH_HOME"
PROVIDER_ENTRY = "llm-pi-ai"
SESSION_LOG_NAMES = ("session.v4.jsonl", "session.v4.jsonl.zstd")


# ---------------------------------------------------------------- small helpers

def _runtime_value(state: str, value: Any = "") -> dict[str, str]:
    text = "" if value is None else str(value)
    if "\n" in text or "\r" in text:
        text = ""
    return {"state": state, "value": text}


def _error(code: str, summary: str, status: str = "failed") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "retryable": False,
        "session_reference": "",
        "submitted_parameters": {},
        "effective_parameters": {},
        "startup_evidence": {},
        "error_code": code,
        "error_summary": summary,
    }


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _launcher() -> str | None:
    override = os.environ.get(LAUNCHER_ENV)
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path)
        return shutil.which(override)
    return shutil.which("dsh")


def _dsh_home() -> Path:
    configured = os.environ.get(HOME_ENV)
    return Path(configured).expanduser() if configured else Path.home() / ".dsh"


def _zstd() -> str | None:
    return shutil.which("zstd") or shutil.which("unzstd")


# ---------------------------------------------------------------- session log reading

def _session_log(session_id: str) -> Path | None:
    if not session_id:
        return None
    root = _dsh_home() / "sessions"
    try:
        directories = sorted(entry for entry in root.iterdir() if entry.is_dir())
    except OSError:
        return None
    for directory in directories:
        candidate = directory / session_id
        if not candidate.is_dir():
            continue
        for name in SESSION_LOG_NAMES:
            path = candidate / name
            if path.is_file():
                return path
    return None


def _read_events(path: Path) -> list[dict[str, Any]] | None:
    """Read durable Session events, tolerating a partially flushed tail."""
    try:
        if path.suffix == ".zstd":
            binary = _zstd()
            if not binary:
                return None
            completed = subprocess.run(
                [binary, "-dc", str(path)], capture_output=True, timeout=READ_TIMEOUT_SECONDS
            )
            data = completed.stdout[:SESSION_BYTES_CAP]
        else:
            data = path.read_bytes()[:SESSION_BYTES_CAP]
    except (OSError, subprocess.SubprocessError):
        return None
    events: list[dict[str, Any]] = []
    for raw in data.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _message_text(data: dict[str, Any]) -> str:
    parts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(_text(block.get("text")))
    return "".join(parts)


def _facts_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "cwd": "",
        "permission_mode": "",
        "sandbox_mode": "",
        "approval_mode": "",
        "model": "",
        "model_provider": "",
        "thinking_depth": "",
        "payload_text": None,
        "approval_blocked": False,
    }
    asked: set[str] = set()
    decided: set[str] = set()
    for event in events:
        kind = event.get("type")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        if kind == "session":
            facts["cwd"] = _text(event.get("cwd")) or facts["cwd"]
        elif kind == "permission/preset":
            facts["permission_mode"] = _text(data.get("preset")) or facts["permission_mode"]
        elif kind == "sandbox/mode":
            facts["sandbox_mode"] = _text(data.get("mode")) or facts["sandbox_mode"]
        elif kind == "approval/policy":
            facts["approval_mode"] = _text(data.get("policy")) or facts["approval_mode"]
        elif kind == "request/header":
            config = ((data.get("header") or {}).get("config") or {})
            if isinstance(config, dict) and config:
                facts["model"] = _text(config.get("model")) or facts["model"]
                facts["model_provider"] = _text(config.get("provider")) or facts["model_provider"]
                facts["thinking_depth"] = _text(config.get("reasoningEffort")) or facts["thinking_depth"]
        elif kind == "request/context":
            facts["model"] = _text(data.get("model")) or facts["model"]
            facts["model_provider"] = _text(data.get("provider")) or facts["model_provider"]
        elif kind == "user/message":
            source = data.get("source") or {}
            if facts["payload_text"] is None and source.get("kind") == "user":
                text = _message_text(data)
                if text:
                    facts["payload_text"] = text
        elif kind == "approval/asked":
            identifier = _text(data.get("id"))
            if identifier:
                asked.add(identifier)
        elif kind == "approval/decided":
            identifier = _text(data.get("id"))
            if identifier:
                decided.add(identifier)
    facts["approval_blocked"] = bool(asked - decided)
    return facts


def _observe_until(session_id: str, deadline: float) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    while True:
        log = _session_log(session_id)
        if log is not None:
            events = _read_events(log)
            if events:
                facts = _facts_from_events(events)
                if facts["payload_text"] is not None and facts["model"] and facts["cwd"]:
                    return facts
        if time.monotonic() >= deadline:
            return facts
        time.sleep(0.3)


# ---------------------------------------------------------------- runtime observation

def read_runtime_context() -> dict[str, dict[str, str]]:
    result = {field: _runtime_value("unavailable") for field in FIELDS}
    log = _session_log(os.environ.get(SESSION_ID_ENV) or "")
    if log is None:
        return result
    events = _read_events(log)
    if not events:
        return result
    facts = _facts_from_events(events)
    for field in ("working_directory", "permission_mode", "sandbox_mode", "approval_mode",
                  "model", "model_provider", "thinking_depth"):
        value = facts["cwd"] if field == "working_directory" else facts[field]
        if value:
            result[field] = _runtime_value("observed", value)
    return result


def detect() -> bool:
    return bool(os.environ.get(SESSION_ID_ENV) and _launcher())


# ---------------------------------------------------------------- composition probing

def _dump_composed(profile: str, patch: Path | None = None) -> tuple[str | None, str]:
    launcher = _launcher()
    if not launcher:
        return None, "dsh launcher not found"
    if not profile:
        return None, "current DSH profile is unknown"
    command = [launcher, "--profile", profile, "--dump-config"]
    if patch is not None:
        command += ["--patch", str(patch)]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=DUMP_TIMEOUT_SECONDS, cwd=os.getcwd(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        summary = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        return None, summary[-400:]
    return completed.stdout, ""


def _entry_block(dump: str, entry_id: str) -> str | None:
    """Return one top-level patch entry from a composed-profile dump, verbatim."""
    lines = dump.split("\n")
    start = None
    for index, line in enumerate(lines):
        if line == f"- id: {entry_id}":
            start = index
            break
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not lines[end].startswith("- "):
        end += 1
    block = "\n".join(lines[start:end]).rstrip()
    return block or None


def _defines_route(entry: str | None, provider: str, model: str) -> bool:
    if not entry:
        return False
    if not re.search(rf"^\s+{re.escape(provider)}:\s*$", entry, re.MULTILINE):
        return False
    return bool(re.search(rf"^\s+- id: {re.escape(model)}\s*$", entry, re.MULTILINE))


def _resolve_model_route(provider: str, model: str) -> tuple[bool, str]:
    """The creation profile must define the model route the Session will use.

    The route is inherited from the creation profile's own composition, so every
    hop of a chain (Web origin or headless origin) resolves it the same way.
    """
    if not provider or not model:
        return False, "model route is missing provider or model"
    dump, error = _dump_composed(CREATION_PROFILE)
    if dump is None:
        return False, f"cannot read composed profile {CREATION_PROFILE!r}: {error}"
    if _defines_route(_entry_block(dump, PROVIDER_ENTRY), provider, model):
        return True, ""
    return False, (
        f"profile {CREATION_PROFILE!r} does not define model route {provider}/{model}; "
        f"add that provider and model to {_dsh_home() / 'profiles' / CREATION_PROFILE / 'cordis.patch.yml'}"
    )


# ---------------------------------------------------------------- preflight

def _expected(ctx: dict[str, Any], field: str) -> str:
    entry = (ctx.get("parameter_expectations") or {}).get(field) or {}
    return _text(entry.get("value"))


def _failure(code: str, problem: str) -> dict[str, Any]:
    return {"ok": False, "error_code": code, "problems": [problem]}


def preflight(ctx: dict[str, Any]) -> dict[str, Any]:
    if not _launcher():
        return _failure("DSH_LAUNCHER_NOT_FOUND", "dsh launcher not found on PATH or via " + LAUNCHER_ENV)
    profile_dir = _dsh_home() / "profiles" / CREATION_PROFILE
    if not profile_dir.is_dir():
        return _failure("DSH_CREATION_PROFILE_MISSING", f"profile directory not found: {profile_dir}")
    if not _session_log(os.environ.get(SESSION_ID_ENV) or ""):
        return _failure("DSH_SESSION_LOG_UNREADABLE",
                        f"current session log for {SESSION_ID_ENV} is not readable")
    cwd = _expected(ctx, "working_directory")
    if not cwd or not Path(cwd).expanduser().is_dir():
        return _failure("DSH_CWD_INVALID", f"invalid working directory: {cwd!r}")
    missing = [field for field in FIELDS if not _expected(ctx, field)]
    if missing:
        return _failure("DSH_RUNTIME_PARAMETER_UNAVAILABLE", f"missing runtime parameters: {missing}")
    sandbox = _expected(ctx, "sandbox_mode")
    if sandbox not in SANDBOX_MODES:
        return _failure("DSH_SANDBOX_INVALID", f"unsupported sandbox_mode: {sandbox!r}")
    approval = _expected(ctx, "approval_mode")
    if approval not in APPROVAL_POLICIES:
        return _failure("DSH_APPROVAL_INVALID", f"unsupported approval_mode: {approval!r}")
    ok, error = _resolve_model_route(_expected(ctx, "model_provider"), _expected(ctx, "model"))
    if not ok:
        return _failure("DSH_MODEL_ROUTE_UNAVAILABLE", error)
    return {"ok": True, "error_code": "", "problems": []}


# ---------------------------------------------------------------- overlay and launch

def _scalar(value: Any) -> str:
    return json.dumps(_text(value), ensure_ascii=False)


def _build_overlay(values: dict[str, str]) -> str:
    preset = values["permission_mode"]
    sandbox = values["sandbox_mode"]
    approval = values["approval_mode"]
    presets = {name: dict(bundle) for name, bundle in DEFAULT_PRESETS.items()}
    if preset not in RESERVED_PRESETS:
        presets[preset] = {"sandbox": sandbox, "approval": approval}
    rows: list[str] = []
    rows.append("\n".join([
        "- id: agent-default-model",
        "  name: '@deepseek-ai/dsh-agent-default-model'",
        "  config:",
        f"    provider: {_scalar(values['model_provider'])}",
        f"    model: {_scalar(values['model'])}",
        f"    reasoningEffort: {_scalar(values['thinking_depth'])}",
    ]))
    rows.append("\n".join([
        "- id: sandbox-policy",
        "  name: '@deepseek-ai/dsh-sandbox-policy'",
        "  config:",
        f"    mode: {_scalar(sandbox)}",
        "    workspaceRoot: !!js process.cwd()",
    ]))
    rows.append("\n".join([
        "- id: approval",
        "  name: '@deepseek-ai/dsh-user-approval'",
        "  config:",
        f"    policy: {_scalar(approval)}",
    ]))
    preset_lines = [
        "- id: permission",
        "  name: '@deepseek-ai/dsh-permission-presets'",
        "  config:",
        "    presets:",
    ]
    for name in sorted(presets):
        bundle = presets[name]
        preset_lines.append(f"      {_scalar(name)}:")
        preset_lines.append(f"        sandbox: {_scalar(bundle['sandbox'])}")
        preset_lines.append(f"        approval: {_scalar(bundle['approval'])}")
    if preset not in RESERVED_PRESETS:
        preset_lines.append(f"    defaultPreset: {_scalar(preset)}")
    rows.append("\n".join(preset_lines))
    return "\n".join(rows) + "\n"


def _consume_event(raw: bytes, state: dict[str, Any]) -> None:
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(event, dict):
        return
    kind = event.get("type")
    if kind == "session":
        state["session_id"] = _text(event.get("sessionId")) or state["session_id"]
        state["cwd"] = _text(event.get("cwd")) or state["cwd"]
    elif kind == "status":
        phase = _text(event.get("phase"))
        if phase in {"turn_start", "step_start"}:
            state["running"] = True
        elif phase == "turn_end":
            reason = event.get("reason") or {}
            state["terminal"] = "turn_end"
            state["end_reason"] = _text(reason.get("kind") if isinstance(reason, dict) else reason)
    elif kind in EXECUTION_EVENTS:
        if kind in {"text", "thinking"}:
            if _text(event.get("text")).strip():
                state["execution"] = kind
        else:
            state["execution"] = kind
    elif kind == "final":
        state["terminal"] = "final"
    elif kind == "error":
        state["terminal"] = "error"
        state["error_message"] = _text(event.get("message"))


def _read_new(path: Path, position: int) -> tuple[bytes, int]:
    try:
        with open(path, "rb") as handle:
            handle.seek(position)
            data = handle.read()
    except OSError:
        return b"", position
    return data, position + len(data)


def _observe_run(process: subprocess.Popen, events_path: Path, deadline: float) -> dict[str, Any]:
    state: dict[str, Any] = {
        "session_id": "", "cwd": "", "running": False, "execution": "",
        "terminal": "", "end_reason": "", "error_message": "",
    }
    position = 0
    buffer = b""

    def drain(data: bytes) -> None:
        nonlocal buffer
        buffer += data
        while b"\n" in buffer:
            raw, _, buffer = buffer.partition(b"\n")
            if raw.strip():
                _consume_event(raw, state)

    while True:
        data, position = _read_new(events_path, position)
        drain(data)
        if state["session_id"] and state["running"] and state["execution"]:
            break
        if state["terminal"]:
            break
        if time.monotonic() >= deadline:
            break
        if process.poll() is not None:
            data, position = _read_new(events_path, position)
            if not data:
                break
            drain(data)
            continue
        time.sleep(0.2)
    state["exit_code"] = process.poll()
    return state


def _stderr_tail(path: Path, limit: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text.strip()[-limit:]


def _effective_parameters(facts: dict[str, Any]) -> dict[str, str]:
    mapping = {
        "working_directory": facts.get("cwd"),
        "model": facts.get("model"),
        "thinking_depth": facts.get("thinking_depth"),
        "permission_mode": facts.get("permission_mode"),
        "sandbox_mode": facts.get("sandbox_mode"),
        "approval_mode": facts.get("approval_mode"),
        "model_provider": facts.get("model_provider"),
    }
    return {field: _text(value) for field, value in mapping.items() if _text(value)}


def create_and_confirm(ctx: dict[str, Any], payload: str) -> dict[str, Any]:
    values = {field: _expected(ctx, field) for field in FIELDS}
    launcher = _launcher()
    if not launcher:
        return _error("DSH_LAUNCHER_NOT_FOUND", "dsh launcher not found on PATH or via " + LAUNCHER_ENV)
    cwd = values["working_directory"]
    if not cwd or not Path(cwd).expanduser().is_dir():
        return _error("DSH_CWD_INVALID", f"invalid working directory: {cwd!r}")
    missing = [field for field in FIELDS if not values[field]]
    if missing:
        return _error("DSH_RUNTIME_PARAMETER_UNAVAILABLE", f"missing runtime parameters: {missing}")
    if values["sandbox_mode"] not in SANDBOX_MODES:
        return _error("DSH_SANDBOX_INVALID", f"unsupported sandbox_mode: {values['sandbox_mode']!r}")
    if values["approval_mode"] not in APPROVAL_POLICIES:
        return _error("DSH_APPROVAL_INVALID", f"unsupported approval_mode: {values['approval_mode']!r}")
    ok, error = _resolve_model_route(values["model_provider"], values["model"])
    if not ok:
        return _error("DSH_MODEL_ROUTE_UNAVAILABLE", error)

    payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    submitted = dict(values)
    submitted["payload_sha256"] = payload_sha

    def unknown(code: str, summary: str) -> dict[str, Any]:
        return {
            **_error(code, summary, "unknown"),
            "submitted_parameters": submitted,
            "effective_parameters": {},
            "startup_evidence": {},
        }

    try:
        run_dir = Path(tempfile.mkdtemp(prefix="ruan-continue2run-dsh-"))
    except OSError as exc:
        return _error("DSH_RUN_DIR_FAILED", f"cannot create private run directory: {exc}")
    os.chmod(run_dir, 0o700)
    overlay_path = run_dir / "overlay.yml"
    payload_path = run_dir / "payload.txt"
    events_path = run_dir / "events.jsonl"
    stderr_path = run_dir / "dsh-stderr.txt"
    try:
        overlay_path.write_text(_build_overlay(values), encoding="utf-8")
        with open(payload_path, "wb") as handle:
            handle.write(payload.encode("utf-8"))
        os.chmod(overlay_path, 0o600)
        os.chmod(payload_path, 0o600)
    except OSError as exc:
        return _error("DSH_RUN_DIR_FAILED", f"cannot write launch inputs: {exc}")

    command = [launcher, "--profile", CREATION_PROFILE, "--patch", str(overlay_path), "--json", "-"]
    try:
        with open(payload_path, "rb") as stdin_handle, \
                open(events_path, "wb") as stdout_handle, \
                open(stderr_path, "wb") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=stdin_handle,
                stdout=stdout_handle,
                stderr=stderr_handle,
                close_fds=True,
                start_new_session=True,
            )
    except OSError as exc:
        return _error("DSH_LAUNCH_FAILED", f"cannot launch {CREATION_PROFILE} app: {exc}")

    state = _observe_run(process, events_path, time.monotonic() + STARTUP_WINDOW_SECONDS)
    session_id = _text(state.get("session_id"))
    observed_cwd = _text(state.get("cwd"))
    evidence: dict[str, Any] = {
        "surface": f"dsh --profile {CREATION_PROFILE} --json",
        "run_dir": str(run_dir),
        "event": state.get("execution") or state.get("terminal") or "",
        "running": bool(state.get("running")),
        "exit_code": state.get("exit_code"),
    }
    if state.get("terminal"):
        evidence["terminal"] = state["terminal"]
    if state.get("end_reason"):
        evidence["end_reason"] = state["end_reason"]
    if not session_id:
        summary = _stderr_tail(stderr_path) or _text(state.get("error_message"))
        if state.get("terminal") == "error" or state.get("exit_code") is not None:
            return {**_error("DSH_SESSION_NOT_CREATED",
                             "headless run ended without creating a Session: " + (summary or "no session event")),
                    "submitted_parameters": submitted, "startup_evidence": evidence}
        return {**unknown("DSH_STARTUP_TIMEOUT",
                          "no Session event within the startup observation window"),
                "startup_evidence": evidence}

    if observed_cwd and str(Path(observed_cwd).resolve()) != str(Path(cwd).resolve()):
        return {**unknown("DSH_CWD_MISMATCH",
                          f"created Session working directory {observed_cwd!r} differs from expected {cwd!r}"),
                "session_reference": session_id, "startup_evidence": evidence}

    facts = _observe_until(session_id, time.monotonic() + LOG_VERIFY_SECONDS)
    effective = _effective_parameters(facts)
    payload_text = facts.get("payload_text")
    evidence["log"] = str(_session_log(session_id) or "")
    evidence["payload_verified"] = "unreadable" if payload_text is None else payload_text == payload
    if facts.get("approval_blocked"):
        return {**unknown("APPROVAL_BLOCKED",
                          "created Session is waiting for approval; startup cannot be confirmed"),
                "session_reference": session_id, "effective_parameters": effective,
                "startup_evidence": evidence}
    if payload_text is not None and payload_text != payload:
        return {**unknown("DSH_PAYLOAD_MISMATCH",
                          "created Session did not record the Payload verbatim"),
                "session_reference": session_id, "effective_parameters": effective,
                "startup_evidence": evidence}
    if payload_text is None:
        return {**unknown("DSH_SESSION_LOG_UNREADABLE",
                          "created Session log could not be verified within the observation window"),
                "session_reference": session_id, "effective_parameters": effective,
                "startup_evidence": evidence}
    drifted = {field: value for field, value in effective.items()
               if field in values and value != values[field]}
    if drifted:
        return {**unknown("DSH_EFFECTIVE_PARAMETER_MISMATCH",
                          f"effective parameters differ from submitted: {drifted}"),
                "session_reference": session_id, "effective_parameters": effective,
                "startup_evidence": evidence}
    if not state.get("running") or not state.get("execution"):
        reason = state.get("end_reason") or state.get("error_message") or "no execution event observed"
        return {**unknown("DSH_STARTUP_UNCONFIRMED",
                          f"Session was created but startup was not observed: {reason}"),
                "session_reference": session_id, "effective_parameters": effective,
                "startup_evidence": evidence}
    try:
        payload_path.unlink()
    except OSError:
        pass
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "confirmed",
        "retryable": False,
        "session_reference": session_id,
        "submitted_parameters": submitted,
        "effective_parameters": effective,
        "startup_evidence": evidence,
        "error_code": "",
        "error_summary": "",
    }