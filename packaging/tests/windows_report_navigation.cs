using System;
using System.Text.Json;

namespace ResearchHelper.Windows;

internal static class WindowsReportNavigationChecks
{
    static void Main()
    {
        var checks = new System.Collections.Generic.List<string>();
        void Check(string name, bool condition)
        {
            if (!condition) throw new InvalidOperationException(name);
            checks.Add(name);
        }

        var origin = new Uri("http://127.0.0.1:49152/");
        var expected = new Uri(origin, "api/tasks/rh_case/report/1?profile=standard");
        var otherVersion = new Uri(origin, "api/tasks/rh_case/report/2?profile=standard");
        var otherProfile = new Uri(origin, "api/tasks/rh_case/report/1?profile=detailed");
        var otherTask = new Uri(origin, "api/tasks/rh_other/report/1?profile=standard");
        Check("legacy reader would allow the wrong saved version", new LegacyReportGate(origin).Allows(otherVersion));
        Check("explicit standard and detailed editions can be opened",
            ReportNavigation.IsReport(origin, expected) && ReportNavigation.IsReport(origin, otherProfile));
        Check("saved default standard report can be opened",
            ReportNavigation.IsReport(origin, new Uri(origin, "api/tasks/rh_case/report/1")));
        Check("historical reports can be opened", ReportNavigation.IsReport(origin, new Uri(origin, "reports/broker.html")));
        foreach (var path in new[] { "api/tasks/rh_case/report", "api/tasks/rh_case/report/0",
            "api/tasks/rh_case/report/1?profile=standard&profile=detailed", "api/tasks/rh_case/report/1#old",
            "api/tasks/rh_case/report/1?profile=unknown", "reports/unknown.html", "login/index.html" })
            Check("rejects " + path, !ReportNavigation.IsReport(origin, new Uri(origin, path)));
        Check("rejects another localhost service",
            !ReportNavigation.IsReport(origin, new Uri("http://127.0.0.1:49153/api/tasks/rh_case/report/1")));
        Check("rejects user info",
            !ReportNavigation.IsReport(origin, new Uri("http://user@127.0.0.1:49152/api/tasks/rh_case/report/1")));

        var navigation = new ReportNavigation(expected);
        Check("blank initialization does not claim the report", navigation.Start("about:blank", 1) && !navigation.Owns(1));
        Check("initial navigation belongs to the selected edition",
            navigation.Start(expected.AbsoluteUri, 2) && navigation.Owns(2) && !navigation.Owns(1));
        Check("other edition cannot reuse the request", !navigation.Start(otherVersion.AbsoluteUri, 2));
        Check("other profile cannot reuse the request", !navigation.Start(otherProfile.AbsoluteUri, 2));
        Check("other task cannot reuse the request", !navigation.Start(otherTask.AbsoluteUri, 2));
        Check("unrelated top-level navigation cannot replace the report",
            !navigation.Start(expected.AbsoluteUri, 3) && !navigation.Start("about:blank", 3));
        Check("in-report citations retain document identity", navigation.Matches(expected.AbsoluteUri + "#ev-e1"));
        Check("successful selected HTML response can be shown", navigation.CanShow(expected.AbsoluteUri, true, 200));
        foreach (var status in new[] { 302, 401, 403, 404, 500 })
            Check("HTTP " + status + " is never presented as a report", !navigation.CanShow(expected.AbsoluteUri, true, status));
        Check("failed navigation and substituted edition are rejected",
            !navigation.CanShow(expected.AbsoluteUri, false, 200) && !navigation.CanShow(otherVersion.AbsoluteUri, true, 200));

        var digest = new string('a', 64);
        var receipt = "rh_case:1:" + digest;
        var matched = navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html; charset=utf-8", receipt, null);
        Check("selected standard report receipt yields the archived fingerprint",
            matched is { Example: null, Profile: "standard" } && matched.Digest == digest);
        Check("details are bound to the requested profile",
            new ReportNavigation(otherProfile).ReadResponse(otherProfile.AbsoluteUri, 200, "text/html", receipt, digest)
                is { Example: null, Profile: "detailed" });
        Check("another task receipt cannot bind a successful HTML response",
            navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html", "rh_other:1:" + digest, null) is null);
        Check("another version receipt cannot bind the selected response",
            navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html", "rh_case:2:" + digest, null) is null);
        Check("missing or malformed receipts cannot bind a saved report",
            navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html", null, null) is null &&
            navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html", "rh_case:1:short", null) is null);
        Check("the requested print digest must equal the response digest",
            navigation.ReadResponse(expected.AbsoluteUri, 200, "text/html", receipt, new string('b', 64)) is null);
        Check("response must be successful HTML at the same route",
            navigation.ReadResponse(expected.AbsoluteUri, 403, "text/html", receipt, null) is null &&
            navigation.ReadResponse(expected.AbsoluteUri, 200, "application/json", receipt, null) is null &&
            navigation.ReadResponse(otherVersion.AbsoluteUri, 200, "text/html", receipt, null) is null);
        var example = new Uri(origin, "reports/broker.html");
        Check("historical HTML binds by example key",
            new ReportNavigation(example).ReadResponse(example.AbsoluteUri, 200, "text/html", null, null)
                is { Example: "broker", Profile: "standard", Digest: null });
        Console.WriteLine(JsonSerializer.Serialize(new { status = "passed", checks }));
    }
}
