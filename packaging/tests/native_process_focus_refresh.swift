// Hidden current-candidate AppKit/WK lifecycle. No OS clipboard or system input.
import AppKit
import WebKit

@main
struct NativeProcessFocusRefresh {
    static var checks: [String] = []
    static var phase = "setup"
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "Chat refresh timed out: " + phase, code: 1) }
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
        view.configuration.userContentController.addUserScript(WKUserScript(source:
            "window.__copyProbe={pending:true,writes:[]};Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText(text){__copyProbe.writes.push(text);return __copyProbe.pending?new Promise(resolve=>window.__resolveCopy=resolve):Promise.resolve()}}});document.execCommand=()=>{throw Error('Unexpected clipboard fallback')};",
            injectionTime: .atDocumentStart, forMainFrameOnly: true))
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
        try check("actual packaged service authenticates workspace", view.url?.path == "/")
        _ = try script("api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);setTask(task);stopWatching();window.__case=task.id});true", view)
        phase = "case"
        try wait { truth("!!window.__case", view) }
        _ = try script("selected.messages=[{role:'user',content:'离线验收：研究事件的影响。'},{role:'assistant',content:'历史回答应当保持稳定，进度变化不能重建已经显示的文字。'}];selected.status='运行中';selected.nodes=[{id:'read',title:'读取原件',status:'运行中'},{id:'verify',title:'核验时点',status:'待执行'}];selected.theses=[];selected.questions=[];selected.gaps=[];selected.gap_records=[];selected.delivery=null;render();document.querySelector('.chat-progress').open=true;{const input=document.querySelector('#chat-input');input.value='原生阅读中的未发送问题';input.dispatchEvent(new Event('input',{bubbles:true}));}true", view)
        _ = try script("window.__processFocus={details:document.querySelector('.chat-progress'),summary:document.querySelector('.chat-progress summary'),label:document.querySelector('.runtime-state-label')};__processFocus.details.open=true;__processFocus.summary.focus({preventScroll:true});selected.nodes[0].status='成功';selected.nodes[1].status='运行中';render();true", view)
        try check("actual WK process summary stays focused", truth("document.activeElement===__processFocus.summary&&__processFocus.summary.isConnected", view))
        try check("actual WK disclosure and live region are retained", truth("document.querySelector('.chat-progress')===__processFocus.details&&document.querySelector('.runtime-state-label')===__processFocus.label&&__processFocus.label.textContent==='核验时点'", view))
        _ = try script("document.querySelector('.chat-progress [data-stage=\"1\"]').focus({preventScroll:true});window.__processView=document.activeElement;selected.nodes[1].title='继续核验原件';render();true", view)
        try check("actual WK evidence control focus is not rebuilt", truth("document.activeElement===__processView&&__processView.isConnected", view))
        _ = try script("selected.nodes[0].status='运行中';selected.nodes[1].status='待执行';selected.nodes[1].title='核验时点';render();true", view)
        _ = try script("{const rows=document.querySelectorAll('#chat>.message');const body=rows[1].querySelector('.message__body');const text=document.createTreeWalker(body,NodeFilter.SHOW_TEXT).nextNode();const range=document.createRange();range.setStart(text,0);range.setEnd(text,10);getSelection().removeAllRanges();getSelection().addRange(range);const button=rows[0].querySelector('[data-action=copy-message]');window.__snapshot={user:rows[0],assistant:rows[1],button,text:getSelection().toString(),orb:document.querySelector('.rh-process canvas')};}true", view)
        try check("real WK text selection exists before refresh", truth("__snapshot.text.length===10", view))
        _ = try script("selected.nodes[0].status='成功';selected.nodes[1].status='运行中';render();true", view)
        try check("progress label changes to actual next node", truth("document.querySelector('.runtime-state-label').textContent==='核验时点'", view))
        try check("previous user remains connected", truth("document.querySelector('#chat>.message')===__snapshot.user", view))
        try check("previous assistant remains connected", truth("document.querySelectorAll('#chat>.message')[1]===__snapshot.assistant", view))
        try check("real WK selected text survives refresh", truth("getSelection().toString()===__snapshot.text", view))
        try check("OH geometry and disclosure survive refresh", truth("document.querySelector('.rh-process canvas')===__snapshot.orb&&document.querySelector('.chat-progress').open", view))
        // WebKit focuses a control by clearing an unrelated text selection.
        // Verify these two user states independently rather than fighting that.
        _ = try script("__snapshot.button.focus({preventScroll:true});true", view)
        try check("real WK message action owns keyboard focus", truth("document.activeElement===__snapshot.button", view))
        _ = try script("selected.nodes[1].title='继续核验时点';render();true", view)
        try check("real WK keyboard focus survives refresh", truth("document.activeElement===__snapshot.button", view))
        _ = try script("__snapshot.button.click();__snapshot.button.click();selected.nodes[1].title='核验来源日期';render();true", view)
        try check("pending-copy probe actually updates progress", truth("document.querySelector('.runtime-state-label').textContent==='核验来源日期'", view))
        try check("pending copy survives independent progress update", truth("document.querySelector('#chat [data-action=copy-message]')===__snapshot.button&&__snapshot.button.getAttribute('aria-busy')==='true'&&__copyProbe.writes.length===1", view))
        _ = try script("__resolveCopy();true", view)
        phase = "copy completion"
        try wait { truth("__snapshot.button.dataset.copied==='true'", view) }
        try check("pending copy completes on same visible message", truth("__snapshot.button.isConnected&&__snapshot.button.getAttribute('aria-label')==='已复制'", view))
        try check("stub captured real body without OS clipboard", truth("__copyProbe.writes[0]===__snapshot.user.querySelector('.message__body').innerText", view))
        _ = try script("selected.messages.push({role:'assistant',content:'新增回答'});render();true", view)
        try check("appending retains prior message and focus", truth("document.querySelectorAll('#chat>.message')[1]===__snapshot.assistant&&document.activeElement===__snapshot.button", view))
        _ = try script("window.__previousResult=document.querySelector('#chat>.message:last-child');selected.theses=[{title:'判断更新',investment_meaning:'新证据改变结论。',limitation:'离线验收内容。',support:[]}];render();true", view)
        try check("changed result is rendered rather than reused stale", truth("document.querySelector('#chat>.message:last-child')!==__previousResult&&document.querySelector('#chat>.message:last-child').textContent.includes('判断更新')", view))
        _ = try script("document.querySelector('#chat>.message:last-child [data-action=copy-message]').click();selected.theses[0].investment_meaning='修订后的判断';render();__resolveCopy();true", view)
        try check("late copy cannot label replacement result", truth("!document.querySelector('#chat>.message:last-child [data-copied=true]')", view))
        _ = try script("document.querySelector('.chat-progress summary').focus({preventScroll:true});window.__terminalSummary=document.activeElement;selected.status='已完成';selected.nodes[1].status='成功';render();true", view)
        try check("actual WK terminal status keeps disclosure focus", truth("document.activeElement===__terminalSummary&&document.querySelector('.runtime-state-label').textContent==='研究已完成'&&!document.querySelector('.rh-process canvas')", view))
        phase = "copy reset"
        try wait { truth("__snapshot.button.getAttribute('aria-label')==='复制我的消息'", view) }
        _ = try script("__snapshot.button.click();window.__oldTask=selected;api('/api/cases',{id:boot.cases[1].id}).then(task=>{task.messages=structuredClone(__oldTask.messages);tasks.unshift(task);setTask(task);stopWatching();__resolveCopy();window.__switched=true});true", view)
        phase = "task switch"
        try wait { truth("window.__switched===true", view) }
        try check("identical history never reuses another task message", truth("!__snapshot.button.isConnected&&document.querySelector('#chat [data-action=copy-message]')!==__snapshot.button", view))
        try check("late old task result never labels new task", truth("!document.querySelector('#chat [data-copied=true]')", view))
        try check("former task draft is preserved independently", truth("draft.composerTexts?.[__oldTask.id]==='原生阅读中的未发送问题'", view))
        try check("no visible window or persistent web store", !owner.window.isVisible && NSApp.activationPolicy() == .prohibited && !view.configuration.websiteDataStore.isPersistent)
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
