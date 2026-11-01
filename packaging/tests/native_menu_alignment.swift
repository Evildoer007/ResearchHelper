// Real controller/menu code; synthetic events go to an isolated receiver only.
import AppKit
import WebKit

private final class EditingReceiver: NSObject, NSApplicationDelegate {
    var calls: [String] = []
    @objc func cut(_ sender: Any?) { calls.append("cut") }
    @objc func copy(_ sender: Any?) { calls.append("copy") }
    @objc func paste(_ sender: Any?) { calls.append("paste") }
    @objc func selectAll(_ sender: Any?) { calls.append("selectAll") }
}

@main
struct NativeMenuAlignment {
    static var checks: [String] = []
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(15)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "WebKit wait", code: 1) }
    }
    static func script(_ code: String, in view: WKWebView) throws -> Any? {
        var done = false, answer: Any?, failure: Error?
        view.evaluateJavaScript(code) { value, error in answer = value; failure = error; done = true }
        try wait { done }
        if let failure { throw failure }
        return answer
    }
    static func run(_ url: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let receiver = EditingReceiver()
        let priorDelegate = NSApp.delegate
        NSApp.delegate = receiver
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                                           styleMask: [.titled, .closable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        owner.installMainMenu()
        owner.installNativeControls()
        defer {
            for window in Array(owner.reportWindows.keys) { window.close() }
            owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close()
            NSApp.mainMenu = nil
            NSApp.delegate = priorDelegate
        }
        let menu = NSApp.mainMenu!
        let edit = menu.items.first { $0.submenu?.title == "编辑" }!.submenu!
        let view = menu.items.first { $0.submenu?.title == "显示" }!.submenu!
        let expected = [("撤销", "undo:", "z"), ("重做", "redo:", "z"), ("剪切", "cut:", "x"),
                        ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")]
        for (title, selector, key) in expected {
            let item = edit.items.first { $0.title == title }!
            try check("edit contract " + title, item.action == NSSelectorFromString(selector) && item.keyEquivalent == key && item.target == nil)
            try check("edit modifiers " + title, item.keyEquivalentModifierMask == (title == "重做" ? [.command, .shift] : .command))
        }
        let sidebar = view.items.first { $0.action == #selector(ResearchHelperApp.toggleSidebar) }!
        try check("OH sidebar shortcut and owner", sidebar.keyEquivalent == "s" && sidebar.keyEquivalentModifierMask == [.control, .command] && sidebar.target === owner)
        try check("sidebar command disabled before workspace", !owner.validateMenuItem(sidebar))
        let zoomIn = view.items.first { $0.action == #selector(ResearchHelperApp.zoomIn) }!
        let zoomOut = view.items.first { $0.action == #selector(ResearchHelperApp.zoomOut) }!
        owner.uiScale = owner.scaleSteps.first!
        try check("minimum scale disables only zoom out", !owner.validateMenuItem(zoomOut) && owner.validateMenuItem(zoomIn))
        owner.uiScale = owner.scaleSteps.last!
        try check("maximum scale disables only zoom in", !owner.validateMenuItem(zoomIn) && owner.validateMenuItem(zoomOut))
        owner.uiScale = 1
        try check("interior scale enables both directions", owner.validateMenuItem(zoomIn) && owner.validateMenuItem(zoomOut))
        let clipboardRevision = NSPasteboard.general.changeCount
        for (key, action) in [("x", "cut"), ("c", "copy"), ("v", "paste"), ("a", "selectAll"), ("C", "copy")] {
            let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: .command, timestamp: 0,
                                        windowNumber: owner.window.windowNumber, context: nil, characters: key,
                                        charactersIgnoringModifiers: key, isARepeat: false, keyCode: 0)!
            let count = receiver.calls.count
            let handled = owner.window.performKeyEquivalent(with: event)
            try check("responder-chain routing " + key, handled && receiver.calls.count == count + 1 && receiver.calls.last == action)
        }
        for modifiers: NSEvent.ModifierFlags in [[], .control, [.command, .control], [.command, .shift], [.command, .option]] {
            let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: modifiers, timestamp: 0,
                                        windowNumber: owner.window.windowNumber, context: nil, characters: "c",
                                        charactersIgnoringModifiers: "c", isARepeat: false, keyCode: 0)!
            let count = receiver.calls.count
            _ = owner.window.performKeyEquivalent(with: event)
            try check("other modifier combination not hijacked " + String(modifiers.rawValue), receiver.calls.count == count)
        }
        try check("synthetic edit routing leaves clipboard revision unchanged", NSPasteboard.general.changeCount == clipboardRevision)
        let close = owner.window.standardWindowButton(.closeButton)!
        let drag = close.superview!.subviews.first { String(describing: type(of: $0)) == "ResearchHelperTitlebarDragView" }!
        try check("OH drag region accepts first mouse", drag.acceptsFirstMouse(for: nil) && drag.mouseDownCanMoveWindow)
        owner.webView.load(URLRequest(url: url))
        try wait { owner.workspaceControlsReady }
        try check("sidebar command enabled only after real ready message", owner.validateMenuItem(sidebar))
        _ = try script("window.__railCalls=0;addEventListener('researchhelper:toggle-sidebar',()=>window.__railCalls++);true", in: owner.webView)
        view.performActionForItem(at: view.items.firstIndex(of: sidebar)!)
        try wait { (try? script("window.__railCalls", in: owner.webView)) as? Int == 1 }
        try check("actual menu dispatch reaches workspace once", (try script("window.__railCalls", in: owner.webView)) as? Int == 1)
        owner.workspaceControlsReady = false
        view.performActionForItem(at: view.items.firstIndex(of: sidebar)!)
        try check("not-ready dispatch stays inert", (try script("window.__railCalls", in: owner.webView)) as? Int == 1)
        let preview = owner.prepareReport(url.appendingPathComponent("reports/semi.html"), printing: false, persistent: false)!
        try check("report gets the same editing window class", preview is ResearchHelperWindow)
        try check("report pinch allowed but workspace scale stays discrete", owner.reportWindows[preview]!.allowsMagnification && !owner.webView.allowsMagnification)
        try check("no native window or external keyboard automation", !owner.window.isVisible && !preview.isVisible && NSApp.activationPolicy() == .prohibited)
    }
    static func main() {
        do {
            try run(URL(string: CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["status": "passed", "checks": checks, "actual_clipboard_changed": false, "actual_keyboard_posted": false], options: [.prettyPrinted, .sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: ["status": "failed", "checks": checks, "error": String(describing: error)], options: [.prettyPrinted, .sortedKeys]))
            exit(1)
        }
    }
}
