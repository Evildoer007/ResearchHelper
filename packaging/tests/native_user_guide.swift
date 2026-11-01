// Real hidden WKWebView with the RH App controller; no activated window or OS keys.
import AppKit
import WebKit

@main
struct NativeUserGuide {
    static var checks:[String]=[]
    static var phase="setup"
    static var geometry:Any=NSNull()
    static func check(_ name:String,_ value:Bool)throws{
        guard value else{throw NSError(domain:name,code:1)};checks.append(name)
    }
    static func wait(_ condition:()->Bool)throws{
        let deadline=Date().addingTimeInterval(20)
        while !condition() && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
        guard condition() else{throw NSError(domain:"Native guide wait: "+phase,code:1)}
    }
    static func script(_ code:String,_ view:WKWebView)throws->Any?{
        var done=false,result:Any?,failure:Error?
        view.evaluateJavaScript(code){value,error in result=value;failure=error;done=true}
        try wait{done};if let failure{throw failure};return result
    }
    static func truth(_ code:String,_ view:WKWebView)->Bool{(try? script(code,view)) as? Bool == true}
    static func paint(_ view:WKWebView)throws{
        // Finish only real finite animations and request WebKit's own snapshot.
        _=try script("document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getComputedTiming().iterations)){try{a.finish()}catch{}}});true",view)
        let configuration=WKSnapshotConfiguration();configuration.afterScreenUpdates=true
        var done=false,failure:Error?
        view.takeSnapshot(with:configuration){_,error in failure=error;done=true};try wait{done};if let failure{throw failure}
    }
    static func run(_ url:URL)throws{
        NSApplication.shared.setActivationPolicy(.prohibited);NSApp.finishLaunching()
        let previous=NSApp.mainMenu,owner=ResearchHelperApp()
        owner.runtimeURL=url
        owner.window=ResearchHelperWindow(contentRect:NSRect(x:0,y:0,width:1440,height:900),styleMask:[.titled,.closable,.resizable,.fullSizeContentView],backing:.buffered,defer:false)
        owner.window.isReleasedWhenClosed=false;owner.webView=owner.createWebView(persistent:false);owner.window.contentView=owner.webView
        owner.installMainMenu();owner.installNativeControls()
        defer{owner.webView.stopLoading();owner.webView.configuration.userContentController.removeAllScriptMessageHandlers();owner.window.close();NSApp.mainMenu=previous}
        let view=owner.webView!
        view.load(URLRequest(url:url));phase="login"
        try wait{truth("!!document.querySelector('#login-submit:not([disabled])')",view)}
        try paint(view);try wait{truth("!document.documentElement.classList.contains('login-boot')",view)}
        _=try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",view)
        phase="authenticated workspace";try wait{owner.workspaceControlsReady}
        try check("guide not forced after authentication",truth("document.querySelector('[data-user-guide]')&&!document.querySelector('.user-guide[open]')",view))
        _=try script("window.__guideFrozen=JSON.stringify(tasks);document.querySelector('#chat-input').value='使用说明保留的原生草稿';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('[data-user-guide]').focus();document.querySelector('[data-user-guide]').click();true",view)
        phase="help footer"
        try wait{truth("!!document.querySelector('.user-guide[open]')",view)}
        try check("footer opens one named guide",truth("document.querySelectorAll('.user-guide').length===1&&document.querySelector('.user-guide').getAttribute('aria-labelledby')==='user-guide-title'",view))
        try check("guide has seven searchable chapters",truth("document.querySelectorAll('.user-guide article').length===7&&document.querySelectorAll('.user-guide nav a').length===7",view))
        try check("close receives initial focus",truth("document.activeElement===document.querySelector('[data-guide-close]')",view))
        _=try script("document.querySelector('#user-guide-search').value='首次公开时间';document.querySelector('#user-guide-search').dispatchEvent(new Event('input'));true",view)
        try check("native search narrows articles and links",truth("document.querySelectorAll('.user-guide article:not([hidden])').length===1&&document.querySelectorAll('.user-guide nav a:not([hidden])').length===1",view))
        _=try script("document.querySelector('.user-guide nav a:not([hidden])').click();true",view)
        try check("chapter moves focus without browser navigation",truth("document.activeElement===document.querySelector('.user-guide article:not([hidden])')&&location.hash===''",view))
        _=try script("document.querySelector('#user-guide-search').value='';document.querySelector('#user-guide-search').dispatchEvent(new Event('input'));true",view)
        for size in [NSSize(width:1440,height:900),NSSize(width:1024,height:768),NSSize(width:390,height:844)]{
            owner.window.setContentSize(size);view.frame=owner.window.contentView!.bounds
            for scale in [0.8,0.9,1.0,1.1,1.25,1.4]{
                phase="guide native zoom \(Int(size.width)) \(scale)";owner.applyScale(CGFloat(scale));try paint(view)
                geometry=try script("(()=>{const el=document.querySelector('.user-guide'),r=el.getBoundingClientRect(),s=getComputedStyle(el);return {rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height},viewport:{width:innerWidth,height:innerHeight,clientWidth:document.documentElement.clientWidth,clientHeight:document.documentElement.clientHeight},scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,style:{width:s.width,height:s.height,margin:s.margin,maxWidth:s.maxWidth,maxHeight:s.maxHeight,zoom:getComputedStyle(document.documentElement).zoom},native:document.documentElement.dataset.nativeShell}})()",view) ?? NSNull()
                try check(phase+" uses WK pageZoom only",view.pageZoom==scale&&truth("!document.documentElement.hasAttribute('data-browser-scale')",view))
                // WebKit rounds scrollWidth and clientWidth separately under
                // fractional pageZoom. Bound that rounding to one CSS pixel,
                // and independently require both actual grid children to fit.
                try check(phase+" modal fits",truth("(()=>{const el=document.querySelector('.user-guide'),r=el.getBoundingClientRect();return r.left>=-1&&r.top>=-1&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1&&el.scrollWidth-el.clientWidth<=1&&Array.from(el.children).every(child=>{const c=child.getBoundingClientRect();return c.left>=r.left&&c.right<=r.right&&c.width<=r.width})})()",view))
            }
        }
        owner.applyScale(1);owner.window.setContentSize(NSSize(width:1440,height:900));view.frame=owner.window.contentView!.bounds;try paint(view)
        _=try script("document.querySelector('[data-guide-close]').click();true",view)
        try check("closing restores visible help origin",truth("!document.querySelector('.user-guide[open]')&&document.activeElement===document.querySelector('[data-user-guide]')",view))
        _=try script("document.querySelector('[data-account-menu-toggle]').click();document.querySelector('#account-menu [data-action=library]').click();true",view)
        try check("same native workspace retains report library",truth("document.querySelector('#dialog').open&&document.querySelector('#dialog-title').textContent==='报告库'&&document.querySelectorAll('#dialog [data-history]').length===6",view))
        try check("read-only guide preserves research and draft",truth("JSON.stringify(tasks)===window.__guideFrozen&&document.querySelector('#chat-input').value==='使用说明保留的原生草稿'",view))
        try check("all windows remain hidden",NSApp.windows.allSatisfy{!$0.isVisible})
    }
    static func main(){
        do{guard CommandLine.arguments.count==2,let url=URL(string:CommandLine.arguments[1]) else{throw NSError(domain:"Missing isolated service",code:1)}
            try run(url);let payload:[String:Any]=["status":"passed","checks":checks,"phase":phase];let bytes=try JSONSerialization.data(withJSONObject:payload,options:[.sortedKeys]);print(String(data:bytes,encoding:.utf8)!)
        }catch{let payload:[String:Any]=["status":"failed","checks":checks,"phase":phase,"error":String(describing:error),"geometry":geometry];let bytes=try! JSONSerialization.data(withJSONObject:payload,options:[.sortedKeys]);print(String(data:bytes,encoding:.utf8)!);exit(1)}
    }
}
