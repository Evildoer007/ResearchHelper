// Read-only historical reports in packaged web resources and a hidden WKWebView.
import AppKit
import WebKit

@main
struct NativeHistoricalComposerIsolation {
    static var checks: [String] = []
    static var phase = "setup"
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "Historical composer timed out: " + phase, code: 1) }
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
        owner.window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        owner.window.isReleasedWhenClosed = false
        owner.window.delegate = owner
        owner.webView = owner.createWebView(persistent: false)
        owner.window.contentView = owner.webView
        let view = owner.webView!
        defer {
            view.stopLoading()
            view.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.delegate = nil
            owner.window.close()
        }
        view.load(URLRequest(url: url))
        phase = "login"
        try wait { truth("!!document.querySelector('#login-submit:not([disabled])')", view) }
        _ = try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true", view)
        phase = "workspace"
        try wait { owner.workspaceControlsReady }
        _ = try script("api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);task.status='运行中';setTask(task);stopWatching();document.querySelector('#chat-input').value='保留原生研究草稿';captureDraft();window.__historicalNativeTask=task;window.__historicalNativeKey=Object.keys(boot.history)[0];window.__historicalNativeCalls=[];const original=api;api=async(path,body)=>{__historicalNativeCalls.push({path,body:body||null});if(body)throw Error('Historical probe forbids mutation');return original(path,body)};selectTaskEntry('history',__historicalNativeKey);window.__historicalNativeReady=true});true", view)
        phase = "historical report"
        try wait { truth("window.__historicalNativeReady===true", view) }
        try check("history selection retains the independent original task", truth("historical.key===__historicalNativeKey&&selected.id===__historicalNativeTask.id", view))
        try check("WK historical assistant is hidden and inert", truth("document.querySelector('.composer-dock').hidden&&document.querySelector('#assistant-panel').inert", view))
        try check("WK historical send is disabled and read-only", truth("document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='历史报告仅供阅读'", view))
        try check("WK history has no stale running indicator", truth("!document.querySelector('#chat-form button[type=submit]').classList.contains('is-generating')&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-busy')==='false'", view))
        try check("WK historical attachments are disabled", truth("document.querySelector('#chat-form [data-action=attach]').disabled", view))
        _ = try script("void sendChat();document.querySelector('#chat-input').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));document.querySelector('#chat-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));true", view)
        try check("WK direct and synthetic submits never read model settings or mutate research", truth("__historicalNativeCalls.length===0", view))
        try check("WK read-only feedback identifies recovery", truth("document.querySelector('#notice').textContent==='历史报告仅供阅读，请新建研究'", view))
        try check("WK historical rejection preserves original budget and draft", truth("JSON.stringify(selected.ledger)===JSON.stringify(__historicalNativeTask.ledger)&&draft.composerTexts[selected.id]==='保留原生研究草稿'", view))
        for stage in 0..<5 {
            _ = try script("view(\(stage));true", view)
            try check("WK history view \(stage) stays reachable and read-only", truth("document.querySelector('#view-\(stage)').getAttribute('aria-selected')==='true'&&document.querySelector('#chat-form button[type=submit]').disabled", view))
        }
        _ = try script("setTask(__historicalNativeTask);stopWatching();switchMode('chat');true", view)
        try check("WK return restores live task state and its draft", truth("!historical&&!document.querySelector('#assistant-panel').inert&&!document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='停止研究'&&document.querySelector('#chat-input').value==='保留原生研究草稿'", view))
        _ = try script("selectTaskEntry('history',__historicalNativeKey);document.querySelector('[data-action=new]').click();true", view)
        try check("WK explicit new task does not inherit historical provenance", truth("!selected&&!historical&&!artifactsOpen&&queuedMaterials.length===0&&pendingSubmission===null&&!document.querySelector('#chat-form button[type=submit]').disabled", view))
        try check("native probe stays hidden and nonpersistent", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited && !view.configuration.websiteDataStore.isPersistent)
    }
    static func main() {
        do {
            try run(URL(string: CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["status":"passed", "checks":checks, "phase":phase, "native_window_opened":false], options:[.prettyPrinted, .sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: ["status":"failed", "checks":checks, "phase":phase, "error":String(describing:error)], options:[.prettyPrinted, .sortedKeys]))
            exit(1)
        }
    }
}
