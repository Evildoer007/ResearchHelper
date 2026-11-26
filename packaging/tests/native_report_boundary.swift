// New acceptance for production window/panel logic; never present system UI.
import AppKit
import WebKit

@main
struct NativeReportBoundary {
  static var checks: [String] = []
  static func check(_ name: String, _ value: Bool) throws {
    guard value else { throw NSError(domain: name, code: 1) }
    checks.append(name)
  }
  static func wait(_ condition: () -> Bool) throws {
    let deadline = Date().addingTimeInterval(15)
    while !condition() && Date() < deadline {
      RunLoop.main.run(until: Date().addingTimeInterval(0.02))
    }
    try check("asynchronous WebKit condition reached", condition())
  }
  static func script(_ text: String, in view: WKWebView) throws -> Any? {
    var done = false
    var value: Any?
    var failure: Error?
    view.evaluateJavaScript(text) { answer, error in
      value = answer
      failure = error
      done = true
    }
    try wait { done }
    if let failure = failure { throw failure }
    return value
  }
  static func run(_ url: URL) throws {
    NSApplication.shared.setActivationPolicy(.prohibited)
    NSApp.finishLaunching()
    let owner = ResearchHelperApp()
    owner.runtimeURL = url
    owner.window = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860), styleMask: [.titled, .closable],
      backing: .buffered, defer: false)
    owner.window.isReleasedWhenClosed = false
    owner.webView = owner.createWebView(persistent: false)
    owner.window.contentView = owner.webView
    defer {
      for report in Array(owner.reportWindows.keys) { report.close() }
      owner.webView.stopLoading()
      owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
      owner.window.close()
    }
    owner.webView.load(URLRequest(url: url))
    try wait { owner.workspaceControlsReady }
    let reportURL = url.appendingPathComponent("reports/semi.html")
    let versionURL = url.appendingPathComponent("api/tasks/rh_1234/report/2")
    try check(
      "RH frozen report and historical paths accepted",
      owner.isReportURL(reportURL) && owner.isReportURL(versionURL))
    for (status, mime, accepted) in [
      (200, "text/html", true), (200, "application/xhtml+xml", true), (404, "text/html", false),
      (401, "text/html", false), (302, "text/html", false), (200, "application/json", false),
    ] {
      let response = HTTPURLResponse(
        url: reportURL, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": mime])!
      try check(
        "report HTTP response \(status) \(mime)", owner.validReportResponse(response) == accepted)
    }
    let foreignResponse = HTTPURLResponse(
      url: url, statusCode: 200, httpVersion: nil, headerFields: ["Content-Type": "text/html"])!
    try check(
      "a login or workbench page is not a report response",
      !owner.validReportResponse(foreignResponse))
    for rejected in [
      "/", "/login/index.html", "/api/settings/model", "/api/tasks/rh_1234/report/0",
      "/api/tasks/rh_1234/report/-1", "/api/tasks/rh_1234/report/2/extra",
      "/reports/a.html?download=unregistered", "/reports/../login/index.html",
    ] {
      try check(
        "non-report route rejected " + rejected,
        !owner.isReportURL(URL(string: rejected, relativeTo: url)!.absoluteURL))
    }
    try check(
      "foreign origin and embedded credentials rejected",
      !owner.isReportURL(URL(string: "http://127.0.0.1:1/reports/semi.html")!)
        && !owner.isReportURL(URL(string: "http://user@127.0.0.1:\(url.port!)/reports/semi.html")!))
    try check(
      "workbench print allowed only after ready",
      owner.canPrint(sourceView: owner.webView, sourceURL: url, mainFrame: true))
    try check(
      "subframe and login cannot print",
      !owner.canPrint(sourceView: owner.webView, sourceURL: reportURL, mainFrame: false)
        && !owner.canPrint(
          sourceView: owner.webView, sourceURL: url.appendingPathComponent("login/index.html"),
          mainFrame: true)
    )
    let content: [String: Any] = [
      "action": "save", "name": "研究报告_v2.html", "text": "<h1>冻结正文</h1>",
    ]
    let saved = owner.saveContent(
      content, sourceView: owner.webView, sourceURL: url, mainFrame: true)
    try check(
      "save preserves Unicode filename and exact body",
      saved?.0 == "研究报告_v2.html" && saved?.1 == "<h1>冻结正文</h1>")
    try check(
      "backup JSON supported",
      owner.saveContent(
        ["action": "save", "name": "任务.json", "text": "{}"], sourceView: owner.webView,
        sourceURL: url, mainFrame: true) != nil)
    for name in [
      "../报告.html", "path/报告.html", "path\\报告.html", "bad\n.html", "report.sh",
      String(repeating: "名", count: 90) + ".html", "",
    ] {
      var body = content
      body["name"] = name
      try check(
        "unsafe filename rejected " + String(name.prefix(20)),
        owner.saveContent(body, sourceView: owner.webView, sourceURL: url, mainFrame: true) == nil)
    }
    for text in ["", String(repeating: "字", count: 6_666_667)] {
      var body = content
      body["text"] = text
      try check(
        "empty or UTF8 oversized body rejected",
        owner.saveContent(body, sourceView: owner.webView, sourceURL: url, mainFrame: true) == nil)
    }
    try check(
      "subframe cannot save",
      owner.saveContent(content, sourceView: owner.webView, sourceURL: url, mainFrame: false) == nil
    )
    owner.workspaceControlsReady = false
    try check(
      "not-ready workbench cannot save or choose files",
      owner.saveContent(content, sourceView: owner.webView, sourceURL: url, mainFrame: true) == nil
        && owner.makeOpenPanel(
          sourceView: owner.webView, sourceURL: url, mainFrame: true, allowsMultiple: true,
          allowsDirectories: false) == nil
    )
    owner.workspaceControlsReady = true
    let multiple = owner.makeOpenPanel(
      sourceView: owner.webView, sourceURL: url, mainFrame: true, allowsMultiple: true,
      allowsDirectories: false)!
    let single = owner.makeOpenPanel(
      sourceView: owner.webView, sourceURL: url, mainFrame: true, allowsMultiple: false,
      allowsDirectories: true)!
    try check(
      "actual AppKit panel follows input multiple/files flags",
      multiple.allowsMultipleSelection && multiple.canChooseFiles && !multiple.canChooseDirectories
        && !multiple.canCreateDirectories && multiple.resolvesAliases)
    try check(
      "actual AppKit panel follows single/directory flags",
      !single.allowsMultipleSelection && single.canChooseDirectories)
    try check("file panels remain unshown", !multiple.isVisible && !single.isVisible)
    owner.activeOpenPanel = multiple
    try check(
      "an existing file panel blocks another chooser",
      owner.makeOpenPanel(
        sourceView: owner.webView, sourceURL: url, mainFrame: true, allowsMultiple: true,
        allowsDirectories: false) == nil)
    owner.cancelFilePanels()
    try check(
      "cancel clears chooser ownership without showing it",
      owner.activeOpenPanel == nil && !multiple.isVisible)
    owner.activeSavePanel = NSSavePanel()
    try check(
      "an existing save panel blocks a file chooser",
      owner.makeOpenPanel(
        sourceView: owner.webView, sourceURL: url, mainFrame: true, allowsMultiple: true,
        allowsDirectories: false) == nil)
    owner.cancelFilePanels()
    try check("cancel clears save ownership", owner.activeSavePanel == nil)
    try check(
      "subframe file selection denied",
      owner.makeOpenPanel(
        sourceView: owner.webView, sourceURL: url, mainFrame: false, allowsMultiple: true,
        allowsDirectories: false) == nil)
    let first = owner.prepareReport(reportURL, printing: false, persistent: false)!
    let view = owner.reportWindows[first]!
    let second = owner.prepareReport(reportURL, printing: false, persistent: false)!
    try check(
      "repeated report preview reuses one window while loading",
      first === second && owner.reportWindows.count == 1)
    let queued = owner.prepareReport(reportURL, printing: true, persistent: false)!
    try check(
      "print request reuses the already loading report",
      queued === first && owner.printViews.count == 1)
    owner.printViews.removeAll()
    try check(
      "report view uses nonpersistent test storage",
      !view.configuration.websiteDataStore.isPersistent)
    try wait { !view.isLoading && view.url == reportURL }
    try check(
      "owned report can print but cannot save or choose files",
      owner.canPrint(sourceView: view, sourceURL: reportURL, mainFrame: true)
        && owner.saveContent(content, sourceView: view, sourceURL: reportURL, mainFrame: true)
          == nil
        && owner.makeOpenPanel(
          sourceView: view, sourceURL: reportURL, mainFrame: true, allowsMultiple: true,
          allowsDirectories: false) == nil
    )
    first.setFrame(NSRect(x: 107, y: 119, width: 820, height: 650), display: false)
    let positioned = first.frame
    let next = owner.prepareReport(versionURL, printing: false, persistent: false)!
    try check(
      "idle preview reused for another report",
      next === first && owner.reportTargets[view]?.url == versionURL)
    try check("preview reuse preserves the user's window position", next.frame == positioned)
    owner.printReport(view)
    try check("loading another version cannot print the old page", owner.pendingPrints.isEmpty)
    owner.printFrom(view)
    try check("print command queues the target being loaded", owner.printViews[view] != nil)
    owner.printViews.removeAll()
    try wait { !view.isLoading && view.url == versionURL }
    owner.webView(
      view, didFailProvisionalNavigation: nil,
      withError: NSError(domain: NSURLErrorDomain, code: NSURLErrorCancelled))
    try check(
      "benign navigation cancellation does not poison a loaded report",
      !owner.failedReportLoads.contains(view))
    owner.failedReportLoads.insert(view)
    owner.printReport(view)
    try check("failed report load cannot queue a print", owner.pendingPrints.isEmpty)
    let retried = owner.prepareReport(versionURL, printing: false, persistent: false)!
    try check(
      "same failed report can be reloaded without another window",
      retried === first && !owner.failedReportLoads.contains(view))
    try wait { !view.isLoading && view.url == versionURL }
    owner.printReport(view)
    owner.printReport(view)
    try check(
      "duplicate print is guarded before delay",
      owner.pendingPrints.count == 1 && owner.printOperations.isEmpty)
    first.close()
    try check(
      "closing preview clears ownership and queued print",
      owner.reportWindows.isEmpty && owner.reportTargets.isEmpty && owner.pendingPrints.isEmpty
        && owner.printViews.isEmpty)
    RunLoop.main.run(until: Date().addingTimeInterval(0.5))
    try check("closed document cannot later start a print operation", owner.printOperations.isEmpty)
    _ = try script(
      "{const frame=document.createElement('iframe');frame.src='/frame.html';frame.onload=()=>window.__frameReady=true;document.body.append(frame)};true",
      in: owner.webView)
    try wait { (try? script("window.__frameReady===true", in: owner.webView)) as? Bool == true }
    try check(
      "real subframe messages do not open save or report UI",
      owner.activeSavePanel == nil && owner.reportWindows.isEmpty && owner.pendingPrints.isEmpty)
    try check(
      "no native windows shown",
      !owner.window.isVisible && !first.isVisible && NSApp.activationPolicy() == .prohibited)
  }
  static func main() {
    do {
      try run(URL(string: CommandLine.arguments[1])!)
      FileHandle.standardOutput.write(
        try JSONSerialization.data(
          withJSONObject: ["status": "passed", "checks": checks],
          options: [.prettyPrinted, .sortedKeys]))
    } catch {
      FileHandle.standardOutput.write(
        try! JSONSerialization.data(
          withJSONObject: [
            "status": "failed", "checks": checks, "error": String(describing: error),
          ], options: [.prettyPrinted, .sortedKeys]))
      exit(1)
    }
  }
}
