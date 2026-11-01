// Native Accessibility and input events only. No WebKit JavaScript or HTTP calls.
import AppKit
import ApplicationServices

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}
func string(_ element: AXUIElement, _ name: String) -> String { attribute(element,name) as? String ?? "" }
func elements(_ element: AXUIElement, _ name: String) -> [AXUIElement] { attribute(element,name) as? [AXUIElement] ?? [] }
func frame(_ element: AXUIElement) -> CGRect {
    var point=CGPoint.zero, size=CGSize.zero
    if let value=attribute(element,"AXPosition") { AXValueGetValue(value as! AXValue,.cgPoint,&point) }
    if let value=attribute(element,"AXSize") { AXValueGetValue(value as! AXValue,.cgSize,&size) }
    return CGRect(origin:point,size:size)
}
func key(_ pid: pid_t, _ code: CGKeyCode, _ flags: CGEventFlags = []) {
    for down in [true,false] { if let event=CGEvent(keyboardEventSource:nil,virtualKey:code,keyDown:down) { event.flags=flags;event.post(tap:.cghidEventTap) } }
    Thread.sleep(forTimeInterval:0.08)
}
func output(_ value: Any) {
    if let data=try? JSONSerialization.data(withJSONObject:value,options:[.sortedKeys]) { FileHandle.standardOutput.write(data);print("") }
}
let arguments=CommandLine.arguments
if arguments.count==2 && arguments[1]=="trusted" { output(["accessibility":AXIsProcessTrusted()]);exit(0) }
guard arguments.count>=3, let pid=pid_t(arguments[2]) else { output(["error":"command and pid required"]);exit(1) }
let application=AXUIElementCreateApplication(pid), command=arguments[1]
if ["key","type","press","click","scroll"].contains(command) {
    NSRunningApplication(processIdentifier:pid)?.activate(options:[.activateIgnoringOtherApps]);Thread.sleep(forTimeInterval:0.2)
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier == pid else { output(["error":"Target App is not foreground; no input sent"]);exit(4) }
}
if command=="windows" {
    let windows=CGWindowListCopyWindowInfo([.optionOnScreenOnly,.excludeDesktopElements],kCGNullWindowID) as? [[String:Any]] ?? []
    output(windows.filter { ($0[kCGWindowOwnerPID as String] as? Int)==Int(pid) && ($0[kCGWindowLayer as String] as? Int)==0 }.map { ["id":$0[kCGWindowNumber as String] ?? 0,"title":$0[kCGWindowName as String] ?? "","bounds":$0[kCGWindowBounds as String] ?? [:]] });exit(0)
}
var nodes=[AXUIElement](), descriptions=[[String:Any]]()
let windows=elements(application,"AXWindows"), windowFrame=windows.first.map(frame) ?? .zero
func visit(_ element: AXUIElement, _ depth: Int) {
    if depth>35 || nodes.count>1200 { return }
    let role=string(element,"AXRole"), box=frame(element)
    if ["AXButton","AXTextField","AXSecureTextField","AXTextArea","AXCheckBox","AXPopUpButton","AXMenuButton","AXLink","AXRadioButton","AXDisclosureTriangle","AXStaticText","AXComboBox"].contains(role) && box.width>0 && box.height>0 && (windowFrame.isEmpty || box.intersects(windowFrame)) {
        nodes.append(element)
        descriptions.append(["index":nodes.count-1,"role":role,"title":role == "AXStaticText" ? string(element,"AXValue") : string(element,"AXTitle"),"description":string(element,"AXDescription"),"help":string(element,"AXHelp"),"identifier":string(element,"AXIdentifier"),"dom":string(element,"AXDOMIdentifier"),"x":box.minX,"y":box.minY,"width":box.width,"height":box.height,"enabled":attribute(element,"AXEnabled") as? Bool ?? true])
    }
    for child in elements(element,"AXChildren") { visit(child,depth+1) }
}
for window in windows { visit(window,0) }
if command=="list" || command=="texts" { output(["trusted":AXIsProcessTrusted(),"window_count":windows.count,"controls":descriptions]);exit(0) }
if command=="key" && arguments.count>3 && arguments[3]=="goto" { key(pid,5,[.maskCommand,.maskShift]);output(["sent":true]);exit(0) }
if command=="scroll" {
    let box=frame(elements(application,"AXWindows")[0]);let amount:Int32=arguments.count>3 && arguments[3]=="up" ? 550 : -550
    if let event=CGEvent(scrollWheelEvent2Source:nil,units:.pixel,wheelCount:1,wheel1:amount,wheel2:0,wheel3:0) { event.location=CGPoint(x:box.midX+100,y:box.midY+100);event.post(tap:.cghidEventTap) }
    output(["action":"scroll","pixels":amount]);exit(0)
}
if command=="key" { let codes:[String:CGKeyCode]=["escape":53,"return":36,"down":125,"up":126,"tab":48,"space":49];let code=codes[arguments.count>3 ? arguments[3] : "return"] ?? 36;key(pid,code);output(["sent":true]);exit(0) }
guard arguments.count>=4 else { output(["error":"UI label required"]);exit(1) }
let target=arguments[3]
let found=descriptions.enumerated().filter { pair in ["title","description","help","identifier","dom"].contains { (pair.element[$0] as? String)==target } }
let active=found.filter { ($0.element["role"] as? String) != "AXStaticText" };let matches=active.isEmpty ? found : active
guard matches.count==1, let selected=matches.first else { output(["error":"UI target must be unique","target":target,"matches":matches.count]);exit(2) }
let node=nodes[selected.offset]
NSRunningApplication(processIdentifier:pid)?.activate(options:[.activateIgnoringOtherApps]);Thread.sleep(forTimeInterval:0.1)
if command=="press" || command=="click" {
    let result=command == "press" ? AXUIElementPerformAction(node,kAXPressAction as CFString) : AXError.actionUnsupported
    if result == .success && command == "press" { output(["action":"press","target":target,"result":result.rawValue]);exit(0) }
    let box=frame(node),point=CGPoint(x:box.minX+min(box.width/2,35),y:box.midY)
    for kind in [CGEventType.leftMouseDown,CGEventType.leftMouseUp] { CGEvent(mouseEventSource:nil,mouseType:kind,mouseCursorPosition:point,mouseButton:.left)?.post(tap:.cghidEventTap) }
    output(["action":"mouse_click","target":target,"x":point.x,"y":point.y]);exit(0)
}
if command=="value" && string(node,"AXRole") != "AXSecureTextField" && !target.lowercased().contains("key") && !target.lowercased().contains("token") { output(["target":target,"value":string(node,"AXValue")]);exit(0) }
if command=="type" && arguments.count>=5 {
    let focused=AXUIElementSetAttributeValue(node,kAXFocusedAttribute as CFString,kCFBooleanTrue)
    guard focused == .success else { output(["error":"Cannot focus UI control","result":focused.rawValue]);exit(3) }
    key(pid,0,.maskCommand)
    let characters=Array(arguments[4].utf16)
    for offset in stride(from:0,to:characters.count,by:20) {
        var chunk=Array(characters[offset..<min(offset+20,characters.count)])
        for down in [true,false] { if let event=CGEvent(keyboardEventSource:nil,virtualKey:0,keyDown:down) { event.keyboardSetUnicodeString(stringLength:chunk.count,unicodeString:&chunk);event.post(tap:.cghidEventTap) } }
        Thread.sleep(forTimeInterval:0.05)
    }
    output(["action":"type","target":target,"characters":characters.count]);exit(0)
}
output(["error":"Unsupported action"]);exit(1)
