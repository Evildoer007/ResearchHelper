// Independent accessibility controller for the actual packaged App process.
// No production shell is recompiled or JavaScript injected. App actions use
// its PID; native save keystrokes require that exact App to remain frontmost.
// Secure input and the system clipboard are never read.
import AppKit
import ApplicationServices

func attribute(_ element: AXUIElement, _ name: String) -> Any? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func string(_ element: AXUIElement, _ name: String) -> String {
    attribute(element, name) as? String ?? ""
}
func reportPath(_ element: AXUIElement) -> String? {
    let raw = attribute(element, kAXURLAttribute)
    let url = raw as? URL ?? (raw as? String).flatMap { URL(string: $0) }
    guard let url = url, url.scheme == "http", url.host == "127.0.0.1",
          url.path.hasPrefix("/api/tasks/"), url.path.contains("/report/") else { return nil }
    return url.path
}
func children(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? []
}
func actions(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success else { return [] }
    return names as? [String] ?? []
}
func nodes(_ root: AXUIElement, limit: Int = 2500) -> [AXUIElement] {
    // Forms and modal controls precede deep report/file-list descendants.
    var pending = [root], result: [AXUIElement] = []
    var cursor = 0
    var visited: [CFHashCode: [AXUIElement]] = [:]
    while cursor < pending.count && result.count < limit {
        let item = pending[cursor]
        cursor += 1
        let key = CFHash(item)
        if visited[key]?.contains(where: { CFEqual($0, item) }) == true { continue }
        visited[key, default: []].append(item)
        result.append(item)
        pending.append(contentsOf: children(item))
    }
    return result
}
func nativePanels(_ application: AXUIElement) -> [AXUIElement] {
    var pending = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    var result: [AXUIElement] = [], visited: [AXUIElement] = []
    while !pending.isEmpty && visited.count < 512 {
        let item = pending.removeFirst()
        if visited.contains(where: { CFEqual($0, item) }) { continue }
        visited.append(item)
        let role = string(item, kAXRoleAttribute)
        if ["AXSheet", "AXDialog"].contains(role) {
            // Save as PDF nests its save sheet inside the print sheet. Only
            // that innermost panel owns input; the printer settings behind it
            // must not consume the bounded traversal or a Cancel action.
            if string(item, "AXIdentifier") == "save-panel" { return [item] }
            result.append(item)
        }
        // Attached native sheets are outside WebKit's content. Avoid walking
        // the inactive report or treating an HTML dialog as a system panel.
        if ["AXWebArea", "AXMenuBar"].contains(role) { continue }
        pending.append(contentsOf: children(item))
    }
    return result
}
// Browser columns expose every row, including rows beyond the visible
// viewport. Search one native list row at a time instead of truncating the
// entire sheet tree before a late-alphabet directory can be reached.
func nativeDirectory(_ panels: [AXUIElement], _ name: String) -> AXUIElement? {
    for list in panels.flatMap({ nodes($0, limit: 250) }).filter({ string($0, kAXRoleAttribute) == "AXList" }).reversed() {
        for row in children(list) {
            if let item = nodes(row, limit: 12).first(where: {
                let role = string($0, kAXRoleAttribute)
                let named = role == "AXTextField" ? string($0, kAXValueAttribute) == name :
                    ["AXCell", "AXRow"].contains(role) && string($0, kAXTitleAttribute) == name
                return named && actions($0).contains("AXOpen")
            }) { return item }
        }
    }
    return nil
}
func matches(_ element: AXUIElement, _ selector: String) -> Bool {
    if selector == "@focused" { return attribute(element, kAXFocusedAttribute) as? Bool == true }
    if selector.hasPrefix("@") { return string(element, kAXRoleAttribute) == String(selector.dropFirst()) }
    if selector.hasPrefix("~") {
        return ["AXIdentifier", kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute].contains {
            string(element, $0).contains(String(selector.dropFirst()))
        }
    }
    return ["AXIdentifier", kAXTitleAttribute, kAXDescriptionAttribute, kAXHelpAttribute].contains {
        string(element, $0) == selector
    }
}
func emit(_ data: [String: Any]) {
    let value = try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys])
    print(String(data: value, encoding: .utf8)!)
}

