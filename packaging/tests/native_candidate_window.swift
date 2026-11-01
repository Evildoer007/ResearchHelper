// Candidate-bound production controller, temporary visible window only.
// Local NSEvents never enter the global event stream; no research is submitted.
import AppKit
import WebKit

@main
struct NativeCandidateWindow {
    static var checks: [String] = []
    static var windowOpened = false
    static var windowClosed = false
    static var faultCallbackCount = 0
    static var diagnostics: [String: Any] = [:]
    static func pumpEvents() {
        // A standalone test entry has no NSApplication.run loop. Drain only
        // this application's queue so activation, key-window and WK input
        // events are processed like the real launcher, not only CF timers.
        while let event = NSApp.nextEvent(matching: .any, until: Date(), inMode: .default, dequeue: true) {
            NSApp.sendEvent(event)
        }
        RunLoop.main.run(until: Date().addingTimeInterval(0.02))
    }
    static func check(_ label: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: label, code: 1) }
        checks.append(label)
        FileHandle.standardError.write(Data((label + "\n").utf8))
    }
    static func wait(_ label: String, _ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline {
            pumpEvents()
        }
        try check(label, condition())
    }
    static func script(_ code: String, _ view: WKWebView) throws -> Any? {
        var done = false, value: Any?, failure: Error?
        view.evaluateJavaScript(code) { result, error in value = result; failure = error; done = true }
        let deadline = Date().addingTimeInterval(5)
        while !done && Date() < deadline { pumpEvents() }
        if let failure { throw failure }
        guard done else { throw NSError(domain: "JavaScript timeout", code: 1) }
        return value
    }
    static func truth(_ code: String, _ view: WKWebView) -> Bool { (try? script(code, view)) as? Bool == true }
    static func descendants(_ view: NSView) -> [NSView] { [view] + view.subviews.flatMap(descendants) }
    static func recovery(_ owner: ResearchHelperApp) -> NSView? {
        descendants(owner.window.contentView!).first { String(describing: type(of: $0)) == "ResearchHelperRecoveryView" }
    }
    static func button(_ title: String, _ owner: ResearchHelperApp) -> NSButton? {
        guard let panel = recovery(owner) else { return nil }
        return descendants(panel).compactMap { $0 as? NSButton }.first { $0.title == title }
    }
    static func sendKey(_ character: String, code: UInt16, to window: NSWindow) throws {
        let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [],
                                    timestamp: ProcessInfo.processInfo.systemUptime,
                                    windowNumber: window.windowNumber, context: nil,
                                    characters: character, charactersIgnoringModifiers: character,
                                    isARepeat: false, keyCode: code)!
        try check("input event belongs to temporary key window " + character,
                  window.isKeyWindow && event.windowNumber == window.windowNumber)
        window.sendEvent(event)
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
    }
    static func run(_ url: URL, _ output: URL) throws {
        let previousApp = NSWorkspace.shared.frontmostApplication
        NSApplication.shared.setActivationPolicy(.regular)
        NSApp.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1024, height: 768),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered, defer: false)
        owner.window.title = "ResearchHelper临时验收"
        owner.window.isReleasedWhenClosed = false
        owner.window.titleVisibility = .hidden
        owner.window.titlebarAppearsTransparent = true
        owner.window.titlebarSeparatorStyle = .none
        owner.window.backgroundColor = .clear
        owner.window.isOpaque = false
        owner.window.delegate = owner
        owner.webView = owner.createWebView(persistent: false)
        let material = NSVisualEffectView()
        material.material = .sidebar
        material.blendingMode = .behindWindow
        material.state = .active
        material.addSubview(owner.webView)
        owner.webView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([
            owner.webView.leadingAnchor.constraint(equalTo: material.leadingAnchor),
            owner.webView.trailingAnchor.constraint(equalTo: material.trailingAnchor),
            owner.webView.topAnchor.constraint(equalTo: material.topAnchor),
            owner.webView.bottomAnchor.constraint(equalTo: material.bottomAnchor)])
        owner.window.contentView = material
        owner.installMainMenu()
        owner.installNativeControls()
        owner.window.center()
        owner.window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        windowOpened = owner.window.isVisible
        defer {
            var safeURL = owner.webView.url.flatMap { URLComponents(url: $0, resolvingAgainstBaseURL: false) }
            let queryKeys = safeURL?.queryItems?.map { $0.name } ?? []
            safeURL?.query = nil
            diagnostics = ["workspace_ready": owner.workspaceControlsReady,
                           "webview_url": safeURL?.url?.absoluteString ?? "",
                           "query_keys": queryKeys,
                           "recovery_visible": recovery(owner)?.isHidden == false]
            if let result = try? script("(()=>{const shell=document.querySelector('[data-workspace-shell]'),status=document.querySelector('#login-status');return {route:location.pathname,ready:document.readyState,workspace:!!shell,input:!!document.querySelector('#chat-input'),loginStatus:status?.textContent||'',rootClass:document.documentElement.className,bodyClass:document.body.className,shellWidth:shell?.getBoundingClientRect().width||0,shellHeight:shell?.getBoundingClientRect().height||0}})()", owner.webView) {
                diagnostics["dom"] = result
            }
            owner.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
            owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.delegate = nil
            owner.window.close()
            windowClosed = !owner.window.isVisible
            NSApp.mainMenu = nil
            previousApp?.activate(options: [])
        }
        try wait("temporary window is visible and key") { owner.window.isVisible && owner.window.isKeyWindow }
        owner.webView.load(URLRequest(url: url))
        try wait("actual WK login ready") { truth("!!document.querySelector('#login-submit:not([disabled])')&&!document.documentElement.classList.contains('login-boot')", owner.webView) }
        _ = try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true", owner.webView)
        try wait("real workspace and rendered page ready") {
            owner.workspaceControlsReady && recovery(owner)?.isHidden != false && truth("!!document.querySelector('#chat-input')", owner.webView)
        }
        try check("all three native toolbar controls ready", owner.nativeControls.count == 3 && owner.nativeControls.allSatisfy { $0.isEnabled && !$0.isHidden })
        try check("OH sidebar material and transparent titlebar", material.material == .sidebar && owner.window.titlebarAppearsTransparent)
        guard let searchMenu = NSApp.mainMenu?.items.compactMap({ $0.submenu }).first(where: {
            $0.items.contains { $0.action == #selector(ResearchHelperApp.searchTasks) }
        }), let search = searchMenu.items.first(where: { $0.action == #selector(ResearchHelperApp.searchTasks) })
        else { throw NSError(domain: "native search menu missing", code: 1) }
        try check("native search uses Command K", search.keyEquivalent == "k" && search.keyEquivalentModifierMask == .command)
        owner.window.makeFirstResponder(owner.webView)
        _ = try script("document.querySelector('#chat-input').focus();true", owner.webView)
        try sendKey("r", code: 15, to: owner.window)
        try sendKey("h", code: 4, to: owner.window)
        try wait("native input reaches actual WK textarea") { truth("document.querySelector('#chat-input').value==='rh'", owner.webView) }
        searchMenu.performActionForItem(at: searchMenu.items.firstIndex(of: search)!)
        try wait("native menu opens actual search") { truth("!!document.querySelector('.task-search-dialog[open]')", owner.webView) }
        // Escape is delivered only to this owned native window, not to the OS.
        try sendKey("\u{1b}", code: 53, to: owner.window)
        try wait("native Escape restores input focus and text") {
            truth("!document.querySelector('.task-search-dialog').open&&document.activeElement.id==='chat-input'&&document.querySelector('#chat-input').value==='rh'", owner.webView)
        }
        // Controlled failure callbacks exercise the real policy, native buttons,
        // reload and WebKit paint checks; they do not claim an actual process kill.
        owner.webView(owner.webView, didFailProvisionalNavigation: nil,
                      withError: NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost))
        faultCallbackCount += 1
        try wait("connection failure shows native recovery") { recovery(owner)?.isHidden == false }
        let retry = button("重新打开上一个页面", owner)!
        try check("failure disables native workspace controls", !owner.workspaceControlsReady && owner.nativeControls.allSatisfy { !$0.isEnabled })
        try check("retry receives focus and Return key", owner.window.firstResponder === retry && retry.keyEquivalent == "\r")
        owner.window.contentView!.layoutSubtreeIfNeeded()
        guard let panel = recovery(owner) else { throw NSError(domain: "recovery view missing", code: 1) }
        try check("recovery has real native geometry", panel.bounds.width > 700 && panel.bounds.height > 400)
        for action in [retry, button("返回工作台", owner)!] {
            let rect = action.convert(action.bounds, to: panel)
            try check("native recovery action visible " + action.title,
                      rect.width > 60 && rect.height >= 20 && panel.bounds.contains(rect))
        }
        guard let bitmap = panel.bitmapImageRepForCachingDisplay(in: panel.bounds)
        else { throw NSError(domain: "recovery pixel capture unavailable", code: 1) }
        panel.cacheDisplay(in: panel.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:])
        else { throw NSError(domain: "recovery PNG unavailable", code: 1) }
        try png.write(to: output.appendingPathComponent("native-recovery.png"))
        retry.performClick(nil)
        try wait("retry restores actual WK workspace and hides recovery") { owner.workspaceControlsReady && recovery(owner)?.isHidden == true }
        try check("retry stays on owned origin", owner.webView.url?.host == url.host && owner.webView.url?.port == url.port)
        let responder = owner.window.firstResponder as? NSView
        try check("retry restores WebKit responder", responder === owner.webView || responder?.isDescendant(of: owner.webView) == true)
        owner.webViewWebContentProcessDidTerminate(owner.webView)
        faultCallbackCount += 1
        try wait("process-exit callback shows recovery") { recovery(owner)?.isHidden == false }
        try check("page recovery does not stop backend or quit", owner.backend == nil && !owner.failed && !owner.quitting)
        button("返回工作台", owner)!.performClick(nil)
        try wait("return-to-workspace reloads actual WK") { owner.workspaceControlsReady && recovery(owner)?.isHidden == true }
        try check("workspace recovery uses root without query", owner.webView.url?.path == "/" && owner.webView.url?.query == nil)
        try check("no research messages submitted", truth("document.querySelector('#chat-input').value==='rh'", owner.webView))
        _ = try script("window.__nativeAcceptanceState=null;fetch('/api/bootstrap').then(r=>r.json()).then(b=>window.__nativeAcceptanceState={offline:b.offline,tasks:b.tasks.length});true", owner.webView)
        try wait("actual backend remains offline with zero research tasks") {
            truth("window.__nativeAcceptanceState?.offline===true&&window.__nativeAcceptanceState.tasks===0", owner.webView)
        }
        try check("no file or print panels", owner.activeOpenPanel == nil && owner.activeSavePanel == nil && owner.printOperations.isEmpty)
    }
    static func main() {
        var record: [String: Any] = [:]
        do {
            try run(URL(string: CommandLine.arguments[1])!, URL(fileURLWithPath: CommandLine.arguments[2]))
            record["status"] = "passed"
        } catch { record = ["status": "failed", "error": String(describing: error)] }
        record["checks"] = checks
        record["native_window_opened"] = windowOpened
        record["native_window_closed"] = windowClosed
        record["fault_callbacks_injected"] = faultCallbackCount
        record["actual_webkit_process_killed"] = false
        record["diagnostics"] = diagnostics
        FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: record, options: [.prettyPrinted, .sortedKeys]))
        if record["status"] as? String != "passed" { exit(1) }
    }
}
