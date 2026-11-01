// QA controller for the installed ResearchHelper only. Not included in releases.
import AppKit
import ApplicationServices

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
    return value
}
func text(_ element: AXUIElement, _ name: String) -> String { attribute(element, name) as? String ?? "" }
func children(_ element: AXUIElement) -> [AXUIElement] { attribute(element, kAXChildrenAttribute) as? [AXUIElement] ?? [] }
func elements(_ root: AXUIElement) -> [(AXUIElement, Int)] {
    var result: [(AXUIElement, Int)] = []
    func visit(_ node: AXUIElement, _ depth: Int) {
        guard depth < 48, result.count < 12000 else { return }
        result.append((node, depth))
        for child in children(node) { visit(child, depth + 1) }
    }
    visit(root, 0)
    return result
}
func run(_ request: [String: Any]) throws -> [String: Any] {
    guard let pid = request["pid"] as? Int32,
          let app = NSRunningApplication(processIdentifier: pid), app.bundleIdentifier == "io.researchhelper.app" else {
        return ["error": "Only the installed ResearchHelper may be controlled"]
    }
    let root = AXUIElementCreateApplication(pid)
    let operation = request["operation"] as? String ?? "tree"
    if operation == "windows" {
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        return ["windows": windows.filter { ($0[kCGWindowOwnerPID as String] as? Int32) == pid }]
    }
    guard AXIsProcessTrusted() else { return ["error": "Accessibility permission is unavailable"] }
    let nodes = elements(root)
    if operation == "tree" {
        return ["nodes": nodes.enumerated().map { index, item in
            let node = item.0
            return ["index": index, "depth": item.1, "role": text(node, kAXRoleAttribute),
                    "subrole": text(node, kAXSubroleAttribute),
                    "title": text(node, kAXTitleAttribute), "description": text(node, kAXDescriptionAttribute),
                    "value": text(node, kAXValueAttribute), "identifier": text(node, kAXIdentifierAttribute)] as [String: Any]
        }]
    }
    guard let index = request["index"] as? Int, nodes.indices.contains(index) else { return ["error": "Element index is not present"] }
    let element = nodes[index].0
    if operation == "press" {
        return ["result": AXUIElementPerformAction(element, kAXPressAction as CFString).rawValue]
    }
    if operation == "focus" {
        app.activate(options: [.activateIgnoringOtherApps])
        return ["result": AXUIElementSetAttributeValue(element, kAXFocusedAttribute as CFString, kCFBooleanTrue).rawValue]
    }
    if operation == "closeWindow", let button = attribute(element, kAXCloseButtonAttribute) {
        return ["result": AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString).rawValue]
    }
    if operation == "set", let value = request["value"] as? String {
        return ["result": AXUIElementSetAttributeValue(element, kAXValueAttribute as CFString, value as CFString).rawValue]
    }
    if operation == "resize", let width = request["width"] as? Double, let height = request["height"] as? Double {
        var size = CGSize(width: width, height: height)
        let value = AXValueCreate(.cgSize, &size)!
        return ["result": AXUIElementSetAttributeValue(element, kAXSizeAttribute as CFString, value).rawValue]
    }
    if operation == "raise" {
        app.activate(options: [.activateIgnoringOtherApps])
        return ["result": AXUIElementPerformAction(element, kAXRaiseAction as CFString).rawValue]
    }
    return ["error": "Unsupported acceptance operation"]
}
do {
    let request = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: Any]
    let result = try run(request)
    let bytes = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    FileHandle.standardOutput.write(bytes)
} catch {
    FileHandle.standardOutput.write(Data("{\"error\":\"Acceptance controller failed\"}".utf8))
}
