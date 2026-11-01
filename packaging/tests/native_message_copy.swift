// Hidden actual RH WK controller; clipboard and execCommand stubs prevent OS writes.
import AppKit
import WebKit

@main
struct NativeMessageCopy {
    static var checks:[String]=[]
    static var phase="setup"
    static func check(_ name:String,_ value:Bool)throws{guard value else{throw NSError(domain:name,code:1)};checks.append(name)}
    static func wait(_ condition:()->Bool)throws{
        let deadline=Date().addingTimeInterval(20)
        while !condition() && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
        guard condition() else{throw NSError(domain:"Native copy wait: "+phase,code:1)}
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
        let owner=ResearchHelperApp();owner.runtimeURL=url
        owner.window=ResearchHelperWindow(contentRect:NSRect(x:0,y:0,width:1440,height:900),styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false;owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView
        let view=owner.webView!
        view.configuration.userContentController.addUserScript(WKUserScript(source:"window.__copyProbe={writes:[],fallbacks:[],mode:'success',fallback:true};Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText(text){__copyProbe.writes.push(text);if(__copyProbe.mode==='pending')return new Promise((resolve,reject)=>{window.__copyResolve=resolve;window.__copyReject=reject});return __copyProbe.mode==='reject'?Promise.reject(Error('synthetic denial')):Promise.resolve()}}});document.execCommand=name=>{if(name!=='copy')return false;const field=document.querySelector('body>textarea');__copyProbe.fallbacks.push({text:field?.value,readonly:field?.readOnly});return __copyProbe.fallback};",injectionTime:.atDocumentStart,forMainFrameOnly:true))
        defer{view.stopLoading();view.configuration.userContentController.removeAllScriptMessageHandlers();owner.window.close()}
        view.load(URLRequest(url:url));phase="login"
        try wait{truth("!!document.querySelector('#login-submit:not([disabled])')",view)};try paint(view)
        try wait{truth("!document.documentElement.classList.contains('login-boot')",view)}
        _=try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",view)
        phase="workspace";try wait{owner.workspaceControlsReady}
        _=try script("api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);setTask(task);window.__copyCase=task.id});true",view)
        phase="case";try wait{truth("!!window.__copyCase",view)}
        _=try script("window.__copyState=JSON.stringify(tasks);document.querySelector('#chat-input').value='复制后保留的原生草稿';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));window.__copyButton=()=>document.querySelector('#chat .message--user [data-action=copy-message]');__copyButton().focus();__copyButton().click();true",view)
        phase="success";try wait{truth("__copyButton().dataset.copied==='true'",view)}
        try check("OH success label and icon",truth("__copyButton().getAttribute('aria-label')==='已复制'&&__copyButton().querySelector('path').getAttribute('d')==='m5 12 4 4L19 6'",view))
        try check("native actual body captured by stub only",truth("__copyProbe.writes[0]===document.querySelector('#chat .message--user .message__body').innerText",view))
        try check("inline live status, no global toast",truth("document.querySelector('#chat .message--user [role=status]').textContent==='已复制'&&document.querySelector('#notice').textContent!=='已复制'",view))
        phase="reset";try wait{truth("__copyButton().getAttribute('aria-label')==='复制我的消息'",view)}
        _=try script("__copyProbe.mode='pending';__copyButton().click();__copyButton().click();true",view)
        try check("duplicate pending press ignored",truth("__copyProbe.writes.length===2&&__copyButton().getAttribute('aria-busy')==='true'",view))
        _=try script("__copyResolve();true",view);phase="pending completion";try wait{truth("__copyButton().dataset.copied==='true'",view)}
        phase="reset pending";try wait{truth("__copyButton().dataset.copied==='false'",view)}
        _=try script("__copyProbe.mode='reject';__copyButton().click();true",view);phase="fallback";try wait{truth("__copyButton().dataset.copied==='true'",view)}
        try check("OH fallback uses readonly temporary field",truth("__copyProbe.fallbacks.length===1&&__copyProbe.fallbacks[0].readonly&&__copyProbe.fallbacks[0].text===__copyProbe.writes.at(-1)",view))
        try check("temporary field removed",truth("!document.querySelector('body>textarea')",view))
        phase="reset fallback";try wait{truth("__copyButton().dataset.copied==='false'",view)}
        _=try script("__copyProbe.fallback=false;__copyButton().click();true",view);phase="failure";try wait{truth("__copyButton().getAttribute('aria-label')==='复制失败，请重试'",view)}
        try check("failure does not report success",truth("__copyButton().dataset.copied!=='true'&&document.querySelector('#chat .message--user [role=status]').textContent==='复制失败，请重试'",view))
        try check("research budget and draft remain unchanged",truth("JSON.stringify(tasks)===__copyState&&document.querySelector('#chat-input').value==='复制后保留的原生草稿'",view))
        _=try script("__copyProbe.mode='pending';__copyButton().click();window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:false}));window.__lateFallbackCount=__copyProbe.fallbacks.length;__copyReject(Error('late teardown denial'));true",view)
        try check("teardown rejection cannot run fallback",truth("__copyProbe.fallbacks.length===__lateFallbackCount",view))
        try check("teardown clears busy state",truth("__copyButton().getAttribute('aria-busy')!=='true'",view))
        try check("all windows hidden",NSApp.windows.allSatisfy{!$0.isVisible})
    }
    static func main(){
        do{guard CommandLine.arguments.count==2,let url=URL(string:CommandLine.arguments[1]) else{throw NSError(domain:"Missing isolated service",code:1)};try run(url)
            let data=try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"phase":phase],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!)
        }catch{let data=try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"phase":phase,"error":String(describing:error)],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!);exit(1)}
    }
}
