// Exercise the actual controller while redirecting only the owned print job
// to PDF. No production binary, system input or printer preference is changed.
import AppKit
import WebKit
import ObjectiveC.runtime

enum PrintCapture {
    static var destination: URL!
    static var calls = 0
    static var originalPanels: [Bool] = []
    static var state: [String: Any] = [:]
    static var redirectedClasses: Set<String> = []
}

extension NSPrintOperation {
    @objc dynamic func rh_testRunModal(for window: NSWindow, delegate: Any?, didRun selector: Selector?, contextInfo: UnsafeMutableRawPointer?) {
        PrintCapture.originalPanels.append(showsPrintPanel && showsProgressPanel)
        PrintCapture.calls += 1
        showsPrintPanel = false
        showsProgressPanel = false
        printInfo.jobDisposition = .save
        printInfo.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = PrintCapture.destination
        PrintCapture.state["panel_suppression_verified"] = !showsPrintPanel && !showsProgressPanel
        PrintCapture.state["pdf_save_disposition_verified"] = printInfo.jobDisposition == .save
        // Swapped implementation now calls the real AppKit asynchronous job.
        rh_testRunModal(for: window, delegate: delegate, didRun: selector, contextInfo: contextInfo)
    }
}

extension WKWebView {
    @objc dynamic func rh_testPrintOperation(with info: NSPrintInfo) -> NSPrintOperation {
        let operation = rh_testPrintOperation(with: info)
        let actual: AnyClass = type(of: operation)
        let name = NSStringFromClass(actual)
        if !PrintCapture.redirectedClasses.contains(name) {
            let originalSelector = #selector(NSPrintOperation.runModal(for:delegate:didRun:contextInfo:))
            let aliasSelector = #selector(NSPrintOperation.rh_testRunModal(for:delegate:didRun:contextInfo:))
            let original = class_getInstanceMethod(actual, originalSelector)!
            let redirect = class_getInstanceMethod(NSPrintOperation.self, aliasSelector)!
            let originalIMP = method_getImplementation(original)
            let encoding = method_getTypeEncoding(original)
            // WebKit uses its own operation subclass. Override that class's
            // selector rather than assuming the base implementation is called.
            class_addMethod(actual, aliasSelector, originalIMP, encoding)
            class_replaceMethod(actual, originalSelector, method_getImplementation(redirect), encoding)
            PrintCapture.redirectedClasses.insert(name)
        }
        PrintCapture.state["actual_operation_class"] = name
        return operation
    }
}

