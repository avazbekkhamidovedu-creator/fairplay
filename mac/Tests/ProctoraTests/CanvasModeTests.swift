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

    @Test func finishMatchesPathAndQueryOnly() {
        let re = CanvasFinish.compile(#"/submissions/\d+"#)
        #expect(CanvasFinish.matches(URL(string: "https://c.example.edu/q/submissions/7?x=1")!, contains: nil, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://c.example.edu/q/take#/submissions/7")!, contains: nil, regex: re))
        #expect(!CanvasFinish.matches(URL(string: "https://submissions.example.edu/q/take")!, contains: nil, regex: re))
        let long = URL(string: "https://c.example.edu/" + String(repeating: "a", count: 600) + "/submissions/7")!
        #expect(!CanvasFinish.matches(long, contains: nil, regex: re))
    }

    @Test func finishMatching() {
        let classic = CanvasFinish.compile(#"/quizzes/\d+/(submissions/\d+|history)"#)
        let yes = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/submissions/333")!
        let no = URL(string: "https://canvas.dku.edu/courses/1/quizzes/22/take")!
        #expect(CanvasFinish.matches(yes, contains: nil, regex: classic))
        #expect(!CanvasFinish.matches(no, contains: nil, regex: classic))
        #expect(!CanvasFinish.matches(yes, contains: nil, regex: nil))
        #expect(CanvasFinish.matches(no, contains: "/take", regex: classic))
        #expect(!CanvasFinish.matches(yes, contains: "/take", regex: classic))
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
        #expect(b.take())
        #expect(b.take())
        #expect(!b.exhausted)
        #expect(b.take())
        #expect(b.exhausted)
        #expect(!b.take())
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
        let hosts = ["canvas.dku.edu", "login.microsoftonline.com"]
        func gone(_ d: String, server: String = "exam.dku.edu") -> Bool {
            CanvasData.shouldRemove(domain: d, serverHost: server, ruleHosts: hosts)
        }
        #expect(!gone("exam.dku.edu"))
        #expect(!gone(".EXAM.dku.edu"))
        #expect(gone("microsoftonline.com"))
        #expect(gone("instructure.com"))
        #expect(gone("dku.edu"))
        #expect(!CanvasData.shouldRemove(domain: "dku.edu", serverHost: "exam.dku.edu", ruleHosts: ["canvas.other.org"]))
        #expect(gone("canvas.dku.edu"))
        #expect(!gone("", server: "exam.dku.edu"))
    }

    private func stubContext(origin: String, withButton: Bool = true) -> JSContext {
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
        var location = { origin: '\(origin)' };
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
        #expect(!script.contains("return v"))
    }
}
