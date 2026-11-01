import AppKit
import WebKit
import Darwin
import CoreFoundation
import UniformTypeIdentifiers

// BEGIN RESEARCHHELPER_NAVIGATION_POLICY
// Foundation-only state is shared by the real shell and independent checks.
struct ResearchHelperNavigationState {
    enum PresentationDecision { case ready, retry, failed }
    private(set) var generation = 0
    private(set) var lastSafeURL: URL?
    private(set) var failureVisible = false

    func accepts(_ url: URL, origin: URL?) -> Bool {
        guard let origin, origin.scheme == "http", origin.host == "127.0.0.1", origin.port != nil,
              url.scheme == origin.scheme, url.host == origin.host, url.port == origin.port,
              url.user == nil, url.password == nil else { return false }
        return ["/", "/login/index.html"].contains(url.path)
    }
    mutating func begin() -> Int { generation += 1; return generation }
    mutating func invalidate() { generation += 1; failureVisible = false }
    mutating func showFailure(generation token: Int) -> Bool {
        guard token == generation else { return false }
        failureVisible = true
        return true
    }
    mutating func complete(_ url: URL, origin: URL?, generation token: Int) -> Bool {
        guard token == generation, accepts(url, origin: origin) else { return false }
        var safe = URLComponents(url: url, resolvingAgainstBaseURL: false)
        safe?.query = nil // Never replay the one-use startup animation or query credentials.
        lastSafeURL = safe?.url
        failureVisible = false
        return true
    }
    func recoveryURL(origin: URL?, workspaceOnly: Bool) -> URL? {
        guard let origin, accepts(origin, origin: origin) else { return nil }
        if !workspaceOnly, let lastSafeURL, accepts(lastSafeURL, origin: origin) { return lastSafeURL }
        return origin
    }
    func decision(contentHealthy: Bool, snapshotReady: Bool, attempt: Int, withinDeadline: Bool) -> PresentationDecision {
        if contentHealthy && snapshotReady { return .ready }
        return withinDeadline ? .retry : .failed
    }
}
// END RESEARCHHELPER_NAVIGATION_POLICY

private final class ResearchHelperRecoveryView: NSView {
    override var wantsUpdateLayer: Bool { true }
    override func updateLayer() { layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor }
}

// Use the responder-chain contract from OH without binding actions to NSText.
@objc private protocol ResearchHelperEditingActions {
    func undo(_ sender: Any?)
    func redo(_ sender: Any?)
    func cut(_ sender: Any?)
    func copy(_ sender: Any?)
    func paste(_ sender: Any?)
    func selectAll(_ sender: Any?)
}

final class ResearchHelperWindow: NSWindow {
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard modifiers == .command,
              let key = event.charactersIgnoringModifiers?.lowercased(),
              let action = switch key {
              case "x": #selector(ResearchHelperEditingActions.cut(_:))
              case "c": #selector(ResearchHelperEditingActions.copy(_:))
              case "v": #selector(ResearchHelperEditingActions.paste(_:))
              case "a": #selector(ResearchHelperEditingActions.selectAll(_:))
              default: nil
              }
        else { return super.performKeyEquivalent(with: event) }
        return NSApp.sendAction(action, to: nil, from: self) || super.performKeyEquivalent(with: event)
    }
}

private final class ResearchHelperTitlebarDragView: NSView {
    override var mouseDownCanMoveWindow: Bool { true }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
}

// The service is not ready yet. Use its packaged identity without requesting
// an origin, reading credentials, or replaying the login animation.
func researchHelperStartupHTML(resources: URL, theme: String) throws -> String {
    let wordmark = try String(contentsOf: resources.appendingPathComponent("app/web/login/wordmark.svg"), encoding: .utf8)
    let appearance = theme == "dark" ? "dark" : "light"
    return """
    <!doctype html><html lang="zh-CN" data-theme="\(appearance)"><head>
    <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
    <title>ResearchHelper</title><style>
    :root { color-scheme:light; background:#EFF1F3; color:#252525; }
    :root[data-theme="dark"] { color-scheme:dark; background:#0E1013; color:#E8EAED; }
    body { margin:0; }
    .native-startup { position:fixed; inset:0; display:grid; place-items:center; overflow:hidden; }
    .native-startup svg { width:min(336px,49vw); height:auto; overflow:visible; }
    .native-startup text { fill:currentColor; }
    </style></head><body><main class="native-startup" role="status" aria-label="正在启动ResearchHelper">\(wordmark)</main></body></html>
    """
}

