import Foundation

enum CanvasPolicy {

    static func allows(_ url: URL, rules: [ExternalExamController.SiteRule]) -> Bool {
        guard url.scheme?.lowercased() == "https", url.user == nil, url.password == nil,
              let host = url.host?.lowercased(), !host.isEmpty else { return false }
        if let port = url.port, port != 443 { return false }
        return rules.contains { $0.matches(host: host, path: url.path) }
    }

    /// The origin the access code may be typed into: https, no credentials, port 443 or none, and
    /// the host exactly the exam's start host (never an SSO or wildcard host).
    static func fillOrigin(page: URL, start: URL, scope: CanvasScope? = nil) -> String? {
        func host(_ u: URL) -> String? {
            guard u.scheme?.lowercased() == "https", u.user == nil, u.password == nil,
                  let h = u.host?.lowercased(), !h.isEmpty else { return nil }
            if let port = u.port, port != 443 { return nil }
            return h
        }
        guard let pageHost = host(page), let startHost = host(start), pageHost == startHost else { return nil }
        if let scope {
            guard CanvasNavigation.isUnderItem(page, scope: scope) else { return nil }
        } else {
            guard isQuizPath(page.path) else { return nil }
        }
        return "https://" + pageHost
    }

    /// The quiz or assignment pages of the exam's Canvas host: the only pages that get the code.
    static func isQuizPath(_ path: String) -> Bool {
        path.range(of: #"^/courses/\d+/(quizzes|assignments)/\d+"#, options: .regularExpression) != nil
    }

    static func isBlank(_ url: URL?) -> Bool {
        guard let url else { return true }
        return url.absoluteString.lowercased() == "about:blank"
    }
}

/// Which exam item the Canvas exam is about, and whether the course material may be opened.
struct CanvasScope: Equatable {
    let courseId: String
    let itemKind: String
    let itemId: String
    let openBook: Bool
}

extension CanvasScope {

    static let maxIdLength = 20

    var itemPath: String { "/courses/\(courseId)/\(itemKind)/\(itemId)" }

    static func isId(_ text: String) -> Bool {
        !text.isEmpty && text.count <= maxIdLength
            && text.unicodeScalars.allSatisfy { $0.value >= 0x30 && $0.value <= 0x39 }
    }

    /// The `canvas_scope` object of the openExternal message. Nil when it is absent or malformed.
    init?(json: Any?) {
        guard let dict = json as? [String: Any] else { return nil }
        func text(_ key: String) -> String? {
            if let s = dict[key] as? String { return s }
            if let n = dict[key] as? Int { return String(n) }
            return nil
        }
        guard let course = text("course_id"), CanvasScope.isId(course),
              let item = text("item_id"), CanvasScope.isId(item),
              let kind = dict["item_kind"] as? String, kind == "quizzes" || kind == "assignments" else { return nil }
        self.init(courseId: course, itemKind: kind, itemId: item, openBook: (dict["open_book"] as? Bool) == true)
    }
}

enum CanvasViewAction: Equatable {
    case goBack
    case load(URL)
}

enum CanvasPhase: Equatable {
    case lobby
    case quiz
}

/// The restricted Canvas browser: which main-frame pages of the exam's Canvas host may open.
/// Hosts other than the exam host are not decided here (the site rules do that).
enum CanvasNavigation {

    enum Decision: Equatable {
        case allow
        case block
    }

    static let alwaysBlocked: Set<String> = [
        "conversations", "inbox", "profile", "groups", "calendar", "accounts", "api", "users"
    ]

    static let openBookRoots: Set<String> = ["pages", "modules", "files", "wiki"]

    /// The path segments of a URL, or nil when the path is not safe to judge: encoded slashes,
    /// backslashes or dots, `.` / `..` segments, empty segments. A single trailing slash is dropped.
    /// The query and the fragment are never part of the path.
    static func segments(_ url: URL) -> [String]? {
        guard let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        return segments(encodedPath: comps.percentEncodedPath)
    }

