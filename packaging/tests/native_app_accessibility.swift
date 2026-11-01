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

// This helper never becomes a foreground application between target actions.
NSApplication.shared.setActivationPolicy(.prohibited)
let arguments = CommandLine.arguments
guard arguments.count >= 2 else { emit(["error": "missing command"]); exit(1) }
let command = arguments[1]
if command == "trusted" { emit(["trusted": AXIsProcessTrusted()]); exit(0) }
if command == "frontmost" {
    emit(["pid": Int(NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0)])
    exit(0)
}
guard arguments.count >= 3, let pid = Int32(arguments[2]), pid > 0 else {
    emit(["error": "missing owned App pid"]); exit(1)
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
    emit(["ok": activated || raised]); exit(activated || raised ? 0 : 1)
}
let application = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(application, 2)
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
    for down in [true, false] {
        guard let event = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else {
            emit(["error": "could not create application-local event"]); exit(1)
        }
        event.flags = arguments[3] == "folder" ? [.maskCommand, .maskShift] : arguments[3] == "select-all" ? [.maskCommand] : []
        if command == "save-key" {
            guard NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "io.researchhelper.app",
                  NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else {
                emit(["error": "owned App lost keyboard focus"]); exit(1)
            }
            event.post(tap: .cghidEventTap)
        } else { event.postToPid(pid) }
    }
    emit(["ok": true, "target_pid": Int(pid), "global_event_posted": command == "save-key", "foreground_guarded": command == "save-key"]); exit(0)
}
// Ask only the owned process to expose WebKit's lazily created AX tree.
AXUIElementSetAttributeValue(application, "AXManualAccessibility" as CFString, kCFBooleanTrue)
AXUIElementSetAttributeValue(application, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
RunLoop.main.run(until: Date().addingTimeInterval(0.1))
let windows = attribute(application, kAXWindowsAttribute) as? [AXUIElement] ?? []
let sheets = windows.flatMap { attribute($0, "AXSheets") as? [AXUIElement] ?? [] }
// A native file sheet owns all input while it is open. Do not scan the
// inactive WebKit report behind it or thousands of unrelated folder rows.
let all = sheets.isEmpty ? windows.flatMap { nodes($0) } + nodes(application) : sheets.flatMap { nodes($0, limit: 250) }
if command == "snapshot" {
    let items: [[String: Any]] = all.map { item in
        let role = string(item, kAXRoleAttribute), subrole = string(item, kAXSubroleAttribute)
        var row: [String: Any] = ["role": role, "subrole": subrole,
            "id": string(item, "AXIdentifier"), "title": string(item, kAXTitleAttribute),
            "description": string(item, kAXDescriptionAttribute), "actions": actions(item),
            "enabled": attribute(item, kAXEnabledAttribute) as? Bool ?? true]
        if subrole != "AXSecureTextField" && role != "AXTextField" {
            row["value"] = String(string(item, kAXValueAttribute).prefix(400))
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
if command == "focus" {
    guard let item = all.first(where: { matches($0, selector) }) else {
        emit(["error": "focus target unavailable", "selector": selector]); exit(1)
    }
    let error = AXUIElementSetAttributeValue(item, kAXFocusedAttribute as CFString, kCFBooleanTrue)
    emit(["ok": error == .success, "code": error.rawValue]); exit(error == .success ? 0 : 1)
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
        matches($0, selector) && ["AXTextField", "AXTextArea"].contains(string($0, kAXRoleAttribute))
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
