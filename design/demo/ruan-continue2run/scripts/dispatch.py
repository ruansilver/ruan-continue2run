"""Adapter 调度：按 Harness 标识加载对应 Adapter，并强制执行统一输出契约。

核心流程通过本模块调用 Adapter，自己不包含任何 Harness 创建细节。
任何异常或违反契约的返回，都会被转成 issued=False 的如实结果，不会让调用方崩溃，也不会重试或自我修复。
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
    """把 Harness 标签解析为注册表里的标识；无标签或不在注册表返回 None。"""
    if not tag:
        return None
    t = _norm(tag)
    for harness_id, entry in load_registry().items():
        if t == harness_id or t in [_norm(a) for a in entry.get("aliases", [])]:
            return harness_id
    return None


def _fail(error):
    return {"issued": False, "info": None, "error": error}


def _validate(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("issued"), bool):
        return _fail(f"ADAPTER_CONTRACT_VIOLATION: Adapter 必须返回含布尔字段 issued 的字典，实际返回：{raw!r}")
    info, error = raw.get("info"), raw.get("error")
    if info is not None and not isinstance(info, str):
        info = str(info)
    if error is not None and not isinstance(error, str):
        error = str(error)
    if not raw["issued"] and not error:
        error = "ADAPTER_CONTRACT_VIOLATION: issued=false 但 Adapter 没有给出 error"
    return {"issued": raw["issued"], "info": info, "error": error}


def run_adapter(context):
    """以 RelayContext 调用对应 Adapter，返回符合契约的结果字典。"""
    harness_id = (context.get("harness") or {}).get("id")
    if not harness_id:
        tag = (context.get("harness") or {}).get("tag")
        if tag:
            return _fail(f"HARNESS_UNREGISTERED: Harness 标签 //{tag} 不在注册表中，未执行任何创建")
        return _fail("HARNESS_UNRESOLVED: 未提供 Harness 标签，无法选择 Adapter，未执行任何创建")
    entry = load_registry().get(harness_id)
    if not entry:
        return _fail(f"HARNESS_UNREGISTERED: {harness_id} 不在注册表中，未执行任何创建")
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        module = importlib.import_module(f"adapters.{entry['module']}")
        create = getattr(module, "create")
    except Exception as e:  # noqa: BLE001
        return _fail(f"ADAPTER_LOAD_FAILED: {harness_id}: {e!r}")
    try:
        raw = create(context)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return _fail(f"ADAPTER_EXCEPTION: {harness_id}: {e!r} ({tb})")
    return _validate(raw)