    static func segments(encodedPath path: String) -> [String]? {
        if path.isEmpty { return [] }
        guard path.hasPrefix("/") else { return nil }
        let lower = path.lowercased()
        for bad in ["%2f", "%5c", "%2e", "%00", "\\"] where lower.contains(bad) { return nil }
        var parts = path.dropFirst().split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        if parts.last == "" { parts.removeLast() }
        for part in parts where part.isEmpty || part == "." || part == ".." { return nil }
        return parts
    }

    private static func onHost(_ url: URL, examHost: String) -> Bool {
        url.host?.lowercased() == examHost.lowercased()
    }

    private static func inCourse(_ segs: [String], _ scope: CanvasScope) -> Bool {
        segs.count >= 2 && segs[0] == "courses" && segs[1] == scope.courseId
    }

    static func isUnderItem(_ segs: [String], scope: CanvasScope) -> Bool {
        inCourse(segs, scope) && segs.count >= 4 && segs[2] == scope.itemKind && segs[3] == scope.itemId
    }

    static func isUnderItem(_ url: URL, scope: CanvasScope) -> Bool {
        guard let segs = segments(url) else { return false }
        return isUnderItem(segs, scope: scope)
    }

    static func decide(url: URL, examHost: String, scope: CanvasScope, phase: CanvasPhase) -> Decision {
        guard onHost(url, examHost: examHost) else { return .allow }
        guard let segs = segments(url) else { return .block }
        if hasDownloadQuery(url) { return .block }
        return decide(segments: segs, scope: scope, phase: phase)
    }

    static let downloadQueryNames: Set<String> = ["download", "download_frd"]

    static func hasDownloadQuery(_ url: URL) -> Bool {
        guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return false }
        return items.contains { downloadQueryNames.contains($0.name.lowercased()) }
    }

    static func decide(segments segs: [String], scope: CanvasScope, phase: CanvasPhase) -> Decision {
        if let first = segs.first?.lowercased(), alwaysBlocked.contains(first) { return .block }
        if segs.count >= 2, segs[0].lowercased() == "courses", segs[1] != scope.courseId { return .block }
        if isUnderItem(segs, scope: scope) { return .allow }
        switch phase {
        case .lobby:
            if segs.isEmpty { return .allow }
            if segs[0] == "login" { return .allow }
            if inCourse(segs, scope) {
                if segs.count == 2 { return .allow }
                if segs.count == 3 && (segs[2] == "quizzes" || segs[2] == "assignments") { return .allow }
            }
            return .block
        case .quiz:
            if segs.first == "login" { return .allow }
            guard scope.openBook else { return .block }
            if inCourse(segs, scope), segs.count >= 3, openBookRoots.contains(segs[2]) {
                return isDownload(segs) ? .block : .allow
            }
            if segs.first == "files" { return isDownload(segs) ? .block : .allow }
            return .block
        }
    }

    private static func isDownload(_ segs: [String]) -> Bool {
        segs.contains { $0.lowercased() == "download" }
    }

    /// Whether a committed main-frame page starts the quiz phase: the classic `take` page, or any
    /// page of the assignment (new quizzes and assignments).
    static func entersQuiz(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url), isUnderItem(segs, scope: scope) else {
            return false
        }
        if scope.itemKind == "assignments" { return true }
        return segs.count >= 5 && segs[4] == "take"
    }

    /// The dashboard or the exam's course page: where a sign-in lands.
    static func isLanding(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url) else { return false }
        return segs.isEmpty || (segs.count == 2 && segs[0] == "courses" && segs[1] == scope.courseId)
    }

    static func isSignIn(url: URL, examHost: String) -> Bool {
        if !onHost(url, examHost: examHost) { return true }
        return segments(url)?.first == "login"
    }
}

/// Limits how often the view is brought back from a blocked page, and records each page once.
struct CanvasRecoveryGate {

    static let minInterval: TimeInterval = 1
    static let maxRemembered = 200

    private(set) var lastRecovery: Date?
    private var seen: Set<String> = []

    mutating func shouldRecover(now: Date) -> Bool {
        if let last = lastRecovery, now.timeIntervalSince(last) < CanvasRecoveryGate.minInterval { return false }
        lastRecovery = now
        return true
    }

