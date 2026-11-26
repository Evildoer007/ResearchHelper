// Current candidate integration in a hidden, nonpersistent WKWebView.
import AppKit
import WebKit

@main
struct CurrentNativeSettingsAcceptance {
    static var checks: [String] = []
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "Current native settings timed out", code: 1) }
    }
    static func script(_ text: String, _ view: WKWebView) throws -> Any? {
        var done = false, value: Any?, error: Error?
        view.evaluateJavaScript(text) { result, failure in value = result; error = failure; done = true }
        try wait { done }
        if let failure = error { throw failure }
        return value
    }
    static func truth(_ text: String, _ view: WKWebView) -> Bool {
        (try? script(text, view)) as? Bool == true
    }
    static func run(_ url: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.uiScale = 0.9
        owner.window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.window.delegate = owner
        owner.window.titleVisibility = .hidden
        owner.window.titlebarAppearsTransparent = true
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        owner.installMainMenu()
        owner.installNativeControls()
        defer {
            owner.cancelFilePanels()
            owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.delegate = nil
            owner.window.close()
            NSApp.mainMenu = nil
        }
        owner.webView.load(URLRequest(url: url))
        try wait { truth("!!document.querySelector('#login-submit:not([disabled])')", owner.webView) }
        try check("login never enables workspace controls", !owner.workspaceControlsReady)
        _ = try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true", owner.webView)
        try wait { owner.workspaceControlsReady }
        try check("current packaged service authenticates a ready workspace", owner.webView.url?.path == "/")
        try check("restored scale reaches the first workspace layout", abs(owner.webView.pageZoom - 0.9) < 0.001 &&
            truth("document.documentElement.dataset.uiScale==='0.9'&&document.documentElement.style.zoom===''&&Math.abs(parseFloat(document.documentElement.style.getPropertyValue('--native-titlebar-height'))-40)<.001", owner.webView))
        _ = try script("{const input=document.querySelector('#chat-input');input.value='当前候选原生设置的未发送问题';input.dispatchEvent(new Event('input',{bubbles:true}));}true", owner.webView)
        let appMenu = NSApp.mainMenu!.items.first!.submenu!
        let settings = appMenu.items.first { $0.action == #selector(ResearchHelperApp.openSettings) }!
        try check("settings uses native Cmd comma and actual owner", settings.keyEquivalent == "," && settings.keyEquivalentModifierMask == .command && settings.target === owner)
        try check("ready workspace validates settings menu", owner.validateMenuItem(settings))
        appMenu.performActionForItem(at: appMenu.items.firstIndex(of: settings)!)
        let frame = "document.querySelector('.workspace-settings-dialog iframe')"
        try wait { truth("!!\(frame)?.contentDocument?.querySelector('#preferences:not([hidden])')", owner.webView) }
        try check("actual menu opens one embedded settings frame", truth("document.querySelectorAll('.workspace-settings-dialog[open] iframe').length===1", owner.webView))
        try check("settings does not replace workspace route", owner.webView.url?.path == "/")
        _ = try script("\(frame).contentDocument.querySelector('[data-theme-set=dark]').click();true", owner.webView)
        try wait { truth("document.documentElement.dataset.theme==='dark'", owner.webView) }
        try check("dark selection reaches AppKit appearance", owner.window.appearance?.name == .darkAqua)
        for scale: CGFloat in [0.8, 0.9, 1, 1.1, 1.25, 1.4] {
            _ = try script("{const select=\(frame).contentDocument.querySelector('#interface-scale');select.value=String(\(scale));select.dispatchEvent(new Event('change',{bubbles:true}));}true", owner.webView)
            try wait { abs(owner.webView.pageZoom - scale) < 0.001 }
            try check("actual settings scale \(scale)", abs(owner.uiScale - scale) < 0.001)
            try check("single scale and titlebar clearance \(scale)", truth("document.documentElement.style.zoom===''&&Math.abs(parseFloat(document.documentElement.style.getPropertyValue('--native-titlebar-height'))-36/\(scale))<.001", owner.webView))
        }
        owner.applyScale(1.25)
        try wait { truth("\(frame).contentDocument.querySelector('#interface-scale').value==='1.25'", owner.webView) }
        try check("native scale synchronizes the open settings frame", abs(owner.webView.pageZoom - 1.25) < 0.001)
        _ = try script("\(frame).contentDocument.querySelector('.settings-close').click();true", owner.webView)
        try wait { truth("!document.querySelector('.workspace-settings-dialog')", owner.webView) }
        try check("settings close preserves current unsent question", truth("document.querySelector('#chat-input').value==='当前候选原生设置的未发送问题'", owner.webView))
        try check("settings close restores visible account focus", truth("document.activeElement.matches('[data-account-menu-toggle]')", owner.webView))
        _ = try script("document.querySelector('[data-account-menu-toggle]').click();document.querySelector('#account-menu [data-action=logout]').click();true", owner.webView)
        try wait { owner.webView.url?.path == "/login/index.html" }
        try wait { !owner.workspaceControlsReady }
        try wait { truth("document.documentElement.dataset.uiScale==='1.25'&&!!document.querySelector('#login-submit')", owner.webView) }
        try check("navigation keeps native zoom and both titlebar clearances", owner.webView.pageZoom == 1.25 &&
            truth("document.documentElement.style.zoom===''&&Math.abs(parseFloat(document.documentElement.style.getPropertyValue('--native-titlebar-height'))-28.8)<.001&&Math.abs(parseFloat(document.documentElement.style.getPropertyValue('--native-titlebar-collapsed-leading-safe-area'))-132.8)<.001", owner.webView))
        try check("sign out disables native research settings", !owner.validateMenuItem(settings))
        appMenu.performActionForItem(at: appMenu.items.firstIndex(of: settings)!)
        try check("signed out settings action stays inert", !truth("!!document.querySelector('.workspace-settings-dialog[open]')", owner.webView))
        try check("no visible native windows and no persistent web store", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited && !owner.webView.configuration.websiteDataStore.isPersistent)
    }
    static func main() {
        do {
            try run(URL(string: CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["status":"passed", "checks":checks, "native_window_opened":false], options:[.prettyPrinted, .sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: ["status":"failed", "checks":checks, "error":String(describing:error)], options:[.prettyPrinted, .sortedKeys]))
            exit(1)
        }
    }
}
