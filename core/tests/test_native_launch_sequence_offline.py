"""Read-only launch ordering checks; never create AppKit windows or a backend."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "products/app/desktop/macos/ResearchHelperApp.swift"


def function_body(source, signature):
    """Return the target method up to the next same-indent Swift method."""
    start = source.index(signature)
    end = source.find("\n    }", start)
    return source[start:end + 6]


class NativeLaunchSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_configuration_does_not_present_a_pre_backend_document(self):
        body = function_body(self.source, "private func startMainWindow()")
        self.assertIn("startBackend()", body)
        for operation in ("makeKeyAndOrderFront", "NSApp.activate", "loadHTMLString", "webView.load("):
            self.assertNotIn(operation, body)

    def test_validated_handshake_precedes_window_and_single_login_navigation(self):
        body = function_body(self.source, "func startBackend()")
        steps = [
            'let address = message?["url"] as? String',
            'url.scheme == "http", url.host == "127.0.0.1", url.port != nil',
            "self.runtimeURL = url", "self.startupTimer?.invalidate()",
            'URLQueryItem(name: "app_startup", value: UUID().uuidString)',
            "self.window.makeKeyAndOrderFront(nil)",
            "NSApp.activate(ignoringOtherApps: true)", "self.webView.load(",
        ]
        positions = [body.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(body.count("self.webView.load("), 1)
        self.assertIn("!self.failed, !self.quitting", body)

    def test_timeout_and_early_errors_remain_visible_and_terminable(self):
        startup = function_body(self.source, "func startBackend()")
        self.assertIn("withTimeInterval: 25", startup)
        self.assertIn("self.stopBackend()", startup)
        for error in ("App资源不存在", "内置研究运行组件不存在", "内置运行组件无法启动",
                      "本地研究服务启动超时", "本地服务未返回有效启动状态"):
            self.assertIn(error, startup)
        failure = function_body(self.source, "func fail(_ message: String)")
        steps = ["failed = true", "startupTimer?.invalidate()", "window.makeKeyAndOrderFront(nil)",
                 "NSApp.activate(ignoringOtherApps: true)", "alert.beginSheetModal(for: window)"]
        positions = [failure.index(step) for step in steps]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("NSApp.terminate(nil)", failure)

    def test_dock_and_menu_reopen_cannot_expose_hidden_startup_window(self):
        reopen = function_body(self.source, "@objc func showMainWindow()")
        self.assertLess(reopen.index("guard runtimeURL != nil || failed"),
                        reopen.index("window.makeKeyAndOrderFront(nil)"))
        dock = function_body(self.source, "func applicationShouldHandleReopen(")
        self.assertIn("showMainWindow()", dock)
        self.assertNotIn("makeKeyAndOrderFront", dock)

    def test_recovery_still_removes_animation_token_and_keeps_origin_boundary(self):
        self.assertIn("safe?.query = nil", self.source)
        self.assertIn('return ["/", "/login/index.html"].contains(url.path)', self.source)
        self.assertIn("url.port == origin.port", self.source)
        self.assertIn("cancelNavigationWatchdog()", self.source)


if __name__ == "__main__":
    unittest.main()