    mutating func firstTime(_ key: String) -> Bool {
        if seen.count >= CanvasRecoveryGate.maxRemembered { seen.removeAll() }
        return seen.insert(key).inserted
    }
}

enum CanvasFinish {

    static let maxPattern = 300
    static let maxInput = 512

    static func compile(_ pattern: String?) -> NSRegularExpression? {
        guard let pattern, !pattern.isEmpty, pattern.count <= maxPattern, isSafe(pattern) else { return nil }
        return try? NSRegularExpression(pattern: pattern)
    }

    private struct GroupInfo {
        var alternation = false
        var quantifier = false
        var nested = false
    }

    /// Rejects back references, lookbehind, and the shapes that make a backtracking engine
    /// explode: a repeated group that holds an alternation, a quantifier or another group.
    static func isSafe(_ pattern: String) -> Bool {
        guard pattern.count <= maxPattern else { return false }
        if pattern.contains("(?<=") || pattern.contains("(?<!") { return false }
        let chars = Array(pattern)
        var stack: [GroupInfo] = []
        var inClass = false
        var i = 0
        while i < chars.count {
            let c = chars[i]
            if c == "\\" {
                if i + 1 < chars.count {
                    let n = chars[i + 1]
                    if ("1"..."9").contains(n) || n == "k" { return false }
                }
                i += 2
                continue
            }
            if inClass {
                if c == "]" { inClass = false }
                i += 1
                continue
            }
            switch c {
            case "[":
                inClass = true
            case "(":
                if !stack.isEmpty { stack[stack.count - 1].nested = true }
                stack.append(GroupInfo())
            case ")":
                guard let group = stack.popLast() else { return false }
                let next: Character? = i + 1 < chars.count ? chars[i + 1] : nil
                let repeated = next == "+" || next == "*" || next == "{"
                if repeated && (group.alternation || group.quantifier || group.nested) { return false }
                if !stack.isEmpty {
                    if group.alternation { stack[stack.count - 1].alternation = true }
                    if group.quantifier || repeated { stack[stack.count - 1].quantifier = true }
                }
            case "|":
                if !stack.isEmpty { stack[stack.count - 1].alternation = true }
            case "+", "*", "{":
                if !stack.isEmpty { stack[stack.count - 1].quantifier = true }
            default:
                break
            }
            i += 1
        }
        return stack.isEmpty && repeatsSafe(pattern)
    }

    static let maxOpenRepeats = 2

    private enum Kind: Equatable {
        case literal(Character)
        case digit
        case word
        case space
        case any
    }

    private struct Item {
        var kind: Kind
        var open: Bool
    }

    private struct Quantifier {
        var present = false
        var open = false
        var next = 0
    }

    private static func quantifier(_ chars: [Character], at j: Int) -> Quantifier {
        var q = Quantifier(present: false, open: false, next: j)
        guard j < chars.count else { return q }
        switch chars[j] {
        case "*", "+":
            q.present = true
            q.open = true
            q.next = j + 1
        case "?":
            q.present = true
            q.next = j + 1
        case "{":
            var k = j + 1
            var digits = 0
            while k < chars.count, ("0"..."9").contains(chars[k]) { k += 1; digits += 1 }
            guard digits > 0, k < chars.count else { return q }
            if chars[k] == "}" {
                q.present = true
                q.next = k + 1
            } else if chars[k] == "," {
                k += 1
                var maxDigits = 0
                while k < chars.count, ("0"..."9").contains(chars[k]) { k += 1; maxDigits += 1 }
                guard k < chars.count, chars[k] == "}" else { return q }
                q.present = true
                q.open = maxDigits == 0
                q.next = k + 1
            }
        default:
            break
        }
        if q.present, q.next < chars.count, chars[q.next] == "?" { q.next += 1 }
        return q
    }

    private static func isWordChar(_ c: Character) -> Bool {
        c == "_" || ("a"..."z").contains(c) || ("0"..."9").contains(c)
    }