// A locked or switched-out desktop cannot validate native input or painting.
// Read only the session flags; never report account names or other sessions.
func interactiveDesktop() -> [String: Any] {
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    let locked = session?["CGSSessionScreenIsLocked"] as? Bool == true
    let onConsole = session?["kCGSSessionOnConsoleKey"] as? Bool == true
    let frontmost = NSWorkspace.shared.frontmostApplication
    let foregroundAvailable = frontmost != nil && frontmost?.bundleIdentifier != "com.apple.loginwindow"
    return ["session_available": session != nil, "screen_locked": locked,
            "on_console": onConsole,
            "interactive_console": session != nil && onConsole && !locked && foregroundAvailable]
}

// Keep activation and the native menu/keyboard action in one helper session.
// Raising the main workspace separately can pull focus from a report's sheet.
func activateOwnedPanel(_ pid: pid_t) -> Bool {
    if NSWorkspace.shared.frontmostApplication?.processIdentifier == pid { return true }
    guard interactiveDesktop()["interactive_console"] as? Bool == true,
          let app = NSRunningApplication(processIdentifier: pid),
          app.bundleIdentifier == "io.researchhelper.app" else { return false }
    app.unhide()
    _ = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
    _ = AXUIElementSetAttributeValue(AXUIElementCreateApplication(pid), kAXFrontmostAttribute as CFString, kCFBooleanTrue)
    let deadline = Date().addingTimeInterval(2)
    while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid && Date() < deadline {
        RunLoop.current.run(until: Date().addingTimeInterval(0.05))
    }
    return NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
}

