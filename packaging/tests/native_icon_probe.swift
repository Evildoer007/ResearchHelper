// Read macOS-resolved file icons without starting an App or opening a window.
import AppKit

let project = URL(fileURLWithPath: "/Users/haoranxu/Desktop/ResearchHelper")
let output = project.appendingPathComponent("result/acceptance/system-icons")
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
var records: [[String: Any]] = []
for (name, identifier) in [("ResearchHelper", "io.researchhelper.app"), ("OptionHelper", "com.optionhelper.app")] {
    let path = "/Applications/\(name).app"
    let icon = NSWorkspace.shared.icon(forFile: path)
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 256, pixelsHigh: 256,
                                  bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                  isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    icon.draw(in: NSRect(x: 0, y: 0, width: 256, height: 256), from: .zero,
              operation: .copy, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    let imageURL = output.appendingPathComponent("\(name)-system.png")
    try bitmap.representation(using: .png, properties: [:])!.write(to: imageURL)
    records.append(["name": name, "bundle": path, "png": imageURL.path,
                    "registeredApplication": NSWorkspace.shared.urlForApplication(withBundleIdentifier: identifier)?.path ?? "",
                    "visibleAppStarted": false])
}
let data = try JSONSerialization.data(withJSONObject: records, options: [.prettyPrinted, .sortedKeys])
try data.write(to: output.appendingPathComponent("resolution.json"))
print(String(data: data, encoding: .utf8)!)
