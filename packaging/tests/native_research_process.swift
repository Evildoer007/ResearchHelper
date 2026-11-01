// Hidden actual RH shell. Synthetic presentation only, no external execution.
import AppKit
import WebKit

@main
struct NativeResearchProcess {
    static var checks:[String]=[]
    static var phase="setup"
    static var geometry:Any=[]
    static func check(_ name:String,_ value:Bool)throws{guard value else{throw NSError(domain:name,code:1)};checks.append(name)}
    static func wait(_ condition:()->Bool)throws{
        let deadline=Date().addingTimeInterval(20)
        while !condition() && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
        guard condition() else{throw NSError(domain:"Native process wait: "+phase,code:1)}
    }
    static func script(_ code:String,_ view:WKWebView)throws->Any?{
        var done=false,result:Any?,failure:Error?
        view.evaluateJavaScript(code){value,error in result=value;failure=error;done=true};try wait{done};if let failure{throw failure};return result
    }
    static func truth(_ code:String,_ view:WKWebView)->Bool{(try? script(code,view)) as? Bool == true}
    static func paint(_ view:WKWebView)throws{
        _=try script("document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getComputedTiming().iterations)){try{a.finish()}catch{}}});true",view)
        let configuration=WKSnapshotConfiguration();configuration.afterScreenUpdates=true
        var done=false,failure:Error?
        view.takeSnapshot(with:configuration){_,error in failure=error;done=true};try wait{done};if let failure{throw failure}
    }
    static func run(_ url:URL)throws{
        NSApplication.shared.setActivationPolicy(.prohibited);NSApp.finishLaunching()
        let previous=NSApp.mainMenu,owner=ResearchHelperApp();owner.runtimeURL=url
        owner.window=ResearchHelperWindow(contentRect:NSRect(x:0,y:0,width:1440,height:900),styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false;owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView
        owner.installMainMenu();owner.installNativeControls()
        let view=owner.webView!
        defer{view.stopLoading();view.configuration.userContentController.removeAllScriptMessageHandlers();owner.window.close();NSApp.mainMenu=previous}
        view.load(URLRequest(url:url));phase="login controls"
        try wait{truth("!!document.querySelector('#login-submit:not([disabled])')",view)};phase="login paint";try paint(view)
        phase="login transition"
        try wait{truth("!document.documentElement.classList.contains('login-boot')",view)}
        _=try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",view)
        phase="workspace";try wait{owner.workspaceControlsReady}
        _=try script("api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);setTask(task);window.__processCase=task.id});true",view)
        phase="case";try wait{truth("!!window.__processCase",view)}
        _=try script("stopWatching();tasks[0].status='运行中';tasks[0].nodes=[{id:'verify',title:'核验材料来源',status:'运行中'},{id:'analyze',title:'分析投资逻辑',status:'待执行'}];tasks[0].delivery=null;render();window.__process=()=>document.querySelector('.rh-process');window.__orb=__process().querySelector('canvas');true",view);try paint(view)
        try check("OH process DOM and true activity label",truth("__process().classList.contains('message--process')&&__process().querySelector('.runtime-state-label').textContent==='核验材料来源'",view))
        try check("actual OH geometric canvas painted",truth("__orb.dataset.renderMode==='canvas'&&[...__orb.getContext('2d').getImageData(0,0,__orb.width,__orb.height).data].some((value,index)=>index%4===3&&value>0)",view))
        try check("details start collapsed",truth("!__process().querySelector('details').open",view))
        _=try script("__process().querySelector('summary').click();tasks[0].nodes[0].status='成功';tasks[0].nodes[1].status='运行中';render();true",view);try paint(view)
        try check("progress update preserves same orb and open disclosure",truth("__process().querySelector('canvas')===__orb&&__process().querySelector('details').open&&__process().querySelector('.runtime-state-label').textContent==='分析投资逻辑'",view))
        try check("actual node count and no invented percent",truth("__process().querySelector('.process-state').textContent==='已处理1/2项'&&__process().querySelectorAll('.process-events li').length===2&&!__process().textContent.includes('%')",view))
        _=try script("pendingTaskActions.add(tasks[0].id);render();true",view)
        try check("pending action disables chat and context controls",truth("__process().querySelector('[data-action=cancel]').disabled&&document.querySelector('#task-actions [data-action=cancel]').disabled",view))
        _=try script("pendingTaskActions.clear();delete tasks[0].pending_html;tasks[0].status='失败';tasks[0].error='合成原件 <b>缺少时点</b>';tasks[0].gaps=[tasks[0].error];render();true",view);try paint(view)
        try check("failure stops and disposes actual animation",truth("__orb.dataset.playback==='disposed'&&!__process().querySelector('canvas')",view))
        try check("failure literal and non-duplicated",truth("__process().querySelector('.process-failure').textContent==='合成原件 <b>缺少时点</b>'&&!__process().querySelector('.process-failure b')&&!document.querySelector('#chat .chat-gaps')",view))
        try check("recovery in actual conversation",truth("__process().querySelector('[data-action=resume]').textContent==='恢复原版本'",view))
        _=try script("tasks[0].pending_html='synthetic saved result';render();true",view)
        try check("save failure retry is distinct from resume",truth("!!__process().querySelector('[data-action=retry-save]')&&!__process().querySelector('[data-action=resume]')",view))
        _=try script("delete tasks[0].pending_html;tasks[0].error=null;tasks[0].gaps=[];tasks[0].status='待开始';tasks[0].nodes=[];render();true",view)
        try check("ready does not animate work",truth("!__process().querySelector('canvas')&&!!__process().querySelector('[data-action=start]')",view))
        _=try script("tasks[0].status='运行中';tasks[0].nodes=[{id:'verify',title:'核验材料来源',status:'运行中'}];render();window.__processFrozen=JSON.stringify(tasks);document.querySelector('#chat-input').value='原生运行状态保留草稿';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));true",view)
        for size in [NSSize(width:1440,height:900),NSSize(width:1024,height:768),NSSize(width:390,height:844)]{
            owner.window.setContentSize(size);view.frame=owner.window.contentView!.bounds
            for scale in [0.8,0.9,1.0,1.1,1.25,1.4]{
                phase="native process \(Int(size.width)) \(scale)";owner.applyScale(CGFloat(scale));try paint(view)
                try check(phase+" uses WK pageZoom only",view.pageZoom==scale&&truth("!document.documentElement.hasAttribute('data-browser-scale')",view))
                geometry=try script("(()=>{const panel=__process(),r=panel.getBoundingClientRect();return {width:innerWidth,scroll:document.documentElement.scrollWidth,left:r.left,right:r.right,font:getComputedStyle(panel.querySelector('.runtime-state-label')).fontSize}})()",view) ?? []
                // Actual WK pageZoom serializes the inherited 13px at 1.4 as
                // 12.999999px. Bound only that observed floating error; retain
                // the independent viewport bounds and one-pixel scroll rounding.
                try check(phase+" process fits viewport",truth("document.documentElement.scrollWidth<=innerWidth+1&&__process().getBoundingClientRect().right<=innerWidth+1&&parseFloat(getComputedStyle(__process().querySelector('.runtime-state-label')).fontSize)>=13-0.00001",view))
            }
        }
        owner.applyScale(1);owner.window.setContentSize(NSSize(width:1440,height:900));view.frame=owner.window.contentView!.bounds;try paint(view)
        _=try script("document.querySelector('[data-mode-switch] [data-mode=desk]').click();document.querySelector('[data-action=assistant-toggle]').click();true",view);try paint(view)
        try check("ResDesk uses same process state",truth("!!__process().querySelector('canvas')&&__process().querySelector('.runtime-state-label').textContent==='核验材料来源'",view))
        try check("research budget and draft not changed",truth("JSON.stringify(tasks)===__processFrozen&&document.querySelector('#chat-input').value==='原生运行状态保留草稿'",view))
        _=try script("window.__closingOrb=__process().querySelector('canvas');window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:false}));true",view)
        try check("teardown explicitly releases OH scheduler",truth("__closingOrb.dataset.playback==='disposed'",view))
        try check("all windows hidden",NSApp.windows.allSatisfy{!$0.isVisible})
    }
    static func main(){
        do{guard CommandLine.arguments.count==2,let url=URL(string:CommandLine.arguments[1]) else{throw NSError(domain:"Missing isolated service",code:1)};try run(url)
            let data=try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"phase":phase,"geometry":geometry],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!)
        }catch{let data=try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"phase":phase,"geometry":geometry,"error":String(describing:error)],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!);exit(1)}
    }
}
