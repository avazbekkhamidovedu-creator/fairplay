import Foundation

enum CanvasPolicy {

    static func allows(_ url: URL, rules: [ExternalExamController.SiteRule]) -> Bool {
        guard url.scheme?.lowercased() == "https", url.user == nil, url.password == nil,
              let host = url.host?.lowercased(), !host.isEmpty else { return false }
        if let port = url.port, port != 443 { return false }
        return rules.contains { $0.matches(host: host, path: url.path) }
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
    var signInHosts: [String] = []
    var quizLtiHosts: [String] = []
}

extension CanvasScope {

    static let maxIdLength = 20

    static func isId(_ text: String) -> Bool {
        !text.isEmpty && text.count <= maxIdLength
            && text.unicodeScalars.allSatisfy { $0.value >= 0x30 && $0.value <= 0x39 }
    }

    static let maxHostPatterns = 50

    /// `host` or `*.suffix`: lower-case letters, digits, dot, dash and underscore only.
    static func isHostPattern(_ text: String) -> Bool {
        let body = text.hasPrefix("*.") ? String(text.dropFirst(2)) : text
        guard !body.isEmpty, body.count <= 253, !body.hasPrefix("."), !body.hasSuffix("."), !body.contains("..")
        else { return false }
        return body.unicodeScalars.allSatisfy {
            ($0.value >= 0x61 && $0.value <= 0x7A) || ($0.value >= 0x30 && $0.value <= 0x39)
                || $0 == "." || $0 == "-" || $0 == "_"
        }
    }

    /// Nil when the value is present but not a list of valid host patterns. Absent or null is empty.
    static func hostList(_ value: Any?) -> [String]? {
        guard let value, !(value is NSNull) else { return [] }
        guard let list = value as? [Any], list.count <= maxHostPatterns else { return nil }
        var result: [String] = []
        for item in list {
            guard let text = item as? String else { return nil }
            let lower = text.lowercased()
            guard isHostPattern(lower) else { return nil }
            result.append(lower)
        }
        return result
    }

    static func hostMatches(_ host: String, patterns: [String]) -> Bool {
        let h = host.lowercased()
        return patterns.contains { pattern in
            if pattern.hasPrefix("*.") { return h.hasSuffix("." + String(pattern.dropFirst(2))) }
            return h == pattern
        }
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
        guard let signIn = CanvasScope.hostList(dict["sign_in_hosts"]),
              let lti = CanvasScope.hostList(dict["quiz_lti_hosts"]) else { return nil }
        self.init(courseId: course, itemKind: kind, itemId: item, openBook: (dict["open_book"] as? Bool) == true,
                  signInHosts: signIn, quizLtiHosts: lti)
    }
}

enum CanvasViewAction: Equatable {
    case goBack
    case load(URL)
}

/// Remembers on this Mac that the quiz of an attempt has started, so a relaunch or a reload
/// cannot fall back to the looser lobby rules.
enum CanvasQuizMarker {

    static func key(_ attemptId: String) -> String { "fairplay.canvasQuizStarted." + attemptId }

    static func isSet(_ attemptId: String, in defaults: UserDefaults) -> Bool {
        defaults.bool(forKey: key(attemptId))
    }

    static func set(_ attemptId: String, in defaults: UserDefaults) {
        defaults.set(true, forKey: key(attemptId))
    }

    static func armedKey(_ attemptId: String) -> String { "fairplay.canvasFinishArmed." + attemptId }

    static func isArmed(_ attemptId: String, in defaults: UserDefaults) -> Bool {
        defaults.bool(forKey: armedKey(attemptId))
    }

    static func setArmed(_ attemptId: String, _ armed: Bool, in defaults: UserDefaults) {
        defaults.set(armed, forKey: armedKey(attemptId))
    }

    static func clear(_ attemptId: String, in defaults: UserDefaults) {
        defaults.removeObject(forKey: key(attemptId))
        defaults.removeObject(forKey: armedKey(attemptId))
    }
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

    static let helperRoots: Set<String> = ["media_objects_iframe", "equation_images"]

    /// Same-host read-only frames Canvas and New Quizzes embed in an exam page.
    static func isHelperFrame(_ url: URL, scope: CanvasScope) -> Bool {
        guard let segs = segments(url), let first = segs.first?.lowercased() else { return false }
        if helperRoots.contains(first) { return true }
        if first == "api" { return segs.count >= 2 && segs[1].lowercased() == "lti" }
        return inCourse(segs, scope) && segs.count >= 3 && segs[2].lowercased() == "external_tools"
    }

    static func isUnderItem(_ url: URL, scope: CanvasScope) -> Bool {
        guard let segs = segments(url) else { return false }
        return isUnderItem(segs, scope: scope)
    }

    /// Other hosts follow the site rules in the lobby. In the quiz phase a main-frame page on another
    /// host opens only over https for the sign-in and quiz-lti hosts the server listed.
    static func decide(url: URL, examHost: String, scope: CanvasScope, phase: CanvasPhase) -> Decision {
        guard onHost(url, examHost: examHost) else {
            guard phase == .quiz else { return .allow }
            guard url.scheme?.lowercased() == "https", let host = url.host, !host.isEmpty else { return .block }
            let listed = CanvasScope.hostMatches(host, patterns: scope.signInHosts)
                || CanvasScope.hostMatches(host, patterns: scope.quizLtiHosts)
            return listed ? .allow : .block
        }
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

    /// The exact item page `/courses/C/K/I` (a trailing slash is fine).
    static func isItemLanding(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url) else { return false }
        return segs.count == 4 && isUnderItem(segs, scope: scope)
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

    /// A finish page under /courses/ must belong to the exam's own course and item. Pages on other
    /// paths (New Quizzes frames) are tied to the exam by the main-frame check instead.
    static func matches(_ url: URL, regex: NSRegularExpression?, scope: CanvasScope?) -> Bool {
        guard matches(url, regex: regex) else { return false }
        guard let scope else { return true }
        guard let segs = CanvasNavigation.segments(url) else { return false }
        if segs.first == "courses" { return CanvasNavigation.isUnderItem(segs, scope: scope) }
        return true
    }

    static func matches(_ url: URL, regex: NSRegularExpression?) -> Bool {
        guard let regex else { return false }
        let text = url.path
        let limited = String(text.prefix(maxInput))
        let range = NSRange(limited.startIndex..., in: limited)
        return regex.firstMatch(in: limited, options: [], range: range) != nil
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

enum CanvasScripts {

    static let popupGuard = """
    (function () {
      document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
      document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);
      try { Object.defineProperty(window, 'print', { value: function () {}, writable: false, configurable: false }); } catch (e) {}
    })();
    """
}
