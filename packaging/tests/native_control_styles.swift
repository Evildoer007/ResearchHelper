// Diagnostic only: resolve actual packaged controls in hidden WebKit.
import AppKit
import WebKit

@main
struct NativeControlStyles {
    static var phase = "startup"
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(25)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "WebKit condition: " + phase, code: 1) }
    }
    static func script(_ code: String, _ view: WKWebView) throws -> Any? {
        var done = false, value: Any?, failure: Error?
        view.evaluateJavaScript(code) { answer, error in value = answer; failure = error; done = true }
        try wait { done }
        if let failure { throw failure }
        return value
    }
    static func truth(_ code: String, _ view: WKWebView) -> Bool { (try? script(code, view)) as? Bool == true }
    static func capture(_ name: String, _ owner: ResearchHelperApp, _ output: URL) throws {
        _ = try script("document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getComputedTiming().iterations)){try{a.finish()}catch{}}});document.documentElement.getBoundingClientRect().width", owner.webView)
        RunLoop.main.run(until: Date().addingTimeInterval(0.25))
        let configuration = WKSnapshotConfiguration()
        configuration.rect = owner.webView.bounds
        configuration.afterScreenUpdates = true
        var done = false, image: NSImage?, failure: Error?
        owner.webView.takeSnapshot(with: configuration) { value, error in image = value; failure = error; done = true }
        try wait { done }
        if let failure { throw failure }
        guard let tiff = image?.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:]) else { throw NSError(domain: "No snapshot", code: 1) }
        try png.write(to: output.appendingPathComponent(name + ".png"), options: .atomic)
    }
    static func run(_ url: URL, _ output: URL) throws -> [String: Any] {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                                           styleMask: [.titled, .closable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        defer {
            owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close()
        }
        owner.webView.load(URLRequest(url: url))
        phase = "login form ready"
        try wait { truth("!!document.querySelector('#login-submit:not([disabled])')", owner.webView) }
        _ = try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true", owner.webView)
        phase = "workspace ready"
        try wait { owner.workspaceControlsReady }
        owner.openSettings()
        let frame = "document.querySelector('.workspace-settings-dialog iframe')"
        phase = "case settings ready"
        try wait { truth("!!\(frame)?.contentDocument?.querySelector('[data-workspace-action=cases]')", owner.webView) }
        _ = try script("\(frame).contentDocument.querySelector('a[href=\"#storage\"]').click();\(frame).contentDocument.querySelector('[data-workspace-action=cases]').click();true", owner.webView)
        phase = "case selector ready"
        try wait { truth("!!document.querySelector('#dialog[open] [data-case]')", owner.webView) }
        _ = try script("document.querySelector('#dialog[open] [data-case]').click();true", owner.webView)
        phase = "case selected and modal closed"
        try wait { truth("!!document.querySelector('.task-item[aria-current=true]')&&!document.querySelector('#dialog[open]')", owner.webView) }
        var themes: [String: Any] = [:]
        for theme in ["light", "dark"] {
            phase = theme + " settings ready"
            owner.openSettings()
            try wait { truth("!!\(frame)?.contentDocument?.querySelector('[data-theme-set=\(theme)]')", owner.webView) }
            _ = try script("\(frame).contentDocument.querySelector('[data-theme-set=\(theme)]').click();true", owner.webView)
            try wait { truth("document.documentElement.dataset.theme==='\(theme)'", owner.webView) }
            _ = try script("\(frame).contentDocument.querySelector('.settings-close').click();true", owner.webView)
            try wait { truth("!document.querySelector('.workspace-settings-dialog')", owner.webView) }
            phase = theme + " snapshot"
            try capture(theme + "-original", owner, output)
            let json = try script("JSON.stringify(['.mode-switch button[data-mode=chat]','.rail-new-task__label','.task-item[aria-current=true]','.task-item[aria-current=true] .task-item__text','#chat-input','.composer .choice-trigger','.rail-account-trigger__name'].map(selector=>{const el=document.querySelector(selector);if(!el)return{selector,missing:true};const chain=[];for(let n=el;n;n=n.parentElement){let c=getComputedStyle(n);chain.push({tag:n.tagName,cls:n.className,appearance:c.getPropertyValue('-webkit-appearance'),background:c.backgroundColor,image:c.backgroundImage,color:c.color,opacity:c.opacity,font:c.fontSize})}return{selector,chain}}))", owner.webView) as! String
            themes[theme] = try JSONSerialization.jsonObject(with: Data(json.utf8))
        }
        // Explicit diagnostic variant, not a change to the released stylesheet.
        _ = try script("{const style=document.createElement('style');style.textContent='.workspace-shell button{appearance:none!important;-webkit-appearance:none!important}';document.head.append(style)};true", owner.webView)
        try capture("dark-appearance-none-experiment", owner, output)
        // A separate navigation gives hidden WebKit a fresh painting lifecycle.
        // This remains an explicit experiment, never an accepted release check.
        owner.webView.configuration.userContentController.addUserScript(WKUserScript(source: "document.addEventListener('DOMContentLoaded',()=>{const style=document.createElement('style');style.textContent='.workspace-shell button{appearance:none!important;-webkit-appearance:none!important}';document.head.append(style)},{once:true})", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        phase = "appearance-none reload"
        owner.webView.load(URLRequest(url: url))
        try wait { owner.workspaceControlsReady && truth("!!document.querySelector('.task-item[data-task]')", owner.webView) }
        _ = try script("document.querySelector('.task-item[data-task]').click();true", owner.webView)
        try wait { truth("!!document.querySelector('.task-item[aria-current=true]')", owner.webView) }
        try capture("dark-appearance-none-reload-experiment", owner, output)
        guard !owner.window.isVisible && NSApp.activationPolicy() == .prohibited else { throw NSError(domain: "Unexpected foreground window", code: 1) }
        return ["status": "inspected", "themes": themes, "native_window_opened": false,
                "screens": ["light-original.png", "dark-original.png", "dark-appearance-none-experiment.png", "dark-appearance-none-reload-experiment.png"],
                "appearance_none_injected_for_comparison_only": true]
    }
    static func main() {
        do {
            let result = try run(URL(string: CommandLine.arguments[1])!, URL(fileURLWithPath: CommandLine.arguments[2]))
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: ["status": "failed", "error": String(describing: error)], options: [.prettyPrinted, .sortedKeys]))
            exit(1)
        }
    }
}
