import Testing
import Foundation
import JavaScriptCore
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

    @Test func finishMatchesPathAndQueryOnly() {
        let re = CanvasFinish.compile(#"/submissions/\d+"#)
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/q/submissions/7?x=1")!, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/q/take#/submissions/7")!, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://submissions.example.edu/q/take")!, regex: re))
        let long = URL(string: "https://c.example.edu/" + String(repeating: "a", count: 600) + "/submissions/7")!
        #expect(!CanvasFinish.matches(long, regex: re))
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

    @Test func fillBudgetStopsAtThree() {
        var b = CanvasFillBudget()
        let first = b.take()
        let second = b.take()
        #expect(first)
        #expect(second)
        #expect(!b.exhausted)
        let third = b.take()
        #expect(third)
        #expect(b.exhausted)
        let fourth = b.take()
        #expect(!fourth)
        #expect(b.used == 3)
    }

    @Test func proofString() {
        #expect(CanvasProof.message(attemptId: "a1b2c3", session: "sess12345", deviceId: "hw-abc", ts: 1_700_000_000_123)
                == "canvascode|a1b2c3|sess12345|hw-abc|1700000000123")
        let body = CanvasProof.body(attemptId: "a1b2c3", session: "s", deviceId: "d", ts: 5) { "sig:" + $0 }
        #expect(body["proof"] as? String == "sig:canvascode|a1b2c3|s|d|5")
        #expect((body["ts"] as? NSNumber)?.int64Value == 5)
    }

    @Test func codeReplyParsing() {
        #expect(CanvasProof.parseCode(status: 200, json: ["code": "Ab3dEf", "valid_for_s": 60]) == "Ab3dEf")
        #expect(CanvasProof.parseCode(status: 403, json: ["code": "Ab3dEf"]) == nil)
        #expect(CanvasProof.parseCode(status: nil, json: nil) == nil)
        #expect(CanvasProof.parseCode(status: 200, json: ["code": "abc"]) == nil)
        #expect(CanvasProof.parseCode(status: 200, json: ["code": String(repeating: "a", count: 65)]) == nil)
        #expect(CanvasProof.parseCode(status: 200, json: ["code": "ab\ncd"]) == nil)
        #expect(CanvasProof.parseCode(status: 200, json: ["code": 12345]) == nil)
    }

    @Test func jsStringRoundTrips() {
        let samples = ["Ab3dEf", "a\"b", "a\\b", "line1\nline2", "</script>", "it's", "x\u{2028}y\u{2029}z",
                       "\"; alert(1); \"", "ключ-коды", "back`tick${1}"]
        let ctx = JSContext()!
        for s in samples {
            let literal = CanvasScripts.jsString(s)
            #expect(ctx.evaluateScript(literal)?.toString() == s)
        }
    }

    @Test func fillHostIsTheStartHostOnly() {
        let start = URL(string: "https://Canvas.DKU.edu/courses/1/quizzes/2")!
        func origin(_ s: String) -> String? {
            CanvasPolicy.fillOrigin(page: URL(string: s)!, start: start)
        }
        #expect(origin("https://canvas.dku.edu/courses/1/quizzes/2/take") == "https://canvas.dku.edu")
        #expect(origin("https://CANVAS.dku.edu/courses/1/quizzes/2") == "https://canvas.dku.edu")
        #expect(origin("https://canvas.dku.edu:443/courses/1/quizzes/2") == "https://canvas.dku.edu")
        #expect(origin("https://canvas.dku.edu:8443/courses/1/quizzes/2") == nil)
        #expect(origin("http://canvas.dku.edu/courses/1/quizzes/2") == nil)
        #expect(origin("https://login.microsoftonline.com/courses/1/quizzes/2") == nil)
        #expect(origin("https://x.instructure.com/courses/1/quizzes/2") == nil)
        #expect(origin("https://x.canvas.dku.edu/courses/1/quizzes/2") == nil)
        #expect(origin("https://canvas.dku.edu.evil.com/courses/1/quizzes/2") == nil)
        #expect(origin("https://canvas.dku.edu./courses/1/quizzes/2") == nil)
        #expect(origin("https://user:pw@canvas.dku.edu/courses/1/quizzes/2") == nil)
        #expect(CanvasPolicy.fillOrigin(page: URL(string: "https://canvas.dku.edu/courses/1/quizzes/2")!,
                                        start: URL(string: "http://canvas.dku.edu/courses/1/quizzes/2")!) == nil)
    }

    @Test func fillOnlyOnQuizPages() {
        let start = URL(string: "https://canvas.dku.edu/courses/1/quizzes/2")!
        func origin(_ s: String) -> String? { CanvasPolicy.fillOrigin(page: URL(string: s)!, start: start) }
        #expect(origin("https://canvas.dku.edu/courses/12/quizzes/34/take") != nil)
        #expect(origin("https://canvas.dku.edu/courses/12/assignments/5") != nil)
        #expect(origin("https://canvas.dku.edu/login/saml") == nil)
        #expect(origin("https://canvas.dku.edu/courses/x/quizzes/1") == nil)
        #expect(origin("https://canvas.dku.edu/courses/1/modules") == nil)
        #expect(origin("https://canvas.dku.edu/x/courses/1/quizzes/2") == nil)
    }

    @Test func retryAfterIsClamped() {
        #expect(CanvasRetry.delay(retryAfter: nil) == 4)
        #expect(CanvasRetry.delay(retryAfter: "abc") == 4)
        #expect(CanvasRetry.delay(retryAfter: "0") == 1)
        #expect(CanvasRetry.delay(retryAfter: "-5") == 1)
        #expect(CanvasRetry.delay(retryAfter: " 7 ") == 7)
        #expect(CanvasRetry.delay(retryAfter: "999") == 20)
        #expect(CanvasRetry.maxRetries == 3)
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

    private func stubContext(origin: String, withButton: Bool = true, pathname: String = "/") -> JSContext {
        let ctx = JSContext()!
        let button = withButton ? "{ disabled: true, removeAttribute: function () {}, click: function () { clicks++; } }" : "null"
        ctx.evaluateScript("""
        var sets = 0, clicks = 0, events = [];
        function HTMLInputElement() {}
        Object.defineProperty(HTMLInputElement.prototype, 'value', {
          configurable: true,
          set: function (v) { this.stored = v; sets++; },
          get: function () { return this.stored; }
        });
        function Event(type, init) { this.type = type; this.bubbles = !!(init && init.bubbles); }
        var button = \(button);
        var el = new HTMLInputElement();
        el.form = { querySelector: function () { return button; } };
        el.dispatchEvent = function (e) { events.push(e.type + ':' + e.bubbles); };
        var document = { querySelector: function () { return el; } };
        var location = { origin: '\(origin)', pathname: '\(pathname)' };
        """)
        return ctx
    }

    @Test func fillScriptRunsInStubDom() {
        let code = "Zq7Xk2Mn"
        let script = CanvasScripts.fill(code: code, origin: "https://canvas.dku.edu")
        let ctx = stubContext(origin: "https://canvas.dku.edu")
        let result = ctx.evaluateScript(script)?.toString() ?? ""
        #expect(result == "clicked")
        #expect(!result.contains(code))
        #expect(ctx.evaluateScript("sets")?.toInt32() == 1)
        #expect(ctx.evaluateScript("clicks")?.toInt32() == 1)
        #expect(ctx.evaluateScript("events.join(',')")?.toString() == "input:true,change:true")
        #expect(ctx.evaluateScript("el.stored")?.toString() == code)
        #expect(ctx.evaluateScript("button.disabled")?.toBool() == false)
    }

    @Test func fillScriptRefusesOtherOrigins() {
        let code = "Zq7Xk2Mn"
        let script = CanvasScripts.fill(code: code, origin: "https://canvas.dku.edu")
        let ctx = stubContext(origin: "https://evil.example.com")
        let result = ctx.evaluateScript(script)?.toString() ?? ""
        #expect(result == "wrongorigin")
        #expect(!result.contains(code))
        #expect(ctx.evaluateScript("sets")?.toInt32() == 0)
        #expect(ctx.evaluateScript("clicks")?.toInt32() == 0)
        #expect(ctx.evaluateScript("events.length")?.toInt32() == 0)
    }

    @Test func fillScriptReportsMissingButtonWithoutTheCode() {
        let code = "Zq7Xk2Mn"
        let ctx = stubContext(origin: "https://canvas.dku.edu", withButton: false)
        let result = ctx.evaluateScript(CanvasScripts.fill(code: code, origin: "https://canvas.dku.edu"))?.toString() ?? ""
        #expect(result == "nobutton")
        #expect(!result.contains(code))
    }

    @Test func fillScriptShape() {
        let probe = CanvasScripts.probe
        #expect(probe.contains("input#quiz_access_code"))
        #expect(probe.contains("input[name=access_code]"))
        let script = CanvasScripts.fill(code: "Zq7Xk2", origin: "https://canvas.dku.edu")
        #expect(script.contains("location.origin !== "))
        #expect(script.contains("\"https:\\/\\/canvas.dku.edu\"") || script.contains("\"https://canvas.dku.edu\""))
        #expect(script.contains("\"Zq7Xk2\""))
        #expect(script.contains("'input'"))
        #expect(script.contains("'change'"))
        #expect(script.contains("btn.click()"))
        #expect(script.contains("button[type=submit], input[type=submit]"))
        #expect(script.contains("button:not([type])"))
        #expect(!script.contains(".btn-primary"))
        #expect(!script.contains("return v"))
    }

    // MARK: - Restricted Canvas browser

    private let host = "canvas.dku.edu"

    private func scope(_ kind: String = "quizzes", openBook: Bool = false) -> CanvasScope {
        CanvasScope(courseId: "123", itemKind: kind, itemId: "456", openBook: openBook)
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
            #expect(decide(path, .lobby) == .block, path)
        }
        #expect(decide("/courses/123/quizzes/456/Take", .lobby) == .allow)
        for path in ["/Inbox", "/INBOX/1", "/Conversations", "/API/v1/x", "/Users/self", "/CALENDAR",
                     "/Courses/999/pages"] {
            #expect(decide(path, .lobby) == .block, path)
            #expect(decide(path, .quiz, openBook: true) == .block, path)
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
            #expect(decide(path, .quiz) == .allow, path)
        }
        for path in ["/login", "/login/", "/login/saml", "/login/session_token?return_to=/inbox"] {
            #expect(decide(path, .quiz) == .allow, path)
            #expect(decide(path, .quiz, kind: "assignments") == .allow, path)
        }
        for path in ["/loginx", "/Login", "/login/../inbox", "/login/%2e%2e/inbox", "/login%2f..%2finbox"] {
            #expect(decide(path, .quiz) == .block, path)
        }
        for path in ["/", "/courses/123", "/courses/123/quizzes", "/courses/123/assignments",
                     "/courses/123/pages/x", "/courses/123/modules", "/courses/123/files/1", "/courses/123/wiki/x",
                     "/files/1", "/courses/12", "/courses/123/quizzes/4567", "/courses/123/quizzes/456x",
                     "/courses/999/quizzes/456", "/conversations", "/inbox", "/profile", "/groups/1", "/calendar",
                     "/accounts/1", "/api/v1/users/self", "/users/1", "/dashboard"] {
            #expect(decide(path, .quiz) == .block, path)
        }
        #expect(decide("/courses/123/assignments/456/anything", .quiz, kind: "assignments") == .allow)
        #expect(decide("/courses/123/quizzes/456", .quiz, kind: "assignments") == .block)
    }

    @Test func openBookAddsCourseMaterialOnly() {
        for path in ["/courses/123/pages", "/courses/123/pages/week-1", "/courses/123/modules",
                     "/courses/123/modules/items/5", "/courses/123/files", "/courses/123/files/77",
                     "/courses/123/files/folder/a/b", "/courses/123/wiki/page", "/files/77", "/files/77/preview",
                     "/courses/123/pages/", "/courses/123/quizzes/456/take"] {
            #expect(decide(path, .quiz, openBook: true) == .allow, path)
            let closed: CanvasNavigation.Decision = path.contains("quizzes") ? .allow : .block
            #expect(decide(path, .quiz, openBook: false) == closed, path)
        }
        for path in ["/courses/123/files/77/download", "/files/77/download", "/courses/123/files/77/download/x",
                     "/courses/12/pages/x", "/courses/1234/modules", "/courses/999/files/1", "/courses/123/pagesx",
                     "/courses/123/modulesx/1", "/courses/123/filesx", "/courses/123/grades", "/courses/123/users",
                     "/courses/123/discussion_topics", "/courses/123/quizzes", "/courses/123/assignments",
                     "/courses/123", "/filesx/1", "/file/1", "/",
                     "/courses/123/pages/../../999/pages", "/courses/123/files/1%2f..%2f..",
                     "/courses/123/modules/%2e%2e/x"] {
            #expect(decide(path, .quiz, openBook: true) == .block, path)
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

    @Test func otherHostsAreLeftToTheSiteRules() {
        #expect(decide("/inbox", .lobby, host: "login.microsoftonline.com") == .allow)
        #expect(decide("/anything", .quiz, host: "x.quiz-lti-iad-prod.instructure.com") == .allow)
        #expect(decide("/inbox", .quiz, host: "canvas.dku.edu.evil.com") == .allow)
        #expect(decide("/inbox", .lobby, host: "CANVAS.dku.edu") == .block)
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

    @Test func fillGateWithScope() {
        let start = URL(string: "https://canvas.dku.edu/courses/123/quizzes/456")!
        let sc = scope()
        func origin(_ s: String, _ scope: CanvasScope?) -> String? {
            CanvasPolicy.fillOrigin(page: URL(string: s)!, start: start, scope: scope)
        }
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/456", sc) == "https://canvas.dku.edu")
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/456/take", sc) == "https://canvas.dku.edu")
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/456/", sc) == "https://canvas.dku.edu")
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/457", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/4567", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/12/quizzes/456", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/123/assignments/456", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/456/../457", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/456%2f..%2f457", sc) == nil)
        #expect(origin("https://canvas.dku.edu/login", sc) == nil)
        #expect(origin("https://x.instructure.com/courses/123/quizzes/456", sc) == nil)
        #expect(origin("https://canvas.dku.edu/courses/123/quizzes/457", nil) != nil)
    }

    @Test func fillScriptChecksThePath() {
        let code = "Zq7Xk2Mn"
        let prefix = "/courses/123/quizzes/456"
        let script = CanvasScripts.fill(code: code, origin: "https://canvas.dku.edu", pathPrefix: prefix)
        let cases: [(String, String)] = [
            ("/courses/123/quizzes/456", "clicked"), ("/courses/123/quizzes/456/take", "clicked"),
            ("/courses/123/quizzes/4567", "wrongpath"), ("/courses/123/quizzes/457", "wrongpath"),
            ("/courses/12/quizzes/456", "wrongpath"), ("/", "wrongpath"),
            ("/x/courses/123/quizzes/456", "wrongpath")
        ]
        for (path, expected) in cases {
            let ctx = stubContext(origin: "https://canvas.dku.edu", pathname: path)
            let result = ctx.evaluateScript(script)?.toString() ?? ""
            #expect(result == expected, "\(path)")
            if expected != "clicked" {
                #expect(ctx.evaluateScript("sets")?.toInt32() == 0)
                #expect(ctx.evaluateScript("clicks")?.toInt32() == 0)
            }
        }
        let other = stubContext(origin: "https://evil.example.com", pathname: prefix)
        #expect(other.evaluateScript(script)?.toString() == "wrongorigin")
        #expect(CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: false).itemPath == prefix)
    }

    @Test func downloadQueriesAreBlocked() {
        for q in ["?download=1", "?download", "?Download=1", "?DOWNLOAD_FRD=1", "?download_frd=1", "?x=1&download_frd=1",
                  "?x=1&Download=true#frag"] {
            #expect(decide("/courses/123/files/77" + q, .quiz, openBook: true) == .block, q)
            #expect(decide("/files/77" + q, .quiz, openBook: true) == .block, q)
            #expect(decide("/courses/123/quizzes/456" + q, .lobby) == .block, q)
        }
        #expect(decide("/courses/123/files/77?wrap=1&x=download", .quiz, openBook: true) == .allow)
        #expect(decide("/courses/123/files/77?downloads=1", .quiz, openBook: true) == .allow)
        #expect(decide("/courses/123/files/77?wrap=1", .quiz, openBook: true) == .allow)
    }
}

