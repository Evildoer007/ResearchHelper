import Foundation

@main
struct NavigationPolicyAcceptance {
    static func main() {
        var checks = 0
        func verify(_ condition: Bool, _ name: String) {
            precondition(condition, name)
            checks += 1
        }
        let origin = URL(string: "http://127.0.0.1:38201/")!
        let login = URL(string: "http://127.0.0.1:38201/login/index.html")!
        var state = ResearchHelperNavigationState()
        verify(state.accepts(origin, origin: origin), "workspace")
        verify(state.accepts(login, origin: origin), "login")
        for value in ["https://127.0.0.1:38201/", "http://localhost:38201/", "http://127.0.0.1:38202/",
                      "http://user:secret@127.0.0.1:38201/", "http://127.0.0.1:38201/reports/semi.html",
                      "http://127.0.0.1:38201/api/bootstrap", "file:///tmp/index.html", "about:blank"] {
            verify(!state.accepts(URL(string: value)!, origin: origin), value)
        }
        verify(!state.accepts(origin, origin: nil), "no runtime origin")
        let first = state.begin()
        verify(state.showFailure(generation: first), "current failure accepted")
        verify(state.failureVisible, "recovery visible")
        verify(!state.complete(login, origin: origin, generation: first - 1), "stale finish refused")
        verify(state.complete(login, origin: origin, generation: first), "login can recover")
        verify(!state.failureVisible && state.lastSafeURL == login, "successful recovery")
        let second = state.begin()
        verify(!state.showFailure(generation: first), "stale failure refused")
        verify(!state.complete(origin, origin: origin, generation: first), "stale finish after retry")
        verify(state.recoveryURL(origin: origin, workspaceOnly: false) == login, "last safe destination")
        verify(state.recoveryURL(origin: origin, workspaceOnly: true) == origin, "workspace destination")
        let startup = URL(string: "http://127.0.0.1:38201/?app_startup=one-use#desk")!
        verify(state.complete(startup, origin: origin, generation: second), "startup navigation can complete")
        verify(state.lastSafeURL?.query == nil && state.lastSafeURL?.fragment == "desk", "query removed, view retained")
        verify(state.recoveryURL(origin: nil, workspaceOnly: false) == nil, "absent service cannot recover")
        verify(state.recoveryURL(origin: URL(string: "http://127.0.0.1:38202/")!, workspaceOnly: false)?.port == 38202,
               "never replay old service origin")
        verify(!state.complete(URL(string: "http://127.0.0.1:38201/reports/semi.html")!, origin: origin, generation: second),
               "reports cannot replace workspace recovery")
        verify(state.decision(contentHealthy: true, snapshotReady: true, attempt: 999, withinDeadline: false) == .ready,
               "healthy completion")
        verify(state.decision(contentHealthy: true, snapshotReady: false, attempt: 0, withinDeadline: true) == .retry,
               "DOM alone is insufficient")
        verify(state.decision(contentHealthy: false, snapshotReady: true, attempt: 0, withinDeadline: true) == .retry,
               "snapshot alone is insufficient")
        verify(state.decision(contentHealthy: false, snapshotReady: false, attempt: 24, withinDeadline: true) == .retry,
               "bootstrap may outlast the old six-second attempt count")
        verify(state.decision(contentHealthy: false, snapshotReady: false, attempt: 0, withinDeadline: false) == .failed,
               "finite deadline")
        state.invalidate()
        verify(!state.showFailure(generation: second), "termination invalidates delayed failure")
        verify(!state.complete(origin, origin: origin, generation: second), "termination invalidates delayed finish")
        print("Foundation policy checks: \(checks)")
    }
}
