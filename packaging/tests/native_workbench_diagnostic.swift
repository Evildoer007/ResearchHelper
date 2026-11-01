// Actual WebKit pixels and geometry. Never order a window front or present UI.
import AppKit
import WebKit

@main
struct NativeWorkbenchDiagnostic {
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
        view.evaluateJavaScript("({url:location.pathname,ready:document.readyState,classes:document.documentElement.className,errors:window.__renderErrors||[],loginDisabled:document.querySelector('#login-submit')?.disabled??null,hasWorkspace:!!document.querySelector('#chat-form')})") { value, _ in
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
    try theme("light", owner)
    try layout("empty-chat-1440", owner, desk: false)
    try capture("empty-chat-1440", owner, output)
    owner.openSettings()
    let settingsFrame = "document.querySelector('.workspace-settings-dialog iframe')"
    try wait {
      truth(
        "!!\(settingsFrame)?.contentDocument?.querySelector('[data-workspace-action=cases]')",
        owner.webView)
    }
    _ = try script(
      "\(settingsFrame).contentDocument.querySelector('a[href=\"#storage\"]').click();\(settingsFrame).contentDocument.querySelector('[data-workspace-action=cases]').click();true",
      owner.webView)
    try wait { truth("!!document.querySelector('#dialog[open] [data-case]')", owner.webView) }
    _ = try script(
      "document.querySelector('#dialog[open] [data-case]').click();true", owner.webView)
    try wait {
      truth("document.querySelectorAll('#task-list [data-task]').length>0", owner.webView)
    }
    owner.openArtifacts()
    try wait {
      truth(
        "!!document.querySelector('#task-actions [data-action=start]:not([disabled])')",
        owner.webView)
    }
    _ = try script(
      "document.querySelector('#task-actions [data-action=start]').click();true", owner.webView)
    try wait {
      truth("!!document.querySelector('#task-actions [data-action=update]')", owner.webView)
    }
    _ = try script(
      "document.querySelector('#task-context:not([hidden]) [data-action=close-context]')?.click();true",
      owner.webView)
    try check(
      "offline fixture actually delivered before snapshots",
      truth(
        "document.querySelector('#task-list').textContent.length>0&&!!document.querySelector('.artifact-card')",
        owner.webView))
    _ = try script(
      "document.querySelector('#chat-input').value='检查下一个研究条件，不执行真实研究';document.querySelector('#chat-input').dispatchEvent(new Event('input',{bubbles:true}));true",
      owner.webView)
    for name in ["light", "dark"] {
      try theme(name, owner)
      _ = try script("document.querySelector('[data-mode=chat]').click();true", owner.webView)
      try layout("chat-1440-" + name, owner, desk: false)
      try capture("chat-1440-" + name, owner, output)
      _ = try script("document.querySelector('[data-mode=desk]').click();true", owner.webView)
      for stage in 0..<5 {
        _ = try script("document.querySelector('#view-\(stage)').click();true", owner.webView)
        if stage == 3 {
          try wait {
            truth(
              "document.querySelector('#report-frame')?.contentDocument?.readyState==='complete'&&document.querySelector('#report-frame').contentDocument.body.textContent.length>200",
              owner.webView)
          }
        }
        try layout("desk-1440-\(name)-\(stage)", owner, desk: true)
        if name == "light" || stage == 1 {
          try capture("desk-1440-\(name)-\(stage)", owner, output)
        }
      }
      owner.openSettings()
      try wait {
        truth(
          "!!document.querySelector('.workspace-settings-dialog iframe')?.contentDocument?.querySelector('#preferences')",
          owner.webView)
      }
      try capture("settings-1440-" + name, owner, output)
      _ = try script(
        "document.querySelector('.workspace-settings-dialog iframe').contentDocument.querySelector('.settings-close').click();true",
        owner.webView)
    }
    try theme("light", owner)
    for (width, height) in [(1024, 768), (390, 844)] {
      owner.window.setContentSize(NSSize(width: width, height: height))
      owner.webView.layoutSubtreeIfNeeded()
      try wait { truth("Math.abs(innerWidth-\(width))<2", owner.webView) }
      if width < 700 {
        owner.searchTasks()
        try wait { truth("!document.querySelector('#tasks').inert", owner.webView) }
      }
      _ = try script("document.querySelector('[data-mode=chat]').click();true", owner.webView)
      try layout("chat-\(width)", owner, desk: false)
      try capture("chat-\(width)", owner, output)
      if width < 700 {
        owner.searchTasks()
        try wait { truth("!document.querySelector('#tasks').inert", owner.webView) }
      }
      _ = try script(
        "document.querySelector('[data-mode=desk]').click();document.querySelector('#view-1').click();true",
        owner.webView)
      try layout("desk-\(width)", owner, desk: true)
      try capture("desk-\(width)", owner, output)
      owner.openSettings()
      try wait {
        truth(
          "!!document.querySelector('.workspace-settings-dialog iframe')?.contentDocument?.querySelector('#preferences')",
          owner.webView)
      }
      try capture("settings-\(width)", owner, output)
      _ = try script(
        "document.querySelector('.workspace-settings-dialog iframe').contentDocument.querySelector('.settings-close').click();true",
        owner.webView)
    }
    try check(
      "unsent research preserved throughout native views",
      truth("document.querySelector('#chat-input').value==='检查下一个研究条件，不执行真实研究'", owner.webView))
    try check(
      "no main-frame script errors", truth("window.__renderErrors.length===0", owner.webView))
    try check(
      "no production browser storage", !owner.webView.configuration.websiteDataStore.isPersistent)
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
