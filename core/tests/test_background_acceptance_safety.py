"""Independent regression: no App launch or desktop action without consent."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[1]


def load_test_helper(name, directory=TESTS):
    spec = importlib.util.spec_from_file_location(name, directory / (name + ".py"))
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
        module = load_test_helper("accept_installed_resdesk_current", ROOT / "tests")
        with patch.dict(os.environ, {}, clear=True), patch.object(module.subprocess, "Popen") as launch, \
                patch.object(module.subprocess, "run") as compile_or_control, \
                patch.object(module.subprocess, "check_output") as process_lookup, \
                patch.object(module.sqlite3, "connect") as production_store:
            with self.assertRaisesRegex(RuntimeError, "前台测试默认禁用"):
                module.run()
            launch.assert_not_called()
            compile_or_control.assert_not_called()
            process_lookup.assert_not_called()
            production_store.assert_not_called()

    def test_existing_foreground_flag_requires_explicit_one(self):
        from core.tests.acceptance_safety import require_foreground_consent
        for value in ('', '0', 'true', 'yes'):
            with self.subTest(value=value), patch.dict(os.environ, {'RESEARCHHELPER_ALLOW_FOREGROUND_TESTS':value}, clear=True):
                with self.assertRaisesRegex(RuntimeError, '前台测试默认禁用'):
                    require_foreground_consent()
        with patch.dict(os.environ, {'RESEARCHHELPER_ALLOW_FOREGROUND_TESTS':'1'}, clear=True):
            self.assertIsNone(require_foreground_consent())

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

    def test_native_port_entry_is_blocked_before_receipt_reads_or_ui_actions(self):
        module=load_test_helper("test_native_installed_port", ROOT / "packaging/tests")
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(module.Path,"read_text") as read_receipt, \
                patch.object(module.native.subprocess,"run") as command, \
                patch.object(module.native.subprocess,"check_output") as lookup:
            with self.assertRaisesRegex(RuntimeError,"前台测试默认禁用"):
                module.run()
            read_receipt.assert_not_called()
            command.assert_not_called()
            lookup.assert_not_called()

    def test_headless_browser_acceptance_is_explicit(self):
        source = (ROOT / "tests/accept_packaged_oh_alignment.mjs").read_text()
        self.assertRegex(source, r"chromium\.launch\(\{[^}]*headless\s*:\s*true")
        self.assertNotRegex(source, r"headless\s*:\s*false")
        self.assertNotIn("native_start(", source)


if __name__ == "__main__":
    unittest.main()
