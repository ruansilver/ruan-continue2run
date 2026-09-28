#!/usr/bin/env python3
"""把**安装产物目录**装到 DSH 的一个技能根目录。

    dist/ruan-continue2run/   →   <DSH_HOME>/skills/ruan-continue2run/

本脚本只做"安装"，不碰源码：产物由 build.py 从 src/ 生成。
改源码之后的顺序是：python build.py  →  python install.py

产物目录里凡是开发维护文件（README.md、design/、install.py、build.py、__pycache__）
都不会被复制进技能目录。

用法：
    python install.py                       # 装到 <DSH_HOME>/skills/ruan-continue2run
    python install.py --dest <目录>          # 装到别的技能根（例如项目级 <仓库根>/.dsh/skills/...）
    python install.py --product <目录>       # 从别的产物目录装（默认 dist/ruan-continue2run）
    python install.py --allow-any-name      # 目标目录名不是 ruan-continue2run 时才需要
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILL_NAME = "ruan-continue2run"
PRODUCT = ROOT / "dist" / SKILL_NAME
SRC = ROOT / "src" / SKILL_NAME

DEV_ONLY = {"README.md", "install.py", "build.py", "design", "__pycache__"}
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "README.md", "install.py", "build.py", "design")
RUNTIME_ITEMS = ("SKILL.md", "scripts", "references")


def default_dest():
    home = os.environ.get("DSH_HOME") or str(Path.home() / ".dsh")
    return Path(home) / "skills" / SKILL_NAME


def _newest_mtime(root: Path):
    """只统计"运行内容"的时间：README 之类的开发文件改动不该让产物显得过期。"""
    newest = 0.0
    for item in RUNTIME_ITEMS:
        p = root / item
        targets = [p] if p.is_file() else (p.rglob("*") if p.is_dir() else [])
        for f in targets:
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                newest = max(newest, f.stat().st_mtime)
    return newest


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dest", help="目标技能目录；缺省为 <DSH_HOME>/skills/ruan-continue2run")
    p.add_argument("--product", help="产物目录；缺省为 dist/ruan-continue2run")
    p.add_argument("--allow-any-name", action="store_true", help="允许目标目录名不是 ruan-continue2run")
    args = p.parse_args()

    product = Path(args.product).expanduser() if args.product else PRODUCT
    dest = Path(args.dest).expanduser() if args.dest else default_dest()

    if not product.is_dir():
        print(f"产物目录不存在：{product}\n先跑：python build.py")
        return 2

    if dest.name != SKILL_NAME and not args.allow_any_name:
        print(f"目标目录名不是 {SKILL_NAME}：{dest}\n安装会先清空目标目录，确认无误后加 --allow-any-name 重跑。")
        return 2

    if SRC.is_dir() and _newest_mtime(SRC) > _newest_mtime(product):
        print("提醒：源码比产物新，可能装了旧版本。建议先跑：python build.py\n")

    strays = sorted(x.name for x in product.iterdir() if x.name in DEV_ONLY)
    if strays:
        print("提醒：产物目录里出现了不该有的开发维护文件（本次不会复制它们）：" + ", ".join(strays) + "\n")

    print(f"产物目录: {product}")
    print(f"安装到  : {dest}")

    if dest.exists():
        shutil.rmtree(dest)
        print("已清空旧版本。")
    dest.mkdir(parents=True)

    copied = []
    for item in sorted(product.iterdir()):
        if item.name in DEV_ONLY:
            continue
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=IGNORE)
        else:
            if item.suffix == ".pyc":
                continue
            shutil.copy2(item, target)
        copied.append(item.name)

    print("\n安装内容：")
    for f in sorted(dest.rglob("*")):
        if f.is_file():
            print("  " + str(f.relative_to(dest)))
    if not copied:
        print("  （空：产物目录里没有可安装的内容）")
    print("\n完成。DSH 会在下一个模型步刷新技能目录，不需要重启。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
