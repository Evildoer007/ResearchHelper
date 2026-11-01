import AppKit
import WebKit

// A background HTML print worker. It never starts research or shows a window.
final class HTMLPDFRenderer: NSObject, WKNavigationDelegate {
    let input: URL
    let output: URL
    var owner: NSWindow!
    var view: WKWebView!
    var operation: NSPrintOperation?
    var started = false
    var finished = false
    var charts = 0
    var printRules = 0
    var printLayout: [[String: Any]] = []
    let deadline = Date().addingTimeInterval(30)

    init(input: URL, output: URL) { self.input = input; self.output = output }

    func start() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        // Generated reports are self-contained. Even a changed input may not
        // contact a remote server while being converted.
        let rules = #"[{"trigger":{"url-filter":"^https?://"},"action":{"type":"block"}},{"trigger":{"url-filter":"^wss?://"},"action":{"type":"block"}}]"#
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "ResearchHelperOfflinePDF", encodedContentRuleList: rules) { list, error in
            guard let list, error == nil else { self.finish(false, "无法启用离线PDF转换"); return }
            config.userContentController.add(list)
            self.owner = NSWindow(contentRect: NSRect(x: -10000, y: -10000, width: 794, height: 1123), styleMask: .borderless, backing: .buffered, defer: false)
            self.owner.isReleasedWhenClosed = false
            self.view = WKWebView(frame: NSRect(x: 0, y: 0, width: 794, height: 1123), configuration: config)
            self.view.navigationDelegate = self
            self.owner.contentView = self.view
            self.view.loadFileURL(self.input, allowingReadAccessTo: self.input)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 32) { self.finish(false, "PDF转换超时，HTML已保留") }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(action.request.url == input ? .allow : .cancel)
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { ready() }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { finish(false, "报告加载失败") }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { finish(false, "报告加载失败") }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { finish(false, "报告渲染进程已退出") }

    func ready() {
        guard !finished && !started else { return }
        guard Date() < deadline else { finish(false, "报告图表尚未就绪"); return }
        view.evaluateJavaScript("""
        (() => {
          // A hidden WKWebView can pause animation-frame callbacks. Apply
          // this report's print rules before measuring or redrawing its SVGs.
          // Edit the CSSOM in place to preserve the original cascade order;
          // the saved HTML and its frozen research content stay unchanged.
          let printRules = 0;
          for (const sheet of document.styleSheets) {
            try {
              for (const rule of sheet.cssRules) {
                if (rule.type === CSSRule.MEDIA_RULE && rule.media.mediaText.trim() === 'print') {
                  rule.media.mediaText = 'all'; printRules++;
                }
              }
            } catch (_) {}
          }
          const plots = [...document.querySelectorAll('main .chart')];
          return {ready: document.readyState === 'complete' && !!document.querySelector('main') &&
            (!document.fonts || document.fonts.status === 'loaded') && plots.every(n => n.querySelector('svg')),
            charts: plots.length, printRules};
        })()
        """) { result, error in
            guard !self.finished else { return }
            if error != nil { self.finish(false, "报告图表无法加载"); return }
            if let value = result as? [String: Any] {
                self.printRules = max(self.printRules, value["printRules"] as? Int ?? 0)
            }
            if let value = result as? [String: Any], value["ready"] as? Bool == true {
                self.charts = value["charts"] as? Int ?? 0
                self.started = true
                self.view.evaluateJavaScript("""
                (() => {
                  window.dispatchEvent(new Event('beforeprint'));
                  const plots = [...document.querySelectorAll('main .chart')];
                  for (const node of plots) window.echarts?.getInstanceByDom(node)?.resize();
                  window.RHVisuals?.applyProfile();
                  return plots.map(node => ({id: node.id, width: node.clientWidth,
                    rendered: window.echarts?.getInstanceByDom(node)?.getWidth() ?? 0}));
                })()
                """) { result, error in
                    if error != nil { self.finish(false, "报告打印样式未就绪"); return }
                    self.printLayout = result as? [[String: Any]] ?? []
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { self.printHTML() }
                }
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { self.ready() }
            }
        }
    }

    func printHTML() {
        guard !finished else { return }
        let info = NSPrintInfo()
        info.paperSize = NSSize(width: 595.2756, height: 841.8898)
        info.leftMargin = 19.8425; info.rightMargin = 19.8425
        info.topMargin = 19.8425; info.bottomMargin = 19.8425
        // WebKit handles CSS units. A second scale would leave A4 mostly blank.
        info.scalingFactor = 1
        info.horizontalPagination = .fit
        info.isHorizontallyCentered = false; info.isVerticallyCentered = false
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = output
        let job = view.printOperation(with: info)
        job.showsPrintPanel = false; job.showsProgressPanel = false
        operation = job
        // WebKit must draw asynchronously; synchronous run() can produce blank pages.
        job.runModal(for: owner, delegate: self, didRun: #selector(didPrint(_:success:contextInfo:)), contextInfo: nil)
    }

    @objc func didPrint(_ operation: NSPrintOperation, success: Bool, contextInfo: UnsafeMutableRawPointer?) {
        DispatchQueue.main.async {
            self.operation = nil
            let data = try? Data(contentsOf: self.output)
            self.finish(success && data?.starts(with: Data("%PDF-".utf8)) == true, "PDF未保存，HTML已保留")
        }
    }

    func finish(_ success: Bool, _ message: String) {
        guard Thread.isMainThread else { DispatchQueue.main.async { self.finish(success, message) }; return }
        guard !finished else { return }; finished = true
        let visible = owner?.isVisible ?? false
        view?.navigationDelegate = nil; view?.stopLoading(); owner?.close()
        let record: [String: Any] = ["status": success ? "saved" : "failed", "engine": "webkit",
          "charts": charts, "print_rules": printRules, "print_layout": printLayout,
          "foreground_opened": visible, "system_panels_shown": false,
          "physical_job_sent": false, "error": success ? "" : message]
        if let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) {
            FileHandle.standardOutput.write(data)
        }
        exit(success && !visible ? 0 : 1)
    }
}

@main
struct HTMLPDFWorker {
    static func main() {
        guard CommandLine.arguments.count == 3 else { exit(2) }
        let input = URL(fileURLWithPath: CommandLine.arguments[1]).standardizedFileURL
        let output = URL(fileURLWithPath: CommandLine.arguments[2]).standardizedFileURL
        guard input.pathExtension == "html", output.pathExtension == "pdf", input != output,
              FileManager.default.fileExists(atPath: input.path), !FileManager.default.fileExists(atPath: output.path)
        else { exit(2) }
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited); app.finishLaunching()
        let renderer = HTMLPDFRenderer(input: input, output: output)
        renderer.start()
        withExtendedLifetime(renderer) { app.run() }
    }
}
