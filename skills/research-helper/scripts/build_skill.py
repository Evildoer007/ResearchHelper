#!/usr/bin/env python3
"""Build a portable Research Helper Skill ZIP without local data or credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_ROOT.parent.parent
RUNTIME_DIRS = ("core", "llm", "render")
RUNTIME_FILES = (
    "main.py", "sector_groups.json", "THESIS_LIBRARY.md", "underlying_map.json", "VERSION",
)
SKILL_ITEMS = (
    "README.md", "SKILL.md", "VERSION", "requirements-skill.txt",
    "agents", "references", "scripts",
)
EXCLUDED_NAMES = {"dist", "runtime_source", "__pycache__", ".pytest_cache"}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in EXCLUDED_NAMES or name.endswith((".pyc", ".pyo"))}


def _copy_item(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, ignore=_ignore)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _manifest(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        values[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(SKILL_ROOT / "dist"))
    args = parser.parse_args()
    version = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    output_dir = Path(args.output_dir).resolve()
    stage = output_dir / f"research-helper-{version}" / "research-helper"
    archive = output_dir / f"research-helper-{version}.zip"

    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)
    for name in SKILL_ITEMS:
        _copy_item(SKILL_ROOT / name, stage / name)

    runtime = stage / "scripts" / "runtime_source"
    runtime.mkdir(parents=True)
    for name in RUNTIME_DIRS:
        _copy_item(PROJECT_ROOT / name, runtime / name)
    for name in RUNTIME_FILES:
        _copy_item(PROJECT_ROOT / name, runtime / name)
    vendor = PROJECT_ROOT / "assets" / "vendor"
    if vendor.is_dir():
        _copy_item(vendor, runtime / "assets" / "vendor")

    forbidden = [
        runtime / "config.local.json", runtime / "sources", runtime / "references",
        runtime / "output", runtime / "data", runtime / "result", runtime / ".optionhelper",
    ]
    if any(path.exists() for path in forbidden):
        raise RuntimeError("打包白名单异常：检测到本地配置、材料或运行数据")

    manifest = _manifest(stage)
    (stage / "manifest.json").write_text(
        json.dumps({"skill": "research-helper", "version": version, "files": manifest},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(item for item in stage.rglob("*") if item.is_file()):
            handle.write(path, Path("research-helper") / path.relative_to(stage))
    print(json.dumps({
        "ok": True,
        "skill_dir": str(stage),
        "archive": str(archive),
        "file_count": len(manifest),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
