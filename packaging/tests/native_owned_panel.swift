// Test-only native panel control. Every target must be a descendant of the
// owned ResearchHelper application; no global input or secure-field reads.
import AppKit
import ApplicationServices

func attr(_ node: AXUIElement, _ key: String) -> Any? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(node, key as CFString, &value) == .success else { return nil }
    return value
}
func text(_ node: AXUIElement, _ key: String) -> String { attr(node, key) as? String ?? "" }
// Native file browsers can expose thousands of filesystem cells. Inspect only
// controls and menus here, not a complete user's directory or embedded report.
func nodes(_ root: AXUIElement) -> [AXUIElement] {
    var queue = [root], result: [AXUIElement] = []
    let deadline = Date().addingTimeInterval(4)
    while let node = queue.popLast(), result.count < 400, Date() < deadline {
        result.append(node)
        if !["AXWebArea", "AXOutline", "AXTable", "AXBrowser"].contains(text(node, kAXRoleAttribute)) {
            queue.append(contentsOf: attr(node, kAXChildrenAttribute) as? [AXUIElement] ?? [])
        }
    }
    return result
}
func emit(_ value: [String: Any], _ code: Int32 = 0) -> Never {
    print(String(data: try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]), encoding: .utf8)!)
    exit(code)
}
func row(_ node: AXUIElement) -> [String: Any] {
    var pid: pid_t = 0
    AXUIElementGetPid(node, &pid)
    var actionList: CFArray?
    AXUIElementCopyActionNames(node, &actionList)
    var value: [String: Any] = ["pid": Int(pid), "role": text(node, kAXRoleAttribute),
        "id": text(node, "AXIdentifier"), "title": text(node, kAXTitleAttribute),
        "description": text(node, kAXDescriptionAttribute),
        "actions": actionList as? [String] ?? [],
        "focused": attr(node, kAXFocusedAttribute) as? Bool ?? false]
    if text(node, kAXSubroleAttribute) != "AXSecureTextField" {
        value["value"] = attr(node, kAXValueAttribute) as? String ?? ""
        value["url"] = (attr(node, kAXURLAttribute) as? URL)?.absoluteString ?? ""
    }
    return value
}

NSApplication.shared.setActivationPolicy(.prohibited)
let args = CommandLine.arguments
guard args.count >= 3, let pid = Int32(args[1]), pid > 0,
      ["inspect", "folder", "choose", "press", "set", "windows", "open-directory", "cancel-menu"].contains(args[2]), AXIsProcessTrusted(),
      let app = NSRunningApplication(processIdentifier: pid) else { emit(["error": "Invalid owned panel request"], 1) }
