// The restricted Canvas browser (Mode B): same tables as the Mac app's CanvasModeTests.
import Testing
import Foundation
@testable import FairPlayCore

@Suite struct CanvasModeTests {

    private typealias Rule = SiteRule

    private var rules: [Rule] {
        ["canvas.dku.edu", "*.instructure.com", "login.microsoftonline.com"].compactMap(Rule.init)
    }

    private func allowed(_ s: String) -> Bool {
        CanvasPolicy.allows(URL(string: s)!, rules: rules)
    }

    @Test func hostPolicyIsHttpsAndExact() {
        #expect(allowed("https://canvas.dku.edu/courses/1/quizzes/2"))
        #expect(allowed("https://x.instructure.com/a"))
        #expect(allowed("https://login.microsoftonline.com/common/oauth2"))
        #expect(!allowed("http://canvas.dku.edu/courses/1"))
        #expect(!allowed("https://x.canvas.dku.edu/"))
        #expect(!allowed("https://canvas.dku.edu.evil.com/"))
        #expect(!allowed("https://evilcanvas.dku.edu/"))
        #expect(!allowed("https://canvas.dku.edu:8443/"))
        #expect(!allowed("https://user:pw@canvas.dku.edu/"))
        #expect(!allowed("https://canvas.dku.edu./"))
        #expect(!allowed("data:text/html,hi"))
        #expect(!allowed("javascript:alert(1)"))
    }

    @Test func regexSafety() {
        #expect(CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#) != nil)
        #expect(CanvasFinish.compile("submission-confirmation|submission-screen") != nil)
        #expect(CanvasFinish.compile(#"/assignments/\d+/submissions/\d+"#) != nil)
        #expect(CanvasFinish.compile(nil) == nil)
        #expect(CanvasFinish.compile("") == nil)
        #expect(CanvasFinish.compile("(a)\\1") == nil)
        #expect(CanvasFinish.compile("(?<name>a)\\k<name>") == nil)
        #expect(CanvasFinish.compile("(?<=a)b") == nil)
        #expect(CanvasFinish.compile("(?<!a)b") == nil)
        #expect(CanvasFinish.compile("(a+)+$") == nil)
        #expect(CanvasFinish.compile("(") == nil)
        #expect(CanvasFinish.compile(String(repeating: "a", count: 301)) == nil)
        #expect(CanvasFinish.compile(String(repeating: "a", count: 300)) != nil)
    }

    @Test func regexRejectsBacktrackingShapes() {
        #expect(CanvasFinish.compile("(a|aa)+$") == nil)
        #expect(CanvasFinish.compile("(a|a)*$") == nil)
        #expect(CanvasFinish.compile("((a+))+") == nil)
        #expect(CanvasFinish.compile("(a+)+$") == nil)
        #expect(CanvasFinish.compile("(a*)*") == nil)
        #expect(CanvasFinish.compile(#"(\d{2})+"#) == nil)
        #expect(CanvasFinish.compile("((ab))+") == nil)
        #expect(CanvasFinish.compile("(x(a+))*") == nil)
        #expect(CanvasFinish.isSafe("(a|b)?c"))
        #expect(CanvasFinish.isSafe("[+*{]x"))
        #expect(CanvasFinish.isSafe("submission-confirmation|submission-screen"))
        #expect(CanvasFinish.isSafe(#"/quizzes/\d+/(submissions/\d+|history)"#))
        #expect(CanvasFinish.isSafe(#"/assignments/\d+/submissions/\d+"#))
        #expect(!CanvasFinish.isSafe(String(repeating: "a", count: 301)))
    }

    @Test func openEndedRepeatsAreLimited() {
        for pattern in [".*.*x", ".*.*.*x", "a+a+", "(a+)+", ".+.+", #"\d+\d+"#, #"\w+\d+"#, "a*A*", "a+?a+", #"\s+\s*"#,
                        "a+.+", ".+a+", "[ab]+[ab]+", "(ab)*c*c*", "a{2,}a{3,}", "a+b+c+", "a+/b+/c+", "(x+)y+z+",
                        "(a*b*)c*", "(b*)+", "é+é+", #"\D+\d+"#, #"\b+\b+"#, "a+\\w+", " +\\s+"] {
            #expect(!CanvasFinish.isSafe(pattern), "\(pattern)")
        }
        for pattern in [#"\d+\s+"#, "a+b+", #"\s+\d+"#, #"\w+\s+"#, #"\s+\w+"#, #"a+\d+"#, #"\d+a+"#, "a+/b+", "a+?b+",
                        "a+bc+", "a*b?c*", "a{2}a{3,}", "a{1,3}a+", ".*x", "x.*y", "(a|b)?c", "a+(b)c+", "a+/+", "_+\\s+",
                        "-+\\d+", "a+\\.+", #"/quizzes/\d+/(submissions/\d+|history)"#, "submission-confirmation|submission-screen",
                        #"/assignments/\d+/submissions/\d+"#, #"/submissions/\d+"#] {
            #expect(CanvasFinish.isSafe(pattern), "\(pattern)")
        }
    }

    @Test func onlyTwoOpenEndedRepeatsInTotal() {
        #expect(CanvasFinish.isSafe(#"a+/b+"#))
        #expect(!CanvasFinish.isSafe(#"a+/b+/c+"#))
        #expect(!CanvasFinish.isSafe(#"a+/b{2,}/c*"#))
        #expect(CanvasFinish.isSafe(#"a{2}/b{3}/c{1,4}/d?/e?"#))
        #expect(CanvasFinish.isSafe(#"a+/(b|c+)"#))
        #expect(!CanvasFinish.isSafe(#"a+/(b+|c+)"#))
        #expect(CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#) != nil)
        #expect(CanvasFinish.compile(".*.*x") == nil)
        #expect(CanvasFinish.compile(#"\d+\s+"#) != nil)
    }

    @Test func lengthAndNestingRulesStay() {
        #expect(!CanvasFinish.isSafe(String(repeating: "a", count: 301)))
        #expect(CanvasFinish.isSafe(String(repeating: "a", count: 300)))
        #expect(!CanvasFinish.isSafe("(a|aa)+$"))
        #expect(!CanvasFinish.isSafe("((ab))+"))
        #expect(!CanvasFinish.isSafe("(a)\\1"))
        #expect(!CanvasFinish.isSafe("(?<=a)b"))
        #expect(!CanvasFinish.isSafe("(a"))
        #expect(!CanvasFinish.isSafe("a)"))
        #expect(!CanvasFinish.isSafe("[a"))
        #expect(!CanvasFinish.isSafe("a\\"))
    }

