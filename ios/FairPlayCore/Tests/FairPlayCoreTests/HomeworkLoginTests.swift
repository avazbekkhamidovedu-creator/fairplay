// 2026-10-30: the homework sign-in window's rules (HomeworkLogin.swift).
import Testing
import Foundation
@testable import FairPlayCore

@Suite struct HomeworkLoginTests {

    private let state = "st4te-abc-0123456789"

    private func body(hosts: [String] = ["*.dukekunshan.edu.cn", "oj.dukekunshan.edu.cn"],
                      start: String = "https://oj.dukekunshan.edu.cn/login",
                      finish: String = "https://exam.example.org/homework/done",
                      resume: [String]? = nil) -> [String: Any] {
        var b: [String: Any] = ["action": "openHomeworkLogin", "state": state, "start_url": start,
                                "allowed_hosts": hosts, "finish_url": finish]
        if let resume { b["resume_paths"] = resume }
        return b
    }

    private func login(resume: [String]? = nil) -> HomeworkLogin { HomeworkLogin(body: body(resume: resume))! }

    private func decide(_ s: String, main: Bool = true) -> HomeworkLogin.Navigation {
        login().decide(URL(string: s)!, mainFrame: main)
    }

    @Test func parsesAGoodMessage() {
        let l = login()
        #expect(l.state == state)
        #expect(l.resumePaths == ["/"])
        #expect(login(resume: ["/home", "/"]).resumePaths == ["/home", "/"])
        #expect(login(resume: []).resumePaths == ["/"])
    }

    @Test func rejectsBadMessages() {
        #expect(HomeworkLogin(body: body(start: "http://oj.dukekunshan.edu.cn/login")) == nil)
        #expect(HomeworkLogin(body: body(start: "https://evil.com/login")) == nil)
        #expect(HomeworkLogin(body: body(hosts: [])) == nil)
        #expect(HomeworkLogin(body: body(hosts: ["*.com"])) == nil)
        #expect(HomeworkLogin(body: body(finish: "javascript:alert(1)")) == nil)
        #expect(HomeworkLogin(body: body(finish: "http://exam.example.org/homework/done")) == nil)
        for bad in ["", "short-state", String(repeating: "a", count: 129), "st4te+abc-0123456789", "st4te=abc-0123456789",
                    "st4te.abc-0123456789", "st4te abc-0123456789", "st4te-abc-01234567é"] {
            var b = body(); b["state"] = bad
            #expect(HomeworkLogin(body: b) == nil, "\(bad)")
        }
        for good in [String(repeating: "a", count: 16), String(repeating: "A_-9", count: 32)] {
            var b = body(); b["state"] = good
            #expect(HomeworkLogin(body: b) != nil)
        }
        var noFinish = body(); noFinish["finish_url"] = nil
        #expect(HomeworkLogin(body: noFinish) == nil)
    }

    @Test func allowsOnlyHttpsOnAllowedHosts() {
        #expect(decide("https://oj.dukekunshan.edu.cn/x") == .allow)
        #expect(decide("https://login.dukekunshan.edu.cn/idp?a=1") == .allow)
        #expect(decide("https://DUKEKUNSHAN.edu.cn/") == .block)
        #expect(decide("https://WWW.dukekunshan.edu.cn/") == .allow)
        #expect(decide("https://oj.dukekunshan.edu.cn:443/x") == .allow)
        #expect(decide("http://oj.dukekunshan.edu.cn/x") == .block)
        #expect(decide("https://example.com/") == .block)
        #expect(decide("javascript:alert(1)") == .block)
        #expect(decide("data:text/html,hi") == .block)
        #expect(decide("https://oj.dukekunshan.edu.cn/x", main: false) == .allow)
        #expect(decide("https://example.com/", main: false) == .block)
    }

    @Test func refusesLookAlikes() {
        #expect(decide("https://dukekunshan.edu.cn.evil.com/") == .block)
        #expect(decide("https://evildukekunshan.edu.cn/") == .block)
        #expect(decide("https://oj-dukekunshan.edu.cn/") == .block)
        #expect(decide("https://xn--dukekunshan-9ib.edu.cn/") == .block)
        #expect(decide("https://dukekunshаn.edu.cn/") == .block)
        #expect(decide("https://oj.dukekunshan.edu.cn@evil.com/") == .block)
        #expect(decide("https://oj.dukekunshan.edu.cn:pw@evil.com/") == .block)
        #expect(decide("https://user@oj.dukekunshan.edu.cn/") == .block)
        #expect(decide("https://oj.dukekunshan.edu.cn:8443/") == .block)
        #expect(decide("https://oj.dukekunshan.edu.cn:80/") == .block)
        #expect(decide("https://evil.com/oj.dukekunshan.edu.cn") == .block)
        #expect(decide("https://evil.com/?h=oj.dukekunshan.edu.cn") == .block)
    }

