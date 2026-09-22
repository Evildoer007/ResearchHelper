"""构建 Research Helper Windows 内部发布版、清单和可分发 ZIP。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest(folder: Path, version: str) -> Path:
    files = []
    target = folder / "release-manifest.json"
    for path in sorted(item for item in folder.rglob("*") if item.is_file() and item != target):
        files.append({
            "path": path.relative_to(folder).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256(path),
        })
    target.write_text(json.dumps({
        "product": "Research Helper",
        "version": version,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def zip_folder(folder: Path, version: str) -> Path:
    target = DIST / f"ResearchHelper-{version}-win64.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(item for item in folder.rglob("*") if item.is_file()):
            archive.write(path, Path("ResearchHelper") / path.relative_to(folder))
    return target


def find_iscc() -> Path | None:
    names = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    return next((path for path in names if path.is_file()), None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--installer", action="store_true", help="检测到 Inno Setup 时同时生成安装器")
    parser.add_argument("--reuse-build", action="store_true", help="保留 PyInstaller 缓存，仅重建变更部分")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not args.skip_tests:
        run([sys.executable, "-m", "pytest", "-q", "tests"])
    if not args.reuse_build:
        for target in (BUILD, DIST):
            if target.exists():
                shutil.rmtree(target)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "ResearchHelper.spec"])
    app = DIST / "ResearchHelper"
    forbidden = [app / "config.local.json", app / "sources", app / "output", app / ".optionhelper"]
    leaked = [str(path) for path in forbidden if path.exists()]
    if leaked:
        raise RuntimeError("发布包混入本机秘密或运行数据：" + "、".join(leaked))
    if not (app / "ResearchHelper.exe").is_file():
        raise RuntimeError("未生成 ResearchHelper.exe")
    manifest_path = manifest(app, version)
    archive = zip_folder(app, version)
    print(f"发布目录：{app}")
    print(f"文件清单：{manifest_path}")
    print(f"便携发布包：{archive}")
    if args.installer:
        iscc = find_iscc()
        if iscc is None:
            print("未安装 Inno Setup 6；已保留 ZIP 发布包和 .iss 安装脚本。")
        else:
            run([str(iscc), str(ROOT / "packaging" / "windows" / "ResearchHelper.iss")])
            print(f"安装器目录：{DIST / 'installer'}")


if __name__ == "__main__":
    main()
