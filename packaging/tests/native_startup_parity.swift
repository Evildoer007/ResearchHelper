// Isolated hidden WebKit probe. The production App entry is never executed.
import AppKit
import WebKit

@main
struct NativeStartupParity {
    static var checks: [String] = []
    static var observations: [[String: Any]] = []

    static func check(_ name: String, _ valid: Bool) throws {
        guard valid else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }

    static func wait(_ predicate: () -> Bool, seconds: Double = 12) throws {
        let deadline = Date().addingTimeInterval(seconds)
        while !predicate() && Date() < deadline {
            RunLoop.main.run(until: Date().addingTimeInterval(0.02))
        }
        guard predicate() else { throw NSError(domain: "Hidden WebKit timed out", code: 1) }
    }

    static func script(_ source: String, in view: WKWebView) throws -> Any? {
        var finished = false
        var value: Any?
        var failure: Error?
        view.evaluateJavaScript(source) { result, error in
            value = result; failure = error; finished = true
        }
        try wait { finished }
        if let failure { throw failure }
        return value
    }

    static func snapshot(_ view: WKWebView, destination: URL) throws {
        var finished = false
        var failure: Error?
        view.takeSnapshot(with: nil) { image, error in
            defer { finished = true }
            if let error { failure = error; return }
            do {
                guard let data = image?.tiffRepresentation,
                      let bitmap = NSBitmapImageRep(data: data),
                      let png = bitmap.representation(using: .png, properties: [:])
                else { throw NSError(domain: "Missing WebKit snapshot", code: 1) }
                try png.write(to: destination)
            } catch { failure = error }
        }
        try wait { finished }
        if let failure { throw failure }
    }

