from pathlib import Path

import pytest

from tools.build_release import find_iscc, verify_runtime_files


def _touch(root: Path, relative: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"test")


def test_verify_runtime_files_accepts_complete_bundle(tmp_path: Path) -> None:
    _touch(tmp_path, "_internal/PySide6/plugins/platforms/qwindows.dll")
    _touch(tmp_path, "_internal/PySide6/QtWebEngineProcess.exe")
    _touch(tmp_path, "_internal/PySide6/resources/qtwebengine_resources.pak")
    _touch(tmp_path, "_internal/assets/vendor/echarts.min.js")

    verify_runtime_files(tmp_path)


def test_verify_runtime_files_reports_missing_runtime_asset(tmp_path: Path) -> None:
    _touch(tmp_path, "qwindows.dll")
    _touch(tmp_path, "QtWebEngineProcess.exe")
    _touch(tmp_path, "qtwebengine_resources.pak")

    with pytest.raises(RuntimeError, match="离线 ECharts"):
        verify_runtime_files(tmp_path)


def test_find_iscc_supports_per_user_install(tmp_path: Path, monkeypatch) -> None:
    compiler = tmp_path / "Programs" / "Inno Setup 6" / "ISCC.exe"
    _touch(tmp_path, "Programs/Inno Setup 6/ISCC.exe")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert find_iscc() == compiler
