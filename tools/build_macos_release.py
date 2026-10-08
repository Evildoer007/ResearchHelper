"""在macOS上构建、验收、签名并可选公证Research Helper。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP = DIST / "ResearchHelper.app"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
DMG = DIST / f"ResearchHelper-{VERSION}-macOS.dmg"


def run(command: list[str], *, env: dict[str, str] | None = None,
        timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=ROOT, env=env, timeout=timeout, check=True,
        text=True, encoding="utf-8", errors="replace",
    )


def preflight_runtime() -> None:
    run([
        sys.executable, "-c",
        "import PySide6.QtCore, PySide6.QtWidgets, PySide6.QtWebEngineWidgets; "
        "import matplotlib, pandas, requests; print('macOS release preflight OK')",
    ], timeout=60)


def verify_app(app: Path = APP) -> None:
    executable = app / "Contents" / "MacOS" / "ResearchHelper"
    if not executable.is_file():
        raise RuntimeError(f"macOS应用缺少主程序：{executable}")
    names = {path.name for path in app.rglob("*") if path.is_file()}
    required = {"QtWebEngineProcess", "qtwebengine_resources.pak", "echarts.min.js"}
    missing = sorted(required - names)
    if missing:
        raise RuntimeError("macOS应用缺少运行资源：" + "、".join(missing))


def smoke_app(app: Path = APP) -> None:
    executable = app / "Contents" / "MacOS" / "ResearchHelper"
    with tempfile.TemporaryDirectory(prefix="research-helper-macos-smoke-") as data_root:
        env = dict(os.environ)
        env["RESEARCH_HELPER_DATA_ROOT"] = data_root
        run([str(executable), "--smoke-imports"], env=env, timeout=90)
        run([str(executable), "--smoke-dispatch"], env=env, timeout=90)
        run([str(executable), "--cli", "--optionhelper"], env=env, timeout=90)
        run([str(executable), "--doctor"], env=env, timeout=90)


def sign_app(identity: str) -> None:
    run([
        "codesign", "--force", "--deep", "--options", "runtime", "--timestamp",
        "--entitlements", str(ROOT / "packaging" / "macos" / "entitlements.plist"),
        "--sign", identity, str(APP),
    ], timeout=600)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(APP)])


def create_dmg() -> None:
    if DMG.exists():
        DMG.unlink()
    with tempfile.TemporaryDirectory(prefix="research-helper-dmg-") as folder:
        staging = Path(folder)
        shutil.copytree(APP, staging / APP.name, symlinks=True)
        (staging / "Applications").symlink_to("/Applications")
        run([
            "hdiutil", "create", "-volname", "Research Helper",
            "-srcfolder", str(staging), "-ov", "-format", "UDZO", str(DMG),
        ], timeout=1200)


def notarize(profile: str) -> None:
    run([
        "xcrun", "notarytool", "submit", str(DMG),
        "--keychain-profile", profile, "--wait",
    ], timeout=1800)
    run(["xcrun", "stapler", "staple", str(DMG)], timeout=300)
    run(["xcrun", "stapler", "validate", str(DMG)], timeout=300)


def write_checksum() -> Path:
    digest = hashlib.sha256(DMG.read_bytes()).hexdigest().upper()
    target = DIST / "SHA256SUMS-macOS.txt"
    target.write_text(f"{digest}  {DMG.name}\n", encoding="ascii")
    return target


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("macOS发布包必须在macOS上构建。")
    preflight_runtime()
    if os.environ.get("RESEARCH_HELPER_SKIP_TESTS") != "1":
        run([sys.executable, "-m", "pytest", "-q", "tests"], timeout=1200)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    shutil.rmtree(APP, ignore_errors=True)
    run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
        "ResearchHelper-macOS.spec",
    ], timeout=1800)
    verify_app()
    smoke_app()
    identity = str(os.environ.get("APPLE_CODESIGN_IDENTITY") or "").strip()
    if identity:
        sign_app(identity)
    create_dmg()
    profile = str(os.environ.get("APPLE_NOTARY_PROFILE") or "").strip()
    if profile:
        if not identity:
            raise RuntimeError("公证要求先设置APPLE_CODESIGN_IDENTITY完成Developer ID签名。")
        notarize(profile)
    checksum = write_checksum()
    print(f"macOS release: {DMG}")
    print(f"checksum: {checksum}")
    if not identity:
        print("warning: 未设置APPLE_CODESIGN_IDENTITY，本次仅生成内部测试包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
