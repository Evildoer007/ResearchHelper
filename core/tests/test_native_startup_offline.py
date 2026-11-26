"""Compile the production startup renderer without importing or running AppKit."""

import hashlib
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "result/acceptance/native-startup"
SOURCE = ROOT / "products/app/desktop/macos/ResearchHelperApp.swift"


def render_documents():
    source = SOURCE.read_text()
    function = source.split("func researchHelperStartupHTML(", 1)[1].split("// One independent App runtime", 1)[0]
    function = "func researchHelperStartupHTML(" + function
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="swift-renderer-", dir=OUT) as temporary:
        folder = Path(temporary)
        resources = folder / "resources"
        wordmark = resources / "app/web/login/wordmark.svg"
        wordmark.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / "products/app/frontend/login/wordmark.svg", wordmark)
        probe = folder / "renderer.swift"
        probe.write_text("import Foundation\n" + function + "\n" + '''
        let resources = URL(fileURLWithPath: ''' + json.dumps(str(resources)) + ''')
        var documents: [String: String] = [:]
        for theme in ["light", "dark", "untrusted-value"] {
            documents[theme] = try researchHelperStartupHTML(resources: resources, theme: theme)
        }
        do {
            _ = try researchHelperStartupHTML(resources: resources.appendingPathComponent("missing-startup-fixture"), theme: "light")
            fatalError("Missing packaged identity was accepted")
        } catch { }
        let data = try JSONSerialization.data(withJSONObject: documents, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
        ''')
        binary = folder / "renderer"
        build = runpy.run_path(str(ROOT / "packaging/app/build_app.py"))
        build["swift_options"].__globals__["OUTPUT"] = folder / "compiler-cache"
        flags = build["swift_options"]()
        subprocess.run(["xcrun", "swiftc", *flags, str(probe), "-o", str(binary)],
                       check=True, capture_output=True, timeout=180)
        value = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
        return json.loads(value.stdout)


class NativeStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = render_documents()
        cls.source = SOURCE.read_text()

    def test_packaged_identity_is_used_without_redrawing(self):
        original = (ROOT / "products/app/frontend/login/wordmark.svg").read_text()
        for document in self.documents.values():
            self.assertIn(original, document)
            self.assertIn('role="status"', document)
            self.assertIn('aria-label="正在启动ResearchHelper"', document)

    def test_appearance_is_validated_and_theme_does_not_change_logo(self):
        self.assertIn('data-theme="dark"', self.documents["dark"])
        self.assertIn('data-theme="light"', self.documents["light"])
        self.assertEqual(self.documents["untrusted-value"], self.documents["light"])
        self.assertIn('UserDefaults.standard.set(preference, forKey: "researchhelper.nativeThemePreference")', self.source)
        self.assertIn('["light", "dark"].contains(theme)', self.source)

    def test_boot_document_is_passive_and_network_is_denied(self):
        for document in self.documents.values():
            self.assertIn("default-src 'none'; style-src 'unsafe-inline'", document)
            self.assertNotIn("<script", document)
            self.assertNotIn("animation:", document)
            self.assertNotIn("https://", document)
            self.assertNotIn("<input", document)

    def test_login_is_shielded_before_first_paint_and_retains_timeouts(self):
        self.assertIn("location.protocol === 'http:' && ['/', '/login/index.html'].includes(location.pathname)", self.source)
        self.assertIn("document.documentElement.classList.add('login-boot');", self.source)
        self.assertIn("injectionTime: .atDocumentStart", self.source)
        self.assertNotIn("padding:72px", self.source)
        self.assertIn("withTimeInterval: 25", self.source)
        self.assertIn("DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: watchdog)", self.source)
        self.assertIn("login document owns the single launch animation", self.source)
        self.assertNotIn('url.scheme == "file"', self.source)

    def test_native_theme_receivers_are_registered_validated_and_released(self):
        self.assertIn('configuration.userContentController.add(self, name: "researchhelperTheme")', self.source)
        self.assertIn('message.webView === webView, message.frameInfo.isMainFrame', self.source)
        self.assertIn('["light", "dark", "auto"].contains(preference)', self.source)
        self.assertEqual(self.source.count('removeScriptMessageHandler(forName: "researchhelperTheme")'), 2)
        self.assertIn("attributeFilter:['data-theme']", self.source)
        self.assertIn('UserDefaults.standard.set(preference, forKey: "researchhelper.nativeThemePreference")', self.source)
        self.assertIn("if (!['light', 'dark', 'auto'].includes(localStorage.getItem('researchhelper.theme')))", self.source)
        html = (ROOT / "products/app/frontend/index.html").read_text()
        self.assertIn('<script src="login/theme-bootstrap.js"></script>', html)
        self.assertNotIn("document.documentElement.dataset.theme='light'", html)

    def test_auto_appearance_inherits_appkit_instead_of_fixing_webkit_media(self):
        self.assertIn('let appearance = savedPreference == "auto" ? nil : NSAppearance', self.source)
        self.assertIn('let appearance = preference == "auto" ? nil : NSAppearance', self.source)
        self.assertEqual(self.source.count('window.appearance = appearance'), 2)
        self.assertEqual(self.source.count('applyNativeTheme(theme, preference: preference)'), 2)


def save_documents():
    documents = render_documents()
    record = {"documents": documents, "renderer": "compiled_production_swift_function",
        "appkit_launched": False, "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("products/app/desktop/macos/ResearchHelperApp.swift", "products/app/frontend/login/wordmark.svg",
                         "core/tests/test_native_startup_offline.py")}}
    (OUT / "documents.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
    return record


if __name__ == "__main__":
    unittest.main()
