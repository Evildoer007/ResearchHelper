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

// BEGIN RESEARCHHELPER_BACKEND_HANDSHAKE
enum ResearchHelperBackendStartup {
    case ready(URL)
    case rejected(String)
    case invalid

    private struct Message: Decodable {
        let ready: Bool
        let url: String?
        let failure_code: String?
    }

    private static let explanations = [
        "store_unavailable": "研究档案无法打开。请确认档案目录可用，并关闭正在使用同一档案的ResearchHelper。",
        "execution_policy_unavailable": "运行策略无法读取。请恢复有效的execution-policy.json后重新打开ResearchHelper。",
        "model_configuration_unavailable": "模型配置无法读取。请恢复有效的model-settings.json后重新打开ResearchHelper。",
        "source_configuration_unavailable": "数据源配置无法读取。请恢复有效的source-settings.json后重新打开ResearchHelper。",
        "account_unavailable": "本地账户无法读取。请恢复有效的local-account.json后重新打开ResearchHelper。",
        "sessions_unavailable": "本地会话无法读取。请恢复有效的local-sessions.json后重新打开ResearchHelper。",
        "local_listener_unavailable": "本地连接端口暂不可用，请稍后重试。"
    ]

    static func decode(_ line: Data) -> Self {
        guard line.count < 8192, let message = try? JSONDecoder().decode(Message.self, from: line) else { return .invalid }
        if !message.ready {
            guard message.url == nil, let code = message.failure_code, let detail = explanations[code] else { return .invalid }
            return .rejected(detail)
        }
        guard message.failure_code == nil, let address = message.url,
              let components = URLComponents(string: address),
              components.scheme == "http", components.host == "127.0.0.1",
              let port = components.port, (1...65535).contains(port),
              components.user == nil, components.password == nil, components.percentEncodedPath == "/",
              components.query == nil, components.fragment == nil, let url = components.url else { return .invalid }
        return .ready(url)
    }
}
// END RESEARCHHELPER_BACKEND_HANDSHAKE

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
    .native-startup svg { width:min(367.127989px,53.539457vw); height:auto; overflow:visible; }
    .native-startup text, .native-startup [id^="researchhelper-word-"] { fill:currentColor; }
    </style></head><body><main class="native-startup" role="status" aria-label="正在启动ResearchHelper">\(wordmark)</main></body></html>
    """
}

// One independent App runtime; no source checkout or user Python is required.
final class ResearchHelperApp: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var backend: Process?
    var backendLifetime: FileHandle?
    private var backendQuitDeadline: DispatchWorkItem?
    var runtimeURL: URL?
    var reportWindows: [NSWindow: WKWebView] = [:]
    struct ReportTarget {
        let url: URL
        let digest: String?
    }
    private struct ReportLoad {
        let navigation: WKNavigation
        var digest: String?
        var ready = false
    }
    var reportTargets: [WKWebView: ReportTarget] = [:]
    private var reportLoads: [WKWebView: ReportLoad] = [:]
    var failedReportLoads: Set<WKWebView> = []
    var printViews: [WKWebView: [String: Any]] = [:]
    var quitting = false
    var terminationSource: DispatchSourceSignal?
    let scaleSteps: [CGFloat] = [0.8, 0.9, 1, 1.1, 1.25, 1.4]
    var uiScale: CGFloat = 1
    var startupTimer: Timer?
    var printOperations: [NSPrintOperation: WKWebView] = [:]
    var pendingPrints: [WKWebView: UUID] = [:]
    private var workspacePrintRequest: UUID?
    private var workspacePrintWindow: NSWindow?
    var activeSavePanel: NSSavePanel?
    private var activeArtifactSave: (operationID: String?, name: String, generation: Int)?
    var activeOpenPanel: NSOpenPanel?
    var failed = false
    var nativeControls: [NSButton] = []
    var workspaceControlsReady = false
    private var deliveryAvailable = false
    var sidebarCollapsed = false
    private var titlebarDragView: ResearchHelperTitlebarDragView?
    private weak var sidebarMaterialView: NSVisualEffectView?
    private var navigationState = ResearchHelperNavigationState()
    private var navigationWatchdog: DispatchWorkItem?
    private var navigationRecoveryView: NSView?
    private var navigationRecoveryTitle: NSTextField?
    private var navigationRecoveryDetail: NSTextField?
    private var navigationRecoveryRetry: NSButton?
    private var navigationRecoveryWorkspace: NSButton?
    private var backendRecoveryPort: Int?
    private var workspaceReadyAfterNavigation = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let preference = UserDefaults.standard.string(forKey: "researchhelper.nativeThemePreference") ?? "light"
        let initialTheme = preference == "dark" || (preference == "auto" && NSApp.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua) ? "dark" : "light"
        applyApplicationIcon(initialTheme)
        let savedScale = CGFloat(UserDefaults.standard.double(forKey: "researchhelper.uiScale"))
        if let scale = scaleSteps.first(where: { abs($0 - savedScale) < 0.0001 }) { uiScale = scale }
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
        webView = createWebView()
        window = makeMainWindow(containing: webView)
        installNativeControls()
        // Keep the configured window hidden until the service is ready. The
        // login document owns the single launch animation, as in OptionHelper.
        startBackend()
    }

    func makeMainWindow(containing view: WKWebView) -> NSWindow {
        let window = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1320, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        window.title = "ResearchHelper"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.backgroundColor = .clear
        window.isMovableByWindowBackground = true
        let savedPreference = UserDefaults.standard.string(forKey: "researchhelper.nativeThemePreference")
        let appearance = savedPreference == "auto" ? nil : NSAppearance(named: savedPreference == "dark" ? .darkAqua : .aqua)
        window.appearance = appearance
        window.isOpaque = false
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        let material = NSVisualEffectView()
        material.material = .sidebar
        material.appearance = appearance
        material.blendingMode = .behindWindow
        material.state = .active
        material.isEmphasized = false
        sidebarMaterialView = material

        // OptionHelper keeps the native backdrop behind the web content as a sibling.
        let content = NSView()
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.clear.cgColor
        for layer in [material, view] as [NSView] {
            layer.translatesAutoresizingMaskIntoConstraints = false
            content.addSubview(layer)
            NSLayoutConstraint.activate([
                layer.leadingAnchor.constraint(equalTo: content.leadingAnchor),
                layer.trailingAnchor.constraint(equalTo: content.trailingAnchor),
                layer.topAnchor.constraint(equalTo: content.topAnchor),
                layer.bottomAnchor.constraint(equalTo: content.bottomAnchor),
            ])
        }
        window.contentView = content
        window.makeFirstResponder(view)
        return window
    }

    func createWebView(persistent: Bool = true, dataStore: WKWebsiteDataStore? = nil) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        if let dataStore { configuration.websiteDataStore = dataStore }
        else if !persistent { configuration.websiteDataStore = .nonPersistent() }
        let savedPreference = UserDefaults.standard.string(forKey: "researchhelper.nativeThemePreference") ?? "light"
        let themePreference = ["light", "dark", "auto"].contains(savedPreference) ? savedPreference : "light"
        configuration.userContentController.add(self, name: "researchhelper")
        configuration.userContentController.add(self, name: "researchhelperTheme")
        let bootstrap = """
        document.documentElement.dataset.nativeShell = 'macos';
        document.documentElement.dataset.nativeMaterial = 'system';
        \(nativeScaleScript())
        if (location.protocol === 'http:' && ['/', '/login/index.html'].includes(location.pathname)) {
          try {
            if (!['light', 'dark', 'auto'].includes(localStorage.getItem('researchhelper.theme'))) {
              localStorage.setItem('researchhelper.theme', '\(themePreference)');
            }
          } catch {}
          document.documentElement.classList.add('login-boot');
        }
        """
        configuration.userContentController.addUserScript(WKUserScript(
            source: bootstrap, injectionTime: .atDocumentStart, forMainFrameOnly: true))
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

    func startBackend(at port: Int? = nil) {
        guard !quitting, !failed, backend?.isRunning != true else { return }
        guard let resources = Bundle.main.resourceURL else { backendStartupFailed("App资源不存在"); return }
        let executable = resources.appendingPathComponent("backend/researchhelper-server/researchhelper-server")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else { backendStartupFailed("内置研究运行组件不存在"); return }
        let process = Process()
        process.executableURL = executable
        process.currentDirectoryURL = resources
        var environment = ProcessInfo.processInfo.environment
        // Independent storage; never inherit Python module search paths or shared tokens.
        environment.removeValue(forKey: "PYTHONPATH")
        environment.removeValue(forKey: "PYTHONHOME")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["RESEARCHHELPER_DESKTOP_LIFETIME"] = "stdin"
        environment["RESEARCHHELPER_DESKTOP_PORT"] = port.map { String($0) }
        // The service owns its versioned default store. Preserve only an explicit QA override.
        process.environment = environment
        let lifetime = Pipe()
        process.standardInput = lifetime
        backendLifetime = lifetime.fileHandleForWriting
        let output = Pipe()
        process.standardOutput = output
        let errors = Pipe()
        process.standardError = errors
        errors.fileHandleForReading.readabilityHandler = { handle in
            // Drain continuously without exposing provider output or credentials in the UI.
            if handle.availableData.isEmpty { handle.readabilityHandler = nil }
        }
        process.terminationHandler = { [weak self] terminated in
            DispatchQueue.main.async {
                guard let self = self, self.backend === terminated else { return }
                if self.quitting {
                    self.finishDeferredQuit()
                    return
                }
                if let origin = self.runtimeURL {
                    self.backendDisconnected(terminated, origin: origin)
                } else if self.backendRecoveryPort != nil && self.startupTimer == nil {
                    self.navigationRecoveryRetry?.isEnabled = true
                }
            }
        }
        backend = process
        do {
            try process.run()
        } catch {
            backendStartupFailed("内置运行组件无法启动")
            return
        }
        startupTimer = Timer.scheduledTimer(withTimeInterval: 25, repeats: false) { [weak self] _ in
            guard let self = self, self.backend === process, self.runtimeURL == nil else { return }
            self.backendStartupFailed("本地研究服务启动超时，请稍后重试。")
        }
        DispatchQueue.global().async { [weak self] in
            var bytes = Data()
            while !bytes.contains(10) && bytes.count < 8192 {
                let part = output.fileHandleForReading.availableData
                if part.isEmpty { break }
                bytes.append(part.prefix(8192 - bytes.count))
            }
            let result = bytes.firstIndex(of: 10).map { ResearchHelperBackendStartup.decode(Data(bytes[..<$0])) } ?? .invalid
            DispatchQueue.main.async {
                guard let self = self, self.backend === process, self.startupTimer != nil,
                      !self.failed, !self.quitting else { return }
                switch result {
                case .rejected(let message):
                    self.backendStartupFailed(message)
                case .invalid:
                    self.backendStartupFailed("本地服务未返回有效启动状态")
                case .ready(let url):
                    guard process.isRunning, port == nil || url.port == port else {
                        self.backendStartupFailed("本地研究服务未能建立预期连接，请重试。")
                        return
                    }
                    self.runtimeURL = url
                    self.startupTimer?.invalidate()
                    self.startupTimer = nil
                    if self.backendRecoveryPort != nil {
                        self.backendRecoveryPort = nil
                        self.webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
                        return
                    }
                    var launchURL = URLComponents(url: url, resolvingAgainstBaseURL: false)
                    launchURL?.queryItems = [URLQueryItem(name: "app_startup", value: UUID().uuidString)]
                    self.window.makeKeyAndOrderFront(nil)
                    NSApp.activate(ignoringOtherApps: true)
                    self.webView.load(URLRequest(url: launchURL?.url ?? url))
                }
            }
            // Continue draining stdout after the startup handshake.
            while !output.fileHandleForReading.availableData.isEmpty {}
        }
    }

    private func backendStartupFailed(_ message: String) {
        startupTimer?.invalidate()
        startupTimer = nil
        stopBackend()
        if backendRecoveryPort != nil {
            navigationRecoveryTitle?.stringValue = "暂时无法连接"
            navigationRecoveryDetail?.stringValue = message
            navigationRecoveryRetry?.isEnabled = backend?.isRunning != true
        } else {
            fail(message)
        }
    }

    private func backendDisconnected(_ process: Process, origin: URL) {
        guard let port = origin.port else { return }
        stopBackend()
        runtimeURL = nil
        backendRecoveryPort = port
        navigationState.invalidate()
        cancelNavigationWatchdog()
        leaveWorkspace()
        guard let recovery = ensureNavigationRecoveryView() else { return }
        navigationRecoveryTitle?.stringValue = "本地研究服务已中断"
        navigationRecoveryDetail?.stringValue = "重新连接后可继续查看已保存的研究。"
        navigationRecoveryRetry?.title = "重新连接"
        navigationRecoveryWorkspace?.isHidden = true
        recovery.isHidden = false

        let ready = { [weak self, weak process] in
            guard let self, let process, self.backend === process, self.backendRecoveryPort == port, !self.quitting else { return }
            self.navigationRecoveryRetry?.isEnabled = true
            self.window.makeFirstResponder(self.navigationRecoveryRetry)
        }
        navigationRecoveryRetry?.isEnabled = false
        if let url = webView.url, navigationState.accepts(url, origin: origin), url.path == "/" {
            webView.evaluateJavaScript("window.ResearchHelperNative?.prepareServiceRecovery?.()") { _, _ in ready() }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1, execute: ready)
        } else {
            ready()
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
        navigationRecoveryTitle = title
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
        navigationRecoveryWorkspace = workspace
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
        cancelWorkspacePrint()
        navigationRecoveryTitle?.stringValue = "页面暂时无法显示"
        navigationRecoveryDetail?.stringValue = detail
        navigationRecoveryRetry?.title = "重新打开上一个页面"
        navigationRecoveryRetry?.isEnabled = true
        navigationRecoveryWorkspace?.isHidden = false
        recovery.isHidden = false
        window.makeFirstResponder(navigationRecoveryRetry)
    }
    private func loadRecoveryURL(workspaceOnly: Bool) {
        guard !quitting, !failed, let url = navigationState.recoveryURL(origin: runtimeURL, workspaceOnly: workspaceOnly) else { return }
        navigationRecoveryDetail?.stringValue = "正在重新打开页面…"
        webView.load(URLRequest(url: url))
    }
    @objc private func recoverLastSafePage() {
        guard let port = backendRecoveryPort else {
            loadRecoveryURL(workspaceOnly: false)
            return
        }
        guard !quitting, navigationRecoveryRetry?.isEnabled == true, backend?.isRunning != true else { return }
        navigationRecoveryRetry?.isEnabled = false
        navigationRecoveryTitle?.stringValue = "正在连接本地研究服务"
        navigationRecoveryDetail?.stringValue = ""
        startBackend(at: port)
    }
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
        NSApp.orderFrontStandardAboutPanel(options: [
            .credits: NSAttributedString(string: "离线候选，真实模型与数据权限未验收")
        ])
    }
    func dispatch(_ name: String) { webView.evaluateJavaScript("window.dispatchEvent(new CustomEvent('researchhelper:\(name)'))") }
    @objc func newTask() { guard workspaceControlsReady else { return }; window.makeKeyAndOrderFront(nil); dispatch("new-task") }
    @objc func showMainWindow() {
        guard runtimeURL != nil || failed else { return }
        window.makeKeyAndOrderFront(nil)
    }
    @objc func toggleSidebar() { guard workspaceControlsReady else { return }; dispatch("toggle-sidebar") }
    @objc func searchTasks() { guard workspaceControlsReady else { return }; dispatch("search-tasks") }
    @objc func openArtifacts() { guard workspaceControlsReady && deliveryAvailable else { return }; dispatch("open-artifacts") }
    @objc func openSettings() { guard workspaceControlsReady else { return }; dispatch("open-settings") }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(openArtifacts) { return workspaceControlsReady && deliveryAvailable }
        if let action = item.action, [#selector(newTask), #selector(toggleSidebar), #selector(searchTasks), #selector(openSettings)].contains(action) {
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
            syncNativeScale(to: view)
        }
    }

    private func nativeScaleScript() -> String {
        """
        document.documentElement.dataset.uiScale = '\(uiScale)';
        document.documentElement.style.setProperty('--native-titlebar-height', '\(36 / uiScale)px');
        document.documentElement.style.setProperty('--native-titlebar-collapsed-leading-safe-area', '\(166 / uiScale)px');
        """
    }

    private func syncNativeScale(to view: WKWebView) {
        view.evaluateJavaScript(nativeScaleScript() + "\nwindow.dispatchEvent(new CustomEvent('researchhelper:zoom',{detail:{scale:\(uiScale)}}))")
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
        for button in nativeControls {
            let available = workspaceControlsReady && (button.action != #selector(openArtifacts) || deliveryAvailable)
            button.isHidden = !available
            button.isEnabled = available
        }
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
    private func leaveWorkspace() {
        workspaceReadyAfterNavigation = false
        setWorkspaceControlsReady(false)
        cancelFilePanels()
        cancelWorkspacePrint()
        printViews.removeAll()
        pendingPrints.removeAll()
        for reportWindow in Array(reportWindows.keys) { reportWindow.close() }
    }
    func receiveWorkspaceState(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) {
        guard sourceView === webView, mainFrame, allowed(sourceURL), sourceURL.path == "/",
              let action = body["action"] as? String else { return }
        func flag(_ key: String) -> Bool? {
            guard let value = body[key] as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { return nil }
            return value.boolValue
        }
        switch action {
        case "workspace-state":
            guard let ready = flag("ready") else { return }
            deliveryAvailable = ready && flag("deliveryAvailable") == true
            workspaceReadyAfterNavigation = ready
            setWorkspaceControlsReady(ready && !navigationState.failureVisible)
        case "rail-state":
            guard let collapsed = flag("collapsed") else { return }
            sidebarCollapsed = collapsed
            updateSidebarLabel()
        case "delivery-state":
            guard workspaceReadyAfterNavigation, let available = flag("available"), deliveryAvailable != available else { return }
            deliveryAvailable = available
            installNativeControls()
        default:
            return
        }
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
            requestReportPrint(source)
            return
        }
        guard source === webView, workspaceControlsReady, source.url?.path == "/", !quitting,
              workspacePrintRequest == nil, workspacePrintWindow == nil, window.attachedSheet == nil else { return }
        let generation = navigationState.generation
        let token = UUID()
        workspacePrintRequest = token
        source.evaluateJavaScript("window.ResearchHelperNative?.printRequest?.() ?? null") { [weak self, weak source] value, _ in
            guard let self, self.workspacePrintRequest == token else { return }
            self.workspacePrintRequest = nil
            guard let source, source === self.webView, self.workspaceControlsReady, !self.quitting,
                  generation == self.navigationState.generation, source.url?.path == "/",
                  self.window.attachedSheet == nil else { return }
            guard let request = value as? [String: Any], let text = request["url"] as? String,
                  let url = URL(string: text), self.isReportURL(url) else {
                if source.window?.isVisible == true {
                    self.inform("请先打开一份研究报告，再打印或保存为PDF。", in: source.window)
                }
                return
            }
            let digest = request["digest"] as? String
            guard url.path.hasPrefix("/reports/") ||
                    digest?.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { return }
            // A print copy has its own WebKit view so loading and applying
            // print styles cannot change a report that is open for reading.
            let (document, view) = self.makeReportWindow(persistent: true)
            self.workspacePrintWindow = document
            self.loadReport(view, target: ReportTarget(url: url, digest: digest))
            self.requestReportPrint(view, presentation: request["presentation"] as? [String: Any])
        }
    }

    private func cancelWorkspacePrint() {
        workspacePrintRequest = nil
        guard let document = workspacePrintWindow else { return }
        let operation = reportWindows[document].flatMap { view in
            printOperations.first(where: { $0.value === view })?.key
        }
        document.close()
        if let operation, let sheet = window.attachedSheet, operation.printPanel === sheet.windowController {
            window.endSheet(sheet, returnCode: .cancel)
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
    func sameReportDocument(_ url: URL?, _ target: URL) -> Bool {
        guard let url else { return false }
        var actual = URLComponents(url: url, resolvingAgainstBaseURL: false)
        var expected = URLComponents(url: target, resolvingAgainstBaseURL: false)
        actual?.fragment = nil
        expected?.fragment = nil
        return actual?.url == expected?.url
    }
    func printInProgress(_ view: WKWebView) -> Bool {
        printViews[view] != nil || pendingPrints[view] != nil || printOperations.values.contains(view)
    }
    func requestReportPrint(_ view: WKWebView, presentation: [String: Any]? = nil) {
        guard reportTargets[view] != nil, !failedReportLoads.contains(view), !printInProgress(view) else { return }
        if view.isLoading || reportLoads[view]?.ready != true { printViews[view] = presentation ?? [:] }
        else { printReport(view, presentation: presentation) }
    }
    private func resetReportLoad(_ view: WKWebView) {
        view.isHidden = true
        reportLoads.removeValue(forKey: view)
        failedReportLoads.remove(view)
        pendingPrints.removeValue(forKey: view)
    }
    private func loadReport(_ view: WKWebView, target: ReportTarget) {
        resetReportLoad(view)
        reportTargets[view] = target
        var request = URLRequest(url: target.url, cachePolicy: .reloadIgnoringLocalCacheData)
        request.setValue("1", forHTTPHeaderField: "X-ResearchHelper-Report-Preview")
        view.load(request)
    }
    func prepareReport(_ url: URL, printing: Bool, persistent: Bool = true,
                       presentation: [String: Any]? = nil, digest: String? = nil) -> NSWindow? {
        guard isReportURL(url) else { return nil }
        // Keep any in-flight print frozen. The same document takes precedence
        // over an unrelated idle window, independent of dictionary order.
        let matchingPreview = reportWindows.first(where: { reader, view in
            reader !== workspacePrintWindow && sameReportDocument(reportTargets[view]?.url, url)
        })
        let reusablePreview = matchingPreview ?? reportWindows.first(where: { reader, view in
            reader !== workspacePrintWindow && !printInProgress(view)
        })
        if let (existingWindow, view) = reusablePreview {
            guard !printInProgress(view) else { return existingWindow }
            let targetChanged = !sameReportDocument(reportTargets[view]?.url, url) ||
                (digest != nil && digest != reportTargets[view]?.digest)
            let refreshForPrint = printing && (presentation != nil || digest != nil)
            let needsLoad = targetChanged || (!view.isLoading &&
                (!sameReportDocument(view.url, url) || failedReportLoads.contains(view) || refreshForPrint))
            if needsLoad {
                // Reapply workspace styles to a fresh report so an earlier
                // print's chart overrides cannot carry over to this request.
                loadReport(view, target: ReportTarget(url: url, digest: digest))
            }
            if printing { requestReportPrint(view, presentation: presentation) }
            return existingWindow
        }
        let (reportWindow, view) = makeReportWindow(persistent: persistent)
        loadReport(view, target: ReportTarget(url: url, digest: digest))
        if printing { requestReportPrint(view, presentation: presentation) }
        reportWindow.center()
        return reportWindow
    }
    private func makeReportWindow(persistent: Bool) -> (NSWindow, WKWebView) {
        let reportWindow = ResearchHelperWindow(contentRect: NSRect(x: 0, y: 0, width: 1050, height: 800),
                                    styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        reportWindow.title = "研究报告"
        reportWindow.isReleasedWhenClosed = false
        reportWindow.delegate = self
        let sessionStore = persistent ? webView?.configuration.websiteDataStore : nil
        let view = createWebView(persistent: persistent, dataStore: sessionStore)
        view.allowsMagnification = true
        reportWindow.contentView = view
        reportWindows[reportWindow] = view
        return (reportWindow, view)
    }
    func openReport(_ url: URL, printing: Bool, presentation: [String: Any]? = nil, digest: String? = nil) {
        guard let reportWindow = prepareReport(url, printing: printing, presentation: presentation, digest: digest) else { return }
        reportWindow.makeKeyAndOrderFront(nil)
    }
    func webView(_ webView: WKWebView, didCommit navigation: WKNavigation!) {
        if webView === self.webView, let url = webView.url, navigationState.accepts(url, origin: runtimeURL) {
            syncNativeScale(to: webView)
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === self.webView, let url = webView.url, navigationState.accepts(url, origin: runtimeURL) {
            syncNativeScale(to: webView)
            startupTimer?.invalidate()
            verifyMainPresentation(generation: navigationState.generation, attempt: 0,
                                   deadline: ProcessInfo.processInfo.systemUptime + 15)
        }
        if let target = reportTargets[webView], let load = reportLoads[webView],
           load.navigation === navigation, sameReportDocument(webView.url, target.url) {
            inspectReportDocument(webView, target: target, digest: load.digest) { [weak self, weak webView] valid in
                guard let self, let view = webView, self.reportLoads[view]?.navigation === load.navigation,
                      self.sameReportDocument(view.url, target.url) else { return }
                guard valid else {
                    self.failReportLoad(view, message: "报告内容与所选版本不一致，请重新打开。")
                    return
                }
                self.reportLoads[view]?.ready = true
                view.isHidden = false
                if let presentation = self.printViews.removeValue(forKey: view) {
                    self.printReport(view, presentation: presentation.isEmpty ? nil : presentation)
                }
            }
        }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        if webView === self.webView {
            workspaceReadyAfterNavigation = false
            setWorkspaceControlsReady(false)
            cancelFilePanels()
            cancelWorkspacePrint()
            if runtimeURL != nil { beginNavigationWatchdog() }
        }
        else if reportTargets[webView] != nil, let navigation {
            resetReportLoad(webView)
            reportLoads[webView] = ReportLoad(navigation: navigation)
        }
    }
    func reportPrintInfo() -> NSPrintInfo {
        // Keep the selected printer, but give each report its own A4 layout
        // and complete page range before presenting the print panel.
        let info = NSPrintInfo.shared.copy() as! NSPrintInfo
        info.orientation = .portrait
        info.paperSize = NSSize(width: 595.2756, height: 841.8898)
        info.leftMargin = 19.8425; info.rightMargin = 19.8425
        info.topMargin = 19.8425; info.bottomMargin = 19.8425
        info.scalingFactor = 1
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isHorizontallyCentered = false; info.isVerticallyCentered = false
        let settings = info.dictionary()
        settings[NSPrintInfo.AttributeKey.allPages] = true
        settings[NSPrintInfo.AttributeKey.selectionOnly] = false
        settings.removeObject(forKey: NSPrintInfo.AttributeKey.firstPage)
        settings.removeObject(forKey: NSPrintInfo.AttributeKey.lastPage)
        return info
    }
    private func inspectReportDocument(_ webView: WKWebView, target: ReportTarget, digest: String?,
                                       printing: Bool = false, presentation: [String: Any]? = nil,
                                       completion: @escaping (Bool) -> Void) {
        let example: Any = target.url.path.hasPrefix("/reports/")
            ? target.url.deletingPathExtension().lastPathComponent as Any : NSNull()
        let variant = URLComponents(url: target.url, resolvingAgainstBaseURL: false)?.queryItems?.first?.value ?? "standard"
        let expected: Any = digest.map { $0 as Any } ?? NSNull()
        let visuals: Any = presentation.map { $0 as Any } ?? NSNull()
        webView.callAsyncJavaScript("""
            if (document.readyState !== 'complete' ||
                !['text/html','application/xhtml+xml'].includes(document.contentType) ||
                !document.querySelector('main') || !document.querySelector('h1')) return false;
            if (example === null) {
                let paper;
                try { paper = JSON.parse(document.querySelector('script#rh-publication-document[type="application/json"]')?.textContent); }
                catch { return false; }
                if (paper?.schema !== 'researchhelper.presentation-document' || paper.version !== 1 ||
                    paper.profile !== variant || document.body?.dataset.reportVariant !== variant ||
                    expected === null || paper.content_hash !== expected) return false;
            } else if (document.querySelector('meta[name="rh-source-key"]')?.content !== example) return false;
            if (visuals !== null) {
                if (typeof window.RHVisuals?.applyProfile !== 'function') return false;
                window.RHVisuals.applyProfile(visuals);
            }
            if (printing) window.dispatchEvent(new Event('beforeprint'));
            return true;
            """, arguments: ["example": example, "variant": variant, "expected": expected,
                             "visuals": visuals, "printing": printing], in: nil, in: .page) { result in
            if case .success(let value) = result { completion(value as? Bool == true) }
            else { completion(false) }
        }
    }
    func printReport(_ webView: WKWebView, presentation: [String: Any]? = nil) {
        guard let owner = webView.window, reportWindows[owner] === webView,
              let target = reportTargets[webView], sameReportDocument(webView.url, target.url),
              let load = reportLoads[webView], load.ready,
              !webView.isLoading, !failedReportLoads.contains(webView), !printInProgress(webView) else { return }
        let fromWorkspace = owner === workspacePrintWindow
        guard let sheetOwner = fromWorkspace ? window : owner else { return }
        let token = UUID()
        pendingPrints[webView] = token
        inspectReportDocument(webView, target: target, digest: load.digest, printing: true, presentation: presentation) { valid in
            guard self.pendingPrints[webView] == token else { return }
            guard valid else {
                self.failReportLoad(webView, message: "报告未能准备打印，请重新打开后再试。")
                return
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                guard self.pendingPrints[webView] == token else { return }
                self.pendingPrints.removeValue(forKey: webView)
                guard self.reportWindows[owner] === webView,
                      self.sameReportDocument(webView.url, target.url), !webView.isLoading,
                      self.reportLoads[webView]?.navigation === load.navigation,
                      !self.failedReportLoads.contains(webView) else { return }
                guard sheetOwner.attachedSheet == nil, !self.quitting,
                      !fromWorkspace || self.workspaceControlsReady else {
                    if fromWorkspace { owner.close() }
                    else { webView.evaluateJavaScript("window.dispatchEvent(new Event('afterprint'))") }
                    return
                }
                let operation = webView.printOperation(with: self.reportPrintInfo())
                operation.showsPrintPanel = true
                operation.showsProgressPanel = true
                // WebKit must draw pages while the AppKit run loop is free.
                self.printOperations[operation] = webView
                operation.runModal(for: sheetOwner, delegate: self,
                                   didRun: #selector(self.printDidRun(_:success:contextInfo:)), contextInfo: nil)
            }
        }
    }
    @objc func printDidRun(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        // AppKit may finish an asynchronous print job on its worker thread.
        // Both the ownership table and WebKit belong to the main thread.
        DispatchQueue.main.async {
            guard let source = self.printOperations.removeValue(forKey: operation) else { return }
            if let document = self.workspacePrintWindow, self.reportWindows[document] === source {
                document.close()
            } else if self.reportTargets[source] != nil {
                source.evaluateJavaScript("window.dispatchEvent(new Event('afterprint'))")
            }
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
        if ["workspace-state", "rail-state", "delivery-state"].contains(action) {
            receiveWorkspaceState(body, sourceView: message.webView, sourceURL: source, mainFrame: message.frameInfo.isMainFrame)
            return
        }
        if action == "signed_out" {
            guard message.webView === webView, message.frameInfo.isMainFrame, source.path == "/" else { return }
            leaveWorkspace()
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
        if ["save", "save_binary", "save_original", "save_backup"].contains(action) {
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
            || reportTargets[sourceView].map { sameReportDocument(sourceURL, $0.url) } == true
    }
    func artifactContent(_ body: [String: Any], sourceView: WKWebView?, sourceURL: URL, mainFrame: Bool) -> (String,Data)? {
        let action = body["action"] as? String
        if action == "save", let (name,text) = saveContent(body, sourceView: sourceView, sourceURL: sourceURL, mainFrame: mainFrame) {
            return (name,Data(text.utf8))
        }
        let original = action == "save_original"
        let backup = action == "save_backup"
        let maximumBytes = backup ? 128 * 1024 * 1024 : original ? 25 * 1024 * 1024 : 20_000_000
        let maximumEncoded = (maximumBytes + 2) / 3 * 4
        guard backup || original || action == "save_binary", sourceView === webView, mainFrame,
              allowed(sourceURL), sourceURL.path == "/", workspaceControlsReady,
              let name = body["name"] as? String, !name.isEmpty, name.utf8.count <= 255,
              !name.contains("/"), !name.contains("\\"), !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              let encoded = body["base64"] as? String, encoded.utf8.count <= maximumEncoded,
              let data = Data(base64Encoded: encoded), data.count <= maximumBytes else { return nil }
        let ext = (name as NSString).pathExtension.lowercased()
        if backup {
            return ext == "zip" && data.starts(with: Data([0x50, 0x4b, 0x03, 0x04])) ? (name,data) : nil
        }
        if original {
            return ["pdf","docx","txt","md","html","htm"].contains(ext) ? (name,data) : nil
        }
        guard !data.isEmpty, ["pdf","docx","xlsx","csv","png"].contains(ext) else { return nil }
        if ext == "pdf" && !data.starts(with: Data("%PDF-".utf8)) { return nil }
        if ["docx","xlsx"].contains(ext) && !data.starts(with: Data([0x50,0x4b])) { return nil }
        if ext == "csv" && String(data: data, encoding: .utf8) == nil { return nil }
        if ext == "png" && !data.starts(with: Data([137, 80, 78, 71, 13, 10, 26, 10])) { return nil }
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
    func applyApplicationIcon(_ theme: String) {
        // Decode the supplied native icon bitmap, avoiding stale Icon Services
        // entries when replacing a candidate at the same installation path.
        let resource = theme == "dark" ? "ResearchHelperDockDark" : "ResearchHelperDock"
        if let iconURL = Bundle.main.url(forResource: resource, withExtension: "png"),
           let icon = NSImage(contentsOf: iconURL) {
            NSApp.applicationIconImage = icon
        }
    }
    func applyNativeTheme(_ theme: String, preference: String) {
        applyApplicationIcon(theme)
        UserDefaults.standard.set(preference, forKey: "researchhelper.nativeThemePreference")
        // An explicit appearance overrides WebKit's system-color query. Auto
        // must inherit AppKit appearance so OS changes can reach matchMedia.
        let appearance = preference == "auto" ? nil : NSAppearance(named: theme == "dark" ? .darkAqua : .aqua)
        window.appearance = appearance
        sidebarMaterialView?.appearance = appearance
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if let target = reportTargets[webView] {
            let sameDocument = sameReportDocument(url, target.url)
            if navigationAction.navigationType == .linkActivated && !sameDocument {
                if url.scheme == "https" { NSWorkspace.shared.open(url) }
                else if isReportURL(url) { openReport(url, printing: false) }
                decisionHandler(.cancel); return
            }
            let reportAllowed = sameDocument || url.absoluteString == "about:blank"
            if !reportAllowed && navigationAction.targetFrame?.isMainFrame != false {
                failReportLoad(webView, message: "报告读取失败，请重新打开后再试。")
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
    private func reportResponseDigest(_ response: URLResponse, target: ReportTarget) -> String? {
        let path = target.url.path.split(separator: "/")
        guard path.count == 5, let version = Int(path[4]), let http = response as? HTTPURLResponse,
              let header = http.value(forHTTPHeaderField: "X-ResearchHelper-Report") else { return nil }
        let fields = header.split(separator: ":", omittingEmptySubsequences: false)
        guard fields.count == 3, fields[0] == path[2], fields[1] == String(version),
              fields[2].range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { return nil }
        let digest = String(fields[2])
        return target.digest == nil || target.digest == digest ? digest : nil
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let target = reportTargets[webView], navigationResponse.isForMainFrame {
            let response = navigationResponse.response
            let example = target.url.path.hasPrefix("/reports/")
            let digest = example ? nil : reportResponseDigest(response, target: target)
            guard validReportResponse(response), sameReportDocument(response.url, target.url),
                  reportLoads[webView] != nil, example || digest != nil else {
                failReportLoad(webView, message: "报告读取失败，请检查登录状态和报告版本后重新打开。")
                decisionHandler(.cancel)
                return
            }
            reportLoads[webView]?.digest = digest
        }
        // The service can redirect an expired session to login before any
        // workspace script runs. Retire its native windows before login renders.
        if webView === self.webView, navigationResponse.isForMainFrame,
           let url = navigationResponse.response.url, allowed(url), url.path == "/login/index.html" {
            leaveWorkspace()
        }
        decisionHandler(.allow)
    }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if webView === self.webView, workspaceControlsReady, navigationAction.sourceFrame.isMainFrame,
           navigationAction.targetFrame == nil, let source = navigationAction.sourceFrame.request.url,
           allowed(source), source.path == "/", let url = navigationAction.request.url, isReportURL(url) {
            openReport(url, printing: false)
        }
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
        if closed === workspacePrintWindow { workspacePrintWindow = nil }
        reportTargets.removeValue(forKey: view)
        reportLoads.removeValue(forKey: view)
        failedReportLoads.remove(view)
        printViews.removeValue(forKey: view)
        pendingPrints.removeValue(forKey: view)
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
        if webView === self.webView {
            showNavigationFailure("页面连接失败。后台研究不受影响，可重新打开页面。", generation: navigationState.generation)
        } else if let load = reportLoads[webView], load.navigation === navigation, reportTargets[webView] != nil {
            failReportLoad(webView, message: "报告加载失败，请重新打开。")
        }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        self.webView(webView, didFailProvisionalNavigation: navigation, withError: error)
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if webView === self.webView {
            let token = navigationState.begin() // Invalidate pending DOM and snapshot callbacks.
            showNavigationFailure("页面进程已结束。后台研究不受影响，可重新打开页面。", generation: token)
        } else if reportTargets[webView] != nil {
            failReportLoad(webView, message: "报告页面已关闭，请重新打开。")
        }
    }
    func failReportLoad(_ view: WKWebView, message: String) {
        view.isHidden = true
        reportLoads.removeValue(forKey: view)
        failedReportLoads.insert(view)
        printViews.removeValue(forKey: view)
        pendingPrints.removeValue(forKey: view)
        let printDocument = workspacePrintWindow.flatMap { reportWindows[$0] === view ? $0 : nil }
        let owner = printDocument == nil ? view.window : window
        if printDocument != nil { cancelWorkspacePrint() }
        if let owner, owner.isVisible, owner.attachedSheet == nil { inform(message, in: owner) }
    }
    func stopBackend() {
        let lifetime = backendLifetime
        backendLifetime = nil
        try? lifetime?.close()
    }

    private func finishDeferredQuit() {
        guard let deadline = backendQuitDeadline else { return }
        backendQuitDeadline = nil
        deadline.cancel()
        stopBackend()
        NSApp.reply(toApplicationShouldTerminate: true)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if quitting { return backendQuitDeadline == nil ? .terminateNow : .terminateLater }
        quitting = true
        cancelWorkspacePrint()
        guard backend?.isRunning == true else {
            stopBackend()
            return .terminateNow
        }
        // The backend owns any admitted write beyond this UI wait. Closing
        // the window does not revoke the backend's Store lease.
        let deadline = DispatchWorkItem { [weak self] in self?.finishDeferredQuit() }
        backendQuitDeadline = deadline
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: deadline)
        if workspaceControlsReady, let view = webView, let url = view.url, allowed(url), url.path == "/" {
            // Keep the loopback service alive briefly to acknowledge the last keystrokes.
            let draftDeadline = DispatchWorkItem { [weak self] in self?.stopBackend() }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5, execute: draftDeadline)
            view.callAsyncJavaScript("return await window.ResearchHelperNative?.flushWorkspaceDraft?.()",
                                     arguments: [:], in: nil, in: .page) { [weak self] _ in
                draftDeadline.cancel()
                self?.stopBackend()
            }
        } else {
            stopBackend()
        }
        return .terminateLater
    }
    func applicationWillTerminate(_ notification: Notification) {
        quitting = true
        backendQuitDeadline?.cancel()
        backendQuitDeadline = nil
        cancelFilePanels()
        cancelWorkspacePrint()
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