@MainActor @Suite struct CanvasControllerTests {

    private func canvasBody(scope: Any? = ["course_id": "123", "item_kind": "quizzes", "item_id": "456",
                                           "open_book": false] as [String: Any],
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

    private func page(_ path: String) -> URL { URL(string: "https://canvas.dku.edu" + path)! }

    @Test func startValidatesTheScope() {
        let c = makeController()
        defer { c.abort() }
        let started1 = c.start(body: canvasBody())
        #expect(started1 == nil)
        c.abort()
        let started2 = c.start(body: canvasBody(scope: nil))
        #expect(started2 == nil)
        c.abort()
        let started3 = c.start(body: canvasBody(scope: NSNull()))
        #expect(started3 == nil)
        c.abort()
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

    @Test func withoutScopeNothingIsRestricted() {
        let c = makeController()
        defer { c.abort() }
        let started7 = c.start(body: canvasBody(scope: nil))
        #expect(started7 == nil)
        #expect(!c.blocksMainFrame(page("/inbox")))
        #expect(!c.blocksMainFrame(page("/courses/999/quizzes/1")))
        #expect(c.allows(page("/courses/999/quizzes/1")))
        #expect(c.childAllows(page("/inbox")))
        c.canvasCommitted(page("/courses/123/quizzes/456/take"))
        #expect(c.phase == .lobby)
        #expect(c.popupPlan(url: page("/courses/999/quizzes/1"), openerHost: "canvas.dku.edu") == .child)
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
        let started = c.start(body: canvasBody(scope: nil))
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
        let started = c.start(body: canvasBody(scope: nil))
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
}