// One independent App runtime; no source checkout or user Python is required.
final class ResearchHelperApp: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var backend: Process?
    var runtimeURL: URL?
    var reportWindows: [NSWindow: WKWebView] = [:]
    var reportTargets: [WKWebView: URL] = [:]
    var failedReportLoads: Set<WKWebView> = []
    var printViews: Set<WKWebView> = []
    var quitting = false
    var terminationSource: DispatchSourceSignal?
    let scaleSteps: [CGFloat] = [0.8, 0.9, 1, 1.1, 1.25, 1.4]
    var uiScale: CGFloat = 1
    var startupTimer: Timer?
    var printOperations: [NSPrintOperation: WKWebView] = [:]
    var pendingPrints: Set<WKWebView> = []
    var activeSavePanel: NSSavePanel?
    private var activeArtifactSave: (operationID: String?, name: String, generation: Int)?
    var activeOpenPanel: NSOpenPanel?
    var failed = false
    var nativeControls: [NSButton] = []
    var workspaceControlsReady = false
    var sidebarCollapsed = false
    private var titlebarDragView: ResearchHelperTitlebarDragView?
    private var navigationState = ResearchHelperNavigationState()
    private var navigationWatchdog: DispatchWorkItem?
    private var navigationRecoveryView: NSView?
    private var navigationRecoveryDetail: NSTextField?
    private var navigationRecoveryRetry: NSButton?
    private var workspaceReadyAfterNavigation = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Use the freshly packaged bitmap for this running App. Icon Services
        // can retain an old ICNS when a candidate is replaced at the same path.
        if let iconURL = Bundle.main.url(forResource: "ResearchHelperDock", withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
        let savedScale = CGFloat(UserDefaults.standard.double(forKey: "researchhelper.uiScale"))
        if scaleSteps.contains(savedScale) { uiScale = savedScale }
        signal(SIGTERM, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        source.setEventHandler { NSApp.terminate(nil) }
        source.resume()
        terminationSource = source
        installMainMenu()
        startMainWindow()
    }

    func installMainMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "ResearchHelper")
        appMenu.addItem(withTitle: "关于ResearchHelper", action: #selector(about), keyEquivalent: "")
        appMenu.addItem(.separator())
        let settings = appMenu.addItem(withTitle: "设置…", action: #selector(openSettings), keyEquivalent: ",")
        settings.target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出ResearchHelper", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        menu.addItem(appItem)
        let fileItem = NSMenuItem(title: "文件", action: nil, keyEquivalent: "")
        let fileMenu = NSMenu(title: "文件")
        let create = NSMenuItem(title: "新建研究", action: #selector(newTask), keyEquivalent: "n")
        create.target = self
        fileMenu.addItem(create)
        let printItem = NSMenuItem(title: "打印报告", action: #selector(printCurrent), keyEquivalent: "p")
        printItem.target = self
        fileMenu.addItem(printItem)
        fileMenu.addItem(.separator())
        fileMenu.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        fileItem.submenu = fileMenu
        menu.addItem(fileItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let edit = NSMenu(title: "编辑")
        edit.addItem(withTitle: "撤销", action: #selector(ResearchHelperEditingActions.undo(_:)), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "重做", action: #selector(ResearchHelperEditingActions.redo(_:)), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "剪切", action: #selector(ResearchHelperEditingActions.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "复制", action: #selector(ResearchHelperEditingActions.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "粘贴", action: #selector(ResearchHelperEditingActions.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "全选", action: #selector(ResearchHelperEditingActions.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        menu.addItem(editItem)
        let viewMenu = NSMenu(title: "显示")
        let sidebar = viewMenu.addItem(withTitle: "收起或展开任务栏", action: #selector(toggleSidebar), keyEquivalent: "s")
        sidebar.keyEquivalentModifierMask = [.control, .command]
        sidebar.target = self
        viewMenu.addItem(.separator())
        for (title, action, key) in [("放大", #selector(zoomIn), "+"), ("缩小", #selector(zoomOut), "-"), ("实际大小", #selector(resetZoom), "0"), ("搜索任务", #selector(searchTasks), "k")] {
            let item = viewMenu.addItem(withTitle: title, action: action, keyEquivalent: key)
            item.target = self
        }
        let viewItem = NSMenuItem(title: "显示", action: nil, keyEquivalent: "")
        viewItem.submenu = viewMenu
        menu.addItem(viewItem)
        let windows = NSMenu(title: "窗口")
        windows.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        let reopen = windows.addItem(withTitle: "研究对话", action: #selector(showMainWindow), keyEquivalent: "")
        reopen.target = self
        let windowItem = NSMenuItem(title: "窗口", action: nil, keyEquivalent: "")
        windowItem.submenu = windows
        menu.addItem(windowItem)
        NSApp.windowsMenu = windows
        NSApp.mainMenu = menu
    }

    private func startMainWindow() {
        window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "ResearchHelper"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.backgroundColor = .clear
        window.isMovableByWindowBackground = true
        let savedPreference = UserDefaults.standard.string(forKey: "researchhelper.nativeThemePreference")
        let startupTheme = savedPreference == "auto"
            ? (NSApp.effectiveAppearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? "dark" : "light")
            : (savedPreference == "dark" ? "dark" : "light")
        window.appearance = savedPreference == "auto" ? nil : NSAppearance(named: startupTheme == "dark" ? .darkAqua : .aqua)
        window.isOpaque = false
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.minSize = NSSize(width: 780, height: 560)
        window.center()
        webView = createWebView()
        let material = NSVisualEffectView()
        material.material = .sidebar
        material.blendingMode = .behindWindow
        material.state = .active
        material.isEmphasized = false
        material.addSubview(webView)
        webView.translatesAutoresizingMaskIntoConstraints = false
        NSLayoutConstraint.activate([webView.leadingAnchor.constraint(equalTo: material.leadingAnchor), webView.trailingAnchor.constraint(equalTo: material.trailingAnchor), webView.topAnchor.constraint(equalTo: material.topAnchor), webView.bottomAnchor.constraint(equalTo: material.bottomAnchor)])
        window.contentView = material
        installNativeControls()
        // Keep the configured window hidden until the service is ready. The
        // login document owns the single launch animation, as in OptionHelper.
        startBackend()
    }

    func createWebView(persistent: Bool = true) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        if !persistent { configuration.websiteDataStore = .nonPersistent() }
        let savedPreference = UserDefaults.standard.string(forKey: "researchhelper.nativeThemePreference") ?? "light"
        let themePreference = ["light", "dark", "auto"].contains(savedPreference) ? savedPreference : "light"
        configuration.userContentController.add(self, name: "researchhelper")
        configuration.userContentController.add(self, name: "researchhelperTheme")
        configuration.userContentController.addUserScript(WKUserScript(source: "document.documentElement.dataset.nativeShell='macos';document.documentElement.dataset.nativeMaterial='system';document.documentElement.style.setProperty('--native-titlebar-height','\(36 / uiScale)px');document.documentElement.style.setProperty('--native-titlebar-collapsed-leading-safe-area','\(166 / uiScale)px');if(location.protocol==='http:'&&['/','/login/index.html'].includes(location.pathname)){try{if(!['light','dark','auto'].includes(localStorage.getItem('researchhelper.theme')))localStorage.setItem('researchhelper.theme','\(themePreference)');}catch{}document.documentElement.classList.add('login-boot');}", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        // Login uses its shared theme controller. The workspace also resolves
        // the same cached preference before CSS, including offline browsers.
        // Observe only the main workspace route; a paper report cannot change
        // the application's material or its next-launch preference.
        configuration.userContentController.addUserScript(WKUserScript(source: "if(location.protocol==='http:'&&location.pathname==='/'){const sync=()=>{const theme=document.documentElement.dataset.theme,preference=document.documentElement.dataset.themePref;if(['light','dark'].includes(theme))window.webkit.messageHandlers.researchhelper.postMessage({action:'theme',theme,preference});};new MutationObserver(sync).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});document.addEventListener('DOMContentLoaded',sync,{once:true});}", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = self
        view.uiDelegate = self
        view.pageZoom = uiScale
        view.allowsMagnification = false
        view.underPageBackgroundColor = .clear
        // Match OH's guarded WebKit material setup. Do not assume a private
        // selector exists on every supported macOS/WebKit build.
        if view.responds(to: NSSelectorFromString("_setDrawsBackground:")) {
            view.setValue(false, forKey: "drawsBackground")
        }
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor.clear.cgColor
        return view
    }

    func startBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("App资源不存在"); return }
        let executable = resources.appendingPathComponent("backend/researchhelper-server/researchhelper-server")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { fail("内置研究运行组件不存在"); return }
        let process = Process()
        process.executableURL = executable
        process.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        // Independent storage; never inherit Python module search paths or shared tokens.
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        // The service owns its versioned default store. Preserve only an explicit QA override.
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        let errors = Pipe()
        process.standardError = errors
        errors.fileHandleForReading.readabilityHandler = { handle in
            // Drain continuously without exposing provider output or credentials in the UI.
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        process.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                self.fail("本地研究服务已退出，关闭App后重新打开；任务检查点保留。")
            }
        }
        backend = process
        do { try process.run() } catch { fail("内置运行组件无法启动"); return }
        startupTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
            guard let self = self, self.runtimeURL == nil else { return }
            self.fail("本地研究服务启动超时，请退出后重试。")
            self.stopBackend()
        }
        DispatchQueue.global().async { [weak self] in
            var bytes = Data()
            while !bytes.contains(10) && bytes.count < 8192 {
                let part = output.fileHandleForReading.availableData
                if part.isEmpty { break }
                bytes.append(part)
            }
            let firstLine = Data(bytes.prefix { $0 != 10 })
            let message = try? JSONSerialization.jsonObject(with: firstLine) as? [String: Any]
            DispatchQueue.main.async {
                guard let self = self, !self.failed, !self.quitting, let address = message?["url"] as? String,
                      let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1", url.port != nil else {
                    self?.fail("本地服务未返回有效启动状态"); return
                }
                self.runtimeURL = url
                self.startupTimer?.invalidate()
                var launchURL = URLComponents(url: url, resolvingAgainstBaseURL: false)
                launchURL?.queryItems = [URLQueryItem(name: "app_startup", value: UUID().uuidString)]
                self.window.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
                self.webView.load(URLRequest(url: launchURL?.url ?? url))
            }
            // Continue draining stdout after the startup handshake.
            while !output.fileHandleForReading.availableData.isEmpty {}
        }
    }

    func fail(_ message: String) {
        guard !failed, !quitting else { return }
        failed = true
        startupTimer?.invalidate()
        cancelNavigationWatchdog()
        navigationState.invalidate()
        let alert = NSAlert()
        alert.messageText = "ResearchHelper无法运行"
        alert.informativeText = message
        alert.addButton(withTitle: "关闭")
        // Startup can fail before the hidden main window has been presented.
        // Always give the error sheet a visible parent and foreground the App.
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    private func ensureNavigationRecoveryView() -> NSView? {
        if let navigationRecoveryView { return navigationRecoveryView }
        guard let content = window?.contentView else { return nil }
        let overlay = ResearchHelperRecoveryView(frame: .zero)
        overlay.translatesAutoresizingMaskIntoConstraints = false
        overlay.wantsLayer = true
        overlay.setAccessibilityLabel("ResearchHelper页面恢复")
        let title = NSTextField(labelWithString: "页面暂时无法显示")
        title.font = .systemFont(ofSize: 24, weight: .semibold)
        let detail = NSTextField(wrappingLabelWithString: "")
        detail.font = .systemFont(ofSize: 14)
        detail.textColor = .secondaryLabelColor
        detail.maximumNumberOfLines = 0
        navigationRecoveryDetail = detail
        let retry = NSButton(title: "重新打开上一个页面", target: self, action: #selector(recoverLastSafePage))
        retry.bezelStyle = .rounded
        retry.keyEquivalent = "\r"
        navigationRecoveryRetry = retry
        let workspace = NSButton(title: "返回工作台", target: self, action: #selector(recoverWorkspace))
        workspace.bezelStyle = .rounded
        let actions = NSStackView(views: [retry, workspace])
        actions.orientation = .horizontal
        actions.alignment = .centerY
        actions.spacing = 10
        let panel = NSStackView(views: [title, detail, actions])
        panel.translatesAutoresizingMaskIntoConstraints = false
        panel.orientation = .vertical
        panel.alignment = .leading
        panel.spacing = 14
        overlay.addSubview(panel)
        content.addSubview(overlay, positioned: .above, relativeTo: webView)
        NSLayoutConstraint.activate([
            overlay.leadingAnchor.constraint(equalTo: content.leadingAnchor),
            overlay.trailingAnchor.constraint(equalTo: content.trailingAnchor),
            overlay.topAnchor.constraint(equalTo: content.topAnchor),
            overlay.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            panel.centerXAnchor.constraint(equalTo: overlay.centerXAnchor),
            panel.centerYAnchor.constraint(equalTo: overlay.centerYAnchor),
            panel.widthAnchor.constraint(lessThanOrEqualToConstant: 520),
            panel.leadingAnchor.constraint(greaterThanOrEqualTo: overlay.leadingAnchor, constant: 24),
            panel.trailingAnchor.constraint(lessThanOrEqualTo: overlay.trailingAnchor, constant: -24),
        ])
        overlay.isHidden = true
        navigationRecoveryView = overlay
        return overlay
    }
    private func cancelNavigationWatchdog() {
        navigationWatchdog?.cancel()
        navigationWatchdog = nil
    }
    private func beginNavigationWatchdog() {
        cancelNavigationWatchdog()
        let token = navigationState.begin()
        let watchdog = DispatchWorkItem { [weak self] in
            self?.showNavigationFailure("页面载入未完成。后台研究不受影响，可重新打开页面。", generation: token)
        }
        navigationWatchdog = watchdog
        DispatchQueue.main.asyncAfter(deadline: .now() + 20, execute: watchdog)
    }
    private func hideNavigationFailure() { navigationRecoveryView?.isHidden = true }
    private func showNavigationFailure(_ detail: String, generation: Int) {
        guard !quitting, !failed, runtimeURL != nil,
              navigationState.showFailure(generation: generation),
              let recovery = ensureNavigationRecoveryView() else { return }
        cancelNavigationWatchdog()
        setWorkspaceControlsReady(false)
        cancelFilePanels()
        navigationRecoveryDetail?.stringValue = detail
        recovery.isHidden = false
        window.makeFirstResponder(navigationRecoveryRetry)
    }
    private func loadRecoveryURL(workspaceOnly: Bool) {
        guard !quitting, !failed, let url = navigationState.recoveryURL(origin: runtimeURL, workspaceOnly: workspaceOnly) else { return }
        navigationRecoveryDetail?.stringValue = "正在重新打开页面…"
        webView.load(URLRequest(url: url))
    }
    @objc private func recoverLastSafePage() { loadRecoveryURL(workspaceOnly: false) }
    @objc private func recoverWorkspace() { loadRecoveryURL(workspaceOnly: true) }

    private func verifyMainPresentation(generation: Int, attempt: Int, deadline: TimeInterval) {
        guard !quitting, !failed, generation == navigationState.generation else { return }
        let script = """
        (()=>{const root=document.querySelector(location.pathname==='/login/index.html'?'body.login-page':'[data-workspace-shell]');if(!root)return {healthy:false,route:location.pathname,root:false};const s=getComputedStyle(root),r=root.getBoundingClientRect();return {healthy:['interactive','complete'].includes(document.readyState)&&root.getClientRects().length>0&&r.width>80&&r.height>80&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth&&s.display!=='none'&&s.visibility==='visible'&&Number(s.opacity)>0.01&&((root.innerText||root.textContent)||'').trim().length>0,route:location.pathname,root:true,width:r.width,height:r.height,top:r.top,left:r.left,viewportWidth:innerWidth,viewportHeight:innerHeight,display:s.display,visibility:s.visibility,opacity:s.opacity,ready:document.readyState};})()
        """
        webView.evaluateJavaScript(script) { [weak self] result, error in
            guard let self, !self.quitting, !self.failed, generation == self.navigationState.generation else { return }
            let presentation = result as? [String: Any] ?? [:]
            let healthy = error == nil && presentation["healthy"] as? Bool == true
            if !healthy && (attempt == 0 || attempt == 24) {
                var diagnostic = presentation
                diagnostic["stage"] = "main-presentation"
                diagnostic["attempt"] = attempt
                diagnostic["webviewWidth"] = self.webView.bounds.width
                diagnostic["webviewHeight"] = self.webView.bounds.height
                if let error { diagnostic["errorDomain"] = (error as NSError).domain; diagnostic["errorCode"] = (error as NSError).code }
                if let data = try? JSONSerialization.data(withJSONObject: diagnostic, options: [.sortedKeys]) {
                    FileHandle.standardError.write(data); FileHandle.standardError.write(Data([10]))
                }
            }
            guard healthy else {
                self.finishPresentationCheck(contentHealthy: false, snapshotReady: false, generation: generation, attempt: attempt, deadline: deadline)
                return
            }
            // Reveal only this verified document before asking WebKit to paint.
            // Otherwise the recovery overlay itself can keep a snapshot blank.
            self.hideNavigationFailure()
            let configuration = WKSnapshotConfiguration()
            configuration.rect = self.webView.bounds
            configuration.snapshotWidth = 480
            self.webView.takeSnapshot(with: configuration) { [weak self] image, error in
                self?.finishPresentationCheck(contentHealthy: true,
                    snapshotReady: error == nil && (image?.size.width ?? 0) > 1 && (image?.size.height ?? 0) > 1,
                    generation: generation, attempt: attempt, deadline: deadline)
            }
        }
    }
    private func finishPresentationCheck(contentHealthy: Bool, snapshotReady: Bool, generation: Int, attempt: Int, deadline: TimeInterval) {
        guard !quitting, !failed, generation == navigationState.generation else { return }
        switch navigationState.decision(contentHealthy: contentHealthy, snapshotReady: snapshotReady,
                                        attempt: attempt, withinDeadline: ProcessInfo.processInfo.systemUptime < deadline) {
        case .ready:
            guard let url = webView.url, navigationState.complete(url, origin: runtimeURL, generation: generation) else { return }
            cancelNavigationWatchdog()
            hideNavigationFailure()
            setWorkspaceControlsReady(workspaceReadyAfterNavigation && url.path == "/")
            window.makeFirstResponder(webView)
        case .retry:
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
                self?.verifyMainPresentation(generation: generation, attempt: attempt + 1, deadline: deadline)
            }
        case .failed:
            showNavigationFailure("页面内容未正确呈现。后台研究不受影响，可重新打开页面。", generation: generation)
        }
    }

    @objc func about() {
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "ResearchHelper", .applicationVersion: "0.10.0", .credits: NSAttributedString(string: "离线候选，真实模型与数据权限未验收")])
    }
    func dispatch(_ name: String) { webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('researchhelper:\(name)'))") }
    @objc func newTask() { guard workspaceControlsReady else { return }; window.makeKeyAndOrderFront(nil); dispatch("new-task") }
    @objc func showMainWindow() {
        guard runtimeURL != nil || failed else { return }
        window.makeKeyAndOrderFront(nil)
    }
    @objc func toggleSidebar() { guard workspaceControlsReady else { return }; dispatch("toggle-sidebar") }
    @objc func searchTasks() { guard workspaceControlsReady else { return }; dispatch("search-tasks") }
    @objc func openArtifacts() { guard workspaceControlsReady else { return }; dispatch("open-artifacts") }
    @objc func openSettings() { guard workspaceControlsReady else { return }; dispatch("open-settings") }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if let action = item.action, [#selector(newTask), #selector(toggleSidebar), #selector(searchTasks), #selector(openArtifacts), #selector(openSettings)].contains(action) {
            return workspaceControlsReady
        }
        if item.action == #selector(printCurrent) {
            return workspaceControlsReady || NSApp.keyWindow.flatMap { reportWindows[$0] } != nil
        }
        if item.action == #selector(zoomIn) { return uiScale < scaleSteps.last! }
        if item.action == #selector(zoomOut) { return uiScale > scaleSteps.first! }
        return true
    }
    @objc func zoomIn() { changeScale(1) }
    @objc func zoomOut() { changeScale(-1) }
    @objc func resetZoom() { applyScale(1) }
    func changeScale(_ step: Int) {
        let index = scaleSteps.firstIndex(of: uiScale) ?? 2
        applyScale(scaleSteps[min(scaleSteps.count - 1, max(0, index + step))])
    }
    func applyScale(_ scale: CGFloat) {
        guard scaleSteps.contains(scale) else { return }
        uiScale = scale
        UserDefaults.standard.set(Double(scale), forKey: "researchhelper.uiScale")
        for view in [webView!] + Array(reportWindows.values) {
            view.pageZoom = scale
            view.evaluateJavaScript("document.documentElement.style.setProperty('--native-titlebar-height','\(36 / scale)px');document.documentElement.style.setProperty('--native-titlebar-collapsed-leading-safe-area','\(166 / scale)px');window.dispatchEvent(new CustomEvent('researchhelper:zoom',{detail:{scale:\(scale)}}))")
        }
    }
    func installNativeControls() {
        guard let close = window.standardWindowButton(.closeButton), let bar = close.superview else { return }
        // AppKit can replace its titlebar during full-screen transitions.
        // Reuse our controls when possible, and remove detached copies first.
        if titlebarDragView?.superview !== bar {
            titlebarDragView?.removeFromSuperview()
            let dragView = ResearchHelperTitlebarDragView()
            bar.addSubview(dragView)
            titlebarDragView = dragView
        }
        if nativeControls.contains(where: { $0.superview !== bar }) {
            nativeControls.forEach { $0.removeFromSuperview() }
            nativeControls.removeAll()
        }
        if nativeControls.isEmpty {
            for (symbol, label, action) in [("sidebar.left", "收起任务栏", #selector(toggleSidebar)), ("magnifyingglass", "搜索任务", #selector(searchTasks)), ("sidebar.right", "打开或收起任务与交付", #selector(openArtifacts))] {
                let button = NSButton()
                button.image = NSImage(systemSymbolName: symbol, accessibilityDescription: label)
                button.toolTip = label
                button.setAccessibilityLabel(label)
                button.isBordered = false
                button.bezelStyle = .inline
                button.contentTintColor = .secondaryLabelColor
                button.target = self
                button.action = action
                bar.addSubview(button)
                nativeControls.append(button)
            }
        }
        nativeControls.forEach { $0.isHidden = !workspaceControlsReady; $0.isEnabled = workspaceControlsReady }
        updateSidebarLabel()
        layoutNativeControls()
    }
    func updateSidebarLabel() {
        guard let button = nativeControls.first else { return }
        let label = sidebarCollapsed ? "展开任务栏" : "收起任务栏"
        button.toolTip = label
        button.setAccessibilityLabel(label)
        button.setAccessibilityValue(sidebarCollapsed ? "已收起" : "已展开")
    }
    func setWorkspaceControlsReady(_ ready: Bool) {
        workspaceControlsReady = ready
        installNativeControls()
    }
    func receiveWorkspaceState(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) {
        guard sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/",
              let action = body["action"] as? String else { return }
        let key = action == "workspace-state" ? "ready" : "collapsed"
        guard ["workspace-state", "rail-state"].contains(action),
              let value = body[key] as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { return }
        if action == "workspace-state" {
            workspaceReadyAfterNavigation = value.boolValue
            setWorkspaceControlsReady(value.boolValue && !navigationState.failureVisible)
        }
        else { sidebarCollapsed = value.boolValue; updateSidebarLabel() }
    }
    func layoutNativeControls() {
        guard nativeControls.count == 3, let close = window.standardWindowButton(.closeButton), let bar = close.superview else { return }
        let positions = [close.frame.maxX + 60, close.frame.maxX + 94, bar.bounds.maxX - 40]
        for (index, button) in nativeControls.enumerated() { button.frame = NSRect(x: positions[index], y: close.frame.midY - 12, width: 26, height: 24) }
        titlebarDragView?.frame = NSRect(x: positions[1] + 34, y: 0, width: max(0, positions[2] - positions[1] - 42), height: bar.bounds.height)
    }
    func windowDidResize(_ notification: Notification) { if notification.object as? NSWindow === window { installNativeControls() } }
    func windowDidEnterFullScreen(_ notification: Notification) { if notification.object as? NSWindow === window { installNativeControls() } }
    func windowDidExitFullScreen(_ notification: Notification) { if notification.object as? NSWindow === window { installNativeControls() } }
    @objc func printCurrent() {
        printFrom(NSApp.keyWindow.flatMap { reportWindows[$0] } ?? webView)
    }
    func inform(_ message: String, in owner: NSWindow?) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: owner ?? window)
    }
    func printFrom(_ source: WKWebView) {
        if reportWindows.values.contains(source) {
            if source.isLoading, let target = reportTargets[source], isReportURL(target) { printViews.insert(source) }
            else { printReport(source) }
            return
        }
        guard source === webView, workspaceControlsReady, source.url?.path == "/" else { return }
        source.evaluateJavaScript("(typeof window.ResearchHelperNative?.reportURL==='function'?window.ResearchHelperNative.reportURL():window.ResearchHelperNative?.reportURL) || document.getElementById('report-frame')?.src") { [weak self, weak source] value, _ in
            guard let self = self, let source = source, source === self.webView,
                  self.workspaceControlsReady, source.url?.path == "/" else { return }
            guard let text = value as? String, let url = URL(string: text, relativeTo: self.runtimeURL)?.absoluteURL, self.isReportURL(url) else {
                self.inform("请先打开一份研究报告，再打印或保存为PDF。", in: source.window); return
            }
            self.openReport(url, printing: true)
        }
    }

    func allowed(_ url: URL) -> Bool {
        return url.scheme == "http" && url.host == "127.0.0.1" && url.port == runtimeURL?.port && url.user == nil && url.password == nil
    }
    func isReportURL(_ url: URL) -> Bool {
        guard allowed(url) else { return false }
        let query = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        guard query.isEmpty || (query.count == 1 && query[0].name == "profile" && ["standard","detailed"].contains(query[0].value ?? "")) else { return false }
        if url.path.range(of: "^/reports/[A-Za-z0-9_-]+\\.html$", options: .regularExpression) != nil { return true }
        let parts = url.path.split(separator: "/", omittingEmptySubsequences: false)
        return parts.count == 6 && parts[0].isEmpty && parts[1] == "api" && parts[2] == "tasks"
            && parts[3].range(of: "^[A-Za-z0-9_-]+$", options: .regularExpression) != nil
            && parts[4] == "report" && Int(parts[5]).map { $0 > 0 } == true
    }
    func prepareReport(_ url: URL, printing: Bool, persistent: Bool = true) -> NSWindow? {
        guard isReportURL(url) else { return nil }
        // OH reuses its preview instead of creating one window per click.
        // Keep an in-flight printed document frozen until its operation ends.
        // A matching document takes precedence over any idle window. Combining
        // these predicates would let Dictionary iteration replace another report.
        let matchingPreview = reportWindows.first(where: { _, view in reportTargets[view] == url })
        let reusablePreview = matchingPreview ?? reportWindows.first(where: { _, view in
            !printViews.contains(view) && !pendingPrints.contains(view) && !printOperations.values.contains(view)
        })
        if let (existingWindow, view) = reusablePreview {
            if reportTargets[view] != url || (!view.isLoading && (view.url != url || failedReportLoads.contains(view))) {
                reportTargets[view] = url; failedReportLoads.remove(view); view.load(URLRequest(url: url))
            }
            if printing {
                if view.isLoading { printViews.insert(view) }
                else { printReport(view) }
            }
            return existingWindow
        }
        let reportWindow = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1050, height: 800),
                                    styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        reportWindow.title = "研究报告"
        reportWindow.isReleasedWhenClosed = false
        reportWindow.delegate = self
        let view = createWebView(persistent: persistent)
        view.allowsMagnification = true
        reportWindow.contentView = view
        reportWindows[reportWindow] = view
        reportTargets[view] = url
        if printing { printViews.insert(view) }
        view.load(URLRequest(url: url))
        reportWindow.center()
        return reportWindow
    }
    func openReport(_ url: URL, printing: Bool) {
        guard let reportWindow = prepareReport(url, printing: printing) else { return }
        reportWindow.makeKeyAndOrderFront(nil)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === self.webView, let url = webView.url, navigationState.accepts(url, origin: runtimeURL) {
            startupTimer?.invalidate()
            verifyMainPresentation(generation: navigationState.generation, attempt: 0,
                                   deadline: ProcessInfo.processInfo.systemUptime + 15)
        }
        if reportWindows.values.contains(webView) { failedReportLoads.remove(webView) }
        if printViews.remove(webView) != nil {
            printReport(webView)
        }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        if webView === self.webView {
            workspaceReadyAfterNavigation = false
            setWorkspaceControlsReady(false)
            cancelFilePanels()
            if runtimeURL != nil { beginNavigationWatchdog() }
        }
        else { pendingPrints.remove(webView) }
    }
    func printReport(_ webView: WKWebView) {
            guard let owner = webView.window, reportWindows[owner] === webView,
                  let url = webView.url, isReportURL(url), !webView.isLoading, !failedReportLoads.contains(webView), !pendingPrints.contains(webView),
                  !printOperations.values.contains(webView) else { return }
            pendingPrints.insert(webView)
            webView.evaluateJavaScript("window.dispatchEvent(new Event('beforeprint'))") { _, _ in
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    guard self.pendingPrints.remove(webView) != nil, self.reportWindows[owner] === webView,
                          webView.url == url, !webView.isLoading, !self.failedReportLoads.contains(webView) else { return }
                    let info = NSPrintInfo.shared.copy() as! NSPrintInfo
                    info.horizontalPagination = .fit
                    let operation = webView.printOperation(with: info)
                    operation.showsPrintPanel = true
                    operation.showsProgressPanel = true
                    // WebKit supplies page drawings asynchronously. A synchronous
                    // run() blocks that work and can save correctly paginated but
                    // entirely blank PDFs. Keep the document and operation alive
                    // until the document-modal print callback has completed.
                    self.printOperations[operation] = webView
                    operation.runModal(for: owner, delegate: self,
                                       didRun: #selector(self.printDidRun(_:success:contextInfo:)), contextInfo: nil)
                }
            }
    }
    @objc func printDidRun(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        // AppKit may finish an asynchronous print job on its worker thread.
        // Both the ownership table and WebKit belong to the main thread.
        DispatchQueue.main.async {
            guard let source = self.printOperations.removeValue(forKey: operation) else { return }
            source.evaluateJavaScript("window.dispatchEvent(new Event('afterprint'))")
        }
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let source = message.frameInfo.request.url, allowed(source),
              let body = message.body as? [String: Any] else { return }
        if message.name == "researchhelperTheme" {
            guard message.webView === webView, message.frameInfo.isMainFrame,
                  let theme = body["theme"] as? String, ["light", "dark"].contains(theme),
                  let preference = body["preference"] as? String, ["light", "dark", "auto"].contains(preference) else { return }
            applyNativeTheme(theme, preference: preference)
            return
        }
        guard let action = body["action"] as? String else { return }
        if ["workspace-state", "rail-state"].contains(action) {
            receiveWorkspaceState(body, sourceView: message.webView, sourceURL: source, mainFrame: message.frameInfo.isMainFrame)
            return
        }
        if action == "signed_out" {
            guard message.webView === webView, message.frameInfo.isMainFrame, source.path == "/" else { return }
            setWorkspaceControlsReady(false)
            cancelFilePanels()
            printViews.removeAll()
            pendingPrints.removeAll()
            for reportWindow in Array(reportWindows.keys) { reportWindow.close() }
            return
        }
        if action == "print", canPrint(sourceView: message.webView, sourceURL: source, mainFrame: message.frameInfo.isMainFrame), let sourceView = message.webView {
            printFrom(sourceView); return
        }
        if action == "theme", let theme = body["theme"] as? String, ["light", "dark"].contains(theme) {
            guard message.webView === webView, message.frameInfo.isMainFrame else { return }
            let preference = body["preference"] as? String ?? theme
            guard ["light", "dark", "auto"].contains(preference) else { return }
            applyNativeTheme(theme, preference: preference)
            return
        }
        if action == "zoom" {
            receiveWorkspaceZoom(body, sourceView: message.webView, sourceURL: source, mainFrame: message.frameInfo.isMainFrame)
            return
        }
        if ["save", "save_binary"].contains(action) {
            receiveArtifactSave(body, sourceView: message.webView, sourceURL: source, mainFrame: message.frameInfo.isMainFrame)
        }
    }
    func artifactSaveFeedback(_ result: [String: Any], generation: Int) {
        guard !quitting, generation == navigationState.generation,
              let url = webView?.url, allowed(url), url.path == "/",
              let raw = try? JSONSerialization.data(withJSONObject: result),
              let json = String(data: raw, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('researchhelper:artifact-saved',{detail:" + json + "}))", completionHandler: nil)
    }
    func artifactSaveResult(operationID: String?, name: String, response: NSApplication.ModalResponse, data: Data, destination: URL?) -> [String: Any] {
        var result: [String: Any] = ["name": name, "status": "cancelled"]
        if let operationID { result["operation_id"] = operationID }
        guard response == .OK else { return result }
        guard let destination else {
            result["status"] = "failed"
            result["error"] = "未选择保存位置"
            return result
        }
        do {
            try data.write(to: destination, options: .atomic)
            guard try Data(contentsOf: destination) == data else {
                throw NSError(domain: "ResearchHelper.ArtifactSave", code: 1)
            }
            result["status"] = "saved"
            result["name"] = destination.lastPathComponent
        } catch {
            result["status"] = "failed"
            result["error"] = "文件未保存，请选择可写目录。报告仍保留在研究档案中。"
        }
        return result
    }
    func receiveArtifactSave(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) {
        guard sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/",
              workspaceControlsReady, !quitting else { return }
        let token = navigationState.generation
        let operationID = (body["operation_id"] as? String).flatMap { value in
            value.isEmpty || value.utf8.count > 128 || value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) ? nil : value
        }
        let failureName = String((body["name"] as? String ?? "报告").prefix(255))
        func reject(_ reason: String) {
            var result: [String: Any] = ["name": failureName, "status": "rejected", "error": reason]
            if let operationID { result["operation_id"] = operationID }
            artifactSaveFeedback(result, generation: token)
        }
        guard activeSavePanel == nil, activeOpenPanel == nil else {
            reject("请先完成或取消当前文件窗口")
            return
        }
        guard let (name, data) = artifactContent(body, sourceView: sourceView, sourceURL: sourceURL, mainFrame: mainFrame) else {
            reject("文件内容或格式无效，请重新导出")
            return
        }
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name
        panel.canCreateDirectories = true
        panel.allowedContentTypes = [UTType(filenameExtension: (name as NSString).pathExtension) ?? .data]
        activeSavePanel = panel
        activeArtifactSave = (operationID, name, token)
        panel.beginSheetModal(for: window) { response in
            guard self.activeSavePanel === panel else { return }
            self.activeSavePanel = nil
            self.activeArtifactSave = nil
            let stillOwned = !self.quitting && self.workspaceControlsReady && token == self.navigationState.generation
                && self.webView.url.map { self.allowed($0) && $0.path == "/" } == true
            guard stillOwned else { return }
            let result = self.artifactSaveResult(operationID: operationID, name: name, response: response, data: data, destination: panel.url)
            self.artifactSaveFeedback(result, generation: token)
        }
    }
    func canPrint(sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) -> Bool {
        guard mainFrame, allowed(sourceURL), let sourceView = sourceView else { return false }
        return sourceView === webView && workspaceControlsReady && sourceURL.path == "/"
            || reportWindows.values.contains(sourceView) && isReportURL(sourceURL)
    }
    func artifactContent(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) -> (String,Data)? {
        if body["action"] as? String == "save", let (name,text) = saveContent(body, sourceView: sourceView, sourceURL: sourceURL, mainFrame: mainFrame) {
            return (name,Data(text.utf8))
        }
        guard body["action"] as? String == "save_binary", sourceView === webView, mainFrame,
              allowed(sourceURL), sourceURL.path == "/", workspaceControlsReady,
              let name = body["name"] as? String, !name.isEmpty, name.utf8.count <= 255,
              !name.contains("/"), !name.contains("\\"), !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              let encoded = body["base64"] as? String, encoded.count <= 28_000_000,
              let data = Data(base64Encoded: encoded), !data.isEmpty, data.count <= 20_000_000 else { return nil }
        let ext = (name as NSString).pathExtension.lowercased()
        guard ["pdf","docx","xlsx","csv"].contains(ext) else { return nil }
        if ext == "pdf" && !data.starts(with: Data("%PDF-".utf8)) { return nil }
        if ["docx","xlsx"].contains(ext) && !data.starts(with: Data([0x50,0x4b])) { return nil }
        if ext == "csv" && String(data: data, encoding: .utf8) == nil { return nil }
        return (name,data)
    }
    func saveContent(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) -> (String,String)? {
        guard body["action"] as? String == "save", sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/", workspaceControlsReady,
              let name = body["name"] as? String, !name.isEmpty, name.utf8.count <= 255,
              !name.contains("/"), !name.contains("\\"), !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              ["html", "json"].contains((name as NSString).pathExtension.lowercased()),
              let text = body["text"] as? String, !text.isEmpty, text.utf8.count <= 20_000_000 else { return nil }
        return (name,text)
    }
    func receiveWorkspaceZoom(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) {
        // Settings forward through the main workbench. A report or material
        // frame must not change global pageZoom or the saved App preference.
        guard sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/",
              body["action"] as? String == "zoom", let value = body["scale"] as? NSNumber,
              CFGetTypeID(value) != CFBooleanGetTypeID() else { return }
        let scale = CGFloat(value.doubleValue)
        guard scale.isFinite, scaleSteps.contains(scale) else { return }
        applyScale(scale)
    }
    func applyNativeTheme(_ theme: String, preference: String) {
        UserDefaults.standard.set(preference, forKey: "researchhelper.nativeThemePreference")
        // An explicit appearance overrides WebKit's system-color query. Auto
        // must inherit AppKit appearance so OS changes can reach matchMedia.
        window.appearance = preference == "auto" ? nil : NSAppearance(named: theme == "dark" ? .darkAqua : .aqua)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if reportWindows.values.contains(webView) {
            if navigationAction.navigationType == .linkActivated && url.scheme == "https" { NSWorkspace.shared.open(url) }
            let reportAllowed = isReportURL(url) || url.absoluteString == "about:blank"
            if !reportAllowed && navigationAction.navigationType != .linkActivated {
                failedReportLoads.insert(webView); printViews.remove(webView); pendingPrints.remove(webView)
            }
            decisionHandler(reportAllowed ? .allow : .cancel); return
        }
        if allowed(url) || url.scheme == "about" { decisionHandler(.allow) }
        else {
            if navigationAction.navigationType == .linkActivated && url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        }
    }
    func validReportResponse(_ response: URLResponse) -> Bool {
        guard let http = response as? HTTPURLResponse, let url = http.url, isReportURL(url),
              (200..<300).contains(http.statusCode),
              let mime = http.mimeType?.lowercased() else { return false }
        return ["text/html", "application/xhtml+xml"].contains(mime)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if reportWindows.values.contains(webView), navigationResponse.isForMainFrame,
           !validReportResponse(navigationResponse.response) {
            failedReportLoads.insert(webView); printViews.remove(webView); pendingPrints.remove(webView)
            decisionHandler(.cancel)
            inform("报告读取失败，请检查登录状态和报告版本后重新打开。", in: webView.window)
            return
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if webView === self.webView, navigationAction.sourceFrame.isMainFrame, navigationAction.targetFrame == nil,
           let url = navigationAction.request.url, isReportURL(url) { openReport(url, printing: false) }
        return nil
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        guard let source = frame.request.url,
              let panel = makeOpenPanel(sourceView: webView, sourceURL: source, mainFrame: frame.isMainFrame,
                                        allowsMultiple: parameters.allowsMultipleSelection, allowsDirectories: parameters.allowsDirectories) else { completionHandler(nil); return }
        activeOpenPanel = panel
        panel.beginSheetModal(for: webView.window ?? window) { result in
            guard self.activeOpenPanel === panel else { completionHandler(nil); return }
            self.activeOpenPanel = nil
            let currentSource = webView.url
            let stillOwned = !self.quitting && self.workspaceControlsReady && webView === self.webView
                && currentSource.map { self.allowed($0) && $0.path == "/" } == true
            completionHandler(result == .OK && stillOwned ? panel.urls : nil)
        }
    }
    func makeOpenPanel(sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool, allowsMultiple: Bool, allowsDirectories: Bool) -> NSOpenPanel? {
        guard sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/", workspaceControlsReady,
              !quitting, activeOpenPanel == nil, activeSavePanel == nil else { return nil }
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = allowsDirectories
        panel.allowsMultipleSelection = allowsMultiple
        panel.canCreateDirectories = false
        panel.resolvesAliases = true
        return panel
    }
    func cancelFilePanels() {
        // Invalidate ownership before AppKit invokes delayed sheet callbacks.
        let open = activeOpenPanel, save = activeSavePanel, artifact = activeArtifactSave
        activeOpenPanel = nil
        activeSavePanel = nil
        activeArtifactSave = nil
        open?.cancel(nil)
        save?.cancel(nil)
        if let artifact {
            var result: [String: Any] = ["name": artifact.name, "status": "cancelled"]
            if let operationID = artifact.operationID { result["operation_id"] = operationID }
            artifactSaveFeedback(result, generation: artifact.generation)
        }
    }
    func windowWillClose(_ notification: Notification) {
        guard let closed = notification.object as? NSWindow, let view = reportWindows.removeValue(forKey: closed) else { return }
        reportTargets.removeValue(forKey: view)
        failedReportLoads.remove(view)
        printViews.remove(view)
        pendingPrints.remove(view)
        view.stopLoading()
        view.configuration.userContentController.removeScriptMessageHandler(forName: "researchhelper")
        view.configuration.userContentController.removeScriptMessageHandler(forName: "researchhelperTheme")
        view.navigationDelegate = nil
        view.uiDelegate = nil
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showMainWindow(); return true }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let value = error as NSError
        if value.domain == NSURLErrorDomain && value.code == NSURLErrorCancelled { return }
        if webView !== self.webView { failedReportLoads.insert(webView); printViews.remove(webView); pendingPrints.remove(webView) }
        if webView === self.webView { showNavigationFailure("页面连接失败。后台研究不受影响，可重新打开页面。", generation: navigationState.generation) }
        else { inform("报告加载失败，请重新打开。", in: webView.window) }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        self.webView(webView, didFailProvisionalNavigation: navigation, withError: error)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if webView === self.webView {
            let token = navigationState.begin() // Invalidate pending DOM and snapshot callbacks.
            showNavigationFailure("页面进程已结束。后台研究不受影响，可重新打开页面。", generation: token)
        } else {
            failedReportLoads.insert(webView)
            printViews.remove(webView)
            pendingPrints.remove(webView)
            inform("报告页面已关闭，请重新打开。", in: webView.window)
        }
    }
    func stopBackend() {
        guard let process = backend, process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(1.5)
        while process.isRunning && Date() < deadline { usleep(20_000) }
        if process.isRunning { kill(process.processIdentifier, SIGKILL) }
    }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        cancelFilePanels()
        startupTimer?.invalidate()
        cancelNavigationWatchdog()
        navigationState.invalidate()
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "researchhelper")
        webView?.configuration.userContentController.removeScriptMessageHandler(forName: "researchhelperTheme")
        stopBackend()
    }
}

#if !RESEARCHHELPER_NATIVE_UI_TEST
@main
struct ResearchHelperLauncher {
    static func main() {
        let app = NSApplication.shared
        let delegate = ResearchHelperApp()
        app.setActivationPolicy(.regular)
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
#endif
