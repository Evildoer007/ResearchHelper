from pathlib import Path

from tools.build_macos_source_bundle import EXCLUDED_TOP_LEVEL, included_files


def test_macos_source_bundle_excludes_local_state_and_keeps_build_inputs() -> None:
    files = {path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
             for path in included_files()}
    assert "ResearchHelper-macOS.spec" in files
    assert "requirements-macos.txt" in files
    assert "tools/build_macos_release.py" in files
    assert "config.local.json" not in files
    assert not any(path.split("/", 1)[0] in EXCLUDED_TOP_LEVEL for path in files)
    assert not any("__pycache__" in path for path in files)