    @Test func finishNeedsPrefixAndMatchingFragment() {
        let f = "https://exam.example.org/homework/done"
        #expect(decide(f + "#ticket=Tick-0123456789ab&state=\(state)") == .finish(ticket: "Tick-0123456789ab"))
        #expect(decide(f + "#state=\(state)&ticket=Tick%2D0123456789ab") == .finish(ticket: "Tick-0123456789ab"))
        #expect(decide(f + "/extra?x=1#ticket=Tick-0123456789ab&state=\(state)") == .finish(ticket: "Tick-0123456789ab"))
        // Wrong or missing state, missing ticket, no fragment: ignored, never loaded.
        #expect(decide(f + "#ticket=Tick-0123456789ab&state=other") == .ignore)
        #expect(decide(f + "#ticket=Tick-0123456789ab") == .ignore)
        #expect(decide(f + "#state=\(state)") == .ignore)
        #expect(decide(f + "#ticket=&state=\(state)") == .ignore)
        for bad in ["short", "Tick+0123456789ab", "Tick=0123456789ab", "Tick.0123456789ab", "Tick%200123456789ab",
                    "Tick-0123456789%C3%A9", String(repeating: "a", count: 129)] {
            #expect(decide(f + "#ticket=\(bad)&state=\(state)") == .ignore, "\(bad)")
        }
        #expect(decide(f + "#ticket=\(String(repeating: "a", count: 128))&state=\(state)") == .finish(ticket: String(repeating: "a", count: 128)))
        #expect(decide(f + "#ticket=\(String(repeating: "a", count: 16))&state=\(state)") == .finish(ticket: String(repeating: "a", count: 16)))
        #expect(decide(f) == .ignore)
        #expect(decide(f + "#ticket=A&ticket=B&state=\(state)") == .ignore)
        #expect(decide(f + "#ticket=Tick-0123456789ab&state=\(state)&state=other") == .ignore)
        // Not the finish URL: a normal check, so the fragment alone finishes nothing.
        #expect(decide("https://oj.dukekunshan.edu.cn/x#ticket=Tick-0123456789ab&state=\(state)") == .allow)
        #expect(decide("https://exam.example.org/homework/done-evil#ticket=Tick-0123456789ab&state=\(state)") == .block)
        #expect(decide("https://exam.example.org/other#ticket=Tick-0123456789ab&state=\(state)") == .block)
        #expect(decide("http://exam.example.org/homework/done#ticket=Tick-0123456789ab&state=\(state)") == .block)
        #expect(decide("https://evil.com/homework/done#ticket=Tick-0123456789ab&state=\(state)") == .block)
        #expect(decide("https://user@exam.example.org/homework/done#ticket=Tick-0123456789ab&state=\(state)") == .block)
        // A sub-frame never finishes the sign-in.
        #expect(decide(f + "#ticket=Tick-0123456789ab&state=\(state)", main: false) == .block)
    }

    @Test func resumesAfterTheLandingPageAtMostThreeTimes() {
        var l = login()
        let start = URL(string: "https://oj.dukekunshan.edu.cn/login")!
        let landing = URL(string: "https://oj.dukekunshan.edu.cn/")!
        #expect(l.resume(afterFinishing: landing) == start)
        #expect(l.resume(afterFinishing: URL(string: "https://oj.dukekunshan.edu.cn")!) == start)
        #expect(l.resume(afterFinishing: URL(string: "https://oj.dukekunshan.edu.cn/?x=1")!) == start)
        #expect(l.resume(afterFinishing: landing) == nil)
        #expect(l.resumes == 3)
    }