// This helper never becomes a foreground application between target actions.
// AX handles stay local to one command instead of Swift script globals.
func runController() {
    NSApplication.shared.setActivationPolicy(.prohibited)
    let arguments = CommandLine.arguments
    guard arguments.count >= 2 else { emit(["error": "missing command"]); exit(1) }
    let command = arguments[1]
    if command == "trusted" { emit(["trusted": AXIsProcessTrusted()]); exit(0) }
    if command == "session" { emit(interactiveDesktop()); exit(0) }
    if command == "frontmost" {
        emit(["pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)])
        exit(0)
    }
    guard arguments.count >= 3, let pid = Int32(arguments[2]), pid > 0 else {
        emit(["error": "missing owned App pid"]); exit(1)
    }
    if command == "identity" {
        guard let app = NSRunningApplication(processIdentifier: pid) else { emit(["error": "App exited"]); exit(1) }
        emit(["pid": Int(pid), "bundle_identifier": app.bundleIdentifier ?? "",
              "executable": app.executableURL?.path ?? "",
              "foreground": NSWorkspace.shared.frontmostApplication?.processIdentifier == pid]); exit(0)
    }
    // Recheck immediately before every action. A screen lock between the
    // preflight and submission must not become an unseen input or a paid run.
    if ["activate", "press", "press-report", "close-report", "show-menu", "select-menu", "set", "focus", "click", "open-folder", "key", "save-key", "save-text", "native-folder", "native-folder-confirm"].contains(command),
       interactiveDesktop()["interactive_console"] as? Bool != true {
        emit(["error": "interactive desktop unavailable; no input posted", "desktop_unavailable": true])
        exit(1)
    }
    if command == "activate" || command == "hide" {
        guard let app = NSRunningApplication(processIdentifier: pid) else { emit(["error": "App exited"]); exit(1) }
        if command == "hide" { emit(["ok": app.hide()]); exit(0) }
        app.unhide()
        let activated = app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
        let target = AXUIElementCreateApplication(pid)
        let raised = (attribute(target, kAXWindowsAttribute) as? [AXUIElement] ?? []).first.map {
            AXUIElementPerformAction($0, kAXRaiseAction as CFString) == .success
        } ?? false
        // Raising a window is not proof that keyboard input belongs to it.
        // Wait for the OS activation request before permitting guarded keys.
        let focused = AXUIElementSetAttributeValue(target, kAXFrontmostAttribute as CFString, kCFBooleanTrue) == .success
        let deadline = Date().addingTimeInterval(2)
        while NSWorkspace.shared.frontmostApplication?.processIdentifier != pid && Date() < deadline {
            RunLoop.current.run(until: Date().addingTimeInterval(0.05))
        }
        let foreground = NSWorkspace.shared.frontmostApplication?.processIdentifier == pid
        var response: [String: Any] = ["ok": foreground, "activation_requested": activated,
            "window_raised": raised, "frontmost_requested": focused, "foreground_confirmed": foreground]
        if !foreground { response["error"] = "owned App did not become foreground" }
        emit(response); exit(foreground ? 0 : 1)
    }
    let application = AXUIElementCreateApplication(pid)
    AXUIElementSetMessagingTimeout(application, 2)
    AXUIElementSetAttributeValue(application, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
    RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    if command == "native-folder" || command == "native-folder-confirm" {
        // Go to Folder is a window key equivalent. Sending it only to the
        // remote filename field's PID skips AppKit's normal event routing.
        // Post this one shortcut only while the owned native save panel has
        // foreground focus. Never type text or touch the system clipboard.
        let panels = nativePanels(application)
        let confirms = command == "native-folder-confirm"
        guard arguments.count == 3,
              NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
              activateOwnedPanel(pid),
              panels.contains(where: { string($0, "AXIdentifier") == "save-panel" }),
              !confirms || panels.flatMap({ nodes($0, limit: 400) }).contains(where: {
                  string($0, "AXIdentifier") == "GoToWindow"
              }) else {
            emit(["error": "foreground owned native save panel required"]); exit(1)
        }
        for down in [true, false] {
            guard interactiveDesktop()["interactive_console"] as? Bool == true,
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  let event = CGEvent(keyboardEventSource: nil, virtualKey: confirms ? 36 : 5, keyDown: down) else {
                emit(["error": "owned save panel lost keyboard focus"]); exit(1)
            }
            event.flags = confirms ? [] : [.maskCommand, .maskShift]
            event.post(tap: .cghidEventTap)
        }
        // Queue key-down and key-up together. Return can close Go to Folder
        // immediately; a run-loop pause between the pair can move focus and
        // leave the shortcut incomplete even though navigation succeeded.
        RunLoop.current.run(until: Date().addingTimeInterval(0.12))
        emit(["ok": true, "global_event_posted": true, "foreground_guarded": true,
              "shortcut": confirms ? "Confirm Go to Folder" : "Go to Folder", "target_pid": Int(pid)]); exit(0)
    }
    if command == "save-text" {
        guard arguments.count == 4, arguments[3].utf16.count <= 4096,
              NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
              !arguments[3].unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
            emit(["error": "plain text for the owned ResearchHelper window required"]); exit(1)
        }
        let units = Array(arguments[3].utf16)
        for offset in stride(from: 0, to: units.count, by: 20) {
            var chunk = Array(units[offset..<min(offset + 20, units.count)])
            for down in [true, false] {
                guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else {
                    emit(["error": "could not create application-local text event"]); exit(1)
                }
                event.flags = []
                event.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: &chunk)
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
                    emit(["error": "owned App lost keyboard focus"]); exit(1)
                }
                event.post(tap: .cghidEventTap)
            }
            Thread.sleep(forTimeInterval: 0.015)
        }
        emit(["ok": true, "target_pid": Int(pid), "global_event_posted": true, "foreground_guarded": true]); exit(0)
    }
    if command == "key" || command == "save-key" {
        let codes: [String: CGKeyCode] = ["folder": 5, "return": 36, "space": 49, "select-all": 0, "escape": 53]
        guard arguments.count == 4, let code = codes[arguments[3]] else {
            emit(["error": "unsupported application-local key"]); exit(1)
        }
        var destination = pid
        if command == "save-key" {
            guard NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
                  let panel = nativePanels(application).first(where: { string($0, "AXIdentifier") == "save-panel" }) else {
                emit(["error": "native save panel owned by ResearchHelper required"]); exit(1)
            }
            let inputs = nodes(panel, limit: 400).filter {
                ["AXTextField", "AXComboBox"].contains(string($0, kAXRoleAttribute)) && string($0, kAXSubroleAttribute) != "AXSecureTextField"
            }
            guard let field = inputs.first(where: { attribute($0, kAXFocusedAttribute) as? Bool == true })
                    ?? inputs.first(where: { string($0, "AXIdentifier") == "saveAsNameTextField" }),
                  AXUIElementGetPid(field, &destination) == .success, destination > 0 else {
                emit(["error": "native save input owner unavailable"]); exit(1)
            }
        }
        for down in [true, false] {
            guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
                emit(["error": "owned native keyboard target unavailable"]); exit(1)
            }
            // PID-local delivery bypasses HID's keyboard-layout translation.
            // Supply the key-equivalent character as well as its virtual key.
            var characters = Array((arguments[3] == "folder" ? "G" : arguments[3] == "select-all" ? "a" :
                arguments[3] == "return" ? "\r" : arguments[3] == "space" ? " " : "\u{1b}").utf16)
            event.keyboardSetUnicodeString(stringLength: characters.count, unicodeString: &characters)
            event.flags = arguments[3] == "folder" ? [.maskCommand, .maskShift] : arguments[3] == "select-all" ? [.maskCommand] : []
            event.postToPid(destination)
            RunLoop.current.run(until: Date().addingTimeInterval(0.15))
        }
        emit(["ok": true, "target_pid": Int(destination), "app_pid": Int(pid),
              "global_event_posted": false, "owned_native_panel": command == "save-key",
              "foreground_guarded": false]); exit(0)
    }
    // Ask only the owned process to expose WebKit's lazily created AX tree.
    AXUIElementSetAttributeValue(application, "AXManualAccessibility" as CFString, kCFBooleanTrue)
    let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
    let sheets = nativePanels(application)
    // A native file sheet owns all input while it is open. Do not scan the
    // inactive WebKit report behind it or thousands of unrelated folder rows.
    let all = sheets.isEmpty ? windows.flatMap { nodes($0) } + nodes(application) : sheets.flatMap { nodes($0, limit: 250) }
    if command == "value" {
        let permitted = ["研究对象", "模型调用上限", "数据请求上限", "执行秒数上限", "任务要求"]
        guard arguments.count == 4, permitted.contains(arguments[3]),
              let item = all.first(where: { matches($0, arguments[3]) }),
              string(item, kAXSubroleAttribute) != "AXSecureTextField" else {
            emit(["error": "research control read unavailable"]); exit(1)
        }
        emit(["value": string(item, kAXValueAttribute)]); exit(0)
    }
    if command == "snapshot" {
        let items: [[String: Any]] = all.map { item in
            let role = string(item, kAXRoleAttribute), subrole = string(item, kAXSubroleAttribute)
            var row: [String: Any] = ["role": role, "subrole": subrole,
                "id": string(item, "AXIdentifier"), "title": string(item, kAXTitleAttribute),
                "description": string(item, kAXDescriptionAttribute), "actions": actions(item),
                "enabled": attribute(item, kAXEnabledAttribute) as? Bool ?? true,
                "focused": attribute(item, kAXFocusedAttribute) as? Bool ?? false]
            if subrole != "AXSecureTextField" && (role != "AXTextField" || !sheets.isEmpty) {
                row["value"] = String(string(item, kAXValueAttribute).prefix(400))
            }
            if ["AXWebArea", "AXLink"].contains(role), let path = reportPath(item) {
                row["report_path"] = path
            }
            return row
        }
        let windows = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []).filter { ($0[kCGWindowOwnerPID as String] as? Int) == Int(pid) }
            .map { ["id": $0[kCGWindowNumber as String] ?? 0,
                    "bounds": $0[kCGWindowBounds as String] ?? [:],
                    "layer": $0[kCGWindowLayer as String] ?? 0] }
        emit(["items": items, "windows": windows]); exit(0)
    }
    guard arguments.count >= 4 else { emit(["error": "missing selector"]); exit(1) }
    let selector = arguments[3]
    if command == "press-report" || command == "close-report" {
        let button: AXUIElement?
        if command == "press-report", arguments.count == 5 {
            button = all.first {
                string($0, kAXRoleAttribute) == "AXLink" &&
                    reportPath($0) == selector && matches($0, arguments[4])
            }
        } else if command == "close-report" {
            button = windows.first {
                string($0, kAXTitleAttribute) == "研究报告" &&
                    nodes($0).contains { string($0, kAXRoleAttribute) == "AXWebArea" && reportPath($0) == selector }
            }.flatMap { window in
                guard let close = attribute(window, kAXCloseButtonAttribute) else { return nil }
                return (close as! AXUIElement)
            }
        } else {
            button = nil
        }
        guard let button = button else { emit(["error": "report control unavailable", "selector": selector]); exit(1) }
        let result = AXUIElementPerformAction(button, kAXPressAction as CFString)
        emit(["ok": result == .success, "code": result.rawValue, "selector": selector])
        exit(result == .success ? 0 : 1)
    }
    if command == "click" || command == "open-folder" {
        let directory = command == "open-folder"
        guard !directory || (!sheets.isEmpty && !selector.isEmpty && selector.count <= 200 &&
                ![".", ".."].contains(selector) && !selector.contains("/") && !selector.contains(":")),
              arguments.count == 4,
              NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
              let item = directory ? nativeDirectory(sheets, selector) : all.first(where: {
                  matches($0, selector) && string($0, kAXSubroleAttribute) == "AXSummary"
              }),
              let rawPosition = attribute(item, kAXPositionAttribute),
              let rawSize = attribute(item, kAXSizeAttribute),
              CFGetTypeID(rawPosition as AnyObject) == AXValueGetTypeID(), CFGetTypeID(rawSize as AnyObject) == AXValueGetTypeID() else {
            emit(["error": "visible native control in the foreground owned App required", "selector": selector]); exit(1)
        }
        if directory {
            // File-browser rows can exist outside the clipped viewport. Native
            // selection scrolls the row into view; the caller verifies the
            // destination and, if needed, performs one subsequent open.
            var ancestor: AXUIElement? = item, child = item
            for _ in 0..<10 {
                guard let node = ancestor else { break }
                let role = string(node, kAXRoleAttribute)
                if role == "AXList" {
                    let selected = attribute(node, kAXSelectedChildrenAttribute) as? [AXUIElement] ?? []
                    var settable = DarwinBoolean(false)
                    if !selected.contains(where: { CFEqual($0, child) }),
                       AXUIElementIsAttributeSettable(node, kAXSelectedChildrenAttribute as CFString, &settable) == .success,
                       settable.boolValue,
                       AXUIElementSetAttributeValue(node, kAXSelectedChildrenAttribute as CFString, [child] as CFArray) == .success {
                        emit(["ok": true, "method": "native_row_selection", "global_event_posted": false]); exit(0)
                    }
                }
                if ["AXCell", "AXRow"].contains(role), attribute(node, kAXSelectedAttribute) as? Bool != true {
                    var settable = DarwinBoolean(false)
                    if AXUIElementIsAttributeSettable(node, kAXSelectedAttribute as CFString, &settable) == .success,
                       settable.boolValue,
                       AXUIElementSetAttributeValue(node, kAXSelectedAttribute as CFString, kCFBooleanTrue) == .success {
                        emit(["ok": true, "method": "native_row_selection", "global_event_posted": false]); exit(0)
                    }
                }
                guard let parent = attribute(node, kAXParentAttribute),
                      CFGetTypeID(parent as AnyObject) == AXUIElementGetTypeID() else { break }
                child = node
                ancestor = unsafeBitCast(parent as AnyObject, to: AXUIElement.self)
            }
        }
        var origin = CGPoint.zero, size = CGSize.zero
        guard AXValueGetValue(unsafeBitCast(rawPosition as AnyObject, to: AXValue.self), .cgPoint, &origin),
              AXValueGetValue(unsafeBitCast(rawSize as AnyObject, to: AXValue.self), .cgSize, &size),
              size.width > 0, size.height > 0 else {
            emit(["error": "summary bounds unavailable"]); exit(1)
        }
        let point = CGPoint(x: origin.x + size.width / 2, y: origin.y + size.height / 2)
        let visible = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]] ?? []).contains { window in
                guard window[kCGWindowOwnerPID as String] as? Int == Int(pid),
                      window[kCGWindowLayer as String] as? Int == 0,
                      let bounds = window[kCGWindowBounds as String] as? [String: Any],
                      let rectangle = CGRect(dictionaryRepresentation: bounds as CFDictionary) else { return false }
                return rectangle.contains(point)
            }
        guard visible else { emit(["error": "native control is outside the owned visible window", "selector": selector]); exit(1) }
        if directory {
            let inside = sheets.contains { panel in
                guard let rawPoint = attribute(panel, kAXPositionAttribute), let rawExtent = attribute(panel, kAXSizeAttribute) else { return false }
                var panelOrigin = CGPoint.zero, panelSize = CGSize.zero
                guard AXValueGetValue(unsafeBitCast(rawPoint as AnyObject, to: AXValue.self), .cgPoint, &panelOrigin),
                      AXValueGetValue(unsafeBitCast(rawExtent as AnyObject, to: AXValue.self), .cgSize, &panelSize) else { return false }
                return CGRect(origin: panelOrigin, size: panelSize).contains(point)
            }
            guard inside else { emit(["error": "directory row is outside the native save panel", "selector": selector]); exit(1) }
        }
        let originalPointer = CGEvent(source: nil)?.location
        let count = directory ? 2 : 1
        for click in 1...count {
            for kind in [CGEventType.mouseMoved, .leftMouseDown, .leftMouseUp] {
                guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                      let event = CGEvent(mouseEventSource: nil, mouseType: kind, mouseCursorPosition: point, mouseButton: .left) else {
                    emit(["error": "owned App lost pointer focus"]); exit(1)
                }
                event.setIntegerValueField(.mouseEventClickState, value: Int64(click))
                event.post(tap: .cghidEventTap)
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
        if let previous = originalPointer, CGEvent(source: nil)?.location == point,
           NSWorkspace.shared.frontmostApplication?.processIdentifier == pid {
            CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: previous, mouseButton: .left)?.post(tap: .cghidEventTap)
        }
        emit(["ok": true, "target_pid": Int(pid), "global_event_posted": true,
              "foreground_guarded": true, "click_count": count]); exit(0)
    }
    if command == "focus" {
        guard let item = all.first(where: { matches($0, selector) }) else {
            emit(["error": "focus target unavailable", "selector": selector]); exit(1)
        }
        let error = AXUIElementSetAttributeValue(item, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        emit(["ok": error == .success, "code": error.rawValue]); exit(error == .success ? 0 : 1)
    }
    if command == "show-menu" || command == "select-menu" {
        // Native split buttons expose a separate menu action. Invoke that
        // action only in the owned foreground panel; never activate Print.
        guard arguments.count == (command == "select-menu" ? 5 : 4), !sheets.isEmpty,
              NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
              activateOwnedPanel(pid),
              let item = all.first(where: {
                  matches($0, selector) && ["AXMenuButton", "AXPopUpButton"].contains(string($0, kAXRoleAttribute)) &&
                  actions($0).contains(kAXShowMenuAction)
              }) else {
            emit(["error": "native menu in the foreground owned panel required", "selector": selector,
                  "native_panel_count": sheets.count, "foreground_owned": NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
                  "menu_candidates": all.filter { matches($0, selector) }.map { ["role": string($0, kAXRoleAttribute), "actions": actions($0)] }]); exit(1)
        }
        var error = AXUIElementPerformAction(item, kAXShowMenuAction as CFString)
        if command == "select-menu" && error == .success {
            // Keep opening and choosing a transient native menu in one AX
            // session. Starting another helper can dismiss the native menu.
            let choice = arguments[4], deadline = Date().addingTimeInterval(3)
            var selected = false
            while Date() < deadline {
                let menuNodes = nodes(item, limit: 160) + sheets.flatMap { nodes($0, limit: 250) }
                if let option = menuNodes.first(where: {
                    string($0, kAXRoleAttribute) == "AXMenuItem" && matches($0, choice) &&
                    attribute($0, kAXEnabledAttribute) as? Bool != false && actions($0).contains(kAXPressAction)
                }) {
                    error = AXUIElementPerformAction(option, kAXPressAction as CFString)
                    selected = error == .success
                    break
                }
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            if !selected {
                emit(["error": "owned native menu choice unavailable", "selector": choice, "code": error.rawValue]); exit(1)
            }
        }
        emit(["ok": error == .success, "code": error.rawValue, "global_event_posted": false]); exit(error == .success ? 0 : 1)
    }
    if command == "press" {
        guard let item = all.first(where: { matches($0, selector) && actions($0).contains(kAXPressAction) }) else {
            emit(["error": "press target unavailable", "selector": selector]); exit(1)
        }
        let error = AXUIElementPerformAction(item, kAXPressAction as CFString)
        emit(["ok": error == .success, "code": error.rawValue]); exit(error == .success ? 0 : 1)
    }
    if command == "set" {
        guard arguments.count == 5, let item = all.first(where: {
            matches($0, selector) && ["AXTextField", "AXTextArea", "AXComboBox"].contains(string($0, kAXRoleAttribute))
        }) else { emit(["error": "input unavailable", "selector": selector]); exit(1) }
        AXUIElementSetAttributeValue(item, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        var error = AXUIElementSetAttributeValue(item, kAXValueAttribute as CFString, arguments[4] as CFString)
        if string(item, kAXRoleAttribute) == "AXTextArea" && string(item, kAXValueAttribute) != arguments[4] {
            error = AXUIElementSetAttributeValue(item, kAXSelectedTextAttribute as CFString, arguments[4] as CFString)
        }
        emit(["ok": error == .success, "code": error.rawValue]); exit(error == .success ? 0 : 1)
    }
    emit(["error": "unknown command"]); exit(1)

}

runController()
