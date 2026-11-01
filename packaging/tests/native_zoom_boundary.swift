// Compile with the actual production controller; every window stays hidden.
import AppKit
import WebKit

@main
struct NativeZoomBoundary {
    static var checks: [String] = []
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain:name,code:1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(15)
        while !condition() && Date() < deadline { RunLoop.main.run(until:Date().addingTimeInterval(0.02)) }
        try check("asynchronous WebKit state reached",condition())
    }
    static func script(_ text: String, in view: WKWebView) throws -> Any? {
        var done=false,answer:Any?,failure:Error?
        view.evaluateJavaScript(text) { value,error in answer=value;failure=error;done=true }
        try wait { done }
        if let failure=failure { throw failure }
        return answer
    }
    static func run(_ url: URL) throws {
        let app=NSApplication.shared;app.setActivationPolicy(.prohibited);app.finishLaunching()
        let saved=UserDefaults.standard.object(forKey:"researchhelper.uiScale")
        defer { if let saved=saved { UserDefaults.standard.set(saved,forKey:"researchhelper.uiScale") }
                else { UserDefaults.standard.removeObject(forKey:"researchhelper.uiScale") } }
        let owner=ResearchHelperApp();owner.runtimeURL=url
        owner.window=NSWindow(contentRect:NSRect(x:0,y:0,width:1320,height:860),styleMask:[.titled,.closable],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false
        owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView
        let other=owner.createWebView(persistent:false)
        defer {
            for view in [owner.webView!,other] { view.stopLoading();view.configuration.userContentController.removeAllScriptMessageHandlers() }
            owner.window.close()
        }
        owner.webView.load(URLRequest(url:url))
        try wait { owner.workspaceControlsReady }
        for scale in owner.scaleSteps {
            _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'zoom',scale:\(scale)});true",in:owner.webView)
            try wait { owner.uiScale == scale }
            try check("main workbench accepts scale \(scale)",owner.webView.pageZoom == scale)
        }
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'zoom',scale:1.25});true",in:owner.webView)
        try wait { owner.uiScale == 1.25 }
        _ = try script("{const frame=document.createElement('iframe');frame.src='/frame.html';frame.onload=()=>window.__frameReady=true;document.body.append(frame)};true",in:owner.webView)
        try wait { (try? script("window.__frameReady===true",in:owner.webView)) as? Bool == true }
        try check("real subframe cannot change global zoom",owner.uiScale == 1.25)
        for value in [true,false,"0.8",NSNull(),0,100,Double.nan,Double.infinity] as [Any] {
            owner.receiveWorkspaceZoom(["action":"zoom","scale":value],sourceView:owner.webView,sourceURL:url,mainFrame:true)
        }
        try check("boolean string null nonfinite and unsupported scales rejected",owner.uiScale == 1.25)
        let request:[String:Any] = ["action":"zoom","scale":0.8]
        owner.receiveWorkspaceZoom(request,sourceView:other,sourceURL:url,mainFrame:true)
        owner.receiveWorkspaceZoom(request,sourceView:owner.webView,sourceURL:url,mainFrame:false)
        owner.receiveWorkspaceZoom(request,sourceView:owner.webView,sourceURL:url.appendingPathComponent("reports/report.html"),mainFrame:true)
        owner.receiveWorkspaceZoom(request,sourceView:owner.webView,sourceURL:URL(string:"http://127.0.0.1:1/")!,mainFrame:true)
        try check("foreign view frame report and origin rejected",owner.uiScale == 1.25)
        owner.webView.load(URLRequest(url:url.appendingPathComponent("report.html")))
        try wait { !owner.webView.isLoading && owner.webView.url?.path == "/report.html" }
        _ = try script("window.webkit.messageHandlers.researchhelper.postMessage({action:'zoom',scale:0.8});true",in:owner.webView)
        try check("real report route message cannot alter preference",owner.uiScale == 1.25 && UserDefaults.standard.double(forKey:"researchhelper.uiScale") == 1.25)
        try check("no native window shown and test storage nonpersistent",!owner.window.isVisible && !owner.webView.configuration.websiteDataStore.isPersistent && NSApp.activationPolicy() == .prohibited)
    }
    static func main() {
        do {
            try run(URL(string:CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks],options:[.prettyPrinted,.sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"error":String(describing:error)],options:[.prettyPrinted,.sortedKeys]))
            exit(1)
        }
    }
}
