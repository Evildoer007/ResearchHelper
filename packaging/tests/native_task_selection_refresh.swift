// Hidden candidate-bound task selection. No system input or OS clipboard.
import AppKit
import WebKit

@main
struct NativeTaskSelectionRefresh {
    static var checks: [String] = []
    static var phase = "setup"
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else { throw NSError(domain: "Task selection timed out: " + phase, code: 1) }
    }
    static func script(_ text: String, _ view: WKWebView) throws -> Any? {
        var done = false, value: Any?, error: Error?
        view.evaluateJavaScript(text) { result, failure in value=result;error=failure;done=true }
        try wait { done }
        if let failure=error { throw failure }
        return value
    }
    static func truth(_ text: String, _ view: WKWebView) -> Bool { (try? script(text,view)) as? Bool == true }
    static func run(_ url: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let owner=ResearchHelperApp()
        owner.runtimeURL=url
        owner.window=ResearchHelperWindow(contentRect:NSRect(x:0,y:0,width:1440,height:900),
            styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false;owner.window.delegate=owner
        owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView
        let view=owner.webView!
        defer { view.stopLoading();view.configuration.userContentController.removeAllScriptMessageHandlers();owner.window.delegate=nil;owner.window.close() }
        view.load(URLRequest(url:url))
        phase="login"
        try wait { truth("!!document.querySelector('#login-submit:not([disabled])')",view) }
        _=try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",view)
        phase="workspace"
        try wait { owner.workspaceControlsReady }
        try check("actual bundled backend authenticates workspace",view.url?.path=="/")
        _=try script("(async()=>{const a=await api('/api/cases',{id:boot.cases[0].id}),b=await api('/api/cases',{id:boot.cases[1].id});tasks.unshift(a,b);setTask(a);stopWatching();window.__nativeTasks=[a,b];await loadSelectedTask(b.id);window.__realSelectionId=selected.id;setTask(a);stopWatching();window.__nativeReady=true;})();true",view)
        phase="actual read"
        try wait { truth("window.__nativeReady===true",view) }
        try check("actual WK accepts real task GET from bundled backend",truth("__realSelectionId===__nativeTasks[1].id",view))
        try check("OH transition module included in actual DMG",truth("typeof RHConversationTransition.createConversationTransition==='function'",view))
        _=try script("window.__realAPI=api;window.__nativeReads=[];api=async(path,body)=>{if(body)throw Error('Unexpected mutation');if(/^\\/api\\/tasks\\/[^/]+$/.test(path))return new Promise((resolve,reject)=>__nativeReads.push({path,resolve,reject}));return __realAPI(path,body)};window.__releaseNative=(index,failure=false)=>{const read=__nativeReads[index];if(failure)read.reject(Error('原生合成读取失败'));else{const id=read.path.split('/').pop(),task=structuredClone(__nativeTasks.find(task=>task.id===id));task.revision+=5;task.question='原生读取最新研究';read.resolve(task)}};document.querySelector('#chat-input').value='任务A原生草稿';captureDraft();selectTaskEntry('task',__nativeTasks[1].id);true",view)
        phase="pending read"
        try wait { truth("__nativeReads.length===1",view) }
        try check("real WK retains readable old task during read",truth("selected.id===__nativeTasks[0].id&&document.querySelector('#chat').textContent.length>0",view))
        try check("real WK loading isolates composer",truth("document.querySelector('#chat-form').inert&&document.querySelector('#chat').getAttribute('aria-busy')==='true'",view))
        _=try script("__releaseNative(0);true",view)
        phase="commit"
        try wait { truth("selected.id===__nativeTasks[1].id&&!selectingTaskId",view) }
        try check("latest research replaces stale bootstrap snapshot",truth("selected.question==='原生读取最新研究'",view))
        try check("real WK releases composer and busy state",truth("!document.querySelector('#chat-form').inert&&!document.querySelector('#chat').hasAttribute('aria-busy')",view))
        _=try script("selectTaskEntry('task',__nativeTasks[0].id);true",view)
        phase="return"
        try wait { truth("__nativeReads.length===2",view) }
        _=try script("__releaseNative(1);true",view)
        try wait { truth("selected.id===__nativeTasks[0].id&&!selectingTaskId",view) }
        try check("actual WK restores task-owned draft",truth("document.querySelector('#chat-input').value==='任务A原生草稿'",view))
        _=try script("selectTaskEntry('task',__nativeTasks[1].id);document.querySelector('[data-action=new]').click();__releaseNative(2);true",view)
        try check("new task cancels pending selection without foreign commit",truth("selected===null&&!selectingTaskId&&!document.querySelector('#chat-form').inert",view))
        _=try script("setTask(tasks.find(task=>task.id===__nativeTasks[0].id));stopWatching();selectTaskEntry('task',__nativeTasks[1].id);__releaseNative(3,true);true",view)
        phase="failure"
        try wait { truth("!selectingTaskId",view) }
        try check("failed WK read preserves task and actual reason",truth("selected.id===__nativeTasks[0].id&&document.querySelector('#notice').textContent==='原生合成读取失败'",view))
        try check("failed WK read restores own draft and controls",truth("!document.querySelector('#chat-form').inert&&document.querySelector('#chat-input').value==='任务A原生草稿'",view))
        _=try script("selectTaskEntry('task',__nativeTasks[1].id);switchMode('desk');__releaseNative(4);true",view)
        try check("mode switch invalidates old pending conversation",truth("selected.id===__nativeTasks[0].id&&artifactsOpen&&!selectingTaskId",view))
        try check("no visible window or persistent web store",!owner.window.isVisible && NSApp.activationPolicy() == .prohibited && !view.configuration.websiteDataStore.isPersistent)
    }
    static func main() {
        do { try run(URL(string:CommandLine.arguments[1])!);FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"phase":phase,"native_window_opened":false],options:[.prettyPrinted,.sortedKeys])) }
        catch { FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"phase":phase,"error":String(describing:error)],options:[.prettyPrinted,.sortedKeys]));exit(1) }
    }
}
