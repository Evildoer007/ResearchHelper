// Exercise the real native report window against disposable local accounts.
import AppKit
import WebKit
import ObjectiveC.runtime

extension NSWindow {
    @objc dynamic func rh_testKeepSessionWindowHidden(_ sender: Any?) {}
}

@main
struct NativeReportSession {
    static func run(_ base: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let visible = class_getInstanceMethod(NSWindow.self, #selector(NSWindow.makeKeyAndOrderFront(_:)))!
        let hidden = class_getInstanceMethod(NSWindow.self, #selector(NSWindow.rh_testKeepSessionWindowHidden(_:)))!
        method_exchangeImplementations(visible, hidden)
        defer { method_exchangeImplementations(visible, hidden) }

        let owner = ResearchHelperApp()
        owner.runtimeURL = base
        owner.window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                                styleMask: [.titled, .closable], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        let workspace = owner.webView!
        defer {
            for preview in Array(owner.reportWindows.keys) { preview.close() }
            workspace.stopLoading()
            workspace.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close()
        }

        let check = HiddenPrintProbe.check
        func script(_ source: String, in view: WKWebView? = nil) throws -> Any? {
            try HiddenPrintProbe.evaluate(view ?? workspace, source)
        }
        func until(_ source: String, in view: WKWebView? = nil) throws {
            try HiddenPrintProbe.wait(20, until: { (try? script(source, in: view)) as? Bool == true })
        }
        func login(_ account: String, password: String) throws {
            try until("!!document.querySelector('#login-submit:not([disabled])')")
            _ = try script("""
                document.querySelector('#login-account').value='\(account)';
                document.querySelector('#login-password').value='\(password)';
                document.querySelector('#login-submit').click();true
                """)
            try until("typeof boot!=='undefined'&&boot.role==='\(account)'&&workspaceReady")
            try HiddenPrintProbe.wait(15, until: { owner.workspaceControlsReady })
        }
        func research() throws -> String {
            _ = try script("""
                api('/api/cases',{id:'mechanism'}).then(task=>{cacheTask(task);setTask(task)});true
                """)
            try until("!!document.querySelector('#chat .rh-process [data-action=start]:not([disabled])')")
            _ = try script("document.querySelector('#chat .rh-process [data-action=start]').click();true")
            try until("selected?.delivery?.version===1")
            return try script("savedReportURL(selected.id,selected.delivery.version)") as! String
        }
        func sharedCookie(from main: WKWebView, into preview: WKWebView) throws {
            let incoming = main.configuration.websiteDataStore.httpCookieStore
            let outgoing = preview.configuration.websiteDataStore.httpCookieStore
            var added = false
            incoming.getAllCookies { cookies in
                guard let cookie = cookies.first(where: { $0.name == "researchhelper_session" }) else { return }
                outgoing.setCookie(cookie) { added = true }
            }
            try HiddenPrintProbe.wait(10, until: { added })
        }

        workspace.load(URLRequest(url: base))
        try login("admin", password: "66666666")
        let adminPath = try research()
        let detailed = URL(string: adminPath + "?profile=detailed", relativeTo: base)!.absoluteURL
        let adminWindow = owner.prepareReport(detailed, printing: false, persistent: false)!
        let adminReport = owner.reportWindows[adminWindow]!
        try sharedCookie(from: workspace, into: adminReport)
        adminReport.load(URLRequest(url: detailed, cachePolicy: .reloadIgnoringLocalCacheData))
        try until("document.body?.dataset.reportVariant==='detailed'", in: adminReport)
        try check("admin detailed report loaded in an isolated native window", !adminWindow.isVisible)

        _ = try script("window.__previousWorkspaceDocument=true")
        workspace.load(URLRequest(url: base, cachePolicy: .reloadIgnoringLocalCacheData))
        try until("!window.__previousWorkspaceDocument&&typeof workspaceReady!=='undefined'&&workspaceReady")
        try HiddenPrintProbe.wait(15, until: { owner.workspaceControlsReady })
        try check("same-account workspace reload keeps its loaded report", owner.reportWindows[adminWindow] === adminReport)

        // Revoke only the disposable session outside the workspace's normal
        // logout bridge. The real service redirects the next main request.
        _ = try script("""
            fetch('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json','X-ResearchHelper-Token':token},body:'{}'})
              .then(response=>{window.__testSessionRevoked=response.ok});true
            """)
        try until("window.__testSessionRevoked===true && !leaving && location.pathname==='/'")
        workspace.load(URLRequest(url: base, cachePolicy: .reloadIgnoringLocalCacheData))
        try until("location.pathname==='/login/index.html'&&!!document.querySelector('#login-submit')")
        try check("server login redirect closes the prior account's native reports",
                  owner.reportWindows.isEmpty && owner.reportTargets.isEmpty && !owner.workspaceControlsReady)

        try login("sales", password: "88888888")
        let salesPath = try research()
        try check("sales gets its own standard report link inside ResChat", try script(
            "!artifactsOpen&&document.querySelector('#chat a.artifact-card')?.getAttribute('href')==='\(salesPath)'") as? Bool == true)
        _ = try script("document.querySelector('#chat a.artifact-card').click();true")
        try HiddenPrintProbe.wait(15, until: { owner.reportWindows.count == 1 })
        let salesReport = owner.reportWindows.values.first!
        try until("document.body?.dataset.reportVariant==='standard'", in: salesReport)
        try check("sales report window uses the exact edition and workspace session",
                  salesReport.url?.path == salesPath && salesReport.url?.query == nil &&
                    salesReport.configuration.websiteDataStore === workspace.configuration.websiteDataStore)
        try check("all report acceptance ran in background", NSApp.keyWindow == nil &&
                  !owner.window.isVisible && owner.reportWindows.keys.allSatisfy { !$0.isVisible })
    }

    static func main() {
        var record: [String: Any] = ["status": "passed", "native_foreground_opened": false]
        do { try run(URL(string: CommandLine.arguments[1])!) }
        catch {
            record["status"] = "failed"
            record["error"] = String(describing: error)
        }
        record["checks"] = HiddenPrintProbe.checks
        FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]))
        if record["status"] as? String != "passed" { exit(1) }
    }
}
