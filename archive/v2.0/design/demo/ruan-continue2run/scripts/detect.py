#!/usr/bin/env python3
"""Adapter loading and Harness detection."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ADAPTER_DIR = Path(__file__).resolve().parent / "adapters"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def list_adapters():
    return sorted(p.stem for p in ADAPTER_DIR.glob("*.py") if not p.name.startswith("_"))


def load_adapter(name):
    if not NAME_RE.match(name or ""):
        return None
    path = ADAPTER_DIR / f"{name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("c2r_adapter_" + re.sub(r"\W", "_", name), path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load adapter {name!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detect_matches():
    matches = []
    for name in list_adapters():
        try:
            module = load_adapter(name)
            if module is not None and bool(module.detect()):
                matches.append(name)
        except Exception:
            continue
    return matches


def resolve_harness(explicit=None):
    if explicit:
        name = explicit.strip().lower()
        try:
            exists = load_adapter(name) is not None
        except Exception as exc:
            return None, f"adapter '{name}' failed to load: {type(exc).__name__}: {exc}"
        if not exists:
            return None, f"no adapter for harness '{name}' (available: {list_adapters() or 'none'})"
        return name, None
    matches = detect_matches()
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, "no harness detected; specify one explicitly (e.g. --harness <name>)"
    return None, f"multiple harnesses matched {matches}; specify one explicitly"


if __name__ == "__main__":
    name, error = resolve_harness(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps({"harness": name, "error": error, "adapters": list_adapters()}, ensure_ascii=False))
    raise SystemExit(0 if name else 2)