    @Test func finishMatchesThePathOnly() {
        let re = CanvasFinish.compile(#"/submissions/\d+"#)
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/q/submissions/7?x=1")!, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/q/take#/submissions/7")!, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://submissions.example.edu/q/take")!, regex: re))
        let long = URL(string: "https://c.example.edu/" + String(repeating: "a", count: 600) + "/submissions/7")!
        #expect(!CanvasFinish.matches(long, regex: re))
        let classic = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/take?x=/quizzes/1/history")!, regex: classic))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/take?next=%2Fquizzes%2F1%2Fhistory")!, regex: classic))
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/history?version=1")!, regex: classic))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/take#/quizzes/1/history")!, regex: classic))
    }

    @Test func finishMatching() {
        let classic = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        let yes = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/submissions/333")!
        let no = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/take")!
        #expect(CanvasFinish.matches(yes, regex: classic))
        #expect(!CanvasFinish.matches(no, regex: classic))
        #expect(!CanvasFinish.matches(yes, regex: nil))
    }

    @Test func crossHostPopups() {
        let opener = "canvas.dku.edu"
        #expect(CanvasPopup.isCrossHost(URL(string: "https://login.microsoftonline.com/")!, openerHost: opener))
        #expect(!CanvasPopup.isCrossHost(URL(string: "https://canvas.dku.edu/x")!, openerHost: opener))
        #expect(!CanvasPopup.isCrossHost(nil, openerHost: opener))
    }

    @Test func clearedWebDataRecords() {
        func gone(_ d: String, server: String = "exam.dku.edu") -> Bool {
            CanvasData.shouldRemove(domain: d, serverHost: server)
        }
        #expect(!gone("exam.dku.edu"))
        #expect(!gone(".EXAM.dku.edu"))
        #expect(!gone("dku.edu"))
        #expect(!gone(".dku.edu"))
        #expect(!gone("edu"))
        #expect(gone("microsoftonline.com"))
        #expect(gone("instructure.com"))
        #expect(gone("canvas.dku.edu"))
        #expect(gone("ku.edu"))
        #expect(gone("xexam.dku.edu"))
        #expect(gone("dku.edu", server: "exam.example.com"))
        #expect(!gone("example.com", server: "exam.example.com"))
        #expect(!gone("", server: "exam.dku.edu"))
        #expect(gone("dku.edu", server: ""))
    }

    // MARK: - Restricted Canvas browser

    private let host = "canvas.dku.edu"

    private func scope(_ kind: String = "quizzes", openBook: Bool = false) -> CanvasScope {
        CanvasScope(courseId: "123", itemKind: kind, itemId: "456", openBook: openBook,
                    signInHosts: ["login.microsoftonline.com", "sso.dku.edu", "*.duosecurity.com"],
                    quizLtiHosts: kind == "assignments" ? ["*.quiz-lti-iad-prod.instructure.com"] : [])
    }

    private func decide(_ path: String, _ phase: CanvasPhase, kind: String = "quizzes", openBook: Bool = false,
                        host h: String? = nil) -> CanvasNavigation.Decision {
        let url = URL(string: "https://" + (h ?? host) + path)!
        return CanvasNavigation.decide(url: url, examHost: host, scope: scope(kind, openBook: openBook), phase: phase)
    }

    @Test func lobbyAllowedPages() {
        for path in ["/", "", "/login", "/login/", "/login/saml", "/login/canvas/x/y",
                     "/courses/123", "/courses/123/", "/courses/123/quizzes", "/courses/123/quizzes/",
                     "/courses/123/assignments",
                     "/courses/123/quizzes/456", "/courses/123/quizzes/456/", "/courses/123/quizzes/456/take",
                     "/courses/123/quizzes/456/take/questions/9", "/courses/123/quizzes/456/submissions/7",
                     "/courses/123/quizzes/456?x=1", "/courses/123/quizzes/456#frag",
                     "/courses/123/quizzes/456/take?user_id=1&x=/inbox"] {
            #expect(decide(path, .lobby) == .allow, "lobby \(path)")
        }
        #expect(decide("/courses/123/assignments/456", .lobby, kind: "assignments") == .allow)
        #expect(decide("/courses/123/assignments/456/submissions/1", .lobby, kind: "assignments") == .allow)
    }

    @Test func lobbyBlockedPages() {
        for path in ["/dashboard", "/courses", "/courses/", "/courses/12", "/courses/1234", "/courses/124",
                     "/courses/12/quizzes/456", "/courses/124/quizzes/456", "/courses/999/quizzes/456",
                     "/courses/123/quizzes/4567", "/courses/123/quizzes/45", "/courses/123/quizzes/456x",
                     "/courses/123/quizzes/x456", "/courses/123/quizzes/0456", "/courses/123/quizzes/457",
                     "/courses/123/assignments/456", "/courses/123/quizzes/456abc/take",
                     "/courses/123/pages", "/courses/123/pages/home", "/courses/123/modules", "/courses/123/files",
                     "/courses/123/grades", "/courses/123/discussion_topics", "/courses/123/users",
                     "/courses/123/quizzes/new", "/courses/123/quizzes/456a", "/courses/123x/quizzes/456",
                     "/loginx", "/login-evil", "/files/1", "/conversations", "/conversations/1",
                     "/inbox", "/inbox/", "/profile", "/profile/settings", "/groups/1", "/calendar",
                     "/calendar?include_contexts=course_123", "/accounts/1", "/api/v1/courses", "/users/5",
                     "/users/self/files"] {
            #expect(decide(path, .lobby) == .block, "lobby \(path)")
        }
        #expect(decide("/courses/123/quizzes/456", .lobby, kind: "assignments") == .block)
        #expect(decide("/courses/123/assignments/4567", .lobby, kind: "assignments") == .block)
    }

    @Test func lookAlikePathsAreRefused() {
        for path in ["/courses/123/quizzes/456/../../../inbox", "/courses/123/quizzes/456/..",
                     "/courses/123/quizzes/456/../789", "/courses/123/./quizzes/456", "/courses/123/quizzes/456/.",
                     "/courses/123/quizzes/456%2f..%2f..%2finbox", "/courses/123%2fquizzes/456",
                     "/courses/123/quizzes%2F456", "/courses/123/quizzes/456%5c..", "/courses/123/quizzes/456/%2e%2e/x",
                     "/courses/123/quizzes/456/%2E%2E/x", "/courses/123/quizzes/456/.%2e/inbox",
                     "/courses/123/quizzes/456/%2e/x", "/courses/123//quizzes/456", "//courses/123/quizzes/456",
                     "/courses/123/quizzes/456//", "/courses/123/quizzes/456//take", "/courses/123/quizzes/456%00",
                     "/courses/123/quizzes/456%20", "/courses/123/quizzes/456;x", "/courses/%31%32%33/quizzes/456"] {
            for phase in [CanvasPhase.lobby, CanvasPhase.quiz] {
                #expect(decide(path, phase) == .block, "\(phase) \(path)")
            }
        }
    }

    @Test func caseIsExact() {
        for path in ["/Courses/123/quizzes/456", "/courses/123/Quizzes/456", "/COURSES/123/QUIZZES/456",
                     "/Login", "/Courses/123", "/courses/123/quizzes/456/TAKE/../x"] {
            #expect(decide(path, .lobby) == .block, "\(path)")
        }
        #expect(decide("/courses/123/quizzes/456/Take", .lobby) == .allow)
        for path in ["/Inbox", "/INBOX/1", "/Conversations", "/API/v1/x", "/Users/self", "/CALENDAR",
                     "/Courses/999/pages"] {
            #expect(decide(path, .lobby) == .block, "\(path)")
            #expect(decide(path, .quiz, openBook: true) == .block, "\(path)")
        }
    }

    @Test func queryAndFragmentAreIgnored() {
        #expect(decide("/inbox?next=/courses/123/quizzes/456", .lobby) == .block)
        #expect(decide("/inbox#/courses/123/quizzes/456", .lobby) == .block)
        #expect(decide("/courses/999?next=/courses/123/quizzes/456", .lobby) == .block)
        #expect(decide("/courses/123/quizzes/456?return_to=/inbox", .lobby) == .allow)
        #expect(decide("/courses/123/quizzes/456#/inbox", .quiz) == .allow)
        #expect(decide("/courses/123/quizzes/4567?x=/courses/123/quizzes/456", .quiz) == .block)
        #expect(decide("/courses/123/quizzes/456%3F/x", .quiz) == .block)
        #expect(decide("/courses/123/quizzes/456%23/x", .quiz) == .block)
    }

    @Test func quizPhaseKeepsOnlyTheItem() {
        for path in ["/courses/123/quizzes/456", "/courses/123/quizzes/456/", "/courses/123/quizzes/456/take",
                     "/courses/123/quizzes/456/submissions/9", "/courses/123/quizzes/456/history?version=1"] {
            #expect(decide(path, .quiz) == .allow, "\(path)")
        }
        for path in ["/login", "/login/", "/login/saml", "/login/session_token?return_to=/inbox"] {
            #expect(decide(path, .quiz) == .allow, "\(path)")
            #expect(decide(path, .quiz, kind: "assignments") == .allow, "\(path)")
        }
        for path in ["/loginx", "/Login", "/login/../inbox", "/login/%2e%2e/inbox", "/login%2f..%2finbox"] {
            #expect(decide(path, .quiz) == .block, "\(path)")
        }
        for path in ["/", "/courses/123", "/courses/123/quizzes", "/courses/123/assignments",
                     "/courses/123/pages/x", "/courses/123/modules", "/courses/123/files/1", "/courses/123/wiki/x",
                     "/files/1", "/courses/12", "/courses/123/quizzes/4567", "/courses/123/quizzes/456x",
                     "/courses/999/quizzes/456", "/conversations", "/inbox", "/profile", "/groups/1", "/calendar",
                     "/accounts/1", "/api/v1/users/self", "/users/1", "/dashboard"] {
            #expect(decide(path, .quiz) == .block, "\(path)")
        }
        #expect(decide("/courses/123/assignments/456/anything", .quiz, kind: "assignments") == .allow)
        #expect(decide("/courses/123/quizzes/456", .quiz, kind: "assignments") == .block)
    }

    @Test func openBookAddsCourseMaterialOnly() {
        for path in ["/courses/123/pages", "/courses/123/pages/week-1", "/courses/123/modules",
                     "/courses/123/modules/items/5", "/courses/123/files", "/courses/123/files/77",
                     "/courses/123/files/folder/a/b", "/courses/123/wiki/page", "/files/77", "/files/77/preview",
                     "/courses/123/pages/", "/courses/123/quizzes/456/take"] {
            #expect(decide(path, .quiz, openBook: true) == .allow, "\(path)")
            let closed: CanvasNavigation.Decision = path.contains("quizzes") ? .allow : .block
            #expect(decide(path, .quiz, openBook: false) == closed, "\(path)")
        }
        for path in ["/courses/123/files/77/download", "/files/77/download", "/courses/123/files/77/download/x",
                     "/courses/12/pages/x", "/courses/1234/modules", "/courses/999/files/1", "/courses/123/pagesx",
                     "/courses/123/modulesx/1", "/courses/123/filesx", "/courses/123/grades", "/courses/123/users",
                     "/courses/123/discussion_topics", "/courses/123/quizzes", "/courses/123/assignments",
                     "/courses/123", "/filesx/1", "/file/1", "/",
                     "/courses/123/pages/../../999/pages", "/courses/123/files/1%2f..%2f..",
                     "/courses/123/modules/%2e%2e/x"] {
            #expect(decide(path, .quiz, openBook: true) == .block, "\(path)")
        }
        #expect(decide("/courses/123/pages/x", .lobby, openBook: true) == .block)
        #expect(decide("/courses/123/modules", .lobby, openBook: true) == .block)
    }

    @Test func alwaysBlockedInEveryPhase() {
        let paths = ["/conversations", "/conversations/1/x", "/inbox", "/inbox/compose", "/profile", "/profile/communication",
                     "/groups", "/groups/9/pages", "/calendar", "/calendar/x", "/courses/999", "/courses/999/pages",
                     "/courses/999/quizzes/456", "/accounts", "/accounts/1/x", "/api/v1/courses", "/users", "/users/self/files"]
        for path in paths {
            for phase in [CanvasPhase.lobby, CanvasPhase.quiz] {
                for open in [false, true] {
                    #expect(decide(path, phase, openBook: open) == .block, "\(phase) \(open) \(path)")
                    #expect(decide(path, phase, kind: "assignments", openBook: open) == .block, "\(phase) \(open) \(path)")
                }
            }
        }
    }

    @Test func otherHostsFollowTheSiteRulesInTheLobby() {
        for h in ["login.microsoftonline.com", "docs.google.com", "canvas.dku.edu.evil.com", "example.com"] {
            #expect(decide("/inbox", .lobby, host: h) == .allow, "\(h)")
        }
        #expect(decide("/inbox", .lobby, host: "CANVAS.dku.edu") == .block)
        #expect(decide("/inbox", .quiz, host: "CANVAS.dku.edu") == .block)
    }

    @Test func otherHostsInTheQuizPhase() {
        for h in ["login.microsoftonline.com", "sso.dku.edu", "api-1a2b3c.duosecurity.com", "SSO.DKU.EDU"] {
            #expect(decide("/anything", .quiz, host: h) == .allow, "\(h)")
            #expect(decide("/anything", .quiz, kind: "assignments", openBook: true, host: h) == .allow, "\(h)")
        }
        #expect(decide("/anything", .quiz, kind: "assignments", host: "x.quiz-lti-iad-prod.instructure.com") == .allow)
        #expect(decide("/anything", .quiz, host: "x.quiz-lti-iad-prod.instructure.com") == .block)
        for h in ["docs.google.com", "example.com", "canvas.dku.edu.evil.com", "evilcanvas.dku.edu", "a.instructure.com",
                  "accounts.google.com", "idp.example.org", "authn.example.edu", "duosecurity.com",
                  "login.microsoftonline.com.evil.com", "evillogin.microsoftonline.com", "xsso.dku.edu",
                  "quiz-lti-iad-prod.instructure.com", "dku.edu", "files.example.com"] {
            #expect(decide("/anything", .quiz, host: h) == .block, "\(h)")
            #expect(decide("/anything", .quiz, kind: "assignments", openBook: true, host: h) == .block, "\(h)")
            let popup = CanvasNavigation.decide(url: URL(string: "https://" + h + "/anything")!, examHost: host,
                                                scope: scope(), phase: .quiz, inPopup: true)
            #expect(popup == .allow, "\(h)")
        }
        #expect(CanvasNavigation.decide(url: URL(string: "about:blank")!, examHost: host, scope: scope(), phase: .quiz) == .block)
        let none = CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: false)
        let blocked = CanvasNavigation.decide(url: URL(string: "https://login.microsoftonline.com/x")!, examHost: host,
                                              scope: none, phase: .quiz)
        #expect(blocked == .block)
    }

    @Test func hostListMatching() {
        #expect(CanvasNavigation.hostListMatches("a.duosecurity.com", ["*.duosecurity.com"]))
        #expect(CanvasNavigation.hostListMatches("A.B.duosecurity.com", ["*.duosecurity.com"]))
        #expect(!CanvasNavigation.hostListMatches("duosecurity.com", ["*.duosecurity.com"]))
        #expect(!CanvasNavigation.hostListMatches("xduosecurity.com", ["*.duosecurity.com"]))
        #expect(CanvasNavigation.hostListMatches("sso.dku.edu", ["sso.dku.edu"]))
        #expect(!CanvasNavigation.hostListMatches("a.sso.dku.edu", ["sso.dku.edu"]))
        #expect(!CanvasNavigation.hostListMatches("sso.dku.edu", []))
    }

    @Test func recoveryGateLimitsToOnePerSecond() {
        var gate = CanvasRecoveryGate()
        let t0 = Date(timeIntervalSince1970: 1_000)
        let first = gate.shouldRecover(now: t0)
        let tooSoon = gate.shouldRecover(now: t0.addingTimeInterval(0.5))
        let stillSoon = gate.shouldRecover(now: t0.addingTimeInterval(0.99))
        let later = gate.shouldRecover(now: t0.addingTimeInterval(1.0))
        let after = gate.shouldRecover(now: t0.addingTimeInterval(2.5))
        #expect(first)
        #expect(!tooSoon)
        #expect(!stillSoon)
        #expect(later)
        #expect(after)
    }

    @Test func recoveryGateRecordsEachPageOnce() {
        var gate = CanvasRecoveryGate()
        let a = gate.firstTime("canvas page /inbox")
        let again = gate.firstTime("canvas page /inbox")
        let b = gate.firstTime("canvas page /calendar")
        #expect(a)
        #expect(!again)
        #expect(b)
        for i in 0..<(CanvasRecoveryGate.maxRemembered + 5) { _ = gate.firstTime("p\(i)") }
        let reset = gate.firstTime("canvas page /inbox")
        #expect(reset)
    }

    @Test func pathSegmentParsing() {
        #expect(CanvasNavigation.segments(encodedPath: "") == [])
        #expect(CanvasNavigation.segments(encodedPath: "/") == [])
        #expect(CanvasNavigation.segments(encodedPath: "/a/b/") == ["a", "b"])
        #expect(CanvasNavigation.segments(encodedPath: "/a//b") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "/a/b//") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "/a/../b") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "/a/%2E%2e/b") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "/a%2Fb") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "a/b") == nil)
        #expect(CanvasNavigation.segments(encodedPath: "/a%20b") == ["a%20b"])
        #expect(CanvasNavigation.segments(URL(string: "https://c.example.edu")!) == [])
        #expect(CanvasNavigation.segments(URL(string: "https://c.example.edu/a/b?x=/y#/z")!) == ["a", "b"])
    }

    @Test func phaseEntry() {
        func enters(_ path: String, kind: String = "quizzes") -> Bool {
            CanvasNavigation.entersQuiz(url: URL(string: "https://" + host + path)!, examHost: host, scope: scope(kind))
        }
        #expect(enters("/courses/123/quizzes/456/take"))
        #expect(enters("/courses/123/quizzes/456/take/"))
        #expect(enters("/courses/123/quizzes/456/take?preview=1"))
        #expect(!enters("/courses/123/quizzes/456"))
        #expect(!enters("/courses/123/quizzes/456/submissions/1"))
        #expect(!enters("/courses/123/quizzes/4567/take"))
        #expect(!enters("/courses/12/quizzes/456/take"))
        #expect(!enters("/courses/123/quizzes/456/taken"))
        #expect(!enters("/courses/123/quizzes/456/%2e%2e/take"))
        #expect(!enters("/courses/123/quizzes"))
        #expect(!enters("/"))
        #expect(enters("/courses/123/assignments/456", kind: "assignments"))
        #expect(enters("/courses/123/assignments/456/", kind: "assignments"))
        #expect(enters("/courses/123/assignments/456/submissions/2", kind: "assignments"))
        #expect(!enters("/courses/123/assignments/4567", kind: "assignments"))
        #expect(!enters("/courses/123/assignments", kind: "assignments"))
        #expect(!enters("/courses/999/assignments/456", kind: "assignments"))
        let other = CanvasNavigation.entersQuiz(url: URL(string: "https://evil.example.com/courses/123/quizzes/456/take")!,
                                                examHost: host, scope: scope())
        #expect(!other)
    }

    @Test func landingPages() {
        func landing(_ s: String) -> Bool {
            CanvasNavigation.isLanding(url: URL(string: s)!, examHost: host, scope: scope())
        }
        #expect(landing("https://canvas.dku.edu/"))
        #expect(landing("https://canvas.dku.edu"))
        #expect(landing("https://canvas.dku.edu/?login_success=1"))
        #expect(landing("https://canvas.dku.edu/courses/123"))
        #expect(landing("https://canvas.dku.edu/courses/123/"))
        #expect(!landing("https://canvas.dku.edu/courses/12"))
        #expect(!landing("https://canvas.dku.edu/courses/1234"))
        #expect(!landing("https://canvas.dku.edu/courses/123/pages"))
        #expect(!landing("https://canvas.dku.edu/login"))
        #expect(!landing("https://other.example.com/"))
    }

    @Test func signInPages() {
        func signIn(_ s: String) -> Bool { CanvasNavigation.isSignIn(url: URL(string: s)!, examHost: host) }
        #expect(signIn("https://canvas.dku.edu/login/saml"))
        #expect(signIn("https://login.microsoftonline.com/x"))
        #expect(!signIn("https://canvas.dku.edu/"))
        #expect(!signIn("https://canvas.dku.edu/courses/123"))
    }

    @Test func scopeParsing() {
        let good: [String: Any] = ["course_id": "123", "item_kind": "quizzes", "item_id": "456", "open_book": true]
        #expect(CanvasScope(json: good) == CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: true))
        let noBook: [String: Any] = ["course_id": "1", "item_kind": "assignments", "item_id": "2"]
        #expect(CanvasScope(json: noBook)?.openBook == false)
        #expect(CanvasScope(json: noBook)?.itemKind == "assignments")
        let numeric: [String: Any] = ["course_id": 7, "item_kind": "quizzes", "item_id": 8, "open_book": false]
        #expect(CanvasScope(json: numeric)?.courseId == "7")
        #expect(CanvasScope(json: nil) == nil)
        #expect(CanvasScope(json: "x") == nil)
        #expect(CanvasScope(json: ["course_id": "12a", "item_kind": "quizzes", "item_id": "2"] as [String: Any]) == nil)
        #expect(CanvasScope(json: ["course_id": "", "item_kind": "quizzes", "item_id": "2"] as [String: Any]) == nil)
        #expect(CanvasScope(json: ["course_id": "1", "item_kind": "pages", "item_id": "2"] as [String: Any]) == nil)
        #expect(CanvasScope(json: ["course_id": "1", "item_kind": "quizzes"] as [String: Any]) == nil)
        #expect(CanvasScope(json: ["course_id": "1/2", "item_kind": "quizzes", "item_id": "2"] as [String: Any]) == nil)
        let long = String(repeating: "9", count: 21)
        #expect(CanvasScope(json: ["course_id": long, "item_kind": "quizzes", "item_id": "2"] as [String: Any]) == nil)
        let longest = String(repeating: "9", count: 20)
        #expect(CanvasScope(json: ["course_id": longest, "item_kind": "quizzes", "item_id": "2"] as [String: Any]) != nil)
        #expect(CanvasScope(json: ["course_id": "\u{0661}", "item_kind": "quizzes", "item_id": "2"] as [String: Any]) == nil)
    }

    @Test func scopeHostLists() {
        let base: [String: Any] = ["course_id": "1", "item_kind": "assignments", "item_id": "2"]
        #expect(CanvasScope(json: base)?.signInHosts == [])
        #expect(CanvasScope(json: base)?.quizLtiHosts == [])
        var full = base
        full["sign_in_hosts"] = ["Login.MicrosoftOnline.com", "*.duosecurity.com"]
        full["quiz_lti_hosts"] = ["x.quiz-lti-iad-prod.instructure.com", "*.quiz-lti-pdx-prod.instructure.com"]
        let parsed = CanvasScope(json: full)
        #expect(parsed?.signInHosts == ["login.microsoftonline.com", "*.duosecurity.com"])
        #expect(parsed?.quizLtiHosts == ["x.quiz-lti-iad-prod.instructure.com", "*.quiz-lti-pdx-prod.instructure.com"])
        var nulls = base
        nulls["sign_in_hosts"] = NSNull()
        #expect(CanvasScope(json: nulls)?.signInHosts == [])
        let bad: [Any] = ["x.com", [1], ["x.com", 5], ["*."], ["a b.com"], ["https://x.com"], ["x.com/y"],
                          ["a..com"], [".a.com"], ["*.*.a.com"], ["a.*.com"], ["x.com:8443"], [""],
                          Array(repeating: "x.com", count: 51)]
        for list in bad {
            var d = base
            d["sign_in_hosts"] = list
            #expect(CanvasScope(json: d) == nil)
            var e = base
            e["quiz_lti_hosts"] = list
            #expect(CanvasScope(json: e) == nil)
        }
        #expect(CanvasScope.isHostPattern("*.a.com"))
    }

    @Test func downloadQueriesAreBlocked() {
        for q in ["?download=1", "?download", "?Download=1", "?DOWNLOAD_FRD=1", "?download_frd=1", "?x=1&download_frd=1",
                  "?x=1&Download=true#frag"] {
            #expect(decide("/courses/123/files/77" + q, .quiz, openBook: true) == .block, "\(q)")
            #expect(decide("/files/77" + q, .quiz, openBook: true) == .block, "\(q)")
            #expect(decide("/courses/123/quizzes/456" + q, .lobby) == .block, "\(q)")
        }
        #expect(decide("/courses/123/files/77?wrap=1&x=download", .quiz, openBook: true) == .allow)
        #expect(decide("/courses/123/files/77?downloads=1", .quiz, openBook: true) == .allow)
        #expect(decide("/courses/123/files/77?wrap=1", .quiz, openBook: true) == .allow)
    }

    // MARK: - Start parsing

    private let goodScope: [String: Any] = ["course_id": "123", "item_kind": "quizzes", "item_id": "456", "open_book": false]

    private func setup(scope: Any? = nil, url: String = "https://canvas.dku.edu/courses/123/quizzes/456",
                       domains: [Any] = ["canvas.dku.edu", "login.microsoftonline.com"],
                       regex: String? = #"/quizzes/\d+/submissions/\d+"#) -> Result<CanvasSetup, CanvasSetup.Failure> {
        CanvasSetup.make(startURL: URL(string: url)!, allowedDomains: domains, rawScope: scope, rawRegex: regex)
    }

    private func failure(_ result: Result<CanvasSetup, CanvasSetup.Failure>) -> CanvasSetup.Failure? {
        if case .failure(let f) = result { return f }
        return nil
    }

    private func success(_ result: Result<CanvasSetup, CanvasSetup.Failure>) -> CanvasSetup? {
        if case .success(let s) = result { return s }
        return nil
    }

    @Test func setupValidatesTheScope() {
        let withScope = success(setup(scope: goodScope))
        #expect(withScope?.scope == CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: false))
        #expect(failure(setup(scope: nil)) == .badScope)
        #expect(failure(setup(scope: NSNull())) == .badScope)
        let bad: [Any] = [
            "x", 5, [String: Any](),
            ["course_id": "12a", "item_kind": "quizzes", "item_id": "456"] as [String: Any],
            ["course_id": "123", "item_kind": "pages", "item_id": "456"] as [String: Any],
            ["course_id": "123", "item_kind": "quizzes"] as [String: Any]
        ]
        for scope in bad {
            #expect(failure(setup(scope: scope)) == .badScope)
        }
        let good: [String: Any] = ["course_id": "123", "item_kind": "quizzes", "item_id": "456"]
        #expect(failure(setup(scope: good, url: "https://canvas.dku.edu/courses/123/quizzes/4567")) == .badScope)
        #expect(failure(setup(scope: good, url: "https://canvas.dku.edu/courses/12/quizzes/456")) == .badScope)
        #expect(failure(setup(scope: good, url: "https://canvas.dku.edu/inbox")) == .badScope)
        #expect(failure(setup(scope: good, url: "https://canvas.dku.edu/courses/123/assignments/456")) == .badScope)
        #expect(failure(setup(scope: good, url: "https://canvas.dku.edu/courses/123/quizzes/456/../../../inbox")) == .badScope)
        #expect(success(setup(scope: good, url: "https://canvas.dku.edu/courses/123/quizzes/456/edit")) != nil)
    }

    @Test func setupNeedsHttps() {
        #expect(failure(setup(scope: goodScope, url: "http://canvas.dku.edu/courses/123/quizzes/456")) == .notHTTPS)
        #expect(failure(setup(url: "ftp://canvas.dku.edu/courses/123/quizzes/456")) == .notHTTPS)
    }

    @Test func setupAddsTheExamHostOnce() {
        func plainHost(_ rules: [SiteRule]) -> Bool {
            rules.contains { !$0.wildcard && $0.host == "canvas.dku.edu" && $0.pathPrefix.isEmpty }
        }
        let plain = success(setup(scope: goodScope, domains: ["login.microsoftonline.com"]))
        #expect(plainHost(plain?.rules ?? []))
        let listed = success(setup(scope: goodScope, domains: ["canvas.dku.edu", "login.microsoftonline.com"]))
        #expect(listed?.rules.count == 2)
        let folderOnly = success(setup(scope: goodScope, domains: ["canvas.dku.edu/courses/123/quizzes/456", 5, "*.duosecurity.com"]))
        let folderRules = folderOnly?.rules ?? []
        let hasWildcard = folderRules.contains { $0.wildcard && $0.host == "duosecurity.com" }
        #expect(plainHost(folderRules))
        #expect(hasWildcard)
    }

    @Test func setupFinishPattern() {
        let ok = success(setup(scope: goodScope))
        #expect(ok?.finishRegex != nil)
        #expect(ok?.finishRejected == false)
        let unsafe = success(setup(scope: goodScope, regex: "(a+)+$"))
        #expect(unsafe?.finishRegex == nil)
        #expect(unsafe?.finishRejected == true)
        let none = success(setup(scope: goodScope, regex: nil))
        #expect(none?.finishRegex == nil)
        #expect(none?.finishRejected == true)
        let empty = success(setup(scope: goodScope, regex: ""))
        #expect(empty?.finishRejected == true)
    }

    // MARK: - Browser state

    private func browser(kind: String = "quizzes", openBook: Bool = false,
                         quizStarted: Bool = false, armed: Bool = false, finishUsable: Bool = true) -> CanvasBrowser {
        let start = "https://canvas.dku.edu/courses/123/\(kind)/456"
        let raw: [String: Any] = ["course_id": "123", "item_kind": kind, "item_id": "456", "open_book": openBook,
                                  "sign_in_hosts": ["login.microsoftonline.com", "*.duosecurity.com"],
                                  "quiz_lti_hosts": [String]()]
        let made = success(setup(scope: raw, url: start))!
        return CanvasBrowser(startURL: URL(string: start)!, rules: made.rules, scope: made.scope, quizStarted: quizStarted,
                       armed: armed, finishUsable: finishUsable)
    }

    private func page(_ path: String) -> URL { URL(string: "https://canvas.dku.edu" + path)! }

    @Test func phaseAndPolicyFollowCommittedPages() {
        var b = browser()
        #expect(b.phase == .lobby)
        #expect(!b.blocksMainFrame(page("/")))
        #expect(!b.blocksMainFrame(page("/courses/123")))
        #expect(b.blocksMainFrame(page("/courses/999")))
        #expect(b.blocksMainFrame(page("/inbox")))
        #expect(b.allowsMainFrame(page("/courses/123/quizzes/456")))
        #expect(!b.allowsMainFrame(URL(string: "https://evil.example.com/")!))
        let a = b.committed(page("/courses/123/quizzes/456"))
        #expect(!a && b.phase == .lobby)
        let other = b.committed(page("/courses/123/quizzes/4567/take"))
        #expect(!other && b.phase == .lobby)
        let entered = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(entered && b.phase == .quiz)
        #expect(b.blocksMainFrame(page("/")))
        #expect(b.blocksMainFrame(page("/courses/123")))
        #expect(b.blocksMainFrame(page("/courses/123/quizzes")))
        #expect(!b.blocksMainFrame(page("/courses/123/quizzes/456/take")))
        #expect(!b.blocksMainFrame(page("/login/saml")))
        #expect(!b.blocksMainFrame(URL(string: "https://login.microsoftonline.com/x")!))
        let again = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(!again)
        let back = b.committed(page("/"))
        #expect(!back && b.phase == .quiz)
    }

    @Test func assignmentScopeEntersQuizOnAnyItemPage() {
        var b = browser(kind: "assignments")
        let entered = b.committed(page("/courses/123/assignments/456"))
        #expect(entered && b.phase == .quiz)
        #expect(b.blocksMainFrame(page("/courses/123")))
    }

    @Test func popupsLoadInTheMainViewOrNotAtAll() {
        var b = browser()
        #expect(b.popupPlan(url: page("/courses/123/quizzes/456")) == .sameView)
        #expect(b.popupPlan(url: page("/login/saml")) == .sameView)
        #expect(b.popupPlan(url: URL(string: "https://login.microsoftonline.com/x")!) == .sameView)
        #expect(b.popupPlan(url: page("/inbox")) == .block)
        #expect(b.popupPlan(url: page("/courses/999")) == .block)
        #expect(b.popupPlan(url: URL(string: "https://evil.example.com/")!) == .block)
        #expect(b.popupPlan(url: URL(string: "http://canvas.dku.edu/courses/123/quizzes/456")!) == .block)
        #expect(b.popupPlan(url: URL(string: "about:blank")) == .block)
        #expect(b.popupPlan(url: nil) == .block)
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.popupPlan(url: page("/courses/123/pages/x")) == .block)
        #expect(b.popupPlan(url: page("/courses/123/quizzes/456/take")) == .sameView)
    }

    @Test func backPredicate() {
        var b = browser()
        #expect(b.backAllowed(page("/")))
        #expect(b.backAllowed(page("/login/saml")))
        #expect(b.backAllowed(page("/courses/123")))
        #expect(b.backAllowed(page("/courses/123/quizzes/456")))
        #expect(!b.backAllowed(page("/inbox")))
        #expect(!b.backAllowed(URL(string: "https://exam.example.com/student/external/a1b2c3")!))
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.backAllowed(page("/courses/123/quizzes/456")))
        #expect(b.backAllowed(page("/courses/123/quizzes/456/take")))
        #expect(!b.backAllowed(page("/")))
        #expect(!b.backAllowed(page("/login")))
        #expect(!b.backAllowed(page("/login/saml")))
        #expect(!b.backAllowed(URL(string: "https://login.microsoftonline.com/x")!))
        #expect(!b.backAllowed(page("/courses/999/quizzes/456")))
        #expect(!b.backAllowed(page("/courses/123")))
        #expect(!b.backAllowed(page("/courses/123/quizzes")))
    }

    @Test func backStaysInsideTheItemDuringTheQuiz() {
        var b = browser(openBook: true)
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(!b.blocksMainFrame(page("/courses/123/pages/x")))
        #expect(!b.backAllowed(page("/courses/123/pages/x")))
        #expect(!b.backAllowed(page("/courses/123/modules")))
        #expect(b.backAllowed(page("/courses/123/quizzes/456")))
        #expect(b.backAllowed(page("/courses/123/quizzes/456/take")))
    }

    @Test func blockDetailCarriesNoQuery() {
        let b = browser()
        #expect(b.blockDetail(page("/inbox?token=secret#frag")) == "canvas page /inbox")
        #expect(b.blockDetail(page("/" + String(repeating: "a", count: 400))).count <= 140)
    }

    @Test func blockedPagesAreRecordedOnceAndCapped() {
        var b = browser()
        let first = b.recordBlocked(page("/inbox"))
        let again = b.recordBlocked(page("/inbox"))
        #expect(first == "canvas page /inbox")
        #expect(again == nil)
        for i in 0..<(CanvasBrowser.maxBlockReports + 20) {
            _ = b.recordBlocked(page("/p\(i)"))
        }
        #expect(b.blockedReports == CanvasBrowser.maxBlockReports)
        let late = b.recordBlocked(page("/late"))
        #expect(late == nil)
    }

    @Test func itemPageIsRecognised() {
        let b = browser()
        #expect(b.isOnItem(page("/courses/123/quizzes/456")))
        #expect(b.isOnItem(page("/courses/123/quizzes/456/take")))
        #expect(!b.isOnItem(page("/courses/123/quizzes/4567")))
        #expect(!b.isOnItem(page("/")))
        #expect(!b.isOnItem(nil))
        #expect(!b.isOnItem(URL(string: "https://evil.example.com/courses/123/quizzes/456")!))
    }

    // MARK: - Recovery

    @Test func recoveryNeverStopsAfterTheLastTry() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        let blocked = page("/inbox")
        var now = Date(timeIntervalSince1970: 10_000)
        var actions: [CanvasViewAction] = []
        for _ in 0..<CanvasBrowser.maxRecoveries {
            now = now.addingTimeInterval(2)
            let step = b.recover(url: blocked, now: now, previous: nil)
            if let action = step.action { actions.append(action) }
        }
        #expect(actions == Array(repeating: CanvasViewAction.load(start), count: CanvasBrowser.maxRecoveries))
        now = now.addingTimeInterval(2)
        let last = b.recover(url: blocked, now: now, previous: nil)
        #expect(last.action == .load(start))
        #expect(!last.recheck)
        now = now.addingTimeInterval(2)
        let stopped1 = b.recover(url: blocked, now: now, previous: nil)
        now = now.addingTimeInterval(2)
        let stopped2 = b.recover(url: blocked, now: now, previous: nil)
        #expect(stopped1.action == nil && stopped2.action == nil)
        #expect(stopped1.notice && stopped2.notice)
        let allowed = b.recover(url: page("/courses/123/quizzes/456/take"), now: now, previous: nil)
        #expect(allowed == CanvasRecovery())
        now = now.addingTimeInterval(2)
        let again = b.recover(url: blocked, now: now, previous: nil)
        #expect(again.action == .load(start))
    }

    @Test func recoveryIsRateLimitedAndIgnoresAllowedPages() {
        var b = browser()
        let now = Date(timeIntervalSince1970: 10_000)
        let a = b.recover(url: page("/courses/123/quizzes/456"), now: now, previous: nil)
        let e = b.recover(url: URL(string: "https://evil.example.com/")!, now: now, previous: nil)
        let n = b.recover(url: nil, now: now, previous: nil)
        #expect(a == CanvasRecovery() && e == CanvasRecovery() && n == CanvasRecovery())
        let first = b.recover(url: page("/inbox"), now: now, previous: nil)
        let second = b.recover(url: page("/inbox"), now: now, previous: nil)
        let third = b.recover(url: page("/calendar"), now: now, previous: nil)
        #expect(first.action == .load(URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!))
        #expect(first.recheck)
        #expect(second.action == nil && second.recheck)
        #expect(third.action == nil && third.recheck)
        #expect(first.report == "canvas page /inbox (in page)")
        #expect(second.report == nil)
        #expect(third.report == "canvas page /calendar (in page)")
    }

    @Test func recoveryGoesBackInTheLobbyWhenThePreviousPageIsAllowed() {
        var b = browser()
        let now = Date(timeIntervalSince1970: 10_000)
        let back = b.recover(url: page("/inbox"), now: now, previous: page("/courses/123"))
        #expect(back.action == .goBack)
        var c = browser()
        let blockedPrevious = c.recover(url: page("/inbox"), now: now, previous: page("/calendar"))
        #expect(blockedPrevious.action == .load(URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!))
        var d = browser()
        let same = d.recover(url: page("/inbox"), now: now, previous: page("/inbox"))
        #expect(same.action == .load(URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!))
    }

    @Test func recoveryInTheQuizAlwaysReloadsTheExamLink() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        let now = Date(timeIntervalSince1970: 10_000)
        let step = b.recover(url: page("/inbox"), now: now, previous: page("/courses/123/quizzes/456"))
        #expect(step.action == .load(URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!))
    }

    // MARK: - Warm sign-in

    @Test func examLinkOpensOnceAfterSignIn() {
        var b = browser()
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        let early = b.autoOpenTarget(page("/"))
        #expect(early == nil)
        _ = b.committed(page("/login/saml"))
        let pages = b.autoOpenTarget(page("/courses/123/pages"))
        let otherCourse = b.autoOpenTarget(page("/courses/12"))
        #expect(pages == nil && otherCourse == nil)
        let landed = b.autoOpenTarget(page("/"))
        #expect(landed == start)
        let second = b.autoOpenTarget(page("/"))
        let third = b.autoOpenTarget(page("/courses/123"))
        #expect(second == nil && third == nil)
        var fresh = browser()
        _ = fresh.committed(page("/login"))
        _ = fresh.committed(page("/courses/123/quizzes/456/take"))
        let inQuiz = fresh.autoOpenTarget(page("/"))
        #expect(inQuiz == nil)
    }

    @Test func warmSessionLandingOpensTheExamLink() {
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        for landing in ["/", "/courses/123"] {
            var b = browser()
            _ = b.committed(page(landing))
            let first = b.autoOpenTarget(page(landing))
            #expect(first == start, "\(landing)")
            let again = b.autoOpenTarget(page(landing))
            _ = b.committed(page("/"))
            let later = b.autoOpenTarget(page("/"))
            #expect(again == nil && later == nil, "\(landing)")
        }
    }

    @Test func landingOnTheExamLinkNeedsNoAutoOpen() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456"))
        let onItem = b.autoOpenTarget(page("/courses/123/quizzes/456"))
        _ = b.committed(page("/"))
        let dashboard = b.autoOpenTarget(page("/"))
        #expect(onItem == nil && dashboard == nil)
    }

    @Test func onlyTheFirstPageCountsAsAWarmLanding() {
        var b = browser()
        _ = b.committed(page("/courses/123/pages/x"))
        _ = b.committed(page("/"))
        let none = b.autoOpenTarget(page("/"))
        #expect(none == nil)
        var other = browser()
        _ = other.committed(URL(string: "https://other.example.com/")!)
        let open = other.autoOpenTarget(page("/"))
        #expect(open != nil)
    }

    // MARK: - Lock-in and restart

    @Test func quizPhaseSurvivesARestart() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        var first = browser()
        #expect(first.phase == .lobby)
        #expect(!first.isLockedIn)
        let entered = first.committed(page("/courses/123/quizzes/456/take"))
        #expect(entered && first.phase == .quiz && first.isLockedIn)
        CanvasQuizMarker.set("a1b2c3", in: defaults)
        let second = browser(quizStarted: CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(second.phase == .quiz)
        #expect(second.isLockedIn)
        #expect(second.blocksMainFrame(page("/")))
        CanvasQuizMarker.clear("a1b2c3", in: defaults)
        let third = browser(quizStarted: CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(third.phase == .lobby)
    }

    @Test func markerIsPerAttempt() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        CanvasQuizMarker.set("other1", in: defaults)
        let unrelated = browser(quizStarted: CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(unrelated.phase == .lobby)
    }

    @Test func markerHelpers() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        #expect(!CanvasQuizMarker.isSet("x1", in: defaults))
        CanvasQuizMarker.set("x1", in: defaults)
        #expect(CanvasQuizMarker.isSet("x1", in: defaults))
        #expect(!CanvasQuizMarker.isSet("x2", in: defaults))
        CanvasQuizMarker.clear("x1", in: defaults)
        #expect(!CanvasQuizMarker.isSet("x1", in: defaults))
    }

    @Test func lockInNeedsTheQuizPhase() {
        var scoped = browser(kind: "assignments")
        #expect(!scoped.isLockedIn)
        _ = scoped.committed(page("/courses/123/assignments/456"))
        #expect(scoped.isLockedIn)
    }

    // MARK: - Armed finish

    private let classicFinish = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)

    @Test func landingThenHistoryDoesNotFinish() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456"))
        _ = b.committed(page("/courses/123/quizzes/456/history?version=1"))
        #expect(!b.armed)
        #expect(!b.isFinish(page("/courses/123/quizzes/456/history?version=1"), regex: classicFinish))
        #expect(!b.isFinish(page("/courses/123/quizzes/456/submissions/5"), regex: classicFinish))
    }

    @Test func takeThenSubmissionFinishes() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456"))
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.armed)
        _ = b.committed(page("/courses/123/quizzes/456/submissions/5"))
        #expect(b.armed)
        #expect(b.isFinish(page("/courses/123/quizzes/456/submissions/5"), regex: classicFinish))
        #expect(!b.isFinish(page("/courses/123/quizzes/456/take"), regex: classicFinish))
    }

    @Test func landingAndMyQuizDisarm() {
        var b = browser()
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.armed)
        _ = b.committed(page("/courses/123/quizzes/456/"))
        #expect(!b.armed)
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.armed)
        b.disarm(keepingOn: nil, regex: nil)
        #expect(!b.armed)
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        _ = b.committed(page("/courses/123/quizzes/456/history"))
        #expect(b.armed)
        _ = b.committed(page("/courses/123/quizzes/4567"))
        #expect(b.armed)
    }

    @Test func assignmentPagesArm() {
        var b = browser(kind: "assignments")
        #expect(!b.armed)
        _ = b.committed(page("/courses/123/assignments/456"))
        #expect(b.armed)
        let assignment = CanvasFinish.compile(#"/assignments/\d+/submissions/\d+"#)
        #expect(b.isFinish(page("/courses/123/assignments/456/submissions/3"), regex: assignment))
    }

    @Test func armedStateComesBackFromTheMarker() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        #expect(!CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        CanvasQuizMarker.set("a1b2c3", in: defaults)
        CanvasQuizMarker.setArmed(true, "a1b2c3", in: defaults)
        let restored = browser(quizStarted: CanvasQuizMarker.isSet("a1b2c3", in: defaults),
                               armed: CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        #expect(restored.phase == .quiz && restored.armed)
        #expect(restored.isFinish(page("/courses/123/quizzes/456/submissions/5"), regex: classicFinish))
        CanvasQuizMarker.setArmed(false, "a1b2c3", in: defaults)
        let disarmed = browser(quizStarted: true, armed: CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        #expect(!disarmed.armed)
        CanvasQuizMarker.setArmed(true, "a1b2c3", in: defaults)
        CanvasQuizMarker.clear("a1b2c3", in: defaults)
        #expect(!CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(!CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        let notStarted = browser(quizStarted: false, armed: true)
        #expect(!notStarted.armed)
    }

    @Test func restoredArmedStateSurvivesTheFirstLanding() {
        var b = browser(quizStarted: true, armed: true)
        _ = b.committed(page("/courses/123/quizzes/456"))
        #expect(b.armed)
        _ = b.committed(page("/courses/123/quizzes/456"))
        #expect(!b.armed)
        var plain = browser(quizStarted: true, armed: false)
        _ = plain.committed(page("/courses/123/quizzes/456"))
        #expect(!plain.armed)
    }

    @Test func disarmKeepsAnArmedFinishPage() {
        var b = browser(quizStarted: true, armed: true)
        b.disarm(keepingOn: page("/courses/123/quizzes/456/submissions/5"), regex: classicFinish)
        #expect(b.armed)
        b.disarm(keepingOn: page("/courses/123/quizzes/456/take"), regex: classicFinish)
        #expect(!b.armed)
        var c = browser(quizStarted: true, armed: true)
        c.disarm(keepingOn: nil, regex: classicFinish)
        #expect(!c.armed)
    }

    @Test func nonHttpsPagesAreIgnoredByCommit() {
        var b = browser()
        let http = b.committed(URL(string: "http://canvas.dku.edu/courses/123/quizzes/456/take")!)
        #expect(!http && b.phase == .lobby && !b.armed)
        let blank = b.committed(URL(string: "about:blank"))
        #expect(!blank)
    }

    @Test func offHostPagesNeedHttpsInTheQuizPhase() {
        let sso = CanvasNavigation.decide(url: URL(string: "http://sso.dku.edu/x")!, examHost: host, scope: scope(), phase: .quiz)
        #expect(sso == .block)
        let ok = CanvasNavigation.decide(url: URL(string: "https://sso.dku.edu/x")!, examHost: host, scope: scope(), phase: .quiz)
        #expect(ok == .allow)
    }

    @Test func hostPatternsMatchTheMac() {
        for ok in ["a.com", "localhost", "*.a.com", "a_b.example.org", "x-1.a.com", "a"] {
            #expect(CanvasScope.isHostPattern(ok), "\(ok)")
        }
        for bad in ["", "*.", ".a.com", "a.com.", "a..com", "A.com", "a b.com", "a.com:1", "*.*.a.com", "a/b.com"] {
            #expect(!CanvasScope.isHostPattern(bad), "\(bad)")
        }
    }

    @Test func anUnusableFinishPatternNeverLocksIn() {
        var b = browser(finishUsable: false)
        _ = b.committed(page("/courses/123/quizzes/456/take"))
        #expect(b.phase == .quiz)
        #expect(!b.isLockedIn)
        let usable = browser(quizStarted: true)
        #expect(usable.isLockedIn)
    }

    // MARK: - Finish pattern on a real attempt

    @Test func finishPatternsOfTheServerMatchTheirPages() {
        let classic = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        let new = CanvasFinish.compile("submission-confirmation|submission-screen")
        let assignment = CanvasFinish.compile(#"/assignments/\d+/submissions/\d+"#)
        #expect(CanvasFinish.matches(page("/courses/123/quizzes/456/submissions/9"), regex: classic))
        #expect(CanvasFinish.matches(page("/courses/123/quizzes/456/history?version=1"), regex: classic))
        #expect(!CanvasFinish.matches(page("/courses/123/quizzes/456/take"), regex: classic))
        #expect(CanvasFinish.matches(page("/courses/123/assignments/456/submission-confirmation"), regex: new))
        #expect(!CanvasFinish.matches(page("/courses/123/assignments/456"), regex: new))
        #expect(CanvasFinish.matches(page("/courses/123/assignments/456/submissions/3"), regex: assignment))
        #expect(!CanvasFinish.matches(page("/courses/123/assignments/456"), regex: assignment))
    }

    // MARK: - Page scripts

    @Test func pageGuardStaysOffTheExamServerAndWindowOpenOffSubFrames() {
        let js = CanvasScripts.pageGuard(serverOrigin: "https://exam.example.edu")
        #expect(js.contains("location.origin === 'https://exam.example.edu'"))
        #expect(js.contains("window.top === window.self"))
        #expect(js.contains("location.assign"))
        #expect(js.contains("return null;"))
        #expect(js.contains("navigator.share = undefined"))
        #expect(js.contains("window.print = function () {}"))
        let shareAt = js.range(of: "navigator.share")!.lowerBound
        let topAt = js.range(of: "window.top")!.lowerBound
        #expect(shareAt < topAt)
    }
}
