// Actual hidden AppKit/WK shell, isolated synthetic history, no OS clipboard.
import AppKit
import WebKit

@main
struct NativeMessageProse {
    static var checks:[String]=[]
    static var phase="setup"
    static var geometry:Any=[]
    static func check(_ name:String,_ value:Bool)throws{guard value else{throw NSError(domain:name,code:1)};checks.append(name)}
    static func wait(_ condition:()->Bool)throws{
        let deadline=Date().addingTimeInterval(20)
        while !condition() && Date()<deadline{RunLoop.main.run(until:Date().addingTimeInterval(0.02))}
        guard condition() else{throw NSError(domain:"Native prose wait: "+phase,code:1)}
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
        view.configuration.userContentController.addUserScript(WKUserScript(source:"Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText(text){window.__proseCopy=text;return Promise.resolve()}}});",injectionTime:.atDocumentStart,forMainFrameOnly:true))
        defer{view.stopLoading();view.configuration.userContentController.removeAllScriptMessageHandlers();owner.window.close();NSApp.mainMenu=previous}
        view.load(URLRequest(url:url));phase="login"
        try wait{truth("!!document.querySelector('#login-submit:not([disabled])')",view)};try paint(view)
        try wait{truth("!document.documentElement.classList.contains('login-boot')",view)}
        _=try script("document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",view)
        phase="workspace";try wait{owner.workspaceControlsReady}
        _=try script("api('/api/cases',{id:boot.cases[0].id}).then(task=>{tasks.unshift(task);setTask(task);window.__proseCase=task.id});true",view)
        phase="case";try wait{truth("!!window.__proseCase",view)}
        _=try script(#"""
        const fixture='## 研究判断\n\n**指标**：0.0045%、-12.30%\n\n3. 原件核对\n5. 时点核对\n\n| 证据 | 来源 |\n| --- | --- |\n| A\\|B | [公开披露](https://example.org/evidence) |\n\n公式：\\(\\sigma=\\sqrt{x}\\)\n\n$$\nR_t=\\frac{P_t}{P_{t-1}}-1\n$$\n\n$100与$200\n\n<img src=x onerror="window.__unsafe=true">\n\n[javascript](javascript:alert(1))';
        tasks[0].messages=[{role:'user',content:'## 原样保留'}, {role:'assistant',content:fixture,task_id:tasks[0].id}];renderChat();
        window.__proseBody=()=>document.querySelector('#chat .message--assistant .message__body');
        window.__proseFrozen=JSON.stringify(tasks);
        document.querySelector('#chat-input').value='原生排版保留草稿';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));
        true
        """#,view);try paint(view)
        try check("OH heading and emphasis in actual WK",truth("__proseBody().querySelector('h3').textContent==='研究判断'&&__proseBody().querySelector('strong').textContent==='指标'",view))
        try check("original numbers and ordered indices",truth("__proseBody().textContent.includes('0.0045%')&&__proseBody().textContent.includes('-12.30%')&&[...__proseBody().querySelectorAll('li')].map(el=>el.value).join(',')==='3,5'",view))
        try check("safe source link and literal unsafe HTML",truth("__proseBody().querySelectorAll('a').length===1&&__proseBody().querySelector('a').rel==='noopener noreferrer'&&!__proseBody().querySelector('img')&&!window.__unsafe",view))
        try check("semantic table preserves escaped pipe",truth("__proseBody().querySelector('thead th').scope==='col'&&__proseBody().querySelector('tbody td').textContent==='A|B'",view))
        try check("inline and multiline offline MathML",truth("__proseBody().querySelectorAll('math').length===2&&__proseBody().querySelector('msqrt')&&__proseBody().querySelector('mfrac')&&__proseBody().textContent.includes('$100与$200')",view))
        try check("user message and RH task control retained",truth("document.querySelector('#chat .message--user .message__body').textContent==='## 原样保留'&&!!__proseBody().querySelector('[data-task]')",view))
        _=try script("document.querySelector('#chat .message--assistant [data-action=copy-message]').click();true",view)
        phase="copy";try wait{truth("!!document.querySelector('#chat .message--assistant [data-copied=true]')",view)}
        try check("native copy uses formatted actual body, stub only",truth("window.__proseCopy===__proseBody().innerText",view))
        for size in [NSSize(width:1440,height:900),NSSize(width:1024,height:768),NSSize(width:390,height:844)]{
            owner.window.setContentSize(size);view.frame=owner.window.contentView!.bounds
            for scale in [0.8,0.9,1.0,1.1,1.25,1.4]{
                phase="native prose \(Int(size.width)) \(scale)";owner.applyScale(CGFloat(scale));try paint(view)
                try check(phase+" uses actual WK zoom",view.pageZoom==scale&&truth("!document.documentElement.hasAttribute('data-browser-scale')",view))
                geometry=try script("(()=>{const body=__proseBody(),rect=body.getBoundingClientRect();return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,body:{left:rect.left,right:rect.right,width:rect.width},overflow:[...document.querySelectorAll('#chat *')].map(el=>({tag:el.tagName,cls:el.className,left:el.getBoundingClientRect().left,right:el.getBoundingClientRect().right,scroll:el.scrollWidth,client:el.clientWidth})).filter(el=>el.right>innerWidth+1||el.left< -1).slice(0,20)}})()",view) ?? []
                try check(phase+" no page overflow",truth("document.documentElement.scrollWidth<=innerWidth+1&&__proseBody().getBoundingClientRect().right<=innerWidth+1&&__proseBody().getBoundingClientRect().left>=-1",view))
            }
        }
        owner.applyScale(1);owner.window.setContentSize(NSSize(width:1440,height:900));view.frame=owner.window.contentView!.bounds;try paint(view)
        _=try script("document.querySelector('[data-mode-switch] [data-mode=desk]').click();document.querySelector('[data-action=assistant-toggle]').click();true",view);try paint(view)
        try check("ResDesk reuses formatted body",truth("__proseBody().querySelector('h3').textContent==='研究判断'",view))
        try check("formatting preserves research, budget and draft",truth("JSON.stringify(tasks)===__proseFrozen&&document.querySelector('#chat-input').value==='原生排版保留草稿'",view))
        try check("all windows remain hidden",NSApp.windows.allSatisfy{!$0.isVisible})
    }
    static func main(){
        do{guard CommandLine.arguments.count==2,let url=URL(string:CommandLine.arguments[1]) else{throw NSError(domain:"Missing isolated service",code:1)};try run(url)
            let data=try JSONSerialization.data(withJSONObject:["status":"passed","checks":checks,"phase":phase,"geometry":geometry],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!)
        }catch{let data=try! JSONSerialization.data(withJSONObject:["status":"failed","checks":checks,"phase":phase,"geometry":geometry,"error":String(describing:error)],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!);exit(1)}
    }
}
