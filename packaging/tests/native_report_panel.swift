// Independent native-panel controller. Targets only a sheet reachable through
// the test App's AX tree; never posts global keyboard input or reads secure text.
import AppKit
import ApplicationServices

func attribute(_ element: AXUIElement, _ name: String) -> Any? {
    var result: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &result) == .success else { return nil }
    return result
}
func descendants(_ root: AXUIElement) -> [AXUIElement] {
    var pending = [root], result: [AXUIElement] = []
    while let node = pending.popLast(), result.count < 2500 {
        result.append(node)
        pending.append(contentsOf: attribute(node, kAXChildrenAttribute) as? [AXUIElement] ?? [])
    }
    return result
}
func emit(_ data: [String: Any], _ code: Int32 = 0) -> Never {
    let encoded = try! JSONSerialization.data(withJSONObject: data, options: [.sortedKeys])
    print(String(data: encoded, encoding: .utf8)!)
    exit(code)
}

NSApplication.shared.setActivationPolicy(.prohibited)
let values = CommandLine.arguments
guard values.count == 3, let appPID = Int32(values[1]), appPID > 0,
      ["folder", "return"].contains(values[2]), AXIsProcessTrusted(),
      let app = NSRunningApplication(processIdentifier: appPID) else {
    emit(["error": "Expected owned App and supported native-panel key"], 1)
}
app.unhide()
app.activate(options: [.activateAllWindows, .activateIgnoringOtherApps])
RunLoop.main.run(until: Date().addingTimeInterval(0.4))
let root = AXUIElementCreateApplication(appPID)
AXUIElementSetMessagingTimeout(root, 2)
let panels = descendants(root).filter {
    ["AXSheet", "AXDialog"].contains(attribute($0, kAXRoleAttribute) as? String ?? "")
}
guard let panel = panels.last else { emit(["error": "No panel belongs to the owned App"], 1) }
var panelPID: pid_t = 0
guard AXUIElementGetPid(panel, &panelPID) == .success, panelPID > 0 else {
    emit(["error": "Native sheet owner unavailable"], 1)
}
AXUIElementPerformAction(panel, kAXRaiseAction as CFString)
let key: CGKeyCode = values[2] == "folder" ? 5 : 36
for down in [true, false] {
    guard let event = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: down) else {
        emit(["error": "Native-panel event unavailable"], 1)
    }
    if values[2] == "folder" { event.flags = [.maskCommand, .maskShift] }
    event.postToPid(panelPID)
    RunLoop.main.run(until: Date().addingTimeInterval(0.15))
}
emit(["ok": true, "app_pid": Int(appPID), "panel_pid": Int(panelPID), "global_input_posted": false])