    @Test func resumeOnlyOnTheOJOriginAndPaths() {
        var l = login(resume: ["/", "/home"])
        #expect(l.resume(afterFinishing: URL(string: "https://oj.dukekunshan.edu.cn/login")!) == nil)
        #expect(l.resume(afterFinishing: URL(string: "https://login.dukekunshan.edu.cn/")!) == nil)
        #expect(l.resume(afterFinishing: URL(string: "http://oj.dukekunshan.edu.cn/")!) == nil)
        #expect(l.resume(afterFinishing: URL(string: "https://oj.dukekunshan.edu.cn:8443/")!) == nil)
        #expect(l.resume(afterFinishing: nil) == nil)
        #expect(l.resumes == 0)
        #expect(l.resume(afterFinishing: URL(string: "https://oj.dukekunshan.edu.cn/home")!) != nil)
        #expect(l.resumes == 1)
    }

    @Test func refusedDuringAnExamOrDeviceTest() {
        #expect(HomeworkLogin.refusal(examActive: true, deviceTestRunning: false) == "Not available during an exam")
        #expect(HomeworkLogin.refusal(examActive: false, deviceTestRunning: true) == "Not available during an exam")
        #expect(HomeworkLogin.refusal(examActive: false, deviceTestRunning: false) == nil)
    }

    @Test func resultMessages() {
        let l = login()
        let ok = l.message(for: .ticket("Tick-0123456789ab"))
        #expect(ok["type"] as? String == "homeworkLoginResult")
        #expect(ok["state"] as? String == state)
        #expect(ok["ticket"] as? String == "Tick-0123456789ab")
        let cancelled = l.message(for: .cancelled)
        #expect(cancelled["cancelled"] as? Bool == true)
        #expect(cancelled["state"] as? String == state && cancelled["ticket"] == nil)
        #expect(HomeworkLogin.message(state: nil, outcome: .cancelled)["state"] == nil)
        let failed = HomeworkLogin.message(state: state, outcome: .failed("x"))
        #expect(failed["state"] as? String == state)
        #expect(failed["error"] as? String == "x")
        #expect(HomeworkLogin.message(state: nil, outcome: .failed("x"))["state"] == nil)
    }

    @Test func displayHostShowsPunycode() {
        #expect(HomeworkLogin.displayHost(URL(string: "https://OJ.DukeKunshan.edu.cn/x")) == "oj.dukekunshan.edu.cn")
        #expect(HomeworkLogin.displayHost(nil) == "")
    }

    @Test func wildcardMatchesSubdomainsOnly() {
        let hosts = ["*.dukekunshan.edu.cn", "oj.example.org"]
        func allowed(_ s: String) -> Bool { HomeworkLogin.allows(URL(string: s)!, hosts: hosts) }
        #expect(allowed("https://a.dukekunshan.edu.cn/"))
        #expect(allowed("https://a.b.dukekunshan.edu.cn/"))
        #expect(!allowed("https://dukekunshan.edu.cn/"))
        #expect(!allowed("https://xdukekunshan.edu.cn/"))
        #expect(!allowed("https://dukekunshan.edu.cn.evil.com/"))
        #expect(allowed("https://oj.example.org/"))
        #expect(!allowed("https://sub.oj.example.org/"))
    }

    @Test func oneBadHostOrResumePathRefusesTheWholeMessage() {
        #expect(HomeworkLogin(body: body(hosts: ["oj.dukekunshan.edu.cn", "https://x.com"])) == nil)
        #expect(HomeworkLogin(body: body(hosts: ["oj.dukekunshan.edu.cn", "*.github.io"])) == nil)
        #expect(HomeworkLogin(body: body(hosts: ["oj.dukekunshan.edu.cn", "10.0.0.1"])) == nil)
        var notStrings = body(); notStrings["allowed_hosts"] = ["oj.dukekunshan.edu.cn", 5]
        #expect(HomeworkLogin(body: notStrings) == nil)
        #expect(HomeworkLogin(body: body(resume: ["/ok", "bad"])) == nil)
        var badResume = body(); badResume["resume_paths"] = ["/ok", 7]
        #expect(HomeworkLogin(body: badResume) == nil)
        badResume["resume_paths"] = "/ok"
        #expect(HomeworkLogin(body: badResume) == nil)
    }

    @Test func atMostTwentyHosts() {
        let many = (0..<21).map { "h\($0).dukekunshan.edu.cn" }
        #expect(HomeworkLogin(body: body(hosts: Array(many.prefix(20)), start: "https://h0.dukekunshan.edu.cn/")) != nil)
        #expect(HomeworkLogin(body: body(hosts: many, start: "https://h0.dukekunshan.edu.cn/")) == nil)
    }
}
