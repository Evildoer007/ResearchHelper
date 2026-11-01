// A new hidden acceptance of the production save result and WebKit event path.
import AppKit
import WebKit

@main
struct NativeArtifactCompletion {
    static var checks: [String] = []
    static func check(_ name: String, _ condition: Bool) throws {
        guard condition else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let end = Date().addingTimeInterval(15)
        while !condition() && Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "WebKit timeout", code: 1) }
    }
    static func script(_ text: String, in view: WKWebView) throws -> Any? {
        var done = false, result: Any?, error: Error?
        view.evaluateJavaScript(text) { result = $0; error = $1; done = true }
        try wait { done }
        if let error { throw error }
        return result
    }
    static func run(_ origin: URL, output: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = origin
        owner.window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1024, height: 768), styleMask: [.titled], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.webView = owner.createWebView(persistent: false)
        owner.webView.navigationDelegate = nil
        owner.window.contentView = owner.webView
        defer {
            owner.cancelFilePanels()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close()
        }
        owner.webView.load(URLRequest(url: origin))
        try wait { owner.webView.url == origin && !owner.webView.isLoading }
        owner.workspaceControlsReady = true
        _ = try script("window.saveResults=[];window.addEventListener('researchhelper:artifact-saved',e=>saveResults.push(e.detail))", in: owner.webView)
        let data = Data("<!doctype html><html lang=\"zh\"><body>贵州茅台报告</body></html>".utf8)
        let file = output.appendingPathComponent("原生保存报告.html")
        let saved = owner.artifactSaveResult(operationID: "qa-save", name: "默认报告.html", response: .OK, data: data, destination: file)
        try check("atomic save followed by exact disk readback", saved["status"] as? String == "saved" && (try Data(contentsOf: file)) == data)
        owner.artifactSaveFeedback(saved, generation: 0)
        try wait { (try? script("saveResults.length", in: owner.webView)) as? Int == 1 }
        let event = try script("saveResults[0]", in: owner.webView) as! [String: Any]
        try check("Renamed Unicode filename and operation ID reach actual WebKit event", event["operation_id"] as? String == "qa-save" && event["name"] as? String == file.lastPathComponent && event["status"] as? String == "saved")
        let cancelled = owner.artifactSaveResult(operationID: "qa-cancel", name: "不保存.html", response: .cancel, data: data, destination: output.appendingPathComponent("不保存.html"))
        try check("cancel does not create a file", cancelled["status"] as? String == "cancelled" && !FileManager.default.fileExists(atPath: output.appendingPathComponent("不保存.html").path))
        let failed = owner.artifactSaveResult(operationID: "qa-fail", name: "失败.html", response: .OK, data: data, destination: output.appendingPathComponent("missing/失败.html"))
        try check("write failure never reports saved", failed["status"] as? String == "failed" && failed["error"] != nil)
        let noURL = owner.artifactSaveResult(operationID: nil, name: "未选择.html", response: .OK, data: data, destination: nil)
        try check("missing destination is a failure", noURL["status"] as? String == "failed" && noURL["operation_id"] == nil)
        owner.receiveArtifactSave(["action": "save", "operation_id": "qa-invalid", "name": "无效.exe", "text": "bad"], sourceView: owner.webView, sourceURL: origin, mainFrame: true)
        try wait { (try? script("saveResults.length", in: owner.webView)) as? Int == 2 }
        try check("invalid artifact emits rejection without a panel", (try script("saveResults[1].status", in: owner.webView)) as? String == "rejected" && owner.activeSavePanel == nil)
        let safe: [String: Any] = ["action": "save", "operation_id": "qa-busy", "name": "报告.html", "text": "<p>有效</p>"]
        owner.activeOpenPanel = NSOpenPanel()
        owner.receiveArtifactSave(safe, sourceView: owner.webView, sourceURL: origin, mainFrame: true)
        try wait { (try? script("saveResults.length", in: owner.webView)) as? Int == 3 }
        try check("another file window rejects overlapping save", (try script("saveResults[2].operation_id", in: owner.webView)) as? String == "qa-busy" && owner.activeSavePanel == nil)
        owner.cancelFilePanels()
        owner.receiveArtifactSave(safe, sourceView: owner.webView, sourceURL: origin, mainFrame: false)
        owner.artifactSaveFeedback(saved, generation: 99)
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        try check("subframe and stale navigation cannot dispatch completion", (try script("saveResults.length", in: owner.webView)) as? Int == 3)
        try check("test never presents a native window", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited)
        let receipt: [String: Any] = ["status": "passed", "checks": checks, "production_store_touched": false, "native_window_presented": false, "model_calls": 0, "data_requests": 0]
        let encoded = try JSONSerialization.data(withJSONObject: receipt, options: [.prettyPrinted, .sortedKeys])
        try encoded.write(to: output.appendingPathComponent("native-save-receipt.json"))
        print(String(data: encoded, encoding: .utf8)!)
    }
    static func main() {
        do { try run(URL(string: CommandLine.arguments[1])!, output: URL(fileURLWithPath: CommandLine.arguments[2])) }
        catch { fputs("\(error)\n", stderr); exit(1) }
    }
}
