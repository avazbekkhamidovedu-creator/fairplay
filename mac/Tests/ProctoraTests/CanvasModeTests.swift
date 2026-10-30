import Testing
import Foundation
@testable import Proctora

@Suite struct CanvasModeTests {

    private typealias Rule = ExternalExamController.SiteRule

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
        let history = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/take?x=/quizzes/1/history")!, regex: history))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2?next=/quizzes/1/submissions/5")!, regex: history))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2#/quizzes/1/history")!, regex: history))
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/courses/1/quizzes/2/history?version=1")!, regex: history))
        let confirmation = CanvasFinish.compile("submission-confirmation|submission-screen")
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/x?page=submission-screen")!, regex: confirmation))
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/submission-screen?x=1")!, regex: confirmation))
    }

    @Test func finishMatching() {
        let classic = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        let yes = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/submissions/333")!
        let no = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/take")!
        #expect(CanvasFinish.matches(yes, regex: classic))
        #expect(!CanvasFinish.matches(no, regex: classic))
        #expect(!CanvasFinish.matches(yes, regex: nil))
    }

    @Test func popupPlans() {
        let r = rules
        let opener = "canvas.dku.edu"
        func plan(_ s: String?, survives: Bool = false) -> CanvasPopup.Plan {
            CanvasPopup.plan(url: s.flatMap { URL(string: $0) }, openerHost: opener, rules: r,
                             openerSurvivesCrossOrigin: survives)
        }
        #expect(plan(nil) == .child)
        #expect(plan("about:blank") == .child)
        #expect(plan("https://canvas.dku.edu/login") == .child)
        #expect(plan("https://login.microsoftonline.com/x") == .sameView)
        #expect(plan("https://login.microsoftonline.com/x", survives: true) == .child)
        #expect(plan("https://evil.example.com/") == .block)
        #expect(plan("http://canvas.dku.edu/") == .block)
        #expect(CanvasPopup.isCrossHost(URL(string: "https://login.microsoftonline.com/")!, openerHost: opener))
        #expect(!CanvasPopup.isCrossHost(URL(string: "https://canvas.dku.edu/x")!, openerHost: opener))
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
                    signInHosts: ["login.microsoftonline.com", "*.duosecurity.com", "sso.dku.edu", "weird-idp.example.edu"],
                    quizLtiHosts: ["*.quiz-lti-iad-prod.instructure.com"])
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
                     "/courses/123/files/folder/a/b", "/courses/123/wiki/page",
                     "/courses/123/pages/", "/courses/123/quizzes/456/take"] {
            #expect(decide(path, .quiz, openBook: true) == .allow, "\(path)")
            let closed: CanvasNavigation.Decision = path.contains("quizzes") ? .allow : .block
            #expect(decide(path, .quiz, openBook: false) == closed, "\(path)")
        }
        for path in ["/courses/123/files/77/download", "/files/77/download", "/files", "/files/77", "/files/77/preview",
                     "/files/folder/users_1", "/files/folder/courses_999", "/files/folder/groups_7", "/courses/123/files/77/download/x",
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
        for h in ["login.microsoftonline.com", "sso.dku.edu", "api-1a2b3c.duosecurity.com", "weird-idp.example.edu",
                  "x.quiz-lti-iad-prod.instructure.com", "SSO.DKU.EDU"] {
            #expect(decide("/anything", .quiz, host: h) == .allow, "\(h)")
            #expect(decide("/anything", .quiz, kind: "assignments", openBook: true, host: h) == .allow, "\(h)")
        }
        for h in ["docs.google.com", "example.com", "canvas.dku.edu.evil.com", "evilcanvas.dku.edu", "a.instructure.com",
                  "authn.example.edu", "accounts.google.com", "idp.example.org", "quiz-lti-pdx-prod.instructure.com",
                  "duosecurity.com", "x.duosecurity.com.evil.com", "quiz-lti-iad-prod.instructure.com",
                  "dku.edu", "files.example.com"] {
            #expect(decide("/anything", .quiz, host: h) == .block, "\(h)")
            #expect(decide("/anything", .quiz, kind: "assignments", openBook: true, host: h) == .block, "\(h)")
        }
        let plain = CanvasNavigation.decide(url: URL(string: "http://sso.dku.edu/x")!, examHost: host,
                                            scope: scope(), phase: .quiz)
        #expect(plain == .block)
        let blank = CanvasNavigation.decide(url: URL(string: "about:blank")!, examHost: host, scope: scope(), phase: .quiz)
        #expect(blank == .block)
        let bare = CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: false)
        let none = CanvasNavigation.decide(url: URL(string: "https://sso.dku.edu/x")!, examHost: host, scope: bare, phase: .quiz)
        #expect(none == .block)
        let lobby = CanvasNavigation.decide(url: URL(string: "https://sso.dku.edu/x")!, examHost: host, scope: bare, phase: .lobby)
        #expect(lobby == .allow)
    }

    @Test func hostListParsing() {
        func parse(_ extra: [String: Any]) -> CanvasScope? {
            var d: [String: Any] = ["course_id": "1", "item_kind": "quizzes", "item_id": "2"]
            for (k, v) in extra { d[k] = v }
            return CanvasScope(json: d)
        }
        let ok = parse(["sign_in_hosts": ["Login.Microsoftonline.com", "*.duosecurity.com"], "quiz_lti_hosts": ["*.quiz-lti-iad-prod.instructure.com"]])
        #expect(ok?.signInHosts == ["login.microsoftonline.com", "*.duosecurity.com"])
        #expect(ok?.quizLtiHosts == ["*.quiz-lti-iad-prod.instructure.com"])
        #expect(parse([:])?.signInHosts == [])
        #expect(parse([:])?.quizLtiHosts == [])
        #expect(parse(["sign_in_hosts": [String]()])?.signInHosts == [])
        #expect(parse(["sign_in_hosts": NSNull()])?.signInHosts == [])
        let bad: [Any] = ["x.com", 5, [1], ["a b.com"], ["https://x.com"], ["*"], ["x.com/path"], ["x.com:443"], ["*."], [""],
                          ["a..com"], [".x.com"], ["x.com."], ["*.*.x.com"], ["é.com"], [["x"]],
                          Array(repeating: "x.com", count: CanvasScope.maxHostPatterns + 1)]
        for value in bad {
            #expect(parse(["sign_in_hosts": value]) == nil)
            #expect(parse(["quiz_lti_hosts": value]) == nil)
        }
    }

    @Test func hostPatternMatching() {
        let patterns = ["login.example.com", "*.duosecurity.com"]
        #expect(CanvasScope.hostMatches("login.example.com", patterns: patterns))
        #expect(CanvasScope.hostMatches("LOGIN.example.com", patterns: patterns))
        #expect(CanvasScope.hostMatches("api-1.duosecurity.com", patterns: patterns))
        #expect(CanvasScope.hostMatches("a.b.duosecurity.com", patterns: patterns))
        #expect(!CanvasScope.hostMatches("duosecurity.com", patterns: patterns))
        #expect(!CanvasScope.hostMatches("evilduosecurity.com", patterns: patterns))
        #expect(!CanvasScope.hostMatches("x.login.example.com", patterns: patterns))
        #expect(!CanvasScope.hostMatches("login.example.com.evil.com", patterns: patterns))
        #expect(!CanvasScope.hostMatches("login.example.com", patterns: []))
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
}