    private static func differs(_ a: Kind, _ b: Kind) -> Bool {
        switch (a, b) {
        case (.literal(let x), .literal(let y)):
            return x != y
        case (.literal(let x), .digit), (.digit, .literal(let x)):
            return !("0"..."9").contains(x)
        case (.literal(let x), .word), (.word, .literal(let x)):
            return !isWordChar(x)
        case (.literal(let x), .space), (.space, .literal(let x)):
            return ![" ", "\t", "\n", "\r", "\u{0B}", "\u{0C}"].contains(x)
        case (.digit, .space), (.space, .digit), (.word, .space), (.space, .word):
            return true
        default:
            return false
        }
    }

    /// At most two open-ended repeats in all, and no two open-ended repeats right after each other
    /// unless both are single characters known to differ.
    static func repeatsSafe(_ pattern: String) -> Bool {
        let chars = Array(pattern)
        var openCount = 0
        var contexts: [Item?] = [nil]
        var i = 0

        func check(_ kind: Kind, repeated: Bool) -> Bool {
            if repeated {
                openCount += 1
                if let prev = contexts[contexts.count - 1], prev.open, !differs(prev.kind, kind) { return false }
            }
            return openCount <= maxOpenRepeats
        }

        while i < chars.count {
            let c = chars[i]
            var kind: Kind = .any
            var length = 1
            switch c {
            case "\\":
                guard i + 1 < chars.count else { return false }
                let e = chars[i + 1]
                length = 2
                switch e {
                case "d": kind = .digit
                case "w": kind = .word
                case "s": kind = .space
                default:
                    if e.isASCII && !e.isLetter && !e.isNumber {
                        kind = .literal(Character(e.lowercased()))
                    }
                }
            case "[":
                var k = i + 1
                var closed = false
                while k < chars.count {
                    if chars[k] == "\\" { k += 2; continue }
                    if chars[k] == "]" { closed = true; break }
                    k += 1
                }
                guard closed else { return false }
                length = k - i + 1
            case "(":
                contexts.append(nil)
                i += 1
                continue
            case ")":
                guard contexts.count > 1 else { return false }
                let inner = contexts.removeLast()
                let q = quantifier(chars, at: i + 1)
                if !check(.any, repeated: q.open) { return false }
                contexts[contexts.count - 1] = Item(kind: .any, open: q.open || (inner?.open ?? false))
                i = q.present ? q.next : i + 1
                continue
            case "|", "^", "$":
                contexts[contexts.count - 1] = nil
                i += 1
                continue
            case ".":
                break
            case "*", "+", "?", "{":
                let q = quantifier(chars, at: i)
                if q.present {
                    if q.open { openCount += 1 }
                    if openCount > maxOpenRepeats { return false }
                    i = q.next
                    continue
                }
                kind = .literal(c)
            default:
                if c.isASCII { kind = .literal(Character(c.lowercased())) }
            }
            let q = quantifier(chars, at: i + length)
            if !check(kind, repeated: q.open) { return false }
            contexts[contexts.count - 1] = Item(kind: kind, open: q.open)
            i = q.present ? q.next : i + length
        }
        return true
    }

    static func matches(_ url: URL, regex: NSRegularExpression?) -> Bool {
        guard let regex else { return false }
        let text = url.path + (url.query.map { "?" + $0 } ?? "")
        let limited = String(text.prefix(maxInput))
        let range = NSRange(limited.startIndex..., in: limited)
        return regex.firstMatch(in: limited, options: [], range: range) != nil
    }
}

enum CanvasRetry {

    static let maxRetries = 3

    /// Seconds to wait after a 429: the Retry-After value (whole seconds) clamped to 1...20, 4 when absent.
    static func delay(retryAfter: String?) -> TimeInterval {
        guard let text = retryAfter?.trimmingCharacters(in: .whitespaces), let n = Int(text) else { return 4 }
        return TimeInterval(min(20, max(1, n)))
    }
}

enum CanvasData {

