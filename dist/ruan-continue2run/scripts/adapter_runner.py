#!/usr/bin/env python3
"""Run one Adapter create_and_confirm call in an isolated process."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import detect  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
        adapter = detect.load_adapter(args.adapter)
        if adapter is None:
            result = {"schema_version": 1, "status": "failed", "retryable": False,
                      "session_reference": "", "submitted_parameters": {}, "effective_parameters": {},
                      "startup_evidence": {}, "error_code": "ADAPTER_MISSING",
                      "error_summary": "adapter missing"}
        else:
            result = adapter.create_and_confirm(request["ctx"], request["payload"])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"schema_version": 1, "status": "unknown", "retryable": False,
                          "session_reference": "", "submitted_parameters": {}, "effective_parameters": {},
                          "startup_evidence": {}, "error_code": "ADAPTER_EXCEPTION",
                          "error_summary": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
