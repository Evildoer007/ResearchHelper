// Actual AppKit controls and menu dispatch into isolated, hidden WebKit.
// No window activation, posted keyboard event, system dialog or real research.
import AppKit
import WebKit

@main
struct NativeTaskSearch {
    static var checks: [String] = []
    static var phase = "setup"
    static weak var diagnosticView: WKWebView?
    static var failureDOM: Any = NSNull()
    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }
    static func wait(_ condition: () -> Bool) throws {
        let deadline = Date().addingTimeInterval(20)
        while !condition() && Date() < deadline { RunLoop.main.run(until: Date().addingTimeInterval(0.02)) }
        guard condition() else {
            if let view=diagnosticView {
                var done=false
                view.evaluateJavaScript("({route:location.pathname,readyState:document.readyState,loginDisabled:document.querySelector('#login-submit')?.disabled,loginBoot:document.documentElement.classList.contains('login-boot'),errors:window.__nativeSearchErrors||[],modal:!!document.querySelector('.task-search-dialog[open]'),inert:document.querySelector('.task-search-dialog')?.inert,focus:document.activeElement?.tagName,searchCount:window.__nativeSearchCount,animations:document.getAnimations().map(a=>({state:a.playState,time:a.currentTime,iterations:a.effect?.getComputedTiming().iterations})),ready:typeof workspaceReady==='undefined'?null:workspaceReady})") { value,_ in failureDOM=value ?? NSNull();done=true }
                let diagnosticDeadline=Date().addingTimeInterval(2)
                while !done&&Date()<diagnosticDeadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
            }
            throw NSError(domain: "Native search wait: " + phase, code: 1)
        }
    }
    static func script(_ code: String, _ view: WKWebView) throws -> Any? {
        var done = false, result: Any?, failure: Error?
        view.evaluateJavaScript(code) { value, error in result=value;failure=error;done=true }
        try wait { done }
        if let failure { throw failure }
        return result
    }
    static func truth(_ code: String, _ view: WKWebView) -> Bool {
        (try? script(code, view)) as? Bool == true
    }
    static func flushSurface(_ view: WKWebView) throws {
        // Match hidden snapshot acceptance: finish only real finite animations,
        // then request a real WebKit paint. Do not mock RAF or mutate UI state.
        _ = try script("document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getComputedTiming().iterations)){try{a.finish()}catch{}}});true",view)
        let configuration=WKSnapshotConfiguration();configuration.afterScreenUpdates=true
        var done=false,failure:Error?
        view.takeSnapshot(with:configuration){_,error in failure=error;done=true}
        try wait{done}
        if let failure{throw failure}
    }
    static func closeSearch(_ view: WKWebView) throws {
        phase="close search after actual control action"
        _ = try script("document.querySelector('.task-search-dialog header button').click();true",view)
        try flushSurface(view)
        try wait { truth("!document.querySelector('.task-search-dialog[open]')",view) }
    }
    static func run(_ url: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let previousMenu = NSApp.mainMenu
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        owner.window = ResearchHelperWindow(contentRect:NSRect(x:0,y:0,width:1440,height:900),
            styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false
        owner.webView=owner.createWebView(persistent:false)
        owner.webView.configuration.userContentController.addUserScript(WKUserScript(source:"window.__nativeSearchErrors=[];addEventListener('error',event=>window.__nativeSearchErrors.push(event.message));",injectionTime:.atDocumentStart,forMainFrameOnly:true))
        diagnosticView=owner.webView
        owner.window.contentView=owner.webView
        owner.installMainMenu();owner.installNativeControls()
        defer {
            owner.cancelFilePanels();owner.webView.stopLoading()
            owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
            owner.window.close();NSApp.mainMenu=previousMenu
        }
        let viewMenu=NSApp.mainMenu!.items.first{$0.submenu?.title=="显示"}!.submenu!
        let searchIndex=viewMenu.items.firstIndex{$0.action == #selector(ResearchHelperApp.searchTasks)}!
        let item=viewMenu.items[searchIndex],button=owner.nativeControls[1]
        try check("OH toolbar tooltip and accessibility name",button.toolTip=="搜索任务"&&button.accessibilityLabel()=="搜索任务")
        try check("OH delivery toggle names both operations",owner.nativeControls[2].toolTip=="打开或收起任务与交付"&&owner.nativeControls[2].accessibilityLabel()=="打开或收起任务与交付")
        try check("native and web share Cmd K",item.title=="搜索任务"&&item.keyEquivalent=="k"&&item.keyEquivalentModifierMask == .command)
        try check("search menu owns real controller",item.target === owner)
        try check("login gates native menu and toolbar",!owner.validateMenuItem(item) && button.isHidden && !button.isEnabled)
        owner.webView.load(URLRequest(url:url))
        phase="login"
        try wait{truth("!!document.querySelector('#login-submit:not([disabled])')",owner.webView)}
        phase="login startup release"
        try flushSurface(owner.webView)
        try wait{truth("!document.documentElement.classList.contains('login-boot')",owner.webView)}
        _ = try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",owner.webView)
        phase="workspace"
        try wait{owner.workspaceControlsReady}
        try check("authenticated readiness exposes toolbar and menu",owner.validateMenuItem(item) && !button.isHidden && button.isEnabled)
        _ = try script("window.__nativeSearchCount=0;addEventListener('researchhelper:search-tasks',()=>window.__nativeSearchCount++);api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);setTask(task);window.__nativeSearchCase=task.id}).catch(e=>window.__nativeSearchError=e.message);true",owner.webView)
        phase="offline case"
        try wait{truth("!!window.__nativeSearchCase",owner.webView)}
        _ = try script("window.__nativeFrozenResearch=JSON.stringify(tasks);document.querySelector('#chat-input').value='原生搜索检查保留的草稿';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#chat-input').focus();true",owner.webView)
        viewMenu.performActionForItem(at:searchIndex)
        phase="actual menu action"
        try wait{truth("!!document.querySelector('.task-search-dialog[open]')",owner.webView)}
        try check("actual NSMenu action opens search once",truth("window.__nativeSearchCount===1&&document.querySelectorAll('.task-search-dialog').length===1",owner.webView))
        try check("menu search uses named focused modal",truth("document.querySelector('.task-search-dialog').getAttribute('aria-label')==='搜索任务'&&document.activeElement===document.querySelector('.task-search-dialog input')",owner.webView))
        try check("search includes actual offline research",truth("Array.from(document.querySelectorAll('.task-search-dialog>div button')).some(b=>b.textContent===taskTitle(selected))",owner.webView))
        try closeSearch(owner.webView)
        try check("real close returns to visible composer",truth("document.activeElement===document.querySelector('#chat-input')",owner.webView))
        button.performClick(nil)
        phase="actual toolbar action"
        try wait{truth("!!document.querySelector('.task-search-dialog[open]')",owner.webView)}
        try check("actual NSButton action uses same singleton",truth("window.__nativeSearchCount===2&&document.querySelectorAll('.task-search-dialog').length===1",owner.webView))
        _ = try script("const input=document.querySelector('.task-search-dialog input');input.value='不存在的原生搜索结果';input.dispatchEvent(new Event('input'));true",owner.webView)
        try check("native WebKit no-match state",truth("document.querySelector('.task-search-dialog>div').textContent==='未找到任务或报告'",owner.webView))
        try closeSearch(owner.webView)
        let event=NSEvent.keyEvent(with:.keyDown,location:.zero,modifierFlags:.command,timestamp:0,
            windowNumber:owner.window.windowNumber,context:nil,characters:"k",charactersIgnoringModifiers:"k",isARepeat:false,keyCode:40)!
        viewMenu.update()
        let handled=viewMenu.performKeyEquivalent(with:event)
        phase="AppKit synthetic key equivalent"
        try wait{truth("!!document.querySelector('.task-search-dialog[open]')",owner.webView)}
        try check("AppKit Cmd K dispatch without OS keyboard automation",handled&&truth("window.__nativeSearchCount===3",owner.webView))
        try closeSearch(owner.webView)
        for (width,height) in [(1440,900),(1024,768),(390,844)] {
            owner.window.setContentSize(NSSize(width:width,height:height));owner.webView.layoutSubtreeIfNeeded()
            for scale in owner.scaleSteps {
                owner.applyScale(scale);button.performClick(nil);phase="native layout \(width) scale \(scale)"
                try wait{truth("!!document.querySelector('.task-search-dialog[open]')",owner.webView)}
                try check(phase+" fits viewport",truth("(()=>{const r=document.querySelector('.task-search-dialog').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=0&&r.bottom<=innerHeight+1})()",owner.webView))
                try check(phase+" uses native zoom only",truth("document.documentElement.style.zoom===''&&document.documentElement.dataset.nativeShell==='macos'",owner.webView))
                try closeSearch(owner.webView)
            }
        }
        owner.applyScale(1)
        owner.nativeControls[2].performClick(nil);phase="actual delivery toolbar open"
        try wait{truth("!document.querySelector('#task-context').hidden",owner.webView)}
        try check("native delivery button opens actual panel",truth("contextOpen&&!document.querySelector('#task-context').hidden",owner.webView))
        owner.nativeControls[2].performClick(nil);phase="actual delivery toolbar close"
        try wait{truth("document.querySelector('#task-context').hidden",owner.webView)}
        try check("native delivery button also closes actual panel",truth("!contextOpen&&document.querySelector('#task-context').hidden",owner.webView))
        try check("search never changes research or budget",truth("JSON.stringify(tasks)===window.__nativeFrozenResearch&&selected.ledger.used.model_calls===0&&selected.ledger.used.data_requests===0",owner.webView))
        try check("all search entries preserve draft",truth("document.querySelector('#chat-input').value==='原生搜索检查保留的草稿'",owner.webView))
        let count=try script("window.__nativeSearchCount",owner.webView) as? Int
        owner.setWorkspaceControlsReady(false)
        viewMenu.performActionForItem(at:searchIndex);button.performClick(nil)
        try check("invalidated native search does not dispatch",!owner.validateMenuItem(item) && button.isHidden && !button.isEnabled && (try script("window.__nativeSearchCount",owner.webView) as? Int)==count)
        try check("no window activated or system panel presented",!owner.window.isVisible&&NSApp.activationPolicy() == .prohibited&&NSApp.windows.allSatisfy{!$0.isVisible})
    }
    static func main(){
        do{
            try run(URL(string:CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"actual_os_keyboard_posted":false,"finite_animations_finished":true],options:[.sortedKeys]))
        }catch{
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"phase":phase,"dom":failureDOM,"error":String(describing:error)],options:[.sortedKeys]));exit(1)
        }
    }
}