    /// Whether a web data record (WebKit's displayName, usually the registrable domain) is
    /// cleared after a Canvas exam. Everything but the exam server's own record and its parent
    /// domains goes.
    static func shouldRemove(domain: String, serverHost: String) -> Bool {
        var d = domain.trimmingCharacters(in: .whitespaces).lowercased()
        if d.hasPrefix(".") { d.removeFirst() }
        let server = serverHost.lowercased()
        guard !d.isEmpty, d != server else { return false }
        if !server.isEmpty && server.hasSuffix("." + d) { return false }
        return true
    }
}

enum CanvasPopup {

    enum Plan: Equatable {
        case child
        case sameView
        case block
    }

    static func plan(url: URL?, openerHost: String?, rules: [ExternalExamController.SiteRule],
                     openerSurvivesCrossOrigin: Bool) -> Plan {
        guard let url, !CanvasPolicy.isBlank(url) else { return .child }
        guard CanvasPolicy.allows(url, rules: rules) else { return .block }
        let host = url.host?.lowercased()
        if host == openerHost?.lowercased() || openerSurvivesCrossOrigin { return .child }
        return .sameView
    }

    static func isCrossHost(_ url: URL?, openerHost: String?) -> Bool {
        guard let host = url?.host?.lowercased(), !host.isEmpty else { return false }
        return host != openerHost?.lowercased()
    }
}

struct CanvasFillBudget {

    static let maxFills = 3

    private(set) var used = 0

    var exhausted: Bool { used >= CanvasFillBudget.maxFills }

    mutating func take() -> Bool {
        guard !exhausted else { return false }
        used += 1
        return true
    }
}

enum CanvasProof {

    static func message(attemptId: String, session: String, deviceId: String, ts: Int64) -> String {
        "canvascode|\(attemptId)|\(session)|\(deviceId)|\(ts)"
    }

    static func body(attemptId: String, session: String, deviceId: String, ts: Int64,
                     sign: (String) -> String) -> [String: Any] {
        let proof = sign(message(attemptId: attemptId, session: session, deviceId: deviceId, ts: ts))
        return ["proof": proof, "ts": NSNumber(value: ts)]
    }

    static func parseCode(status: Int?, json: [String: Any]?) -> String? {
        guard status == 200, let code = json?["code"] as? String,
              code.count >= 4, code.count <= 64,
              code.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value != 127 }) else { return nil }
        return code
    }
}

enum CanvasScripts {

    static let field = "input#quiz_access_code, input[name=access_code]"

    static let probe = "(function () { try { return !!document.querySelector('\(field)'); } catch (e) { return false; } })();"

    static let popupGuard = """
    (function () {
      document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
      document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);
    })();
    """

    static func jsString(_ s: String) -> String {
        guard let data = try? JSONSerialization.data(withJSONObject: [s]),
              var text = String(data: data, encoding: .utf8), text.count >= 2 else { return "\"\"" }
        text.removeFirst()
        text.removeLast()
        return text
            .replacingOccurrences(of: "\u{2028}", with: "\\u2028")
            .replacingOccurrences(of: "\u{2029}", with: "\\u2029")
    }

    static func fill(code: String, origin: String, pathPrefix: String? = nil) -> String {
        var pathCheck = ""
        if let pathPrefix {
            pathCheck = """
              var pre = \(jsString(pathPrefix));
              var here = location.pathname;
              if (!(here === pre || here.indexOf(pre + '/') === 0)) { return 'wrongpath'; }

            """
        }
        return """
        (function () {
          if (location.origin !== \(jsString(origin))) { return 'wrongorigin'; }
        \(pathCheck)
          try {
            var el = document.querySelector('\(field)');
            if (!el) { return 'nofield'; }
            var v = \(jsString(code));
            var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
            if (d && d.set) { d.set.call(el, v); } else { el.value = v; }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            var form = el.form || el.closest('form');
            var btn = form ? (form.querySelector('button[type=submit], input[type=submit]') || form.querySelector('button:not([type])')) : null;
            if (!btn) { return 'nobutton'; }
            btn.removeAttribute('disabled');
            btn.disabled = false;
            btn.click();
            return 'clicked';
          } catch (e) { return 'error'; }
        })();
        """
    }
}