let root = AXUIElementCreateApplication(pid)
AXUIElementSetMessagingTimeout(root, 0.5)
if args[2] == "windows" {
    let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] ?? []
    emit(["windows": windows.filter { ($0[kCGWindowOwnerPID as String] as? Int) == Int(pid) }
          .map { ["id": $0[kCGWindowNumber as String] ?? 0, "layer": $0[kCGWindowLayer as String] ?? -1,
                   "bounds": $0[kCGWindowBounds as String] ?? [:]] }, "global_input_posted": false])
}
let panels = nodes(root).filter { ["AXSheet", "AXDialog"].contains(text($0, kAXRoleAttribute)) }
guard let panel = panels.last else { emit(["error": "Owned sheet unavailable"], 1) }
let contents = nodes(panel)
if args[2] == "cancel-menu" {
    guard let item = contents.first(where: {
        guard text($0, kAXRoleAttribute) == "AXMenuItem" else { return false }
        var actions: CFArray?
        AXUIElementCopyActionNames($0, &actions)
        return (actions as? [String] ?? []).contains(kAXCancelAction)
    }) else { emit(["error": "Owned menu cancellation unavailable"], 1) }
    let error = AXUIElementPerformAction(item, kAXCancelAction as CFString)
    emit(["ok": error == .success, "code": error.rawValue, "global_input_posted": false], error == .success ? 0 : 1)
}
if args[2] == "open-directory" {
    guard args.count == 4, args[3].hasPrefix("native-frozen-export-"), !args[3].contains("/") else {
        emit(["error": "Only the exact independent acceptance directory is eligible"], 1)
    }
    var pending: [(AXUIElement, [AXUIElement])] = [(panel, [])]
    let deadline = Date().addingTimeInterval(6)
    var visited = 0
    while let (node, ancestors) = pending.popLast(), visited < 1800, Date() < deadline {
        visited += 1
        let role = text(node, kAXRoleAttribute)
        if role == "AXWebArea" { continue }
        let matches = [text(node, kAXTitleAttribute), text(node, kAXDescriptionAttribute),
                       text(node, kAXValueAttribute)].contains(args[3])
        if matches {
            let targets = [node] + ancestors.reversed()
            for target in targets where ["AXCell", "AXRow", "AXTextField", "AXStaticText"].contains(text(target, kAXRoleAttribute)) {
                var actions: CFArray?
                AXUIElementCopyActionNames(target, &actions)
                if (actions as? [String] ?? []).contains(kAXPressAction) {
                    let error = AXUIElementPerformAction(target, kAXPressAction as CFString)
                    emit(["ok": error == .success, "method": "AXPress", "target": row(target),
                          "directory": args[3], "global_input_posted": false], error == .success ? 0 : 1)
                }
                if ["AXCell", "AXRow"].contains(text(target, kAXRoleAttribute)) {
                    let error = AXUIElementSetAttributeValue(target, kAXSelectedAttribute as CFString, kCFBooleanTrue)
                    if error == .success {
                        emit(["ok": true, "method": "AXSelected", "target": row(target),
                              "directory": args[3], "global_input_posted": false])
                    }
                }
            }
            emit(["error": "Exact private directory has no native selection action"], 1)
        }
        let children = attr(node, kAXChildrenAttribute) as? [AXUIElement] ?? []
        pending.append(contentsOf: children.map { ($0, ancestors + [node]) })
    }
    emit(["error": "Exact private directory unavailable in the native browser", "visited": visited], 1)
}
if ["press", "set"].contains(args[2]) {
    guard args.count >= 4, let item = contents.first(where: {
        [text($0, "AXIdentifier"), text($0, kAXTitleAttribute), text($0, kAXDescriptionAttribute)].contains(args[3])
    }) else { emit(["error": "Exact owned panel control unavailable"], 1) }
    if args[2] == "set" {
        guard args.count == 5, text(item, kAXRoleAttribute) == "AXTextField",
              text(item, kAXSubroleAttribute) != "AXSecureTextField" else {
            emit(["error": "Expected owned nonsecure native text field"], 1)
        }
        let focused = AXUIElementSetAttributeValue(item, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let error = AXUIElementSetAttributeValue(item, kAXValueAttribute as CFString, args[4] as CFString)
        emit(["ok": error == .success, "code": error.rawValue, "focus_code": focused.rawValue,
              "global_input_posted": false], error == .success ? 0 : 1)
    }
    let error = AXUIElementPerformAction(item, kAXPressAction as CFString)
    emit(["ok": error == .success, "code": error.rawValue, "global_input_posted": false], error == .success ? 0 : 1)
}
if args[2] == "choose" {
    guard args.count == 4, let item = contents.first(where: {
        text($0, kAXRoleAttribute) == "AXMenuItem" && text($0, kAXTitleAttribute) == args[3]
    }) else { emit(["error": "Exact owned menu item unavailable"], 1) }
    let error = AXUIElementPerformAction(item, kAXPressAction as CFString)
    emit(["ok": error == .success, "code": error.rawValue, "global_input_posted": false], error == .success ? 0 : 1)
}
if args[2] == "inspect" {
    let menus = contents.filter { text($0, kAXRoleAttribute) == "AXMenuItem" }
    let controls = contents.filter { ["AXSheet", "AXDialog", "AXTextField", "AXButton", "AXPopUpButton",
                                      "AXDisclosureTriangle", "AXMenuItem", "AXComboBox", "AXMenuButton"].contains(text($0, kAXRoleAttribute)) }
    var result: [String: Any] = ["items": controls.prefix(80).map(row), "menu_items": menus.prefix(40).map(row),
                               "global_input_posted": false]
    if let focused = attr(root, kAXFocusedUIElementAttribute) {
        let element = focused as! AXUIElement
        result["focused_item"] = row(element)
    }
    emit(result)
}
guard let field = contents.first(where: { text($0, "AXIdentifier") == "saveAsNameTextField" }) else {
    emit(["error": "Owned save field unavailable"], 1)
}
app.unhide()
app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
AXUIElementPerformAction(panel, kAXRaiseAction as CFString)
let focus = AXUIElementSetAttributeValue(field, kAXFocusedAttribute as CFString, kCFBooleanTrue)
RunLoop.main.run(until: Date().addingTimeInterval(0.3))
var owner: pid_t = 0
guard AXUIElementGetPid(field, &owner) == .success, owner > 0 else { emit(["error": "Owned field process unavailable"], 1) }
for down in [true, false] {
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: 5, keyDown: down) else {
        emit(["error": "Local native event unavailable"], 1)
    }
    event.flags = [.maskCommand, .maskShift]
    event.postToPid(owner)
    RunLoop.main.run(until: Date().addingTimeInterval(0.15))
}
emit(["ok": true, "field_pid": Int(owner), "app_pid": Int(pid),
    "focus_code": focus.rawValue, "global_input_posted": false])
