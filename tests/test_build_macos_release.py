from pathlib import Path

import pytest

from tools.build_macos_release import verify_app


def _touch(root: Path, relative: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test")


def test_verify_macos_app_accepts_complete_bundle(tmp_path: Path) -> None:
    _touch(tmp_path, "Contents/MacOS/ResearchHelper")
    _touch(tmp_path, "Contents/Frameworks/QtWebEngineProcess")
    _touch(tmp_path, "Contents/Resources/qtwebengine_resources.pak")
    _touch(tmp_path, "Contents/Resources/assets/vendor/echarts.min.js")
    verify_app(tmp_path)


def test_verify_macos_app_reports_missing_resource(tmp_path: Path) -> None:
    _touch(tmp_path, "Contents/MacOS/ResearchHelper")
    with pytest.raises(RuntimeError, match="QtWebEngineProcess"):
        verify_app(tmp_path)


def test_macos_spec_excludes_unrelated_machine_learning_stacks() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "ResearchHelper-macOS.spec").read_text(encoding="utf-8")
    for package in ("tensorflow", "torch", "jax", "shapely", "sklearn", "cv2"):
        assert f'"{package}"' in spec
    assert '"matplotlib": {"backends": ["Agg"]}' in spec


def test_macos_builder_clears_pyinstaller_cache() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools" / "build_macos_release.py").read_text(encoding="utf-8")
    assert '"PyInstaller", "--clean", "--noconfirm"' in source
