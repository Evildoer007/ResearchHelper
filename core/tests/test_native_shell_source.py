"""Source-level guards for isolation and native integration, without launching AppKit."""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[2] / "products/app/desktop/macos/ResearchHelperApp.swift").read_text()


class NativeShellContractTests(unittest.TestCase):
    def test_service_owns_default_store(self):
        self.assertNotIn('environment["RESEARCHHELPER_STORE"] =', SOURCE)
        # Product names in design comments do not constitute shared storage.
        for reference in ('/OptionHelper/', 'OptionHelper.app', 'com.optionhelper.app'):
            self.assertNotIn(reference, SOURCE)
        self.assertIn('resources.appendingPathComponent("backend/researchhelper-server/researchhelper-server")', SOURCE)
        self.assertIn('process.currentDirectoryURL = resources', SOURCE)

    def test_shutdown_and_output_cannot_wait_indefinitely(self):
        self.assertNotIn('waitUntilExit()', SOURCE)
        self.assertNotIn('kill(process.processIdentifier', SOURCE)
        self.assertIn('process.standardInput = lifetime', SOURCE)
        self.assertIn('try? lifetime?.close()', SOURCE)
        self.assertIn('DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: deadline)', SOURCE)
        self.assertIn('NSApp.reply(toApplicationShouldTerminate: true)', SOURCE)
        self.assertIn('errors.fileHandleForReading.readabilityHandler', SOURCE)
        self.assertIn('withTimeInterval: 25', SOURCE)

    def test_print_and_export_follow_initiating_window(self):
        self.assertIn('printFrom(sourceView)', SOURCE)
        self.assertIn('reportWindows[$0]', SOURCE)
        printing = SOURCE.split('func printReport(_ webView: WKWebView,', 1)[1].split('@objc func printDidRun', 1)[0]
        self.assertIn('guard let owner = webView.window, reportWindows[owner] === webView', printing)
        self.assertIn('self.reportWindows[owner] === webView', printing)
        self.assertIn('beforeprint', SOURCE)
        self.assertIn('afterprint', SOURCE)
        self.assertIn('webView.printOperation(with: self.reportPrintInfo())', printing)
        self.assertIn('operation.runModal(for: sheetOwner', printing)
        self.assertNotIn('operation.run()', SOURCE)
        self.assertIn('printOperations.removeValue(forKey: operation)', SOURCE)

    def test_artifact_save_requires_the_current_workspace_and_preserves_its_owner(self):
        saving = SOURCE.split('func receiveArtifactSave(', 1)[1].split('func canPrint(', 1)[0]
        self.assertIn('sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/"', saving)
        self.assertIn('panel.beginSheetModal(for: window)', saving)
        self.assertIn('self.activeSavePanel === panel', saving)
        self.assertIn('token == self.navigationState.generation', saving)

    def test_report_close_releases_handlers(self):
        self.assertIn('reportWindows.removeValue(forKey: closed)', SOURCE)
        self.assertIn('removeScriptMessageHandler(forName: "researchhelper")', SOURCE)

    def test_native_controls_and_zoom_are_independent(self):
        self.assertIn('NSApp.applicationIconImage = icon', SOURCE)
        self.assertIn('theme == "dark" ? "ResearchHelperDockDark" : "ResearchHelperDock"', SOURCE)
        self.assertIn('.fullSizeContentView', SOURCE)
        self.assertIn('[0.8, 0.9, 1, 1.1, 1.25, 1.4]', SOURCE)
        self.assertIn('view.pageZoom = scale', SOURCE)
        self.assertNotIn('style.zoom', SOURCE)
        self.assertIn('applicationShouldHandleReopen', SOURCE)


if __name__ == "__main__":
    unittest.main()
