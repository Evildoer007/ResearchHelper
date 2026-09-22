import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core import app_paths, process_launcher
from render.layout import _echarts_assets


class ReleasePathTests(unittest.TestCase):
    def test_explicit_user_data_root_wins(self) -> None:
        expected = Path.cwd() / "tmp" / "release-user"
        with patch.dict(os.environ, {"RESEARCH_HELPER_DATA_ROOT": str(expected)}):
            self.assertEqual(app_paths.user_data_root(), expected.resolve())

    def test_explicit_resource_root_wins(self) -> None:
        expected = Path.cwd() / "tmp" / "release-resource"
        with patch.dict(os.environ, {"RESEARCH_HELPER_RESOURCE_ROOT": str(expected)}):
            self.assertEqual(app_paths.resource_root(), expected.resolve())

    def test_source_research_command_uses_main_script(self) -> None:
        with patch.object(process_launcher, "IS_FROZEN", False):
            program, arguments = process_launcher.research_command(["-b", "机器人"])
        self.assertTrue(program.lower().endswith("python.exe") or "python" in program.lower())
        self.assertEqual(arguments[-2:], ["-b", "机器人"])
        self.assertTrue(arguments[0].endswith("main.py"))

    def test_frozen_worker_uses_executable_dispatcher(self) -> None:
        with patch.object(process_launcher, "IS_FROZEN", True):
            _program, arguments = process_launcher.internal_worker_command("search-test")
        self.assertEqual(arguments, ["--worker", "search-test"])

    def test_customer_html_has_no_install_relative_echarts_path(self) -> None:
        script = _echarts_assets()
        self.assertIn("data:application/javascript;base64,", script)
        self.assertNotIn("../assets/vendor", script)


if __name__ == "__main__":
    unittest.main()
