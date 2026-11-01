// Independent regression for two owned previews. No windows or print panels are shown.
import AppKit
import WebKit

@main
struct NativeReportReuse {
    static var checks: [String] = []

    static func check(_ name: String, _ value: Bool) throws {
        guard value else { throw NSError(domain: name, code: 1) }
        checks.append(name)
    }

    static func run(_ url: URL) throws {
        NSApplication.shared.setActivationPolicy(.prohibited)
        NSApp.finishLaunching()
        let owner = ResearchHelperApp()
        owner.runtimeURL = url
        defer {
            owner.printViews.removeAll()
            owner.pendingPrints.removeAll()
            for window in Array(owner.reportWindows.keys) { window.close() }
        }
        let firstURL = url.appendingPathComponent("reports/semi.html")
        let secondURL = url.appendingPathComponent("api/tasks/rh_1234/report/2")
        let first = owner.prepareReport(firstURL, printing: false, persistent: false)!
        let firstView = owner.reportWindows[first]!
        // Occupy the first view without scheduling an actual print.
        owner.pendingPrints.insert(firstView)
        let second = owner.prepareReport(secondURL, printing: false, persistent: false)!
        try check("busy preview creates a separate owned document", first !== second && owner.reportWindows.count == 2)
        owner.pendingPrints.removeAll()
        let ordered = Array(owner.reportWindows)
        // Target the non-first dictionary entry: regression must not depend on hash order.
        let idle = ordered[0]
        let target = ordered[1]
        let targetURL = owner.reportTargets[target.value]!
        let idleURL = owner.reportTargets[idle.value]!
        target.key.setFrame(NSRect(x: 93, y: 121, width: 860, height: 640), display: false)
        let frame = target.key.frame
        let opened = owner.prepareReport(targetURL, printing: false, persistent: false)!
        try check("exact document wins over an earlier idle preview", opened === target.key)
        try check("reopening leaves the other document intact", owner.reportTargets[idle.value] == idleURL)
        try check("reopening preserves position and window count", opened.frame == frame && owner.reportWindows.count == 2)
        owner.pendingPrints.formUnion([idle.value, target.value])
        let busyMatch = owner.prepareReport(targetURL, printing: false, persistent: false)!
        try check("same document can reopen during a pending print", busyMatch === target.key && owner.pendingPrints.count == 2)
        let thirdURL = url.appendingPathComponent("api/tasks/rh_1234/report/3")
        let third = owner.prepareReport(thirdURL, printing: false, persistent: false)!
        try check("new document cannot overwrite either busy preview", third !== idle.key && third !== target.key && owner.reportWindows.count == 3)
        try check("busy document targets stay frozen", owner.reportTargets[target.value] == targetURL && owner.reportTargets[idle.value] == idleURL)
        let thirdView = owner.reportWindows[third]!
        let fourthURL = url.appendingPathComponent("api/tasks/rh_1234/report/4")
        let reused = owner.prepareReport(fourthURL, printing: false, persistent: false)!
        try check("new document reuses the only idle preview", reused === third && owner.reportTargets[thirdView] == fourthURL && owner.reportWindows.count == 3)
        try check("all previews remain hidden and nonpersistent", owner.reportWindows.allSatisfy { !$0.key.isVisible && !$0.value.configuration.websiteDataStore.isPersistent })
        try check("application never activates", NSApp.activationPolicy() == .prohibited)
    }

    static func main() {
        do {
            try run(URL(string: CommandLine.arguments[1])!)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: ["status": "passed", "checks": checks], options: [.prettyPrinted, .sortedKeys]))
        } catch {
            FileHandle.standardOutput.write(try! JSONSerialization.data(withJSONObject: ["status": "failed", "checks": checks, "error": String(describing: error)], options: [.prettyPrinted, .sortedKeys]))
            exit(1)
        }
    }
}
