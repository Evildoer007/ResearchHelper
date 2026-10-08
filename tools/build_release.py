"""构建 Research Helper Windows 内部发布版、清单和可分发 ZIP。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def run(command: list[str], *, env: dict[str, str] | None = None,
        timeout: int | None = None) -> None:
    subprocess.run(command, cwd=ROOT, check=True, env=env, timeout=timeout)


def preflight_runtime() -> None:
    """在耗时构建前确认当前解释器可以加载桌面端关键依赖。"""
    run([
        sys.executable, "-c",
        (
            "from PySide6.QtCore import qVersion; "
            "from PySide6.QtWidgets import QApplication; "
            "from PySide6.QtWebEngineWidgets import QWebEngineView; "
            "import matplotlib, pandas, requests; "
            "print('release preflight OK, Qt', qVersion())"
        ),
    ], timeout=60)


def verify_runtime_files(app: Path) -> None:
    required = {
        "Qt 平台插件": "qwindows.dll",
        "Qt WebEngine 进程": "QtWebEngineProcess.exe",
        "Qt WebEngine 资源": "qtwebengine_resources.pak",
        "离线 ECharts": "echarts.min.js",
    }
    missing = [label for label, name in required.items()
               if not any(app.rglob(name))]
    if missing:
        raise RuntimeError("发布包缺少运行资源：" + "、".join(missing))


def smoke_frozen_app(app: Path) -> None:
    """用隔离数据目录验证冻结入口、Qt 导入和环境检查。"""
    smoke_root = BUILD / "release-smoke-data"
    if smoke_root.exists():
        shutil.rmtree(smoke_root)
    smoke_root.mkdir(parents=True)
    env = dict(os.environ)
    env["RESEARCH_HELPER_DATA_ROOT"] = str(smoke_root)
    executable = app / "ResearchHelper.exe"
    try:
        run([str(executable), "--smoke-imports"], env=env, timeout=60)
        run([str(executable), "--smoke-dispatch"], env=env, timeout=60)
        # 走一次真正的 ``--cli -> _internal/main.py`` 分派，但故意不给
        # OptionHelper 子命令，让 main.py 在完成导入后立即返回。这样既不取数，
        # 又能拦截曾经把内部 CLI 错指向 runtime_source/main.py 的发布缺陷。
        run([str(executable), "--cli", "--optionhelper"], env=env, timeout=60)
        run([str(executable), "--doctor"], env=env, timeout=60)
        report = smoke_root / "output" / "environment-check.json"
        if not report.is_file():
            raise RuntimeError("冻结版未生成环境检查报告")
        payload = json.loads(report.read_text(encoding="utf-8"))
        if Path(payload.get("resource_root", "")).resolve() == ROOT.resolve():
            raise RuntimeError("冻结版仍在读取源码目录")
        checks = payload.get("checks") or {}
        if not (checks.get("user_data_writable") or {}).get("ok"):
            raise RuntimeError("冻结版用户数据目录不可写")
        if not (checks.get("echarts_asset") or {}).get("ok"):
            raise RuntimeError("冻结版未找到离线 ECharts 资源")
    finally:
        shutil.rmtree(smoke_root, ignore_errors=True)


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
    local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app_data:
        names.append(Path(local_app_data) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    for path in names:
        try:
            if path.is_file():
                return path
        except OSError:
            # 受限运行环境可能看得到用户级目录但无权读取；继续检查其余位置，
            # 由调用方给出“未检测到安装器”的明确结果。
            continue
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--installer", action="store_true", help="检测到 Inno Setup 时同时生成安装器")
    parser.add_argument("--reuse-build", action="store_true", help="保留 PyInstaller 缓存，仅重建变更部分")
    args = parser.parse_args()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    preflight_runtime()
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
    verify_runtime_files(app)
    smoke_frozen_app(app)
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
            run([
                str(iscc), f"/DMyAppVersion={version}",
                str(ROOT / "packaging" / "windows" / "ResearchHelper.iss"),
            ])
            print(f"安装器目录：{DIST / 'installer'}")


if __name__ == "__main__":
    main()
