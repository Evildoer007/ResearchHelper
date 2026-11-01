// Exercise the real AppKit/WebKit settings bridge without showing a window.
import AppKit
import WebKit

@main
struct NativeSettingsAcceptance {
    static var checks: [String] = []
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain:name,code:1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline=Date().addingTimeInterval(20)
        while !condition() && Date()<deadline { RunLoop.main.run(until:Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain:"Native settings timed out",code:1) }
    }
    static func script(_ text: String, _ view: WKWebView) throws -> Any? {
        var done=false,value:Any?,error:Error?
        view.evaluateJavaScript(text) { result,failure in value=result;error=failure;done=true }
        try wait { done };if let failure=error { throw failure };return value
    }
    static func run(_ url: URL) throws {
        let app=NSApplication.shared
        app.setActivationPolicy(.prohibited);app.finishLaunching()
        let owner=ResearchHelperApp();owner.runtimeURL=url
        owner.window=NSWindow(contentRect:NSRect(x:0,y:0,width:1320,height:860),styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false;owner.window.delegate=owner
        owner.window.titleVisibility = .hidden;owner.window.titlebarAppearsTransparent=true
        owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView;owner.installNativeControls()
        defer {
            owner.webView.stopLoading();owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.delegate=nil;owner.window.close()
        }
        owner.webView.load(URLRequest(url:url));try wait { owner.workspaceControlsReady }
        try check("actual workspace enables native settings",owner.workspaceControlsReady)
        _=try script("{const input=document.querySelector('#chat-input');input.value='原生设置保留的未发送问题';input.dispatchEvent(new Event('input',{bubbles:true}));}true",owner.webView)
        owner.openSettings()
        let frame="document.querySelector('.workspace-settings-dialog iframe')"
        try wait { (try? script("!!\(frame)?.contentDocument?.querySelector('#preferences:not([hidden])')",owner.webView)) as? Bool == true }
        try check("native command opens current embedded settings",(try script("document.querySelectorAll('.workspace-settings-dialog[open] iframe').length",owner.webView)) as? Int == 1)
        try check("native settings never replaces workbench route",owner.webView.url?.path == "/")
        _=try script("\(frame).contentDocument.querySelector('[data-theme-set=dark]').click();true",owner.webView)
        try wait { (try? script("document.documentElement.dataset.theme==='dark'",owner.webView)) as? Bool == true }
        try check("iframe appearance applies through trusted parent",owner.window.appearance?.name == .darkAqua)
        _=try script("{const select=\(frame).contentDocument.querySelector('#interface-scale');select.value='1.25';select.dispatchEvent(new Event('change',{bubbles:true}));}true",owner.webView)
        try wait { owner.webView.pageZoom == 1.25 }
        try check("iframe zoom reaches real WKWebView pageZoom",owner.uiScale == 1.25)
        try check("native page has no duplicate CSS zoom",(try script("document.documentElement.style.zoom===''",owner.webView)) as? Bool == true)
        owner.applyScale(1.4)
        try wait { (try? script("\(frame).contentDocument.querySelector('#interface-scale').value==='1.4'",owner.webView)) as? Bool == true }
        try check("host scale update synchronizes open settings",owner.webView.pageZoom == 1.4)
        try check("macOS titlebar clearance follows native scale",(try script("Math.abs(parseFloat(document.documentElement.style.getPropertyValue('--native-titlebar-height'))-36/1.4)<.001",owner.webView)) as? Bool == true)
        _=try script("\(frame).contentDocument.querySelector('.settings-close').click();true",owner.webView)
        try wait { (try? script("!document.querySelector('.workspace-settings-dialog')",owner.webView)) as? Bool == true }
        try check("settings close preserves unsent research",(try script("document.querySelector('#chat-input').value==='原生设置保留的未发送问题'",owner.webView)) as? Bool == true)
        try check("settings close restores visible keyboard focus",(try script("document.activeElement.matches('[data-account-menu-toggle]')",owner.webView)) as? Bool == true)
        try check("hidden native window and nonpersistent web storage",!owner.window.isVisible && !owner.webView.configuration.websiteDataStore.isPersistent && NSApp.activationPolicy() == .prohibited)
    }
    static func main() {
        do {
            try run(URL(string:CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"native_window_opened":false],options:[.prettyPrinted,.sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"error":String(describing:error)],options:[.prettyPrinted,.sortedKeys]));exit(1)
        }
    }
}
