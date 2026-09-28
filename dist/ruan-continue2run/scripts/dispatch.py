"""Adapter 调度：按 Harness 标识加载对应 Adapter，并强制执行统一输出契约。

核心流程通过本模块调用 Adapter；本模块自己**不包含任何 Harness 创建细节**。

任何异常或违反契约的返回，都会被转成 issued=False 的如实结果：
不崩溃、不重试、不自我修复、不判断新会话是否运行成功。
"""
import importlib
import json
import sys
import traceback
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPTS_DIR / "adapters" / "registry.json"


def _norm(tag):
    return tag.strip().lower().replace("_", "-").replace(" ", "-")


def load_registry():
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)["adapters"]


def resolve_harness(tag):
    """由显式标签选 Adapter；无标签时只接受唯一的被动识别结果。"""
    registry = load_registry()
    if tag:
        t = _norm(tag)
        for harness_id, entry in registry.items():
            names = [harness_id] + list(entry.get("aliases") or [])
            if t in [_norm(a) for a in names]:
                return harness_id
        return None

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    matches = []
    for harness_id, entry in registry.items():
        try:
            module = importlib.import_module(f"adapters.{entry['module']}")
            detect = getattr(module, "detect", None)
            if callable(detect) and detect() is True:
                matches.append(harness_id)
        except Exception:  # noqa: BLE001 - 被动识别失败时不猜测 Harness
            continue
    return matches[0] if len(matches) == 1 else None


def _fail(error):
    return {"issued": False, "info": None, "error": error}


def _validate(raw):
    """把 Adapter 的返回值收敛到契约规定的结构。"""
    if not isinstance(raw, dict) or set(raw) != {"issued", "info", "error"}:
        return _fail(f"ADAPTER_CONTRACT_VIOLATION: Adapter 必须只返回 issued/info/error 字段，实际返回：{raw!r}")
    if not isinstance(raw["issued"], bool):
        return _fail(f"ADAPTER_CONTRACT_VIOLATION: issued 必须是布尔值，实际返回：{raw!r}")
    info, error = raw.get("info"), raw.get("error")
    if info is not None and not isinstance(info, str):
        return _fail(f"ADAPTER_CONTRACT_VIOLATION: info 必须是字符串或 null，实际返回：{raw!r}")
    if error is not None and not isinstance(error, str):
        return _fail(f"ADAPTER_CONTRACT_VIOLATION: error 必须是字符串或 null，实际返回：{raw!r}")
    if not raw["issued"] and not error:
        return _fail("ADAPTER_CONTRACT_VIOLATION: issued=false 但 Adapter 没有给出 error")
    if raw["issued"] and error is not None:
        return _fail("ADAPTER_CONTRACT_VIOLATION: issued=true 时 error 必须为 null")
    return {"issued": raw["issued"], "info": info, "error": error}


def run_adapter(context):
    """以 RelayContext 调用对应 Adapter，返回符合契约的结果字典。"""
    harness = context.get("harness") or {}
    harness_id = harness.get("id")
    if not harness_id:
        tag = harness.get("tag")
        if tag:
            return _fail(f"HARNESS_UNREGISTERED: Harness 标签 {tag} 不在注册表中，未执行任何创建")
        return _fail("HARNESS_UNRESOLVED: 未提供 Harness 标签，无法选择 Adapter，未执行任何创建")

    entry = load_registry().get(harness_id)
    if not entry:
        return _fail(f"HARNESS_UNREGISTERED: {harness_id} 不在注册表中，未执行任何创建")

    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        module = importlib.import_module(f"adapters.{entry['module']}")
        create = getattr(module, "create")
    except Exception as e:  # noqa: BLE001 - 任何加载失败都如实上报
        return _fail(f"ADAPTER_LOAD_FAILED: {harness_id}: {e!r}")

    try:
        raw = create(context)
    except Exception as e:  # noqa: BLE001 - 任何异常都如实上报，不重试
        tb = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return _fail(f"ADAPTER_EXCEPTION: {harness_id}: {e!r} ({tb})")

    return _validate(raw)
