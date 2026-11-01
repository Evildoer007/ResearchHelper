// Actual WebKit pixels and geometry. Never order a window front or present UI.
import AppKit
import WebKit

@main
struct NativeConversationOutlineGeometry {
  static var phase = "initial"
  static weak var diagnosticView: WKWebView?
  static var failureDOM: Any = NSNull()
  static var checks: [String] = []
  static var screens: [String] = []
  static func check(_ label: String, _ value: Bool) throws {
    guard value else { throw NSError(domain: label, code: 1) }
    checks.append(label)
  }
  static func wait(_ condition: () -> Bool) throws {
    let deadline = Date().addingTimeInterval(25)
    while !condition() && Date() < deadline {
      RunLoop.main.run(until: Date().addingTimeInterval(0.02))
    }
    guard condition() else {
      // Capture bounded DOM diagnostics before the owner closes its hidden view.
      if let view = diagnosticView {
        var completed = false
        view.evaluateJavaScript("({url:location.pathname,ready:document.readyState,classes:document.documentElement.className,errors:window.__renderErrors||[],hasWorkspace:!!document.querySelector('#chat-form'),chatHeight:document.querySelector('#chat')?.clientHeight??null,chatRects:document.querySelector('#chat')?.getClientRects().length??null,turnAnchored:document.querySelector('#chat')?.dataset.turnAnchored??null,framePulse:window.__outlineFramePulse??null,readingKey:typeof conversationReadingKey==='function'?conversationReadingKey():null,hiddenAncestor:document.querySelector('#chat')?.closest('[hidden],[inert]')?.id??null})") { value, _ in
          failureDOM = value ?? NSNull(); completed = true
        }
        let diagnosticDeadline = Date().addingTimeInterval(3)
        while !completed && Date() < diagnosticDeadline { RunLoop.main.run(until:Date().addingTimeInterval(0.02)) }
      }
      throw NSError(domain: "WebKit render condition timed out: " + phase, code: 1)
    }
  }
  static func script(_ code: String, _ view: WKWebView) throws -> Any? {
    var done = false
    var result: Any?
    var error: Error?
    view.evaluateJavaScript(code) { value, failure in
      result = value
      error = failure
      done = true
    }
    try wait { done }
    if let error = error { throw error }
    return result
  }
  static func truth(_ code: String, _ view: WKWebView) -> Bool {
    (try? script(code, view)) as? Bool == true
  }
  static func capture(_ label: String, _ owner: ResearchHelperApp, _ output: URL) throws {
    phase = label
    let view = owner.webView!
    view.layoutSubtreeIfNeeded()
    // Hidden windows suspend the visual animation clock. Capture only the
    // real finite animation end states, as browser screenshot tools do.
    _ = try script(
      "document.getAnimations().forEach(a=>{if(Number.isFinite(a.effect?.getComputedTiming().iterations)){try{a.finish()}catch{}}});true",
      view)
    _ = try script("document.documentElement.getBoundingClientRect().width", view)
    RunLoop.main.run(until: Date().addingTimeInterval(0.25))
    let config = WKSnapshotConfiguration()
    config.rect = view.bounds
    config.snapshotWidth = NSNumber(value: Double(view.bounds.width))
    config.afterScreenUpdates = true
    var done = false
    var image: NSImage?
    var error: Error?
    view.takeSnapshot(with: config) { value, failure in
      image = value
      error = failure
      done = true
    }
    try wait { done }
    if let error = error { throw error }
    guard let image = image, let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:])
    else { throw NSError(domain: "No snapshot pixels", code: 1) }
    // Do not silently accept a blank hidden-window snapshot.
    var colors: Set<String> = []
    for y in stride(from: 0, to: bitmap.pixelsHigh, by: max(1, bitmap.pixelsHigh / 36)) {
      for x in stride(from: 0, to: bitmap.pixelsWide, by: max(1, bitmap.pixelsWide / 48)) {
        if let color = bitmap.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB) {
          colors.insert(
            "\(Int(color.redComponent * 255)),\(Int(color.greenComponent * 255)),\(Int(color.blueComponent * 255))"
          )
        }
      }
    }
    try png.write(to: output.appendingPathComponent(label + ".png"), options: .atomic)
    try check(label + " has actual nonblank WebKit pixels", colors.count > 12)
    screens.append(label + ".png")
    try check(
      label + " never shows a native window",
      !owner.window.isVisible && NSApp.activationPolicy() == .prohibited)
  }
  static func theme(_ name: String, _ owner: ResearchHelperApp) throws {
    owner.openSettings()
    let frame = "document.querySelector('.workspace-settings-dialog iframe')"
    try wait {
      truth("!!\(frame)?.contentDocument?.querySelector('[data-theme-set=\(name)]')", owner.webView)
    }
    _ = try script(
      "\(frame).contentDocument.querySelector('[data-theme-set=\(name)]').click();true",
      owner.webView)
    try wait { truth("document.documentElement.dataset.theme==='\(name)'", owner.webView) }
    _ = try script(
      "\(frame).contentDocument.querySelector('.settings-close').click();true", owner.webView)
    try wait { truth("!document.querySelector('.workspace-settings-dialog')", owner.webView) }
  }
  static func layout(_ label: String, _ owner: ResearchHelperApp, desk: Bool) throws {
    let view = owner.webView!
    try check(
      label + " no horizontal overflow",
      truth("document.documentElement.scrollWidth<=innerWidth+1", view))
    try check(
      label + " native shell uses one scale",
      truth(
        "document.documentElement.dataset.nativeShell==='macos'&&document.documentElement.style.zoom===''",
        view))
    if desk {
      try check(
        label + " five readable stage targets",
        truth(
          "[...document.querySelectorAll('.views button')].length===5&&[...document.querySelectorAll('.views button')].every(el=>{let r=el.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.height>=44&&parseFloat(getComputedStyle(el).fontSize)>=13})",
          view))
    } else {
      try check(
        label + " composer stays inside viewport",
        truth(
          "(()=>{let r=document.querySelector('#chat-form').getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.top>=36&&r.bottom<=innerHeight+1})()",
          view))
    }
  }
  static func run(_ url: URL, _ output: URL) throws {
    NSApplication.shared.setActivationPolicy(.prohibited)
    NSApp.finishLaunching()
    let owner = ResearchHelperApp()
    owner.runtimeURL = url
    owner.window = NSWindow(
      contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
      styleMask: [.titled, .closable, .resizable, .fullSizeContentView], backing: .buffered,
      defer: false)
    owner.window.isReleasedWhenClosed = false
    owner.window.delegate = owner
    owner.window.titleVisibility = .hidden
    owner.window.titlebarAppearsTransparent = true
    owner.webView = owner.createWebView(persistent: false)
    if #available(macOS 14.0, *) {
      // Public WebKit preference on this disposable hidden view only. The
      // production App and its user preferences are not changed by the probe.
      owner.webView.configuration.preferences.inactiveSchedulingPolicy = .none
    }
    owner.webView.configuration.userContentController.addUserScript(
      WKUserScript(
        source:
          "window.__renderErrors=[];window.addEventListener('error',e=>window.__renderErrors.push(e.message));",
        injectionTime: .atDocumentStart, forMainFrameOnly: true))
    owner.window.contentView = owner.webView
    owner.installNativeControls()
    defer {
      owner.cancelFilePanels()
      owner.webView.stopLoading()
      owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
      owner.window.delegate = nil
      owner.window.close()
    }
    diagnosticView = owner.webView
    phase = "login load"
    owner.webView.load(URLRequest(url: url))
    try wait { truth("!!document.querySelector('#login-submit:not([disabled])')", owner.webView) }
    phase = "login startup animation"
    try wait { truth("!document.documentElement.classList.contains('login-boot')", owner.webView) }
    try capture("login-1440", owner, output)
    _ = try script(
      "document.querySelector('#login-account').value='admin';document.querySelector('#login-password').value='66666666';document.querySelector('#login-submit').click();true",
      owner.webView)
    phase = "authenticated workspace"
    try wait { owner.workspaceControlsReady }

    phase = "offline fixture for navigation";
    _ = try script("""
      (async()=>{const task=await api('/api/cases',{id:'mechanism'});tasks.unshift(task);
      task.messages=Array.from({length:12},(_,i)=>({role:i%2?'assistant':'user',
      content:i%2?'原生导航测试回答'+i+'。仅用于检查界面。'.repeat(30):'测试问题'+(i/2+1)+'：'+'核对同版证据。'.repeat(30)}));
      setTask(task);window.__outlineFixture=true})().catch(e=>window.__renderErrors.push(e.message));true
      """, owner.webView)
    try wait { truth("window.__outlineFixture===true&&document.querySelectorAll('.conversation-outline__tick').length===6",owner.webView) }
    try check("offline fixture consumes no research budget",
      truth("boot.offline&&selected.ledger.used.model_calls===0&&selected.ledger.used.data_requests===0",owner.webView))
    try check("navigation outside the stream",
      truth("!document.querySelector('#chat').contains(document.querySelector('.conversation-outline'))",owner.webView))
    try check("OH navigation targets six actual user turns",
      truth("document.querySelectorAll('.conversation-outline__tick').length===6",owner.webView))
    _ = try script("document.querySelectorAll('.conversation-outline__tick')[3].focus();true",owner.webView)
    try check("WebKit preview shows corresponding question and answer",
      truth("document.querySelector('.conversation-outline__preview strong').textContent.startsWith('测试问题4')&&document.querySelector('.conversation-outline__preview p').textContent.startsWith('原生导航测试回答7')",owner.webView))
    _ = try script("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));true",owner.webView)
    try check("synthetic Home moves to first real target",
      truth("document.activeElement===document.querySelector('.conversation-outline__tick')",owner.webView))
    _ = try script("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'End',bubbles:true}));true",owner.webView)
    try check("synthetic End moves to last real target",
      truth("document.activeElement===Array.from(document.querySelectorAll('.conversation-outline__tick')).at(-1)",owner.webView))
    _ = try script("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));true",owner.webView)
    try check("synthetic Escape closes preview",
      truth("document.querySelector('.conversation-outline__preview').hidden",owner.webView))
    _ = try script("document.querySelectorAll('.conversation-outline__tick')[3].click();true",owner.webView)
    try wait { truth("Math.abs(document.querySelectorAll('#chat>.message--user')[3].getBoundingClientRect().top-document.querySelector('#chat').getBoundingClientRect().top-16)<3",owner.webView) }
    try check("real WebKit jump reaches selected turn",true)
    _ = try script("document.querySelector('#chat-input').value='未发送的原生测试问题';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));true",owner.webView)
    for scale in [0.8,0.9,1.0,1.1,1.25,1.4] {
      phase = "native pageZoom \(scale)";
      owner.webView.pageZoom = CGFloat(scale)
      _ = try script("window.dispatchEvent(new CustomEvent('researchhelper:zoom',{detail:{scale:\(scale)}}));true",owner.webView)
      RunLoop.main.run(until:Date().addingTimeInterval(0.2))
      _ = try script("document.querySelectorAll('.conversation-outline__tick')[3].click();true",owner.webView)
      try wait { truth("Math.abs(document.querySelectorAll('#chat>.message--user')[3].getBoundingClientRect().top-document.querySelector('#chat').getBoundingClientRect().top-16)<3",owner.webView) }
      try check("native pageZoom \(scale) reaches the correct turn",true)
      try check("native pageZoom \(scale) does not duplicate CSS zoom",
        truth("document.documentElement.style.zoom===''&&document.documentElement.scrollWidth<=innerWidth+1",owner.webView))
    }
    owner.webView.pageZoom=1
    _ = try script("window.dispatchEvent(new CustomEvent('researchhelper:zoom',{detail:{scale:1}}));true",owner.webView)
    try theme("dark",owner)
    _ = try script("document.querySelectorAll('.conversation-outline__tick')[2].focus();true",owner.webView)
    try capture("outline-dark-1440",owner,output)
    try check("dark preview body at least 13px",
      truth("parseFloat(getComputedStyle(document.querySelector('.conversation-outline__preview')).fontSize)>=13",owner.webView))
    try theme("light",owner)
    for (width,height) in [(1440,900),(1024,768),(390,844)] {
      phase = "responsive \(width)";
      owner.window.setContentSize(NSSize(width:CGFloat(width),height:CGFloat(height)))
      owner.webView.layoutSubtreeIfNeeded()
      RunLoop.main.run(until:Date().addingTimeInterval(0.25))
      try check("\(width) no horizontal overflow",truth("document.documentElement.scrollWidth<=innerWidth+1",owner.webView))
      let visible="!!document.querySelector('.conversation-outline').getClientRects().length&&!document.querySelector('.conversation-outline').hidden"
      try check("\(width) navigation uses OH responsive visibility",truth(visible,owner.webView)==(width>700))
      try check("\(width) composer visible",truth("document.querySelector('#chat-input').getClientRects().length>0",owner.webView))
      try capture("outline-\(width)",owner,output)
    }
    owner.window.setContentSize(NSSize(width:1440,height:900))
    _ = try script("document.querySelector('[data-mode=desk]').click();true",owner.webView)
    try check("Desk has no fixed conversation navigation",
      truth("document.querySelector('.conversation-outline').getClientRects().length===0",owner.webView))
    _ = try script("document.querySelector('[data-action=assistant-toggle]').click();true",owner.webView)
    try check("Desk reuses the same single stream",
      truth("document.querySelectorAll('#chat').length===1&&document.querySelector('.conversation-outline').getClientRects().length===0",owner.webView))
    _ = try script("document.querySelector('[data-mode=chat]').click();true",owner.webView)
    try check("draft preserved through navigation and mode changes",
      truth("document.querySelector('#chat-input').value==='未发送的原生测试问题'",owner.webView))
    phase = "submitted-turn frame"
    _ = try script("window.__outlineFramePulse=false;requestAnimationFrame(()=>window.__outlineFramePulse=true);conversationReader.beginTurn();true",owner.webView)
    // Snapshot requests a real hidden WebKit frame without showing the window.
    // This is not approval of foreground animation or system keyboard input.
    try capture("outline-turn-entry",owner,output)
    try wait { truth("document.querySelector('#chat').dataset.turnAnchored==='true'",owner.webView) }
    _ = try script("document.querySelector('.conversation-outline__tick').click();true",owner.webView)
    try wait { truth("document.querySelector('#chat').dataset.turnAnchored===undefined",owner.webView) }
    try check("history jump cancels pending turn entry",true)
    _ = try script("window.__explicitTop=document.querySelector('#chat').scrollTop;selected.messages.push({role:'assistant',content:'迟到测试回答。'.repeat(80)});render();true",owner.webView)
    RunLoop.main.run(until:Date().addingTimeInterval(0.25))
    try check("late reply does not revoke history jump",
      truth("Math.abs(document.querySelector('#chat').scrollTop-window.__explicitTop)<3",owner.webView))
    _ = try script("selected.messages=[{role:'user',content:'仅一轮测试问题'}];render();true",owner.webView)
    try wait { truth("document.querySelectorAll('.conversation-outline__tick').length===1",owner.webView) }
    try check("short conversation hides navigation and old preview",
      truth("document.querySelector('.conversation-outline').hidden&&document.querySelector('.conversation-outline__preview').hidden",owner.webView))
    try check("no main frame errors",truth("window.__renderErrors.length===0",owner.webView))
    try check("test WebKit storage is nonpersistent",!owner.webView.configuration.websiteDataStore.isPersistent)
  }
  static func main() {
    do {
      try run(
        URL(string: CommandLine.arguments[1])!, URL(fileURLWithPath: CommandLine.arguments[2]))
      FileHandle.standardOutput.write(
        try JSONSerialization.data(
          withJSONObject: ["status": "passed", "checks": checks, "screens": screens],
          options: [.prettyPrinted, .sortedKeys]))
    } catch {
      FileHandle.standardOutput.write(
        try! JSONSerialization.data(
          withJSONObject: [
            "status": "failed", "checks": checks, "screens": screens,
            "error": String(describing: error), "phase": phase, "dom": failureDOM,
          ], options: [.prettyPrinted, .sortedKeys]))
      exit(1)
    }
  }
}
