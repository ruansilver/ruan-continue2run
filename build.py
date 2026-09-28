#!/usr/bin/env python3
"""把源码 Skill 同步成"可独立安装分发"的产物目录。

    src/ruan-continue2run/   →   dist/ruan-continue2run/

产物目录只放运行 Skill 需要的文件：SKILL.md、scripts/、references/。
源码目录里的开发维护文件（README.md、design/ 等）不会进产物——产物是给 DSH 加载的，不是给人读的。

产物目录每次都是**清空重建**，所以不要手动改 dist/：改源码，然后重跑本脚本。

用法：
    python build.py                 # 根目录下的默认路径
    python build.py --check         # 只检查产物是否与源码一致/是否过期，不写文件
"""
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_NAME = "ruan-continue2run"
SRC = ROOT / "src" / SKILL_NAME
DIST = ROOT / "dist" / SKILL_NAME

# 运行 Skill 需要的内容（其余源码文件一律不进产物）
RUNTIME_ITEMS = ("SKILL.md", "scripts", "references")
# 复制时一律跳过的东西
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "README.md", "design", "install.py", "build.py")


def _newest_mtime(root: Path):
    """只统计"运行内容"的时间：README/design 之类的开发文件改动不该让产物显得过期。"""
    newest = 0.0
    for item in RUNTIME_ITEMS:
        p = root / item
        targets = [p] if p.is_file() else (p.rglob("*") if p.is_dir() else [])
        for f in targets:
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                newest = max(newest, f.stat().st_mtime)
    return newest


def check():
    """返回 (是否最新, 说明列表)。"""
    notes = []
    if not DIST.exists():
        return False, [f"产物目录不存在：{DIST}"]
    src_missing = [i for i in RUNTIME_ITEMS if not (SRC / i).exists()]
    if src_missing:
        notes.append(f"源码目录缺少：{', '.join(src_missing)}")
    src_time, dist_time = _newest_mtime(SRC), _newest_mtime(DIST)
    if src_time > dist_time:
        notes.append("源码比产物新，产物可能过期（重跑 python build.py）")
    return (not notes), notes


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = p.parse_args()

    if not SRC.is_dir():
        print(f"源码目录不存在：{SRC}")
        return 2

    if args.check:
        ok, notes = check()
        print(f"源码：{SRC}\n产物：{DIST}")
        print("产物是最新的。" if ok else "检查结果：")
        for n in notes:
            print("  - " + n)
        return 0 if ok else 1

    missing = [i for i in RUNTIME_ITEMS if not (SRC / i).exists()]
    if missing:
        print(f"源码目录缺少运行内容：{', '.join(missing)}")
        return 2

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    for item in RUNTIME_ITEMS:
        s, d = SRC / item, DIST / item
        if s.is_dir():
            shutil.copytree(s, d, ignore=IGNORE)
        else:
            shutil.copy2(s, d)

    skipped = sorted(
        x.name for x in SRC.iterdir()
        if x.name not in RUNTIME_ITEMS and x.name != "__pycache__"
    )
    print(f"源码：{SRC}")
    print(f"产物：{DIST}\n")
    print("产物内容：")
    for f in sorted(DIST.rglob("*")):
        if f.is_file():
            print("  " + str(f.relative_to(DIST)))
    if skipped:
        print("\n未进产物（开发维护文件）：" + ", ".join(skipped))
    print("\n完成。分发时直接打包/复制 dist/ruan-continue2run/ 即可；装到本机用 python install.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
