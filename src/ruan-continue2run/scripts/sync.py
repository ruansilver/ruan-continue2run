#!/usr/bin/env python3
"""Maintenance-only whole-package synchronizer for ruan-continue2run.

The authoritative copy is the source. No version arbitration or merge is performed.
Use --dry-run to inspect formal files that would be replaced or deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

NAME = "ruan-continue2run"
SKIP_DIRS = {".ruan-continue2run", "__pycache__", ".pytest_cache", ".git"}
SKIP_FILES = {".DS_Store"}


def skip_file(name: str) -> bool:
    return name in SKIP_FILES or name.endswith(".pyc")


def manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for filename in files:
            if skip_file(filename):
                continue
            path = Path(directory) / filename
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def validate_root(path: Path, allow_missing: bool) -> str | None:
    if path.name != NAME:
        return f"path must end with '{NAME}'"
    if not path.exists() and allow_missing:
        return None
    if not path.is_dir() or not (path / "SKILL.md").is_file():
        return "path is not a valid ruan-continue2run Skill root containing SKILL.md"
    return None


def dangerous_relation(source: Path, target: Path) -> bool:
    return source in target.parents or target in source.parents


def diff_manifest(source: dict[str, str], target: dict[str, str]) -> dict[str, list[str]]:
    additions = sorted(set(source) - set(target))
    deletions = sorted(set(target) - set(source))
    replacements = sorted(name for name in set(source) & set(target) if source[name] != target[name])
    return {"add": additions, "replace": replacements, "delete": deletions}


def overwrite(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in list(target.iterdir()):
        if child.name in SKIP_DIRS or skip_file(child.name):
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for directory, dirs, files in os.walk(source):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        relative = Path(directory).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        for filename in files:
            if not skip_file(filename):
                shutil.copy2(Path(directory) / filename, target / relative / filename)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    error = validate_root(source, allow_missing=False)
    if error:
        print(json.dumps({"error_code": "INVALID_SOURCE", "error_summary": error}, ensure_ascii=False))
        raise SystemExit(2)
    wanted = manifest(source)
    results = []
    seen = {source}
    for raw_target in args.target:
        target = Path(raw_target).expanduser().resolve()
        if target in seen:
            results.append({"target": raw_target, "status": "skipped", "reason": "duplicate realpath"})
            continue
        seen.add(target)
        if target == source:
            results.append({"target": raw_target, "status": "skipped", "reason": "is the source"})
            continue
        if dangerous_relation(source, target):
            results.append({"target": raw_target, "status": "failed", "error_code": "DANGEROUS_PATH_RELATION",
                            "reason": "source and target may not be parent/child paths"})
            continue
        error = validate_root(target, allow_missing=True)
        if error:
            results.append({"target": raw_target, "status": "failed", "error_code": "INVALID_TARGET", "reason": error})
            continue
        current = manifest(target) if target.exists() else {}
        diff = diff_manifest(wanted, current)
        if args.dry_run:
            results.append({"target": str(target), "status": "dry_run", "diff": diff})
            continue
        try:
            overwrite(source, target)
            got = manifest(target)
            if got == wanted:
                results.append({"target": str(target), "status": "synced", "files": len(got), "diff": diff})
            else:
                mismatch = sorted(set(wanted) ^ set(got) | {name for name in wanted if name in got and wanted[name] != got[name]})
                results.append({"target": str(target), "status": "failed", "error_code": "VERIFY_MISMATCH",
                                "reason": "verification mismatch", "diff": mismatch[:50]})
        except Exception as exc:
            results.append({"target": str(target), "status": "failed", "error_code": "SYNC_FAILED",
                            "reason": f"{type(exc).__name__}: {exc}"})

    payload = {"authoritative": str(source), "dry_run": args.dry_run, "results": results}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(item["status"] == "failed" for item in results) else 0)


if __name__ == "__main__":
    main()
