// Actual packaged UI in hidden WebKit. Mutation replies are local test stubs.
import AppKit
import WebKit

@main
struct NativeComposerOperationIdentity {
    static var checks: [String] = []
    static var phase = "setup"
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "Composer ownership timed out: " + phase, code: 1) }
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
        try check("actual readonly DMG backend authenticates hidden workspace", view.url?.path == "/")
        _ = try script("Promise.all([api('/api/cases',{id:boot.cases[0].id}),api('/api/cases',{id:boot.cases[1].id})]).then(items=>{tasks.unshift(...items);window.__nativeTasks=items;window.__nativeCalls=[];window.__nativePending={};window.__originalAPI=api;api=async(path,body)=>{if(/^\\/api\\/tasks\\/[^/]+\\/(cancel|resume)$/.test(path)){const index=__nativeCalls.length;__nativeCalls.push({path,body:structuredClone(body)});return new Promise((resolve,reject)=>__nativePending[index]={resolve,reject})}if(body&&(path==='/api/tasks'||path.endsWith('/messages')))throw Error('Unexpected question dispatch');return __originalAPI(path,body)};items[0].status='运行中';setTask(items[0]);stopWatching();window.__nativeReady=true});true", view)
        phase = "fixtures"
        try wait { truth("window.__nativeReady===true", view) }
        _ = try script("document.querySelector('#chat-input').value='原生任务A草稿';void sendChat();true", view)
        phase = "first cancel"
        try wait { truth("__nativeCalls.length===1", view) }
        try check("real WK pending stop is disabled and accurately labeled", truth("document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='正在停止研究'", view))
        try check("real WK pending stop exposes busy state", truth("document.querySelector('#chat-form button[type=submit]').getAttribute('aria-busy')==='true'", view))
        try check("cancel request binds the original task", truth("__nativeCalls[0].body.expected_task.id===__nativeTasks[0].id", view))
        _ = try script("void sendChat();render();true", view)
        try check("render and direct submit never replay cancellation", truth("__nativeCalls.length===1&&document.querySelector('#chat-form button[type=submit]').disabled", view))
        _ = try script("__nativeTasks[1].status='运行中';setTask(__nativeTasks[1]);stopWatching();document.querySelector('#chat-input').value='原生任务B草稿';true", view)
        try check("another task owns an enabled stop", truth("!document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='停止研究'", view))
        _ = try script("void sendChat();true", view)
        phase = "second cancel"
        try wait { truth("__nativeCalls.length===2", view) }
        _ = try script("__nativePending[0].resolve({...structuredClone(__nativeTasks[0]),status:'已取消',revision:__nativeTasks[0].revision+1});true", view)
        phase = "first cancel completes"
        try wait { truth("!pendingTaskActions.has(__nativeTasks[0].id)", view) }
        try check("late other-task completion cannot select or unlock the current task", truth("selected.id===__nativeTasks[1].id&&document.querySelector('#chat-form button[type=submit]').disabled", view))
        try check("current-task draft survives late completion", truth("document.querySelector('#chat-input').value==='原生任务B草稿'", view))
        _ = try script("window.__nativeLedger=JSON.stringify(selected.ledger);__nativePending[1].reject(Error('合成连接中断'));true", view)
        phase = "cancel fails"
        try wait { truth("!pendingTaskActions.has(__nativeTasks[1].id)", view) }
        try check("failed cancellation restores real running stop without budget mutation", truth("selected.status==='运行中'&&!document.querySelector('#chat-form button[type=submit]').disabled&&JSON.stringify(selected.ledger)===__nativeLedger&&__nativeCalls.length===2", view))
        _ = try script("selected.status='失败';render();void taskAction('resume').catch(error=>notice(error.message));true", view)
        phase = "resume pending"
        try wait { truth("__nativeCalls.length===3", view) }
        try check("real WK recovery blocks composer before new-question creation", truth("document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='正在处理研究操作'", view))
        _ = try script("void sendChat();__nativePending[2].reject(Error('合成恢复失败'));true", view)
        phase = "resume fails"
        try wait { truth("!pendingTaskActions.has(__nativeTasks[1].id)", view) }
        try check("failed recovery restores send and preserves independent drafts", truth("!document.querySelector('#chat-form button[type=submit]').disabled&&document.querySelector('#chat-form button[type=submit]').getAttribute('aria-label')==='发送研究问题'&&draft.composerTexts[__nativeTasks[0].id]==='原生任务A草稿'&&document.querySelector('#chat-input').value==='原生任务B草稿'&&__nativeCalls.length===3", view))
        try check("no foreground window or persistent web store", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited && !view.configuration.websiteDataStore.isPersistent)
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
