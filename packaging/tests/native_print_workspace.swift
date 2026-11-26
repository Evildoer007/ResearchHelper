// Exercise the real File menu entry against a disposable, offline workbench.
import AppKit
import WebKit
import ObjectiveC.runtime

extension NSWindow {
    @objc dynamic func rh_testKeepPrintPreviewHidden(_ sender: Any?) {
        // Intercept showing the preview only within this background probe.
    }
}

@main
struct NativeWorkspacePrintProbe {
    static func run(_ base: URL, _ output: URL, _ etf: String, _ updated: String) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let windowMethod = class_getInstanceMethod(NSWindow.self, #selector(NSWindow.makeKeyAndOrderFront(_:)))!
        let hiddenMethod = class_getInstanceMethod(NSWindow.self, #selector(NSWindow.rh_testKeepPrintPreviewHidden(_:)))!
        let printMethod = class_getInstanceMethod(WKWebView.self, #selector(WKWebView.printOperation(with:)))!
        let captureMethod = class_getInstanceMethod(WKWebView.self, #selector(WKWebView.rh_testPrintOperation(with:)))!
        method_exchangeImplementations(windowMethod, hiddenMethod)
        method_exchangeImplementations(printMethod, captureMethod)
        defer {
            method_exchangeImplementations(printMethod, captureMethod)
            method_exchangeImplementations(windowMethod, hiddenMethod)
        }

        let owner = ResearchHelperApp()
        owner.runtimeURL = base
        owner.window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                                styleMask: [.titled, .closable], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        defer {
            for preview in Array(owner.reportWindows.keys) { preview.close() }
            owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close()
        }
        let view = owner.webView!
        let check = HiddenPrintProbe.check
        func evaluate(_ script: String) throws -> Any? {
            try HiddenPrintProbe.evaluate(view, script)
        }
        func until(_ script: String) throws {
            try HiddenPrintProbe.wait(25, until: { (try? evaluate(script)) as? Bool == true })
        }
        func next(_ predicate: @escaping () -> Bool) throws {
            try HiddenPrintProbe.wait(20, until: predicate)
        }
        func settle() { RunLoop.main.run(until: Date().addingTimeInterval(0.55)) }
        func fault(_ value: String) throws {
            try value.write(to: output.appendingPathComponent("fault.txt"), atomically: true, encoding: .utf8)
        }
        func printPDF(_ name: String, from reader: WKWebView? = nil) throws {
            let calls = PrintCapture.calls
            PrintCapture.destination = output.appendingPathComponent(name + ".pdf")
            if let reader { owner.printFrom(reader) }
            else {
                owner.printCurrent()
                owner.printCurrent()  // A double press must still create one sheet.
            }
            try next { PrintCapture.calls > calls }
            try HiddenPrintProbe.wait(45, until: { owner.printOperations.isEmpty && owner.pendingPrints.isEmpty })
            let bytes = try FileManager.default.attributesOfItem(atPath: PrintCapture.destination.path)[.size] as? NSNumber
            try check(name + " is a single completed native print", PrintCapture.calls == calls + 1 && (bytes?.intValue ?? 0) > 1024)
            try check(name + " preview remains hidden", NSApp.keyWindow == nil && owner.reportWindows.keys.allSatisfy { !$0.isVisible })
            try check(name + " leaves only the independent reader", owner.reportWindows.count == 1)
        }

        view.load(URLRequest(url: base))
        try next { owner.workspaceControlsReady }
        _ = try evaluate("document.querySelector('#task-list [data-task=\"\(etf)\"]').click()")
        try until("selected?.id === '\(etf)' && !selectingTaskId && !artifactsOpen")
        owner.printCurrent()
        settle()
        try check("the real File menu cannot print from Chat", owner.reportWindows.isEmpty && PrintCapture.calls == 0)

        _ = try evaluate("document.querySelector('[data-mode=desk]').click();document.querySelector('#view-3').click()")
        try until("!!window.ResearchHelperNative.printRequest()")
        let printURL = try evaluate("window.ResearchHelperNative.printRequest().url") as? String
        let target = URL(string: printURL ?? "")
        let digest = try evaluate("window.ResearchHelperNative.printRequest().digest") as? String
        try check("visible standard report supplies the exact edition",
                  target?.path == "/api/tasks/\(etf)/report/1" && target?.query == "profile=standard" && digest?.count == 64)

        let readerURL = base.appendingPathComponent("reports/semi.html")
        guard let readerWindow = owner.prepareReport(readerURL, printing: false, persistent: false),
              let reader = owner.reportWindows[readerWindow] else {
            throw NSError(domain: "Independent reader unavailable", code: 1)
        }
        try next { reader.url == readerURL && !reader.isLoading && !reader.isHidden }
        _ = try HiddenPrintProbe.evaluate(reader, "document.documentElement.dataset.readerPosition = 'open'")

        func rejectSubstitutedReport(_ faultName: String) throws {
            try fault(faultName)
            let calls = PrintCapture.calls
            owner.printCurrent()
            try next { owner.reportWindows.count > 1 || reader.url != readerURL }
            try check("workspace print leaves the open reading window in place",
                      owner.reportWindows.count == 2 && reader.url == readerURL && !reader.isHidden)
            try next {
                let hit = try? String(contentsOf: output.appendingPathComponent("fault-hit.txt"), encoding: .utf8)
                return hit == faultName && owner.reportWindows.count == 1
            }
            settle()
            try check(faultName + " cannot open a print sheet or leave an orphan view",
                      PrintCapture.calls == calls && reader.url == readerURL &&
                        owner.printViews.isEmpty && owner.pendingPrints.isEmpty)
        }
        try rejectSubstitutedReport("wrong-document")
        try rejectSubstitutedReport("wrong-route")
        try rejectSubstitutedReport("wrong-profile")

        try fault("slow-document")
        let priorPrints = PrintCapture.calls
        owner.printCurrent()
        try next {
            (try? String(contentsOf: output.appendingPathComponent("fault-hit.txt"), encoding: .utf8)) == "slow-document"
                && owner.reportWindows.count == 2
        }
        view.reload()
        try next { owner.reportWindows.count == 1 }
        try until("!!window.ResearchHelperNative.printRequest()")
        settle()
        try check("workspace reload cancels an in-flight print and preserves the reader",
                  PrintCapture.calls == priorPrints && owner.reportWindows.count == 1 && reader.url == readerURL && !reader.isHidden)
        try fault("normal")

        let selection = try evaluate("""
            const visuals = document.querySelector('#report-frame').contentWindow.RHVisuals;
            const item = visuals.contexts().find(row=>row.compatible.some(scheme=>scheme!==row.scheme));
            if(!item) throw Error('A compatible alternate chart is required');
            const scheme = item.compatible.find(scheme=>scheme!==item.scheme);
            window.rhProbeChart = {id:item.target,scheme,original:item.scheme};
            visuals.applyProfile({theme:'porcelain',schemes:{[item.target]:scheme}});
            JSON.stringify(window.rhProbeChart)
            """) as? String ?? ""
        guard let choice = try JSONSerialization.jsonObject(with: Data(selection.utf8)) as? [String: String],
              let chart = choice["id"], let scheme = choice["scheme"], let initial = choice["original"] else {
            throw NSError(domain: "Chart selection was not applied", code: 1)
        }
        try printPDF("custom")
        let printed = PrintCapture.state["printed_profile"] as? String ?? ""
        let profile = try JSONSerialization.jsonObject(with: Data(printed.utf8)) as? [String: Any]
        try check("native print applied the visible palette and chart scheme",
                  profile?["theme"] as? String == "porcelain" &&
                    (profile?["schemes"] as? [String: String])?[chart] == scheme)
        PrintCapture.state["applied_chart"] = ["id": chart, "scheme": scheme, "initial": initial]

        // Simulate reopening the saved standard report without a chart override.
        _ = try evaluate("""
            delete draft.reportStyles?.[selected.id+':'+shownReport(selected).version];
            const frame=document.querySelector('#report-frame');
            frame.replaceWith(frame.cloneNode(false));installReader();
            """)
        try until("window.ResearchHelperNative.printRequest()?.presentation?.theme === 'research'")
        try printPDF("default")
        let defaultProfile = try JSONSerialization.jsonObject(with:
            Data((PrintCapture.state["printed_profile"] as? String ?? "").utf8)) as? [String: Any]
        try check("a new print starts with the selected report's original style",
                  defaultProfile?["theme"] as? String == "research" &&
                    (defaultProfile?["schemes"] as? [String: String])?[chart] == nil)

        _ = try evaluate("document.querySelector('#task-list [data-task=\"\(updated)\"]').click()")
        try until("selected?.id === '\(updated)' && !selectingTaskId")
        _ = try evaluate("document.querySelector('#view-3').click()")
        try until("!!window.ResearchHelperNative.printRequest()")
        _ = try evaluate("document.querySelector('#content [data-report-profile=detailed]').click()")
        try until("window.ResearchHelperNative.printRequest()?.url.endsWith('/report/2?profile=detailed') === true")
        try rejectSubstitutedReport("wrong-edition")
        try fault("normal")
        try printPDF("version-two")
        try check("the exact detailed edition was printed",
                  PrintCapture.state["printed_path"] as? String == "/api/tasks/\(updated)/report/2" &&
                    PrintCapture.state["printed_query"] as? String == "profile=detailed")
        let readerPosition = try HiddenPrintProbe.evaluate(reader, "document.documentElement.dataset.readerPosition") as? String
        try check("the separate reader kept its original document and page state",
                  reader.url == readerURL && !reader.isHidden && readerPosition == "open")
        try check("the workspace print sheet belongs to the initiating workbench",
                  PrintCapture.state["sheet_path"] as? String == "/")
        try printPDF("reader", from: reader)
        try check("printing an independent report keeps its original sheet owner",
                  PrintCapture.state["sheet_path"] as? String == readerURL.path && reader.url == readerURL)
        try check("only normal native print jobs were intercepted",
                  PrintCapture.originalPanels == [true, true, true, true] &&
                    PrintCapture.state["panel_suppression_verified"] as? Bool == true &&
                    PrintCapture.state["pdf_save_disposition_verified"] as? Bool == true &&
                    NSApp.activationPolicy() == .prohibited)
    }

    static func main() {
        var status = "failed"
        var error: String?
        do {
            guard CommandLine.arguments.count == 5, let base = URL(string: CommandLine.arguments[1]),
                  base.scheme == "http", base.host == "127.0.0.1" else { throw NSError(domain: "Loopback workbench required", code: 1) }
            try run(base, URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true),
                    CommandLine.arguments[3], CommandLine.arguments[4])
            status = "passed"
        } catch let failure { error = String(describing: failure) }
        var result: [String: Any] = ["status": status, "checks": HiddenPrintProbe.checks,
            "print_operations": PrintCapture.calls, "diagnostic": PrintCapture.state,
            "foreground_opened": false, "system_panels_shown": false, "physical_job_sent": false]
        if let error { result["error"] = error }
        FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]))
        if status != "passed" { exit(1) }
    }
}
