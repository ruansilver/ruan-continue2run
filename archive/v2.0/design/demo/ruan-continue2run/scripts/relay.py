#!/usr/bin/env python3
"""Deterministic Relay orchestration for ruan-continue2run 2.0.

The script owns Capture, expected/observed parameter validation, task-entry hashing,
atomic RelayContext/log writes, cwd-level STOP handling, one-time handoff claims,
and tri-state Adapter results. Harness-specific creation remains in an Adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect  # noqa: E402

SKILL = "ruan-continue2run"
SCHEMA_VERSION = 1
RUN_DIR = ".ruan-continue2run"
HDR_OPEN, HDR_CLOSE = "=== RELAY CONTEXT ===", "=== END CONTEXT ==="
CTRL_OPEN, CTRL_CLOSE = "<<<relay-control", ">>>"
TASK_OPEN_RE = re.compile(r"^<<<relay-task-entry bytes=(\d+)>>>$")
TASK_CLOSE = "<<<relay-task-end>>>"
BASE_CONTROL = [
    "model",
    "thinking_depth",
    "working_directory",
    "permission_mode",
    "sandbox_mode",
    "approval_mode",
]
UNAVAILABLE = "unavailable"
NOT_APPLICABLE = "not_applicable"
VALID_STATES = {"observed", "supplied", "inherited", UNAVAILABLE, NOT_APPLICABLE}
STOP_NOTICE = "已检测到 STOP，本轮继续执行，但结束后不会继续接力。"
INVOKE_RE = re.compile(r"^[$/@]?" + re.escape(SKILL) + r"$")
HARNESS_RE = re.compile(r"^//([A-Za-z0-9][A-Za-z0-9_-]*)$")
ERROR_LIMIT = 800
NOTE_LIMIT = 500
ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SECRET_FIELD_RE = re.compile(r"(?i)(?:secret|token|password|passwd|api[_-]?key|authorization|credential)")


class CaptureError(Exception):
    pass


class ParameterError(Exception):
    pass


def emit(obj: dict[str, Any], code: int = 0) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    raise SystemExit(code)


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp() -> str:
    return now().strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------- redaction and safe writes
_SECRETS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd)\s*[:=]\s*)[^\s,;\"']+"), r"\1[REDACTED]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[REDACTED]"),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{10,}"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
]


def redact(value: Any, limit: int = ERROR_LIMIT) -> str:
    text = "" if value is None else str(value)
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        chmod_private(tmp)
        os.replace(tmp, path)
        chmod_private(path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_append(path: Path, text: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(path, existing + text)


# ---------------------------------------------------------------- capture

def normalize_message(text: str) -> str:
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _line_text(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def _parse_control(lines: list[str], start: int) -> tuple[dict[str, str], int]:
    control: dict[str, str] = {}
    i = start + 1
    while i < len(lines):
        current = _line_text(lines[i])
        if current == CTRL_CLOSE:
            return control, i + 1
        if current:
            key, sep, value = current.partition("=")
            if not sep or not key.strip():
                raise CaptureError(f"bad control line: {current!r}")
            key = key.strip()
            if key in control:
                raise CaptureError(f"duplicate control key: {key}")
            if "\n" in value or "\r" in value:
                raise CaptureError(f"control value must be one line: {key}")
            control[key] = value
        i += 1
    raise CaptureError("unterminated relay-control block")


def _parse_task_frame(text: str, lines: list[str], start: int) -> tuple[str, int] | None:
    if start >= len(lines):
        return None
    marker = _line_text(lines[start])
    match = TASK_OPEN_RE.match(marker)
    if not match:
        return None
    if not lines[start].endswith("\n"):
        raise CaptureError("task-entry frame must end its header with LF")
    length = int(match.group(1))
    body_start = sum(len(part) for part in lines[: start + 1])
    raw = text[body_start:].encode("utf-8")
    if len(raw) < length:
        raise CaptureError("task-entry frame is shorter than declared")
    body = raw[:length]
    remainder = raw[length:]
    expected_prefix = ("\n" + TASK_CLOSE).encode("utf-8")
    if remainder not in (expected_prefix, expected_prefix + b"\n"):
        raise CaptureError("task-entry frame terminator missing")
    try:
        task = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaptureError(f"task-entry is not valid UTF-8: {exc}") from exc
    return task, start


def parse_message(text: str) -> dict[str, Any]:
    """Parse only leading control positions; framed task text is preserved byte-for-byte."""
    original = text
    text = normalize_message(text)
    # split only LF: splitlines() would also split valid body Unicode separators.
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    if not lines:
        raise CaptureError("empty message")
    i = 0
    if i >= len(lines) or not INVOKE_RE.match(_line_text(lines[i]).strip()):
        raise CaptureError(f"first line must be the '{SKILL}' invocation")
    i += 1

    harness = None
    if i < len(lines):
        match = HARNESS_RE.match(_line_text(lines[i]).strip())
        if match:
            harness = match.group(1).lower()
            i += 1

    control: dict[str, str] = {}
    if i < len(lines) and _line_text(lines[i]).strip() == CTRL_OPEN:
        control, i = _parse_control(lines, i)

    carried = "task_entry_sha256" in control
    if carried and original != text:
        raise CaptureError("carried Payload is not canonical UTF-8/LF without BOM")
    if control.get("schema_version") not in (None, str(SCHEMA_VERSION)):
        raise CaptureError("unsupported Payload schema_version")
    if "schema_version" in control and not carried:
        raise CaptureError("structured Payload must carry task_entry_sha256")
    framed = _parse_task_frame(text, lines, i) if carried else None
    if carried and framed is None:
        raise CaptureError("carried Payload requires length-delimited task entry")
    if framed is not None:
        task_entry, _ = framed
    else:
        task_entry = "".join(lines[i:])
    if task_entry == "":
        raise CaptureError("empty task entry")

    expected_hash = control.get("task_entry_sha256", "")
    if expected_hash and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
        raise CaptureError("task_entry_sha256 must be a 64-character hex digest")

    expected: dict[str, dict[str, Any]] = {}
    bare: dict[str, str] = {}
    for key, value in control.items():
        if key.startswith("param.") and key.endswith(".state"):
            field = key[6:-6]
            if value not in VALID_STATES:
                raise CaptureError(f"invalid state for {field}: {value}")
            expected.setdefault(field, {})["state"] = value
        elif key.startswith("param.") and key.endswith(".value"):
            field = key[6:-6]
            expected.setdefault(field, {})["value"] = value
        elif key not in {"schema_version", "task_entry_sha256"}:
            bare[key] = value
    for field, entry in expected.items():
        if "state" not in entry:
            raise CaptureError(f"missing state for parameter {field}")
        if entry["state"] not in {NOT_APPLICABLE, UNAVAILABLE} and "value" not in entry:
            raise CaptureError(f"missing value for parameter {field}")
        if "value" not in entry:
            entry["value"] = ""
    return {
        "harness": harness,
        "control": control,
        "bare_control": bare,
        "parameters": expected,
        "expected_hash": expected_hash.lower(),
        "task_entry": task_entry,
    }


def task_hash(task_entry: str) -> str:
    return hashlib.sha256(task_entry.encode("utf-8")).hexdigest()


def control_fields(adapter: Any) -> list[str]:
    extra = [f for f in getattr(adapter, "EXTRA_FIELDS", []) if f not in BASE_CONTROL]
    fields = BASE_CONTROL + extra
    unsafe = [field for field in fields if SECRET_FIELD_RE.search(field)]
    if unsafe:
        raise ParameterError(f"secret-bearing runtime fields are not allowed in RelayContext: {unsafe}")
    return fields


def normalize_param(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"state": UNAVAILABLE, "value": ""}
    state = str(raw.get("state", UNAVAILABLE))
    value = raw.get("value", "")
    if state not in VALID_STATES:
        return {"state": UNAVAILABLE, "value": ""}
    if value is None:
        value = ""
    value = str(value)
    if "\n" in value or "\r" in value:
        return {"state": UNAVAILABLE, "value": ""}
    return {"state": state, "value": value}


def normalize_runtime(raw: Any, fields: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raw = {}
    return {field: normalize_param(raw.get(field)) for field in fields}


def parse_cli_set(items: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key or "\n" in value or "\r" in value:
            raise CaptureError(f"invalid --set value: {item!r}")
        if key in result:
            raise CaptureError(f"duplicate --set field: {key}")
        result[key] = value
    return result


def assert_safe_parameters(parameters: dict[str, dict[str, Any]]) -> None:
    for field, entry in parameters.items():
        value = str(entry.get("value", ""))
        if redact(value, max(ERROR_LIMIT, len(value) + 1)) != value:
            raise CaptureError(f"runtime parameter {field} looks like a Secret; keep credentials outside RelayContext")


def resolve_context(adapter: Any, harness: str, captured: dict[str, Any], cli_set: dict[str, str]) -> dict[str, Any]:
    fields = control_fields(adapter)
    unknown = (set(cli_set) | set(captured["bare_control"]) | set(captured["parameters"])) - set(fields)
    if unknown:
        raise CaptureError(f"unknown control fields: {sorted(unknown)}")
    if captured["expected_hash"] and set(captured["parameters"]) != set(fields):
        raise CaptureError("carried expected parameters must cover exactly the Adapter fields")
    if captured["expected_hash"] and (cli_set or captured["bare_control"]):
        raise CaptureError("carried expectations cannot be overridden")
    try:
        runtime_raw = adapter.read_runtime_context() or {}
    except Exception:
        runtime_raw = {}
    observations = normalize_runtime(runtime_raw, fields)
    if observations["working_directory"]["state"] == UNAVAILABLE:
        observations["working_directory"] = {"state": "observed", "value": str(Path.cwd().resolve())}
    expectations: dict[str, dict[str, Any]] = {}
    for field in fields:
        if field in captured["parameters"]:
            expectation = dict(captured["parameters"][field])
        elif field in cli_set:
            expectation = {"state": "supplied", "value": cli_set[field]}
        elif field in captured["bare_control"]:
            expectation = {"state": "supplied", "value": captured["bare_control"][field]}
        elif applicability(adapter, field) == NOT_APPLICABLE:
            expectation = {"state": NOT_APPLICABLE, "value": ""}
        else:
            expectation = dict(observations[field])
            if field == "working_directory" and expectation["state"] in {UNAVAILABLE, NOT_APPLICABLE}:
                expectation = {"state": "observed", "value": os.getcwd()}
        expectations[field] = normalize_param(expectation)

    assert_safe_parameters(expectations)
    assert_safe_parameters(observations)
    digest = task_hash(captured["task_entry"])
    return {
        "schema_version": SCHEMA_VERSION,
        "task_entry": captured["task_entry"],
        "task_entry_sha256": digest,
        "expected_task_entry_sha256": captured["expected_hash"],
        "harness": harness,
        "slug": make_slug(captured["task_entry"]),
        "parameter_expectations": expectations,
        "parameter_observations": observations,
    }


def make_slug(task_entry: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", task_entry)[:3]
    prefix = "-".join(w.lower() for w in words)[:24] or "task"
    return f"{prefix}-{task_hash(task_entry)[:8]}"


def build_payload(adapter: Any, ctx: dict[str, Any]) -> str:
    lines = [getattr(adapter, "INVOCATION", SKILL), f"//{ctx['harness']}", CTRL_OPEN]
    lines.append(f"schema_version={SCHEMA_VERSION}")
    lines.append(f"task_entry_sha256={ctx['task_entry_sha256']}")
    for field in control_fields(adapter):
        entry = ctx["parameter_expectations"].get(field, {"state": UNAVAILABLE, "value": ""})
        lines.append(f"param.{field}.state={entry['state']}")
        if entry["state"] != NOT_APPLICABLE:
            value = str(entry.get("value", ""))
            if "\n" in value or "\r" in value:
                raise CaptureError(f"value of {field} must be a single line")
            lines.append(f"param.{field}.value={value}")
    lines.append(CTRL_CLOSE)
    task_bytes = ctx["task_entry"].encode("utf-8")
    lines.append(f"<<<relay-task-entry bytes={len(task_bytes)}>>>")
    return "\n".join(lines) + "\n" + ctx["task_entry"] + "\n" + TASK_CLOSE + "\n"


# ---------------------------------------------------------------- log + STOP

def run_root() -> Path:
    return Path(os.getcwd()) / RUN_DIR


def ensure_git_exclude(cwd: str) -> bool:
    try:
        r = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=cwd,
                           capture_output=True, text=True, timeout=10)
        if r.returncode != 0 or not r.stdout.strip():
            return False
        path = Path(r.stdout.strip())
        path = path if path.is_absolute() else Path(cwd) / path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if RUN_DIR + "/" not in existing.split("\n"):
            atomic_write_text(path, existing + ("" if existing.endswith("\n") or not existing else "\n") + RUN_DIR + "/\n")
        return True
    except Exception:
        return False


def new_log(root: Path, ctx: dict[str, Any]) -> Path:
    log_dir = root / "relay"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{utc_stamp()}-{ctx['slug']}-{secrets.token_hex(2)}.log"
    atomic_write_text(path, HDR_OPEN + "\n" + json.dumps(ctx, ensure_ascii=False, indent=2) + "\n" + HDR_CLOSE + "\n")
    return path


def log_event(path: Path, event: str, text: Any = "") -> None:
    body = redact(text, NOTE_LIMIT).replace("\n", "\n  ")
    atomic_append(path, f"[{now().strftime('%Y-%m-%dT%H:%M:%SZ')}] {event}: {body}\n")


def read_context(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").split("\n")
    try:
        start = lines.index(HDR_OPEN)
        end = lines.index(HDR_CLOSE)
    except ValueError as exc:
        raise CaptureError("RelayContext markers missing") from exc
    ctx = json.loads("\n".join(lines[start + 1:end]))
    if ctx.get("schema_version") != SCHEMA_VERSION:
        raise CaptureError("unsupported RelayContext schema_version")
    if task_hash(ctx["task_entry"]) != ctx["task_entry_sha256"]:
        raise CaptureError("persisted task entry hash mismatch")
    return ctx


def result_path(log: Path) -> Path:
    return Path(str(log) + ".handoff.result.json")


def claim_path(log: Path) -> Path:
    return Path(str(log) + ".handoff.claim")


def read_result(log: Path) -> dict[str, Any] | None:
    path = result_path(log)
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def write_result(log: Path, result: dict[str, Any]) -> None:
    atomic_write_text(result_path(log), json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def acquire_claim(log: Path) -> bool:
    path = claim_path(log)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": SCHEMA_VERSION, "claimed_at": now().isoformat(), "pid": os.getpid()}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except FileExistsError:
        return False


def error_result(code: str, summary: str, status: str = "unknown") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "retryable": False,
        "session_reference": "",
        "submitted_parameters": {},
        "effective_parameters": {},
        "startup_evidence": {},
        "error_code": code,
        "error_summary": redact(summary),
    }


# ---------------------------------------------------------------- validation and adapter process

def applicability(adapter: Any, field: str) -> str:
    if field == "working_directory":
        return "required"
    declared = getattr(adapter, "PARAMETER_APPLICABILITY", {}) or {}
    value = declared.get(field, "applicable")
    return str(value)


def validate_context(adapter: Any, ctx: dict[str, Any]) -> tuple[list[str], str]:
    problems: list[str] = []
    if ctx.get("expected_task_entry_sha256") and ctx["expected_task_entry_sha256"] != ctx["task_entry_sha256"]:
        problems.append("task_entry_sha256 does not match the carried expected hash")
        return problems, "TASK_ENTRY_HASH_MISMATCH"
    current_cwd = str(Path.cwd().resolve())
    expected_cwd = ctx["parameter_expectations"].get("working_directory", {})
    if expected_cwd.get("state") in {UNAVAILABLE, NOT_APPLICABLE} or not expected_cwd.get("value"):
        problems.append("working_directory is unavailable")
        return problems, "PARAMETER_UNAVAILABLE"
    if str(Path(expected_cwd["value"]).expanduser().resolve()) != current_cwd:
        problems.append(f"working_directory mismatch: expected {expected_cwd['value']!r}, current {current_cwd!r}")
        return problems, "WORKING_DIRECTORY_DRIFT"

    for field, expected in ctx["parameter_expectations"].items():
        mode = applicability(adapter, field)
        observed = ctx["parameter_observations"].get(field, {"state": UNAVAILABLE, "value": ""})
        if mode not in {"required", "applicable", NOT_APPLICABLE}:
            return [f"invalid applicability for {field}: {mode}"], "INVALID_ADAPTER_CONTRACT"
        if mode == "not_applicable":
            if expected.get("state") != NOT_APPLICABLE:
                problems.append(f"{field} is declared not_applicable but expected {expected}")
                return problems, "PARAMETER_APPLICABILITY_CHANGED"
            continue
        if expected.get("state") in {UNAVAILABLE, NOT_APPLICABLE} or not expected.get("value"):
            problems.append(f"applicable parameter {field} has no reliable expected value")
            return problems, "PARAMETER_UNAVAILABLE"
        if observed.get("state") == UNAVAILABLE:
            # A user-supplied expected value may be used only when the Adapter
            # can submit it explicitly; carried observed/inherited values need
            # a reliable current observation to prevent silent drift.
            if expected.get("state") != "supplied":
                problems.append(f"observed value unavailable for applicable parameter {field}")
                return problems, "PARAMETER_UNAVAILABLE"
            continue
        if observed.get("state") == NOT_APPLICABLE:
            problems.append(f"observed {field} as not_applicable but expected an applicable value")
            return problems, "PARAMETER_APPLICABILITY_CHANGED"
        if observed.get("value") != expected.get("value"):
            problems.append(f"parameter drift for {field}: expected {expected.get('value')!r}, observed {observed.get('value')!r}")
            return problems, "PARAMETER_DRIFT"
    return problems, ""


def sanitize_result(raw: Any, ctx: dict[str, Any], adapter: Any, payload_sha: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return error_result("INVALID_ADAPTER_RESULT", "adapter returned a non-object result")
    if type(raw.get("schema_version")) is not int or raw["schema_version"] != SCHEMA_VERSION:
        return error_result("INVALID_ADAPTER_RESULT", "missing/unsupported AdapterResult schema_version")
    if type(raw.get("retryable", False)) is not bool:
        return error_result("INVALID_ADAPTER_RESULT", "retryable must be a boolean")
    status = raw.get("status")
    if status not in {"confirmed", "failed", "unknown"}:
        return error_result("INVALID_ADAPTER_RESULT", f"invalid status {status!r}")

    def params(value: Any) -> dict[str, str]:
        return {str(k): redact(v, 1000) for k, v in value.items() if k in set(control_fields(adapter)) | {"payload_sha256"}} if isinstance(value, dict) else {}

    result = {
        "schema_version": raw["schema_version"],
        "status": status,
        "retryable": bool(raw.get("retryable", False)) and status == "failed",
        "session_reference": redact(raw.get("session_reference"), 300),
        "submitted_parameters": params(raw.get("submitted_parameters")),
        "effective_parameters": params(raw.get("effective_parameters")),
        "startup_evidence": redact(json.dumps(raw.get("startup_evidence"), ensure_ascii=False), 1000) if raw.get("startup_evidence") else "",
        "error_code": redact(raw.get("error_code"), 100),
        "error_summary": redact(raw.get("error_summary")),
    }
    if result["schema_version"] != SCHEMA_VERSION:
        return error_result("INVALID_ADAPTER_RESULT", "unsupported AdapterResult schema_version")
    if not ERROR_CODE_RE.fullmatch(result["error_code"] or ""):
        result["error_code"] = "UNKNOWN" if status != "confirmed" else ""
    if status == "confirmed":
        why: list[str] = []
        if not result["session_reference"]:
            why.append("no session_reference")
        if not result["startup_evidence"]:
            why.append("no startup_evidence")
        submitted = result["submitted_parameters"]
        for field, expected in ctx["parameter_expectations"].items():
            if expected.get("state") == NOT_APPLICABLE:
                continue
            if submitted.get(field) != expected.get("value"):
                why.append(f"submitted {field} != expected")
            effective = raw.get("effective_parameters") or {}
            if field in effective and effective[field] not in (None, UNAVAILABLE) and effective[field] != expected.get("value"):
                why.append(f"effective {field} != expected")
        if submitted.get("payload_sha256") and submitted.get("payload_sha256") != payload_sha:
            why.append("payload_sha256 mismatch")
        if why:
            result["status"] = "unknown"
            result["retryable"] = False
            result["error_code"] = "MISSING_CONFIRMATION_EVIDENCE"
            result["error_summary"] = "confirmed downgraded to unknown: " + "; ".join(why)
    if status != "confirmed" and not result["error_code"]:
        result["error_code"] = "UNKNOWN"
    return result


def invoke_adapter(adapter_name: str, ctx: dict[str, Any], payload: str, deadline: float) -> dict[str, Any]:
    if not math.isfinite(deadline) or deadline <= 0:
        return error_result("INVALID_ADAPTER_CONTRACT", "deadline must be positive and finite", "failed")
    runner = Path(__file__).resolve().parent / "adapter_runner.py"
    request = json.dumps({"ctx": ctx, "payload": payload}, ensure_ascii=False)
    try:
        process = subprocess.Popen(
            [sys.executable, str(runner), "--adapter", adapter_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=os.getcwd(),
            start_new_session=(os.name != "nt"),
        )
        try:
            stdout, stderr = process.communicate(request, timeout=deadline)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return error_result("ADAPTER_TIMEOUT", f"Adapter exceeded hard deadline of {deadline:g}s")
        if process.returncode != 0:
            return error_result("ADAPTER_EXCEPTION", redact(stderr or f"adapter exited {process.returncode}"))
        try:
            raw = json.loads(stdout)
        except Exception as exc:
            return error_result("INVALID_ADAPTER_RESULT", f"adapter output was not JSON: {exc}")
        return raw if isinstance(raw, dict) else error_result("INVALID_ADAPTER_RESULT", "adapter output was not an object")
    except Exception as exc:
        return error_result("ADAPTER_EXCEPTION", f"adapter invocation failed: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- commands

def display_value(field: str, value: Any) -> str:
    if SECRET_FIELD_RE.search(field):
        return "[REDACTED]"
    return redact(value, 300)


def context_report(ctx: dict[str, Any]) -> dict[str, Any]:
    expectations = ctx.get("parameter_expectations", {})
    return {
        "task_entry_sha256": ctx.get("task_entry_sha256"),
        "model": display_value("model", expectations.get("model", {}).get("value", UNAVAILABLE)),
        "thinking": display_value("thinking_depth", expectations.get("thinking_depth", {}).get("value", UNAVAILABLE)),
        "cwd": display_value("working_directory", expectations.get("working_directory", {}).get("value", UNAVAILABLE)),
        "permission": display_value("permission_mode", expectations.get("permission_mode", {}).get("value", UNAVAILABLE)),
        "sandbox_mode": display_value("sandbox_mode", expectations.get("sandbox_mode", {}).get("value", UNAVAILABLE)),
        "approval_mode": display_value("approval_mode", expectations.get("approval_mode", {}).get("value", UNAVAILABLE)),
        "parameter_expectations": {
            field: {"state": entry.get("state"), "value": display_value(field, entry.get("value", ""))}
            for field, entry in ctx.get("parameter_expectations", {}).items()
        },
        "parameter_observations": {
            field: {"state": entry.get("state"), "value": display_value(field, entry.get("value", ""))}
            for field, entry in ctx.get("parameter_observations", {}).items()
        },
    }


def cmd_start(args: argparse.Namespace) -> None:
    try:
        raw = sys.stdin.read() if args.message_file == "-" else Path(args.message_file).read_text(encoding="utf-8")
        captured = parse_message(raw)
        cli_set = parse_cli_set(args.set)
        harness, error = detect.resolve_harness(args.harness or captured["harness"])
        if not harness:
            emit({"ok": False, "stage": "harness", "report": {"error_code": "HARNESS_UNAVAILABLE", "error_summary": error}}, 2)
        try:
            adapter = detect.load_adapter(harness)
        except Exception as exc:
            emit({"ok": False, "stage": "adapter", "report": {"error_code": "ADAPTER_LOAD_FAILED", "error_summary": redact(exc)}}, 2)
        if adapter is None:
            emit({"ok": False, "stage": "adapter", "report": {"error_code": "ADAPTER_MISSING", "error_summary": f"no adapter for harness {harness!r}"}}, 2)
        ctx = resolve_context(adapter, harness, captured, cli_set)
    except (OSError, UnicodeError, CaptureError, ParameterError) as exc:
        emit({"ok": False, "stage": "capture", "report": {"error_code": "CAPTURE_FAILED", "error_summary": redact(exc)}}, 2)

    root = run_root()
    git_excluded = ensure_git_exclude(os.getcwd())
    log = new_log(root, ctx)
    stop = (root / "STOP").exists()
    out = {
        "ok": True,
        "stop": stop,
        "log_path": str(log),
        "harness": harness,
        "slug": ctx["slug"],
        "task_entry_sha256": ctx["task_entry_sha256"],
        "runtime": ctx["parameter_expectations"],
    }
    if not git_excluded:
        out["warning"] = "could not update .git/info/exclude; runtime files may be visible to Git"

    if ctx.get("expected_task_entry_sha256") and ctx["expected_task_entry_sha256"] != ctx["task_entry_sha256"]:
        log_event(log, "preflight", "FAILED TASK_ENTRY_HASH_MISMATCH")
        emit({"ok": False, "stage": "preflight", "log_path": str(log),
              "report": {"harness": harness, "adapter": harness, "stage": "preflight",
                         "status": "failed", "error_code": "TASK_ENTRY_HASH_MISMATCH",
                         **context_report(ctx),
                         "error_summary": "task entry hash differs from carried expected hash"}}, 3)

    if stop:
        log_event(log, "stop", "STOP present at start; this session will not hand off")
        out["notice"] = STOP_NOTICE
        emit(out)

    problems, code = validate_context(adapter, ctx)
    try:
        pf = adapter.preflight(ctx) or {}
        if not pf.get("ok"):
            problems += [redact(item) for item in (pf.get("problems") or ["adapter preflight failed"])]
            code = pf.get("error_code") or code or "PRECHECK_FAILED"
    except Exception as exc:
        problems.append(redact(f"adapter preflight raised {type(exc).__name__}: {exc}"))
        code = "PRECHECK_FAILED"
    if problems:
        safe_problems = [redact(problem) for problem in problems]
        log_event(log, "preflight", f"FAILED {code}: {' | '.join(safe_problems)}")
        emit({"ok": False, "stage": "preflight", "log_path": str(log),
              "report": {"harness": harness, "adapter": harness, "stage": "preflight",
                         "status": "failed", "error_code": code,
                         **context_report(ctx),
                         "error_summary": safe_problems,
                         "next": "do not run the task; report this and let the user open a Maintenance session"}}, 3)
    log_event(log, "preflight", "ok")
    emit(out)


def cmd_note(args: argparse.Namespace) -> None:
    path = Path(args.log)
    if not path.is_file():
        emit({"ok": False, "error_code": "LOG_NOT_FOUND", "error_summary": "log path not found"}, 2)
    log_event(path, "note", args.text)
    emit({"ok": True})


def report_for(ctx: dict[str, Any], final: dict[str, Any], attempts: list[dict[str, Any]], payload_sha: str) -> dict[str, Any]:
    expectations = ctx.get("parameter_expectations", {})
    return {
        "harness": ctx.get("harness"),
        "adapter": ctx.get("harness"),
        "stage": "handoff",
        "status": final.get("status"),
        "error_code": final.get("error_code", ""),
        "session_reference": final.get("session_reference", ""),
        "model": display_value("model", expectations.get("model", {}).get("value", UNAVAILABLE)),
        "thinking": display_value("thinking_depth", expectations.get("thinking_depth", {}).get("value", UNAVAILABLE)),
        "cwd": display_value("working_directory", expectations.get("working_directory", {}).get("value", UNAVAILABLE)),
        "permission": display_value("permission_mode", expectations.get("permission_mode", {}).get("value", UNAVAILABLE)),
        "sandbox_mode": display_value("sandbox_mode", expectations.get("sandbox_mode", {}).get("value", UNAVAILABLE)),
        "approval_mode": display_value("approval_mode", expectations.get("approval_mode", {}).get("value", UNAVAILABLE)),
        "task_entry_sha256": ctx.get("task_entry_sha256"),
        "payload_sha256": payload_sha,
        "submitted_parameters": final.get("submitted_parameters", {}),
        "effective_parameters": final.get("effective_parameters", {}),
        "error_summary": final.get("error_summary", ""),
        "attempts": [item.get("status") for item in attempts],
    }


def cmd_handoff(args: argparse.Namespace) -> None:
    log = Path(args.log)
    if not log.is_file():
        emit({"ok": False, "outcome": "usage_error", "error_code": "LOG_NOT_FOUND",
              "error_summary": "log path not found; use the log_path returned by start"}, 2)
    try:
        ctx = read_context(log)
    except Exception as exc:
        emit({"ok": False, "outcome": "usage_error", "error_code": "INVALID_LOG",
              "error_summary": redact(exc)}, 2)

    existing = read_result(log)
    if existing is not None:
        if existing.get("status") == "confirmed" or existing.get("outcome") == "stopped_by_stop":
            emit(existing, 0)
        emit(existing, 11)

    root = log.resolve().parent.parent
    if claim_path(log).exists():
        existing = read_result(log)
        if existing is not None:
            emit(existing, 0 if existing.get("outcome") == "confirmed" else 11)
        final = error_result("HANDOFF_CLAIM_IN_PROGRESS", "claim exists without final result; no second Adapter call is safe")
        emit({"ok": False, "outcome": "unknown", "report": report_for(ctx, final, [final], "")}, 11)
    if (root / "STOP").exists():
        stopped = {"schema_version": SCHEMA_VERSION, "ok": True, "outcome": "stopped_by_stop", "status": "stopped_by_stop",
                   "error_code": "STOP_PRESENT", "notice": "STOP 存在，本轮不创建下一会话。正常结束即可。"}
        write_result(log, stopped)
        log_event(log, "handoff", "STOP present; no next session created")
        emit(stopped, 10)

    if not acquire_claim(log):
        existing = read_result(log)
        if existing is not None:
            emit(existing, 0 if existing.get("status") == "confirmed" else 11)
        unknown = {"ok": False, "outcome": "unknown", "status": "unknown", "error_code": "HANDOFF_CLAIM_IN_PROGRESS",
                   "error_summary": "handoff claim exists without a final result; no second Adapter call is safe"}
        emit(unknown, 11)

    # Reduce the normal STOP race window after the claim, while keeping the
    # documented no-lock cwd usage convention.
    if (root / "STOP").exists():
        stopped = {"schema_version": SCHEMA_VERSION, "ok": True, "outcome": "stopped_by_stop", "status": "stopped_by_stop",
                   "error_code": "STOP_PRESENT", "notice": "STOP 存在，本轮不创建下一会话。正常结束即可。"}
        write_result(log, stopped)
        log_event(log, "handoff", "STOP appeared after claim; no next session created")
        emit(stopped, 10)

    try:
        adapter = detect.load_adapter(ctx["harness"])
    except Exception as exc:
        final = error_result("ADAPTER_LOAD_FAILED", redact(exc), "failed")
        write_result(log, final)
        emit({"ok": False, "outcome": "failed", "report": report_for(ctx, final, [final], "")}, 11)
    if adapter is None:
        final = error_result("ADAPTER_MISSING", "adapter missing at handoff time", "failed")
        write_result(log, final)
        emit({"ok": False, "outcome": "failed", "report": report_for(ctx, final, [final], "")}, 11)

    try:
        payload = build_payload(adapter, ctx)
    except Exception as exc:
        final = error_result("PAYLOAD_BUILD_FAILED", redact(exc), "failed")
        write_result(log, final)
        emit({"ok": False, "outcome": "failed", "report": report_for(ctx, final, [final], "")}, 11)
    payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    log_event(log, "handoff", f"calling adapter '{ctx['harness']}' (payload sha256 {payload_sha[:12]})")
    deadline = float(getattr(adapter, "HANDOFF_DEADLINE_SECONDS", 0))
    attempts: list[dict[str, Any]] = []
    raw = invoke_adapter(ctx["harness"], ctx, payload, deadline)
    attempts.append(sanitize_result(raw, ctx, adapter, payload_sha))
    log_event(log, "attempt-1", json.dumps(attempts[0], ensure_ascii=False))
    if attempts[0]["status"] == "failed" and attempts[0]["retryable"] and not (root / "STOP").exists():
        log_event(log, "retry", "failed+retryable=true; one identical mechanical retry")
        raw = invoke_adapter(ctx["harness"], ctx, payload, deadline)
        attempts.append(sanitize_result(raw, ctx, adapter, payload_sha))
        log_event(log, "attempt-2", json.dumps(attempts[1], ensure_ascii=False))

    final = attempts[-1]
    report = report_for(ctx, final, attempts, payload_sha)
    write_result(log, {"schema_version": SCHEMA_VERSION, "adapter_result": final, "ok": final["status"] == "confirmed", "outcome": final["status"], "status": final["status"], "report": report})
    log_event(log, "result", f"{final['status']} session={final.get('session_reference') or '-'} code={final.get('error_code') or '-'}")
    if final["status"] == "confirmed":
        emit({"ok": True, "outcome": "confirmed", "report": report,
              "next": "handoff done; make no further changes, give your final reply"}, 0)
    emit({"ok": False, "outcome": final["status"], "report": report,
          "next": "do not retry or debug here; report this to the user and suggest a Maintenance session"}, 11)


def main() -> None:
    parser = argparse.ArgumentParser(prog="relay.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start")
    start.add_argument("--message-file", required=True)
    start.add_argument("--harness")
    start.add_argument("--set", action="append", metavar="KEY=VALUE")
    note = sub.add_parser("note")
    note.add_argument("--log", required=True)
    note.add_argument("--text", required=True)
    handoff = sub.add_parser("handoff")
    handoff.add_argument("--log", required=True)
    args = parser.parse_args()
    {"start": cmd_start, "note": cmd_note, "handoff": cmd_handoff}[args.cmd](args)


if __name__ == "__main__":
    main()
