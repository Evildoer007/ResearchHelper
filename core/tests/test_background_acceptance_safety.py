"""Independent regression: no App launch or desktop action without consent."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent


def load_test_helper(name):
    spec = importlib.util.spec_from_file_location(name, TESTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules):
        safety_spec = importlib.util.spec_from_file_location("acceptance_safety", TESTS / "acceptance_safety.py")
        safety = importlib.util.module_from_spec(safety_spec)
        safety_spec.loader.exec_module(safety)
        sys.modules["acceptance_safety"] = safety
        spec.loader.exec_module(module)
    return module


class BackgroundAcceptanceSafetyTests(unittest.TestCase):
    def test_native_app_launch_is_blocked_before_subprocess(self):
        module = load_test_helper("rebuild_package_acceptance")
        with patch.dict(os.environ, {}, clear=True), patch.object(module.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "前台测试默认禁用"):
                module.native_start(Path("/Applications/ResearchHelper.app"), TESTS, None)
            launch.assert_not_called()

    def test_package_test_cannot_launch_app_by_accident(self):
        module = load_test_helper("rebuild_package_acceptance")
        with patch.dict(os.environ, {}, clear=True), patch.object(module.subprocess, "run") as command:
            with self.assertRaisesRegex(RuntimeError, "前台测试默认禁用"):
                module.verify()
            command.assert_not_called()

    def test_desktop_keyboard_accessibility_and_capture_are_blocked(self):
        module = load_test_helper("native_ui_tools")
        operations = (lambda: module.control("press", index=1),
                      lambda: module.keys(code=35, modifiers=("command",)),
                      lambda: module.screenshot("blocked.png"))
        for operation in operations:
            with self.subTest(operation=operation), patch.dict(os.environ, {}, clear=True), \
                    patch.object(module.subprocess, "run") as command, \
                    patch.object(module.subprocess, "check_output") as lookup:
                with self.assertRaisesRegex(RuntimeError, "前台测试默认禁用"):
                    operation()
                command.assert_not_called()
                lookup.assert_not_called()

    def test_headless_browser_acceptance_is_explicit(self):
        source = (TESTS / "rebuild_chat_port_acceptance.mjs").read_text()
        self.assertIn("headless:true", source)
        self.assertNotIn("headless:false", source)
        self.assertNotIn("native_start(", source)


if __name__ == "__main__":
    unittest.main()
