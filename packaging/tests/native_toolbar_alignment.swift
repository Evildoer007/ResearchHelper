// Independent acceptance of the actual production controller. No foreground
// window, installed App, production browser storage or research service.
import AppKit
import WebKit

@main
struct NativeToolbarAcceptance {
    static var checks: [String] = []
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let end = Date().addingTimeInterval(15)
        while !condition() && Date() < end {
            RunLoop.main.run(until: Date().addingTimeInterval(0.02))
        }
        guard condition() else { throw NSError(domain:"Native operation timed out", code:1) }
    }
    static func script(_ text: String, in view: WKWebView) throws -> Any? {
        var finished = false, result: Any?, failure: Error?
        view.evaluateJavaScript(text) { value, error in result = value; failure = error; finished = true }
        try wait { finished }
        if let error = failure { throw error }
        return result
    }
    static func run(_ url: URL) throws {
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        app.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.webView = owner.createWebView(persistent: false)
        owner.window = owner.makeMainWindow(containing: owner.webView)
        owner.installNativeControls()
        guard let content = owner.window.contentView,
              let backdrop = content.subviews.first as? NSVisualEffectView else {
            throw NSError(domain: "Missing sidebar material layer", code: 1)
        }
        try check("OH sidebar material and WebKit are separate layers", content.subviews == [backdrop, owner.webView] &&
                  backdrop.material == .sidebar && backdrop.blendingMode == .behindWindow && backdrop.state == .active)
        try check("native workspace controls hidden before readiness", owner.nativeControls.count == 3 && owner.nativeControls.allSatisfy { $0.isHidden && !$0.isEnabled })
        try check("only nonpersistent test browser storage", !owner.webView.configuration.websiteDataStore.isPersistent)
        try check("WebKit zoom uses native pageZoom without pinch magnification", owner.webView.pageZoom == 1 && !owner.webView.allowsMagnification)
        try check("clear WebKit under-page material", owner.webView.underPageBackgroundColor.alphaComponent == 0)
        let settings = NSMenuItem(title: "设置…", action: #selector(ResearchHelperApp.openSettings), keyEquivalent: ",")
        try check("settings command disabled before readiness", !owner.validateMenuItem(settings))
        owner.webView.load(URLRequest(url: url))
        try wait { owner.workspaceControlsReady }
        try check("Chat reveals only sidebar and search after the workspace is ready",
                  owner.nativeControls.prefix(2).allSatisfy { !$0.isHidden && $0.isEnabled } &&
                  owner.nativeControls[2].isHidden && !owner.nativeControls[2].isEnabled)
        let delivery = NSMenuItem(title: "任务与交付", action: #selector(ResearchHelperApp.openArtifacts), keyEquivalent: "")
        try check("Chat does not expose the delivery menu action", !owner.validateMenuItem(delivery))
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'delivery-state',available:true});true", in: owner.webView)
        try wait { !owner.nativeControls[2].isHidden }
        try check("the active Desk can open delivery", owner.nativeControls[2].isEnabled && owner.validateMenuItem(delivery))
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'delivery-state',available:false});true", in: owner.webView)
        try wait { owner.nativeControls[2].isHidden }
        try check("native settings command enabled in workspace", owner.validateMenuItem(settings))
        let originals = owner.nativeControls
        for _ in 0..<6 { owner.installNativeControls() }
        try check("repeated titlebar setup does not duplicate buttons", owner.nativeControls == originals)
        let close = owner.window.standardWindowButton(.closeButton)!
        let bar = close.superview!
        let expected = [close.frame.maxX + 60, close.frame.maxX + 94, bar.bounds.maxX - 40]
        try check("OH titlebar geometry and control size", owner.nativeControls.enumerated().allSatisfy { index, button in
            abs(button.frame.minX - expected[index]) < 0.01 && button.frame.size == NSSize(width: 26, height: 24)
        })
        try check("all toolbar buttons have accessible SF-symbol names", owner.nativeControls.allSatisfy { $0.image != nil && $0.accessibilityLabel()?.isEmpty == false })
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'rail-state',collapsed:true});true", in: owner.webView)
        try wait { owner.sidebarCollapsed }
        try check("collapsed rail exposes expand action and state", owner.nativeControls[0].toolTip == "展开任务栏" && owner.nativeControls[0].accessibilityValue() as? String == "已收起")
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'rail-state',collapsed:false});true", in: owner.webView)
        try wait { !owner.sidebarCollapsed }
        try check("expanded rail exposes collapse action and state", owner.nativeControls[0].toolTip == "收起任务栏" && owner.nativeControls[0].accessibilityValue() as? String == "已展开")
        // Real subframe message, not a fabricated WKScriptMessage.
        _ = try script("{const frame=document.createElement('iframe');frame.src='/frame.html';frame.onload=()=>{window.__frameReady=true};document.body.append(frame)};true", in: owner.webView)
        try wait { (try? script("window.__frameReady === true", in:owner.webView)) as? Bool == true }
        try check("subframe cannot hide controls or change rail", owner.workspaceControlsReady && !owner.sidebarCollapsed)
        for rejected in [0, 1, "false", NSNull()] as [Any] {
            owner.receiveWorkspaceState(["action":"workspace-state", "ready":rejected], sourceView:owner.webView, sourceURL:url, mainFrame:true)
            owner.receiveWorkspaceState(["action":"rail-state", "collapsed":rejected], sourceView:owner.webView, sourceURL:url, mainFrame:true)
        }
        try check("numeric and string substitutes rejected for bridge booleans", owner.workspaceControlsReady && !owner.sidebarCollapsed)
        let body: [String:Any] = ["action":"workspace-state", "ready":false]
        let other = owner.createWebView(persistent:false)
        owner.receiveWorkspaceState(body, sourceView:other, sourceURL:url, mainFrame:true)
        owner.receiveWorkspaceState(body, sourceView:owner.webView, sourceURL:url.appendingPathComponent("report.html"), mainFrame:true)
        owner.receiveWorkspaceState(body, sourceView:owner.webView, sourceURL:URL(string:"http://127.0.0.1:1/")!, mainFrame:true)
        owner.receiveWorkspaceState(body, sourceView:owner.webView, sourceURL:url, mainFrame:false)
        try check("other windows routes origins and frames cannot change workspace state", owner.workspaceControlsReady)
        // Detached buttons must be removed and recreated in the current bar.
        owner.nativeControls[0].removeFromSuperview()
        owner.installNativeControls()
        try check("detached titlebar controls rebuilt without leftover copies", owner.nativeControls.count == 3 && owner.nativeControls != originals && originals.allSatisfy { $0.superview == nil } && owner.nativeControls.allSatisfy { $0.superview === bar })
        owner.window.setContentSize(NSSize(width:1024,height:768))
        owner.windowDidResize(Notification(name:NSWindow.didResizeNotification, object:owner.window))
        try check("resized titlebar retains OH right inset", abs(owner.nativeControls[2].frame.maxX - (bar.bounds.maxX - 14)) < 0.01)
        content.layoutSubtreeIfNeeded()
        try check("material and WebKit continue to fill the same content bounds", backdrop.frame == content.bounds &&
                  owner.webView.frame == content.bounds)
        // Navigation away resets readiness immediately, not only after load.
        owner.webView.load(URLRequest(url:url.appendingPathComponent("login/index.html")))
        try wait { !owner.workspaceControlsReady && !owner.webView.isLoading }
        try check("login route hides all workspace controls and settings action", owner.nativeControls.allSatisfy { $0.isHidden && !$0.isEnabled } && !owner.validateMenuItem(settings))
        owner.webView.load(URLRequest(url:url))
        try wait { owner.workspaceControlsReady }
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'signed_out'});true", in:owner.webView)
        try wait { !owner.workspaceControlsReady }
        try check("sign-out hides toolbar before navigation", owner.nativeControls.allSatisfy { $0.isHidden })
        try check("acceptance never opens a native window", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited)
        owner.webView.stopLoading()
        owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
        other.configuration.userContentController.removeAllScriptMessageHandlers()
        owner.window.delegate = nil
        owner.window.close()
    }
    static func main() {
        do {
            try run(URL(string:CommandLine.arguments[1])!)
            let record: [String:Any] = ["status":"passed", "checks":checks, "native_window_opened":false, "production_browser_storage":false]
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:record, options:[.prettyPrinted,.sortedKeys]))
        } catch {
            let record: [String:Any] = ["status":"failed", "checks":checks, "error":String(describing:error)]
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:record, options:[.prettyPrinted,.sortedKeys]))
            exit(1)
        }
    }
}
