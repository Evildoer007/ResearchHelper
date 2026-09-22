"""VS Code's script runner must preserve the project import root."""

import sys
from pathlib import Path
from unittest.mock import patch

from gui import start


def test_current_python_starts_project_module() -> None:
    current = Path(sys.executable).resolve()
    with patch.object(start, "_candidates", return_value=[current]), \
            patch.object(start, "_has_pyside", return_value=True), \
            patch.object(start.runpy, "run_module") as run_module:
        start.main()
    run_module.assert_called_once_with("launcher", run_name="__main__")
    assert str(start.ROOT) in sys.path


def test_other_python_starts_from_project_root() -> None:
    candidate = Path("C:/different/python.exe")
    with patch.object(start, "_candidates", return_value=[candidate]), \
            patch.object(start, "_has_pyside", return_value=True), \
            patch.object(start.subprocess, "Popen") as popen:
        start.main()
    popen.assert_called_once_with(
        [str(candidate), "-m", "launcher"], cwd=start.ROOT,
    )
