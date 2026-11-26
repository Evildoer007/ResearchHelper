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
        PrintCapture.state["sheet_path"] = (window.contentView as? WKWebView)?.url?.path
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
        PrintCapture.state["page_geometry"] = ["width": info.paperSize.width, "height": info.paperSize.height,
            "left": info.leftMargin, "right": info.rightMargin, "top": info.topMargin, "bottom": info.bottomMargin,
            "scale": info.scalingFactor, "centered_x": info.isHorizontallyCentered, "centered_y": info.isVerticallyCentered]
        let settings = info.dictionary()
        PrintCapture.state["vertical_pagination"] = info.verticalPagination.rawValue
        PrintCapture.state["all_pages"] = settings[NSPrintInfo.AttributeKey.allPages] as? Bool ?? true
        PrintCapture.state["first_page"] = settings[NSPrintInfo.AttributeKey.firstPage] as? Int ?? 0
        PrintCapture.state["last_page"] = settings[NSPrintInfo.AttributeKey.lastPage] as? Int ?? 0
#if RESEARCHHELPER_WORKSPACE_PRINT_TEST
        PrintCapture.state["printed_path"] = url?.path
        PrintCapture.state["printed_query"] = url?.query
        PrintCapture.state["print_view_window"] = window?.windowNumber
        PrintCapture.state["printed_profile"] = try? HiddenPrintProbe.evaluate(self, "JSON.stringify(RHVisuals.profile())")