    static func run() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let base = environment["RESEARCHHELPER_STARTUP_FIXTURE"].flatMap(URL.init(string:)),
              let output = environment["RESEARCHHELPER_STARTUP_OUTPUT"],
              let privateHome = environment["CFFIXED_USER_HOME"]
        else { throw NSError(domain: "Missing isolated fixture", code: 1) }
        try check("Foundation home is isolated", NSHomeDirectory() == privateHome)
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        app.finishLaunching()
        for (theme, width, height) in [("light", 1320, 860), ("dark", 390, 844)] {
            UserDefaults.standard.set(theme, forKey: "researchhelper.nativeThemePreference")
            let owner = ResearchHelperApp()
            owner.runtimeURL = base
            owner.window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height),
                                    styleMask: [.titled, .fullSizeContentView], backing: .buffered, defer: false)
            owner.window.isReleasedWhenClosed = false
            owner.webView = owner.createWebView(persistent: false)
            owner.window.contentView = owner.webView
            defer {
                owner.webView.stopLoading()
                owner.webView.configuration.userContentController.removeAllScriptMessageHandlers()
                owner.webView.navigationDelegate = nil
                owner.webView.uiDelegate = nil
                owner.window.close()
            }
            let probe = """
            window.__startupProbe={pathFills:0,textFills:0,samples:0,frames:0,errors:[]};
            window.addEventListener('error',e=>window.__startupProbe.errors.push(e.message));
            const originalFill=CanvasRenderingContext2D.prototype.fill;
            CanvasRenderingContext2D.prototype.fill=function(...args){if(args[0] instanceof Path2D)window.__startupProbe.pathFills++;return originalFill.apply(this,args)};
            const originalText=CanvasRenderingContext2D.prototype.fillText;
            CanvasRenderingContext2D.prototype.fillText=function(...args){window.__startupProbe.textFills++;return originalText.apply(this,args)};
            const originalPixels=CanvasRenderingContext2D.prototype.getImageData;
            CanvasRenderingContext2D.prototype.getImageData=function(...args){const value=originalPixels.apply(this,args);if(value.data.some((v,i)=>i%4===3&&v>60))window.__startupProbe.samples++;return value};
            const originalFrame=window.requestAnimationFrame.bind(window);
            window.requestAnimationFrame=callback=>originalFrame(time=>{window.__startupProbe.frames++;callback(time)});
            """
            owner.webView.configuration.userContentController.addUserScript(
                WKUserScript(source: probe, injectionTime: .atDocumentStart, forMainFrameOnly: true))
            let url = URL(string: "/login/index.html?app_startup=hidden-probe", relativeTo: base)!.absoluteURL
            owner.webView.load(URLRequest(url: url))
            try wait { (try? script("document.readyState==='complete'&&!!window.__startupProbe", in: owner.webView)) as? Bool == true }
            let reduced = try script("matchMedia('(prefers-reduced-motion:reduce)').matches", in: owner.webView) as? Bool == true
            if !reduced {
                try wait { (try? script("!!document.querySelector('.login-splash__reference-logo')", in: owner.webView)) as? Bool == true }
                let geometry = try script("""
                (()=>{const logo=document.querySelector('.login-splash__reference-logo'),disc=logo.querySelector('.login-splash__reference-disc').getBoundingClientRect(),arc=logo.querySelector('.login-splash__reference-arc');return {pathFills:__startupProbe.pathFills,textFills:__startupProbe.textFills,samples:__startupProbe.samples,diameter:disc.width,arcOpacity:arc.querySelector('path').getAttribute('opacity'),svgTexts:logo.querySelectorAll('text').length}})()
                """, in: owner.webView) as! [String: Any]
                try check(theme + " fixed paths sampled in real WebKit", geometry["pathFills"] as? Int == 2 && geometry["textFills"] as? Int == 0)
                try check(theme + " glyph pixels are nonempty", (geometry["samples"] as? Int ?? 0) > 0)
                try check(theme + " arc opacity stays on child path", geometry["arcOpacity"] as? String == ".42")
                try check(theme + " no system-font SVG text", geometry["svgTexts"] as? Int == 0)
                let expected = min(336.0, Double(width) * 0.49) * 256 / 1053
                try check(theme + " disc matches OH physical diameter", abs((geometry["diameter"] as? Double ?? 0) - expected) < 0.1)
                observations.append(["theme": theme, "geometry": geometry])
            }
            try wait { (try? script("!document.querySelector('#login-splash')", in: owner.webView)) as? Bool == true }
            let state = try script("""
            ({theme:document.documentElement.dataset.theme,inert:document.querySelector('.login-wrap').hasAttribute('inert'),token:new URL(location.href).searchParams.has('app_startup'),overflow:document.documentElement.scrollWidth>innerWidth,errors:__startupProbe.errors,frames:__startupProbe.frames,reduced:matchMedia('(prefers-reduced-motion:reduce)').matches})
            """, in: owner.webView) as! [String: Any]
            try check(theme + " login released", state["inert"] as? Bool == false)
            try check(theme + " launch token consumed", state["token"] as? Bool == false)
            try check(theme + " correct native theme", state["theme"] as? String == theme)
            try check(theme + " no horizontal overflow", state["overflow"] as? Bool == false)
            try check(theme + " no script errors", (state["errors"] as? [String])?.isEmpty == true)
            try check(theme + " hidden window never became visible", !owner.window.isVisible && !owner.window.isKeyWindow)
            try check(theme + " nonpersistent browser store", !owner.webView.configuration.websiteDataStore.isPersistent)
            // Hidden WK does not advance display-frame animations. Finish only
            // this test's static reveal before snapshotting; retain the real
            // frame count above and never call this native animation acceptance.
            _ = try script("document.getAnimations().forEach(animation=>{if(Number.isFinite(animation.effect.getComputedTiming().endTime))animation.finish()});true", in: owner.webView)
            let staticState = try script("({opacity:getComputedStyle(document.querySelector('.login-wrap')).opacity,visible:getComputedStyle(document.querySelector('.login-wrap')).visibility})", in: owner.webView) as! [String: Any]
            try check(theme + " static login opacity after explicit CSS finish", staticState["opacity"] as? String == "1")
            try check(theme + " static login visibility after explicit CSS finish", staticState["visible"] as? String == "visible")
            try snapshot(owner.webView, destination: URL(fileURLWithPath: output).appendingPathComponent("webkit-\(theme)-login.png"))
            observations.append(["theme": theme, "final": state, "static_snapshot": staticState,
                                 "static_snapshot_css_finished_by_test": true,
                                 "native_animation_visually_verified": false])
        }
    }

    static func main() {
        var record: [String: Any] = ["status": "failed", "native_window_opened": false,
                                   "formal_app_launched": false, "production_store_touched": false,
                                   "native_animation_visually_verified": false,
                                   "scope": "real WebKit vector sampling, geometry, DOM release and CSS-completed static snapshot only"]
        do { try run(); record["status"] = "scoped_passed" }
        catch { record["error"] = String(describing: error) }
        record["checks"] = checks; record["observations"] = observations
        FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]))
        if record["status"] as? String != "scoped_passed" { exit(1) }
    }
}