@MainActor @Suite struct CanvasControllerTests {

    private func canvasBody(scope: Any? = ["course_id": "123", "item_kind": "quizzes", "item_id": "456",
                                           "open_book": false, "sign_in_hosts": ["login.microsoftonline.com", "sso.dku.edu"],
                                           "quiz_lti_hosts": ["*.quiz-lti-iad-prod.instructure.com"]] as [String: Any],
                            url: String = "https://canvas.dku.edu/courses/123/quizzes/456") -> [String: Any] {
        var body: [String: Any] = [
            "attempt_id": "a1b2c3", "url": url, "token": "tok12345", "session": "sess12345",
            "kind": "canvas", "title": "Quiz", "allowed_domains": ["canvas.dku.edu", "login.microsoftonline.com"],
            "finish_url_regex": #"/quizzes/\d+/submissions/\d+"#
        ]
        if let scope { body["canvas_scope"] = scope }
        return body
    }

    private func makeController() -> ExternalExamController {
        let c = ExternalExamController(server: URL(string: "https://exam.example.com")!)
        c.defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString) ?? .standard
        c.clearsWebData = false
        c.networkEnabled = false
        return c
    }

    private final class Recorder {
        var actions: [CanvasViewAction] = []
        var clock = Date(timeIntervalSince1970: 10_000)
    }

    private func record(_ c: ExternalExamController) -> Recorder {
        let r = Recorder()
        c.actionObserver = { r.actions.append($0) }
        c.now = { r.clock }
        return r
    }

    private func plainBody() -> [String: Any] {
        var body = canvasBody(scope: nil)
        body["kind"] = nil
        body["url"] = "https://canvas.dku.edu/exam"
        body["finish_url_regex"] = nil
        return body
    }

    private func page(_ path: String) -> URL { URL(string: "https://canvas.dku.edu" + path)! }

    @Test func startValidatesTheScope() {
        let c = makeController()
        defer { c.abort() }
        let started1 = c.start(body: canvasBody())
        #expect(started1 == nil)
        c.abort()
        c.abort()
        let started2 = c.start(body: canvasBody(scope: nil))
        #expect(started2 == "bad_request")
        let started3 = c.start(body: canvasBody(scope: NSNull()))
        #expect(started3 == "bad_request")
        #expect(!c.isActive)
        let bad: [Any] = [
            "x", 5, [String: Any](),
            ["course_id": "12a", "item_kind": "quizzes", "item_id": "456"] as [String: Any],
            ["course_id": "123", "item_kind": "pages", "item_id": "456"] as [String: Any],
            ["course_id": "123", "item_kind": "quizzes"] as [String: Any]
        ]
        for scope in bad {
            let result = c.start(body: canvasBody(scope: scope))
            #expect(result == "bad_request")
            #expect(!c.isActive)
        }
        let good: [String: Any] = ["course_id": "123", "item_kind": "quizzes", "item_id": "456"]
        let wrongItem = c.start(body: canvasBody(scope: good, url: "https://canvas.dku.edu/courses/123/quizzes/4567"))
        #expect(wrongItem == "bad_request")
        let wrongCourse = c.start(body: canvasBody(scope: good, url: "https://canvas.dku.edu/courses/12/quizzes/456"))
        #expect(wrongCourse == "bad_request")
        let elsewhere = c.start(body: canvasBody(scope: good, url: "https://canvas.dku.edu/inbox"))
        #expect(elsewhere == "bad_request")
        let wrongKind = c.start(body: canvasBody(scope: good, url: "https://canvas.dku.edu/courses/123/assignments/456"))
        #expect(wrongKind == "bad_request")
        let sub = c.start(body: canvasBody(scope: good, url: "https://canvas.dku.edu/courses/123/quizzes/456/edit"))
        #expect(sub == nil)
        #expect(c.isActive)
    }

    @Test func phaseAndPolicyFollowCommittedPages() {
        let c = makeController()
        defer { c.abort() }
        let started4 = c.start(body: canvasBody())
        #expect(started4 == nil)
        #expect(c.phase == .lobby)
        #expect(!c.blocksMainFrame(page("/")))
        #expect(!c.blocksMainFrame(page("/courses/123")))
        #expect(c.blocksMainFrame(page("/courses/999")))
        #expect(c.blocksMainFrame(page("/inbox")))
        #expect(c.allowsMainFrame(page("/courses/123/quizzes/456")))
        #expect(!c.allowsMainFrame(URL(string: "https://evil.example.com/")!))
        c.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(c.phase == .lobby)
        c.canvasCommitted(page("/courses/123/quizzes/4567/take"))
        #expect(c.phase == .lobby)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.phase == .quiz)
        #expect(c.blocksMainFrame(page("/")))
        #expect(c.blocksMainFrame(page("/courses/123")))
        #expect(c.blocksMainFrame(page("/courses/123/quizzes")))
        #expect(!c.blocksMainFrame(page("/courses/123/quizzes/456/take")))
        #expect(!c.blocksMainFrame(page("/login/saml")))
        #expect(!c.blocksMainFrame(URL(string: "https://login.microsoftonline.com/x")!))
        c.canvasCommitted(page("/"))
        #expect(c.phase == .quiz)
        c.abort()
        let started5 = c.start(body: canvasBody())
        #expect(started5 == nil)
        #expect(c.phase == .lobby)
    }

    @Test func assignmentScopeEntersQuizOnAnyItemPage() {
        let c = makeController()
        defer { c.abort() }
        let scope: [String: Any] = ["course_id": "123", "item_kind": "assignments", "item_id": "456"]
        let started6 = c.start(body: canvasBody(scope: scope, url: "https://canvas.dku.edu/courses/123/assignments/456"))
        #expect(started6 == nil)
        c.canvasCommitted(page("/courses/123/assignments/456"))
        #expect(c.phase == .quiz)
        #expect(c.blocksMainFrame(page("/courses/123")))
    }

    @Test func canvasWithoutAScopeIsRefused() {
        let c = makeController()
        defer { c.abort() }
        let missing = c.start(body: canvasBody(scope: nil))
        #expect(missing == "bad_request")
        let null = c.start(body: canvasBody(scope: NSNull()))
        #expect(null == "bad_request")
        #expect(!c.isActive)
    }

    @Test func plainExternalExamHasNoCanvasRules() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: plainBody())
        #expect(started == nil)
        #expect(!c.isCanvas)
        #expect(!c.blocksMainFrame(page("/inbox")))
        #expect(!c.isLockedIn)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.phase == .lobby)
        #expect(c.popupPlan(url: page("/courses/999/quizzes/1"), openerHost: "canvas.dku.edu") == .block)
    }

    @Test func popupsOnTheCanvasHostLoadInTheMainView() {
        let c = makeController()
        defer { c.abort() }
        let started8 = c.start(body: canvasBody())
        #expect(started8 == nil)
        let opener = "canvas.dku.edu"
        #expect(c.popupPlan(url: page("/courses/123/quizzes/456"), openerHost: opener) == .sameView)
        #expect(c.popupPlan(url: page("/login/saml"), openerHost: opener) == .sameView)
        #expect(c.popupPlan(url: page("/"), openerHost: "login.microsoftonline.com") == .sameView)
        #expect(c.popupPlan(url: page("/inbox"), openerHost: opener) == .block)
        #expect(c.popupPlan(url: page("/courses/999"), openerHost: opener) == .block)
        #expect(c.popupPlan(url: URL(string: "https://evil.example.com/")!, openerHost: opener) == .block)
        #expect(c.popupPlan(url: URL(string: "http://canvas.dku.edu/courses/123/quizzes/456")!, openerHost: opener) == .block)
        #expect(c.popupPlan(url: URL(string: "about:blank"), openerHost: opener) == .child)
        #expect(c.popupPlan(url: nil, openerHost: opener) == .child)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.popupPlan(url: page("/courses/123/pages/x"), openerHost: opener) == .block)
        #expect(c.popupPlan(url: page("/courses/123/quizzes/456/take"), openerHost: opener) == .sameView)
    }

    @Test func backPredicate() {
        let c = makeController()
        defer { c.abort() }
        let started9 = c.start(body: canvasBody())
        #expect(started9 == nil)
        #expect(c.backAllowed(page("/")))
        #expect(c.backAllowed(page("/login/saml")))
        #expect(c.backAllowed(page("/courses/123")))
        #expect(c.backAllowed(page("/courses/123/quizzes/456")))
        #expect(!c.backAllowed(page("/inbox")))
        #expect(!c.backAllowed(URL(string: "https://exam.example.com/student/external/a1b2c3")!))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.backAllowed(page("/courses/123/quizzes/456")))
        #expect(c.backAllowed(page("/courses/123/quizzes/456/take")))
        #expect(!c.backAllowed(page("/")))
        #expect(!c.backAllowed(page("/login")))
        #expect(!c.backAllowed(page("/login/saml")))
        #expect(!c.backAllowed(URL(string: "https://login.microsoftonline.com/x")!))
        #expect(!c.backAllowed(page("/courses/999/quizzes/456")))
    }

    @Test func blockDetailCarriesNoQuery() {
        let c = makeController()
        defer { c.abort() }
        let started10 = c.start(body: canvasBody())
        #expect(started10 == nil)
        let detail = c.blockDetail(page("/inbox?token=secret#frag"))
        #expect(detail == "canvas page /inbox")
        let long = c.blockDetail(page("/" + String(repeating: "a", count: 400)))
        #expect(long.count <= 140)
    }

    @Test func recoveryNeverStopsAfterTheLastTry() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        let r = record(c)
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        let blocked = page("/inbox")
        for _ in 0..<ExternalExamController.maxRecoveries {
            r.clock = r.clock.addingTimeInterval(2)
            c.recoverIfBlocked(blocked)
        }
        #expect(r.actions == Array(repeating: CanvasViewAction.load(start), count: ExternalExamController.maxRecoveries))
        r.clock = r.clock.addingTimeInterval(2)
        c.recoverIfBlocked(blocked)
        #expect(r.actions.count == ExternalExamController.maxRecoveries + 1)
        #expect(r.actions.last == CanvasViewAction.load(start))
        r.clock = r.clock.addingTimeInterval(2)
        c.recoverIfBlocked(blocked)
        r.clock = r.clock.addingTimeInterval(2)
        c.recoverIfBlocked(blocked)
        #expect(r.actions.count == ExternalExamController.maxRecoveries + 1)
        c.recoverIfBlocked(page("/courses/123/quizzes/456/take"))
        r.clock = r.clock.addingTimeInterval(2)
        c.recoverIfBlocked(blocked)
        #expect(r.actions.count == ExternalExamController.maxRecoveries + 2)
    }

    @Test func recoveryIsRateLimitedAndIgnoresAllowedPages() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        c.recoverIfBlocked(page("/courses/123/quizzes/456"))
        c.recoverIfBlocked(URL(string: "https://evil.example.com/")!)
        c.recoverIfBlocked(nil)
        #expect(r.actions.isEmpty)
        c.recoverIfBlocked(page("/inbox"))
        c.recoverIfBlocked(page("/inbox"))
        c.recoverIfBlocked(page("/calendar"))
        #expect(r.actions.count == 1)
    }

    @Test func recoveryNeedsAScope() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: plainBody())
        #expect(started == nil)
        let r = record(c)
        c.recoverIfBlocked(page("/inbox"))
        #expect(r.actions.isEmpty)
    }

    @Test func examLinkOpensOnceAfterSignIn() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.isEmpty)
        c.canvasCommitted(page("/login/saml"))
        c.autoOpenIfNeeded(url: page("/courses/123/pages"))
        c.autoOpenIfNeeded(url: page("/courses/12"))
        #expect(r.actions.isEmpty)
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions == [.load(start)])
        c.autoOpenIfNeeded(url: page("/"))
        c.autoOpenIfNeeded(url: page("/courses/123"))
        #expect(r.actions == [.load(start)])
        let again = c.start(body: canvasBody())
        #expect(again == nil)
        c.canvasCommitted(page("/login"))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions == [.load(start)])
    }

    @Test func examLinkOpensOnlyWithAScope() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: plainBody())
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(page("/login"))
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.isEmpty)
    }

    @Test func canvasFinishIgnoresTheContainsText() {
        let c = makeController()
        defer { c.abort() }
        var body = canvasBody()
        body["finish_url_contains"] = "/take"
        let started = c.start(body: body)
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(!c.isFinishPage(page("/courses/123/quizzes/456/take")))
        #expect(c.isFinishPage(page("/courses/123/quizzes/456/submissions/9")))
        c.abort()
        var plain = canvasBody()
        plain["kind"] = nil
        plain["finish_url_contains"] = "/done"
        plain["url"] = "https://canvas.dku.edu/exam"
        plain["canvas_scope"] = nil
        let startedPlain = c.start(body: plain)
        #expect(startedPlain == nil)
        #expect(c.isFinishPage(URL(string: "https://canvas.dku.edu/exam/done")!))
        #expect(!c.isFinishPage(URL(string: "https://canvas.dku.edu/exam/other")!))
    }

    @Test func unsafeFinishPatternStillStarts() {
        let c = makeController()
        defer { c.abort() }
        var body = canvasBody()
        body["finish_url_regex"] = "(a+)+$"
        let started = c.start(body: body)
        #expect(started == nil)
        #expect(!c.isFinishPage(page("/courses/123/quizzes/456/submissions/9")))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.phase == .quiz)
        #expect(!c.isLockedIn)
    }

    @Test func missingFinishPatternDoesNotLockIn() {
        let c = makeController()
        defer { c.abort() }
        var body = canvasBody()
        body["finish_url_regex"] = nil
        let started = c.start(body: body)
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(!c.isLockedIn)
    }

    @Test func validFinishPatternLocksIn() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.isLockedIn)
    }

    @Test func framesThatMatchTheFinishPatternFinishTheExam() {
        let lti = "x.quiz-lti-iad-prod.instructure.com"
        var body = canvasBody()
        body["finish_url_regex"] = "submission-confirmation|submission-screen"
        body["allowed_domains"] = ["canvas.dku.edu", lti]
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: body)
        #expect(started == nil)
        c.checkFrameFinish(URL(string: "https://\(lti)/session/1/submission-screen")!)
        #expect(!c.finishScheduled)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        c.checkFrameFinish(URL(string: "https://\(lti)/session/1/take")!)
        #expect(!c.finishScheduled)
        c.checkFrameFinish(URL(string: "https://evil.example.com/submission-screen")!)
        #expect(!c.finishScheduled)
        c.checkFrameFinish(URL(string: "https://\(lti)/session/1/submission-screen?x=1#y")!)
        #expect(c.finishScheduled)
        c.abort()
        #expect(!c.finishScheduled)
    }

    @Test func frameFinishNeedsACanvasSession() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: plainBody())
        #expect(started == nil)
        c.checkFrameFinish(page("/courses/123/quizzes/456/submissions/9"))
        #expect(!c.finishScheduled)
    }

    @Test func offHostPagesInTheQuizPhaseNeedASignInOrQuizHost() {
        var body = canvasBody()
        body["allowed_domains"] = ["canvas.dku.edu", "login.microsoftonline.com", "sso.dku.edu",
                                   "x.quiz-lti-iad-prod.instructure.com", "docs.google.com"]
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: body)
        #expect(started == nil)
        let docs = URL(string: "https://docs.google.com/document/d/1")!
        let sso = URL(string: "https://sso.dku.edu/x")!
        let lti = URL(string: "https://x.quiz-lti-iad-prod.instructure.com/s")!
        #expect(!c.blocksMainFrame(docs))
        #expect(c.popupPlan(url: docs, openerHost: "canvas.dku.edu") != .block)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.blocksMainFrame(docs))
        #expect(!c.blocksMainFrame(sso))
        #expect(!c.blocksMainFrame(lti))
        #expect(!c.blocksMainFrame(URL(string: "https://login.microsoftonline.com/x")!))
        #expect(c.popupPlan(url: docs, openerHost: "canvas.dku.edu") == .block)
        #expect(c.popupPlan(url: sso, openerHost: "canvas.dku.edu") != .block)
        #expect(c.popupPlan(url: URL(string: "about:blank"), openerHost: "canvas.dku.edu") == .child)
        #expect(!c.childAllows(docs))
        #expect(c.childAllows(sso))
        #expect(c.childAllows(URL(string: "about:blank")!))
    }

    @Test func nonHttpsCommitsAreIgnored() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(URL(string: "about:blank"))
        c.canvasCommitted(URL(string: "data:text/html,hi")!)
        c.canvasCommitted(URL(string: "http://canvas.dku.edu/courses/123/quizzes/456/take")!)
        #expect(c.phase == .lobby)
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.isEmpty)
        c.canvasCommitted(page("/"))
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.count == 1)
    }

    @Test func warmSessionLandingOpensTheExamLink() {
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        for landing in ["/", "/courses/123"] {
            let c = makeController()
            defer { c.abort() }
            let started = c.start(body: canvasBody())
            #expect(started == nil)
            let r = record(c)
            c.canvasCommitted(page(landing))
            c.autoOpenIfNeeded(url: page(landing))
            #expect(r.actions == [.load(start)], "\(landing)")
            c.autoOpenIfNeeded(url: page(landing))
            c.canvasCommitted(page("/"))
            c.autoOpenIfNeeded(url: page("/"))
            #expect(r.actions == [.load(start)], "\(landing)")
        }
    }

    @Test func landingOnTheExamLinkNeedsNoAutoOpen() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(page("/courses/123/quizzes/456"))
        c.autoOpenIfNeeded(url: page("/courses/123/quizzes/456"))
        c.canvasCommitted(page("/"))
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.isEmpty)
    }

    @Test func onlyTheFirstPageCountsAsAWarmLanding() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(page("/courses/123/pages/x"))
        c.canvasCommitted(page("/"))
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.isEmpty)
        let again = c.start(body: canvasBody())
        #expect(again == nil)
        c.canvasCommitted(URL(string: "https://other.example.com/")!)
        c.autoOpenIfNeeded(url: page("/"))
        #expect(r.actions.count == 1)
    }

    @Test func quizPhaseSurvivesARestart() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        let first = makeController()
        first.defaults = defaults
        let started = first.start(body: canvasBody())
        #expect(started == nil)
        #expect(first.phase == .lobby)
        #expect(!first.isLockedIn)
        first.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(first.phase == .quiz)
        #expect(first.isLockedIn)
        #expect(CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        let second = makeController()
        second.defaults = defaults
        let restarted = second.start(body: canvasBody())
        #expect(restarted == nil)
        #expect(second.phase == .quiz)
        #expect(second.isLockedIn)
        #expect(second.blocksMainFrame(page("/")))
        let again = second.start(body: canvasBody())
        #expect(again == nil)
        #expect(second.phase == .quiz)
        second.abort()
        #expect(!CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        let third = makeController()
        third.defaults = defaults
        let fresh = third.start(body: canvasBody())
        #expect(fresh == nil)
        #expect(third.phase == .lobby)
        first.abort()
        third.abort()
    }

    @Test func finishClearsTheQuizMarker() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        let c = makeController()
        c.defaults = defaults
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        c.finish(reason: "finish_url")
        #expect(!c.isActive)
        #expect(CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        c.externalDonePageLoaded(URL(string: "https://exam.example.com/student/external-done/zzz9")!)
        c.externalDonePageLoaded(URL(string: "https://evil.example.com/student/external-done/a1b2c3")!)
        c.externalDonePageLoaded(nil)
        #expect(CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        c.externalDonePageLoaded(URL(string: "https://exam.example.com/student/external-done/a1b2c3?reason=finish_url")!)
        #expect(!CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(!CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
    }

    @Test func aRestartedAttemptKeepsItsMarkerAfterAnUnfinishedHandIn() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        let c = makeController()
        c.defaults = defaults
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        c.finish(reason: "finish_url")
        let again = c.start(body: canvasBody())
        #expect(again == nil)
        c.externalDonePageLoaded(URL(string: "https://exam.example.com/student/external-done/a1b2c3")!)
        #expect(CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        #expect(c.phase == .quiz)
        c.abort()
    }

    @Test func markerIsPerAttemptAndNeedsAScope() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        CanvasQuizMarker.set("other1", in: defaults)
        let c = makeController()
        c.defaults = defaults
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        #expect(c.phase == .lobby)
        c.abort()
        CanvasQuizMarker.set("a1b2c3", in: defaults)
        let noScope = c.start(body: plainBody())
        #expect(noScope == nil)
        #expect(c.phase == .lobby)
        #expect(!c.isLockedIn)
        c.abort()
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

    @Test func lockInNeedsTheQuizPhaseAndAScope() {
        let c = makeController()
        defer { c.abort() }
        #expect(!c.isLockedIn)
        let started = c.start(body: plainBody())
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(!c.isLockedIn)
        let assignment: [String: Any] = ["course_id": "123", "item_kind": "assignments", "item_id": "456"]
        let scoped = c.start(body: canvasBody(scope: assignment, url: "https://canvas.dku.edu/courses/123/assignments/456"))
        #expect(scoped == nil)
        #expect(!c.isLockedIn)
        c.canvasCommitted(page("/courses/123/assignments/456"))
        #expect(c.isLockedIn)
    }

    @Test func backStaysInsideTheItemDuringTheQuiz() {
        let c = makeController()
        defer { c.abort() }
        let open: [String: Any] = ["course_id": "123", "item_kind": "quizzes", "item_id": "456", "open_book": true]
        let started = c.start(body: canvasBody(scope: open))
        #expect(started == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(!c.blocksMainFrame(page("/courses/123/pages/x")))
        #expect(!c.backAllowed(page("/courses/123/pages/x")))
        #expect(!c.backAllowed(page("/courses/123/modules")))
        #expect(c.backAllowed(page("/courses/123/quizzes/456")))
        #expect(c.backAllowed(page("/courses/123/quizzes/456/take")))
    }

    @Test func examServerPagesAreBlockedDuringACanvasExam() {
        let hand = URL(string: "https://exam.example.com/student/external-done/123")!
        let others = ["https://exam.example.com/student", "https://exam.example.com/student/exam/a1b2c3",
                      "https://exam.example.com/", "https://exam.example.com/teacher/login",
                      "https://EXAM.example.com:443/student/done/a1b2c3"].map { URL(string: $0)! }
        let c = makeController()
        defer { c.abort() }
        #expect(!c.blocksExamServer(hand))
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        for phase in 0..<2 {
            #expect(c.blocksExamServer(hand))
            #expect(!c.allows(hand))
            #expect(c.blocksMainFrame(page("/")) == (phase == 1))
            for url in others {
                #expect(c.blocksExamServer(url), "\(url)")
                #expect(!c.allows(url), "\(url)")
                #expect(!c.childAllows(url), "\(url)")
                #expect(c.popupPlan(url: url, openerHost: "canvas.dku.edu") == .block)
            }
            #expect(!c.blocksExamServer(page("/courses/123/quizzes/456")))
            #expect(!c.blocksExamServer(URL(string: "https://login.microsoftonline.com/x")!))
            c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        }
        #expect(c.phase == .quiz)
        c.finish(reason: "finish_url")
        #expect(!c.isActive)
        #expect(!c.blocksExamServer(hand))
    }

    @Test func examServerInAllowedDomainsStillBlockedDuringACanvasExam() {
        let c = makeController()
        defer { c.abort() }
        var body = canvasBody()
        body["allowed_domains"] = ["canvas.dku.edu", "*.example.com"]
        let started = c.start(body: body)
        #expect(started == nil)
        let hand = URL(string: "https://exam.example.com/student/external-done/123")!
        #expect(!c.allows(hand))
        #expect(c.popupPlan(url: hand, openerHost: "canvas.dku.edu") == .block)
        #expect(c.allows(URL(string: "https://other.example.com/x")!))
    }

    @Test func nonCanvasExamsKeepTheirServerRules() {
        let c = makeController()
        defer { c.abort() }
        var plain = canvasBody()
        plain["kind"] = nil
        plain["canvas_scope"] = nil
        plain["url"] = "https://canvas.dku.edu/exam"
        let started = c.start(body: plain)
        #expect(started == nil)
        #expect(!c.blocksExamServer(URL(string: "https://exam.example.com/student/external-done/123")!))
        #expect(c.canvasStartURL == nil)
    }

    @Test func finishCountsOnlyWhenArmed() {
        let history = page("/courses/123/quizzes/456/history?version=1")
        let submission = page("/courses/123/quizzes/456/submissions/5")
        let c = makeController()
        defer { c.abort() }
        var body = canvasBody()
        body["finish_url_regex"] = #"/quizzes/\d+/(submissions/\d+|history)"#
        let started = c.start(body: body)
        #expect(started == nil)
        #expect(!c.armed)
        #expect(!c.isFinishPage(submission))
        c.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(!c.armed)
        #expect(!c.isFinishPage(history))
        #expect(!c.isFinishPage(submission))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.armed)
        #expect(c.isFinishPage(submission))
        #expect(c.isFinishPage(history))
        c.canvasCommitted(page("/courses/123/quizzes/456/"))
        #expect(!c.armed)
        #expect(!c.isFinishPage(history))
        #expect(!c.isFinishPage(submission))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.isFinishPage(submission))
    }

    @Test func goToMyQuizDisarms() {
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: canvasBody())
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.armed)
        c.openExamLink()
        #expect(!c.armed)
        #expect(r.actions == [.load(URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!)])
        #expect(!c.isFinishPage(page("/courses/123/quizzes/456/submissions/5")))
    }

    @Test func armedStateSurvivesARestart() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        let submission = page("/courses/123/quizzes/456/submissions/5")
        let first = makeController()
        first.defaults = defaults
        let started = first.start(body: canvasBody())
        #expect(started == nil)
        first.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        let second = makeController()
        second.defaults = defaults
        let restarted = second.start(body: canvasBody())
        #expect(restarted == nil)
        #expect(second.armed)
        #expect(second.isFinishPage(submission))
        second.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(second.armed)
        second.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(!CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        let third = makeController()
        third.defaults = defaults
        let again = third.start(body: canvasBody())
        #expect(again == nil)
        #expect(third.phase == .quiz)
        #expect(!third.armed)
        #expect(!third.isFinishPage(submission))
        third.abort()
        #expect(!CanvasQuizMarker.isArmed("a1b2c3", in: defaults))
        #expect(!CanvasQuizMarker.isSet("a1b2c3", in: defaults))
        first.abort()
        second.abort()
    }

    @Test func assignmentPagesArmTheFinish() {
        let c = makeController()
        defer { c.abort() }
        let scope: [String: Any] = ["course_id": "123", "item_kind": "assignments", "item_id": "456"]
        var body = canvasBody(scope: scope, url: "https://canvas.dku.edu/courses/123/assignments/456")
        body["finish_url_regex"] = #"/assignments/\d+/submissions/\d+"#
        let started = c.start(body: body)
        #expect(started == nil)
        #expect(!c.armed)
        c.canvasCommitted(page("/courses/123/assignments/456"))
        #expect(c.armed)
        #expect(c.isFinishPage(page("/courses/123/assignments/456/submissions/9")))
    }

    @Test func restoredArmedStateSurvivesTheStartPageLoad() {
        let defaults = UserDefaults(suiteName: "fairplay.canvas.tests." + UUID().uuidString)!
        let submission = page("/courses/123/quizzes/456/submissions/5")
        let first = makeController()
        first.defaults = defaults
        let started = first.start(body: canvasBody())
        #expect(started == nil)
        first.canvasCommitted(page("/courses/123/quizzes/456/take"))
        let second = makeController()
        second.defaults = defaults
        let restarted = second.start(body: canvasBody())
        #expect(restarted == nil)
        #expect(second.armed)
        second.canvasCommitted(URL(string: "about:blank"))
        second.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(second.armed)
        #expect(second.isFinishPage(submission))
        second.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(!second.armed)
        first.abort()
        second.abort()
    }

    @Test func aPendingFinishIsNotCancelledByTheQuizLink() {
        let lti = "x.quiz-lti-iad-prod.instructure.com"
        var body = canvasBody()
        body["finish_url_regex"] = "submission-confirmation|submission-screen"
        body["allowed_domains"] = ["canvas.dku.edu", lti]
        let c = makeController()
        defer { c.abort() }
        let started = c.start(body: body)
        #expect(started == nil)
        let r = record(c)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        c.checkFrameFinish(URL(string: "https://\(lti)/session/1/submission-screen")!)
        #expect(c.finishScheduled)
        c.openExamLink()
        #expect(c.armed)
        #expect(r.actions.isEmpty)
        c.canvasCommitted(page("/courses/123/quizzes/456"))
        #expect(c.armed)
        c.abort()
        #expect(!c.finishScheduled)
        let again = c.start(body: body)
        #expect(again == nil)
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        c.openExamLink()
        #expect(!c.armed)
        #expect(r.actions.count == 1)
    }
}