#endif
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
    static func run(_ base: URL, _ output: URL, route: String = "reports/semi.html") throws {
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
        let target = base.appendingPathComponent(route)
        let retained = route == "reports/retained.html"
        let originalPrintInfo = NSPrintInfo.shared
        if retained {
            let inherited = originalPrintInfo.copy() as! NSPrintInfo
            inherited.paperSize = NSSize(width: 612, height: 792)
            inherited.orientation = .landscape
            inherited.leftMargin = 72; inherited.rightMargin = 72
            inherited.topMargin = 72; inherited.bottomMargin = 72
            inherited.scalingFactor = 0.5
            inherited.isHorizontallyCentered = true; inherited.isVerticallyCentered = true
            let scenario = ProcessInfo.processInfo.environment["RH_TEST_PRINT_PREFERENCES"] ?? "range"
            if scenario == "clip" { inherited.verticalPagination = .clip }
            if scenario == "range" {
                let settings = inherited.dictionary()
                settings[NSPrintInfo.AttributeKey.allPages] = false
                settings[NSPrintInfo.AttributeKey.firstPage] = 1
                settings[NSPrintInfo.AttributeKey.lastPage] = 1
            }
            NSPrintInfo.shared = inherited
        }
        defer { NSPrintInfo.shared = originalPrintInfo }
        let sharedSize = NSPrintInfo.shared.paperSize
        let sharedRange = NSPrintInfo.shared.dictionary()[NSPrintInfo.AttributeKey.allPages] as? Bool
        let info = owner.reportPrintInfo()
        // AppKit normalizes registered A4 stock to integral printer points.
        try check("report starts in A4 portrait despite inherited preferences",
            abs(info.paperSize.width - 595.2756) < 0.5 && abs(info.paperSize.height - 841.8898) < 0.5 && info.orientation == .portrait)
        try check("report uses the PDF export's seven millimeter margins",
            [info.leftMargin, info.rightMargin, info.topMargin, info.bottomMargin].allSatisfy { abs($0 - 19.8425) < 0.01 })
        try check("report keeps CSS sizing and top-left alignment",
            info.scalingFactor == 1 && !info.isHorizontallyCentered && !info.isVerticallyCentered)
        let settings = info.dictionary()
        try check("report starts with every page and automatic vertical pagination",
            info.verticalPagination == .automatic && settings[NSPrintInfo.AttributeKey.allPages] as? Bool == true &&
            settings[NSPrintInfo.AttributeKey.selectionOnly] as? Bool != true)
        try check("creating a report job does not alter shared printer preferences",
            NSPrintInfo.shared.paperSize == sharedSize && (!retained || NSPrintInfo.shared.scalingFactor == 0.5) &&
            NSPrintInfo.shared.dictionary()[NSPrintInfo.AttributeKey.allPages] as? Bool == sharedRange)
        guard let window = owner.prepareReport(target, printing: false, persistent: false),
              let view = owner.reportWindows[window] else { throw NSError(domain: "owned preview missing", code: 1) }
        defer { window.close() }
        try wait(30, until: { !view.isLoading && view.url == target })
        var chartReady = false
        let chartDeadline = Date().addingTimeInterval(20)
        while !chartReady && Date() < chartDeadline {
            chartReady = (try evaluate(view, "document.querySelectorAll('.example-chart svg,.chart svg').length === 5") as? Bool) == true
            if !chartReady { RunLoop.main.run(until: Date().addingTimeInterval(0.1)) }
        }
        try check("five actual offline charts rendered in WebKit", chartReady)
        try check("owned report remains hidden", !window.isVisible && NSApp.keyWindow == nil)
        try check("controller accepts the same actual report URL", owner.isReportURL(target))
        let mainText = try evaluate(view, "document.querySelector('main').innerText") as? String ?? ""
        try mainText.write(to: output.appendingPathComponent("main-text.txt"), atomically: true, encoding: .utf8)
        let blocks = try evaluate(view, "JSON.stringify([...document.querySelector('main').querySelectorAll('h1,h2,h3,p,figcaption')].filter(n=>n.getClientRects().length).map(n=>n.textContent))") as? String ?? "[]"
        try blocks.write(to: output.appendingPathComponent("client-blocks.json"), atomically: true, encoding: .utf8)
        let figures = try evaluate(view, "JSON.stringify(window.RH_EXAMPLE_FIGURES || JSON.parse(document.getElementById('rh-publication-document')?.textContent || '{}'))") as? String ?? "[]"
        let variants = retained ? ["closed-reader"] : ["closed-reader", "open-reader"]
        for (index, name) in variants.enumerated() {
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
                PrintCapture.state["pending_after_wait"] = owner.pendingPrints[view] != nil
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
        try check("expected production controller operations captured", PrintCapture.calls == variants.count)
        try check("print never changes disclosed figure data", (try evaluate(view, "JSON.stringify(window.RH_EXAMPLE_FIGURES || JSON.parse(document.getElementById('rh-publication-document')?.textContent || '{}'))") as? String) == figures)
        try check("test suppressed both panel types before native run", PrintCapture.state["panel_suppression_verified"] as? Bool == true)
        try check("test selected PDF save, not a physical printer", PrintCapture.state["pdf_save_disposition_verified"] as? Bool == true)
        try check("production requested its normal panels before test interception", PrintCapture.originalPanels == Array(repeating: true, count: variants.count))
        try check("application never activates", NSApp.activationPolicy() == .prohibited)
    }
    static func main() {
        do {
            guard [3, 4].contains(CommandLine.arguments.count), let base = URL(string: CommandLine.arguments[1]),
                  base.scheme == "http", base.host == "127.0.0.1" else { throw NSError(domain: "local fixture required", code: 1) }
            let route = CommandLine.arguments.count == 4 ? CommandLine.arguments[3] : "reports/semi.html"
            guard ["reports/semi.html", "reports/retained.html"].contains(route) else { throw NSError(domain: "registered local report required", code: 1) }
            try run(base, URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true), route: route)
            let data = try JSONSerialization.data(withJSONObject: ["status": "passed", "checks": checks,
                "native_controller_used": true, "print_operations": PrintCapture.calls,
                "page_geometry": PrintCapture.state["page_geometry"] ?? [:],
                "diagnostic": PrintCapture.state,
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

#if !RESEARCHHELPER_WORKSPACE_PRINT_TEST
@main
struct HiddenPrintMain {
    static func main() { HiddenPrintProbe.main() }
}
#endif