@main
struct HiddenPrintProbe {
    static var checks: [String] = []
    static func check(_ name: String, _ condition: Bool) throws {
        guard condition else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ seconds: Double, until predicate: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(seconds)
        while !predicate() && Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.025))
        }
        guard predicate() else { throw NSError(domain: "bounded native operation timed out", code: 1) }
    }
    static func evaluate(_ view: WKWebView, _ script: String) throws -> Any? {
        var complete = false, result: Any?, failure: Error?
        view.evaluateJavaScript(script) { value, error in result = value; failure = error; complete = true }
        try wait(10, until: { complete })
        if let failure = failure { throw failure }
        return result
    }
    static func run(_ base: URL, _ output: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let original = class_getInstanceMethod(WKWebView.self,
            #selector(WKWebView.printOperation(with:)))!
        let redirected = class_getInstanceMethod(WKWebView.self,
            #selector(WKWebView.rh_testPrintOperation(with:)))!
        method_exchangeImplementations(original, redirected)
        defer { method_exchangeImplementations(original, redirected) }
        let owner = ResearchHelperApp()
        owner.runtimeURL = base
        let target = base.appendingPathComponent("reports/semi.html")
        guard let window = owner.prepareReport(target, printing: false, persistent: false),
              let view = owner.reportWindows[window] else { throw NSError(domain: "owned preview missing", code: 1) }
        defer { window.close() }
        try wait(30, until: { !view.isLoading && view.url == target })
        var chartReady = false
        let chartDeadline = Date().addingTimeInterval(20)
        while !chartReady && Date() < chartDeadline {
            chartReady = (try evaluate(view, "document.querySelectorAll('.example-chart svg').length === 5") as? Bool) == true
            if !chartReady { RunLoop.main.run(until: Date().addingTimeInterval(0.1)) }
        }
        try check("five actual offline charts rendered in WebKit", chartReady)
        try check("owned report remains hidden", !window.isVisible && NSApp.keyWindow == nil)
        try check("controller accepts the same actual report URL", owner.isReportURL(target))
        let mainText = try evaluate(view, "document.querySelector('main').innerText") as? String ?? ""
        try mainText.write(to: output.appendingPathComponent("main-text.txt"), atomically: true, encoding: .utf8)
        let blocks = try evaluate(view, "JSON.stringify([...document.querySelector('main').querySelectorAll('h1,h2,h3,p,figcaption')].filter(n=>n.getClientRects().length).map(n=>n.textContent))") as? String ?? "[]"
        try blocks.write(to: output.appendingPathComponent("client-blocks.json"), atomically: true, encoding: .utf8)
        let figures = try evaluate(view, "JSON.stringify(window.RH_EXAMPLE_FIGURES)") as? String ?? "[]"
        for (index, name) in ["closed-reader", "open-reader"].enumerated() {
            if index == 1 {
                _ = try evaluate(view, "document.querySelector('a[href=\"#history-source\"]').click()")
                try check("original inspector opened before second print", (try evaluate(view,
                    "document.getElementById('report-inspector').open") as? Bool) == true)
            }
            PrintCapture.destination = output.appendingPathComponent(name + ".pdf")
            PrintCapture.state = ["view_attached_to_owner": view.window === window,
                "owner_has_view": owner.reportWindows[window] === view,
                "loading": view.isLoading, "failed_load": owner.failedReportLoads.contains(view)]
            owner.printReport(view)
            do { try wait(10, until: { !owner.printOperations.isEmpty }) }
            catch {
                PrintCapture.state["pending_after_wait"] = owner.pendingPrints.contains(view)
                PrintCapture.state["operation_count"] = owner.printOperations.count
                throw error
            }
            try wait(45, until: { owner.printOperations.isEmpty && owner.pendingPrints.isEmpty })
            let attributes = try FileManager.default.attributesOfItem(atPath: PrintCapture.destination.path)
            try check("native asynchronous PDF exists: " + name, (attributes[.size] as? NSNumber)?.intValue ?? 0 > 1024)
            try check("native preview stays hidden after print: " + name, !window.isVisible && NSApp.keyWindow == nil)
            if index == 1 {
                try check("afterprint preserves the selected original", (try evaluate(view,
                    "document.getElementById('report-inspector').open") as? Bool) == true)
            }
        }
        try check("two production controller operations captured", PrintCapture.calls == 2)
        try check("print never changes disclosed figure data", (try evaluate(view, "JSON.stringify(window.RH_EXAMPLE_FIGURES)") as? String) == figures)
        try check("test suppressed both panel types before native run", PrintCapture.state["panel_suppression_verified"] as? Bool == true)
        try check("test selected PDF save, not a physical printer", PrintCapture.state["pdf_save_disposition_verified"] as? Bool == true)
        try check("production requested its normal panels before test interception", PrintCapture.originalPanels == [true, true])
        try check("application never activates", NSApp.activationPolicy() == .prohibited)
    }
    static func main() {
        do {
            guard CommandLine.arguments.count == 3, let base = URL(string: CommandLine.arguments[1]),
                  base.scheme == "http", base.host == "127.0.0.1" else { throw NSError(domain: "local fixture required", code: 1) }
            try run(base, URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true))
            let data = try JSONSerialization.data(withJSONObject: ["status": "passed", "checks": checks,
                "native_controller_used": true, "print_operations": PrintCapture.calls,
                "foreground_opened": false, "system_panels_shown": false, "physical_job_sent": false], options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
        } catch {
            let data = try! JSONSerialization.data(withJSONObject: ["status": "failed", "checks": checks,
                "error": String(describing: error), "print_operations": PrintCapture.calls,
                "diagnostic": PrintCapture.state,
                "foreground_opened": false, "system_panels_shown": false, "physical_job_sent": false], options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
            exit(1)
        }
    }
}
