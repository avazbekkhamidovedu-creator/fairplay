import Foundation

public enum CanvasPolicy {

    public static func allows(_ url: URL, rules: [SiteRule]) -> Bool {
        guard url.scheme?.lowercased() == "https", url.user == nil, url.password == nil,
              let host = url.host?.lowercased(), !host.isEmpty else { return false }
        if let port = url.port, port != 443 { return false }
        return rules.contains { $0.matches(host: host, path: url.path) }
    }

    public static func isBlank(_ url: URL?) -> Bool {
        guard let url else { return true }
        return url.absoluteString.lowercased() == "about:blank"
    }
}

public struct CanvasScope: Equatable {
    public let courseId: String
    public let itemKind: String
    public let itemId: String
    public let openBook: Bool
    public let signInHosts: [String]
    public let quizLtiHosts: [String]

    public static let maxIdLength = 20
    public static let maxHosts = 50

    public init(courseId: String, itemKind: String, itemId: String, openBook: Bool,
                signInHosts: [String] = [], quizLtiHosts: [String] = []) {
        self.courseId = courseId
        self.itemKind = itemKind
        self.itemId = itemId
        self.openBook = openBook
        self.signInHosts = signInHosts
        self.quizLtiHosts = quizLtiHosts
    }

    public static func isHostPattern(_ text: String) -> Bool {
        let body = text.hasPrefix("*.") ? String(text.dropFirst(2)) : text
        guard !body.isEmpty, body.count <= 253, !body.hasPrefix("."), !body.hasSuffix("."), !body.contains("..")
        else { return false }
        return body.unicodeScalars.allSatisfy {
            ($0.value >= 0x61 && $0.value <= 0x7A) || ($0.value >= 0x30 && $0.value <= 0x39)
                || $0 == "." || $0 == "-" || $0 == "_"
        }
    }

    private static func hostList(_ raw: Any?) -> [String]? {
        guard let raw, !(raw is NSNull) else { return [] }
        guard let list = raw as? [Any], list.count <= maxHosts else { return nil }
        var out: [String] = []
        for item in list {
            guard let text = item as? String else { return nil }
            let host = text.lowercased()
            guard isHostPattern(host) else { return nil }
            out.append(host)
        }
        return out
    }

    public static func isId(_ text: String) -> Bool {
        !text.isEmpty && text.count <= maxIdLength
            && text.unicodeScalars.allSatisfy { $0.value >= 0x30 && $0.value <= 0x39 }
    }

    public init?(json: Any?) {
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

public enum CanvasViewAction: Equatable {
    case goBack
    case load(URL)
}

public enum CanvasPhase: Equatable {
    case lobby
    case quiz
}

public enum CanvasQuizMarker {

    public static func key(_ attemptId: String) -> String { "fairplay.canvasQuizStarted." + attemptId }

    public static func isSet(_ attemptId: String, in defaults: UserDefaults) -> Bool {
        defaults.bool(forKey: key(attemptId))
    }

    public static func set(_ attemptId: String, in defaults: UserDefaults) {
        defaults.set(true, forKey: key(attemptId))
    }

    public static func clear(_ attemptId: String, in defaults: UserDefaults) {
        defaults.removeObject(forKey: key(attemptId))
        defaults.removeObject(forKey: armedKey(attemptId))
    }

    public static func armedKey(_ attemptId: String) -> String { "fairplay.canvasQuizArmed." + attemptId }

    public static func isArmed(_ attemptId: String, in defaults: UserDefaults) -> Bool {
        defaults.bool(forKey: armedKey(attemptId))
    }

    public static func setArmed(_ armed: Bool, _ attemptId: String, in defaults: UserDefaults) {
        if armed {
            defaults.set(true, forKey: armedKey(attemptId))
        } else {
            defaults.removeObject(forKey: armedKey(attemptId))
        }
    }
}

public enum CanvasNavigation {

    public enum Decision: Equatable {
        case allow
        case block
    }

    public static let alwaysBlocked: Set<String> = [
        "conversations", "inbox", "profile", "groups", "calendar", "accounts", "api", "users"
    ]

    public static let openBookRoots: Set<String> = ["pages", "modules", "files", "wiki"]

    public static func segments(_ url: URL) -> [String]? {
        guard let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        return segments(encodedPath: comps.percentEncodedPath)
    }

    public static func segments(encodedPath path: String) -> [String]? {
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

    public static func isUnderItem(_ segs: [String], scope: CanvasScope) -> Bool {
        inCourse(segs, scope) && segs.count >= 4 && segs[2] == scope.itemKind && segs[3] == scope.itemId
    }

    public static let helperRoots: Set<String> = ["media_objects_iframe", "equation_images"]

    /// Same-host read-only frames Canvas and New Quizzes embed in an exam page.
    public static func isHelperFrame(_ url: URL, scope: CanvasScope) -> Bool {
        guard let segs = segments(url), let first = segs.first?.lowercased() else { return false }
        if helperRoots.contains(first) { return true }
        if first == "api" { return segs.count >= 2 && segs[1].lowercased() == "lti" }
        return inCourse(segs, scope) && segs.count >= 3 && segs[2].lowercased() == "external_tools"
    }

    public static func isUnderItem(_ url: URL, scope: CanvasScope) -> Bool {
        guard let segs = segments(url) else { return false }
        return isUnderItem(segs, scope: scope)
    }

    public static func hostListMatches(_ host: String, _ list: [String]) -> Bool {
        let h = host.lowercased()
        return list.contains { entry in
            if entry.hasPrefix("*.") { return h.hasSuffix(String(entry.dropFirst(1))) && h.count > entry.count - 1 }
            return h == entry
        }
    }

    public static func decide(url: URL, examHost: String, scope: CanvasScope, phase: CanvasPhase,
                              inPopup: Bool = false) -> Decision {
        guard onHost(url, examHost: examHost) else {
            guard phase == .quiz, !inPopup else { return .allow }
            guard url.scheme?.lowercased() == "https", let host = url.host, !host.isEmpty else { return .block }
            return hostListMatches(host, scope.signInHosts) || hostListMatches(host, scope.quizLtiHosts) ? .allow : .block
        }
        guard let segs = segments(url) else { return .block }
        if hasDownloadQuery(url) { return .block }
        return decide(segments: segs, scope: scope, phase: phase)
    }

    public static let downloadQueryNames: Set<String> = ["download", "download_frd"]

    public static func hasDownloadQuery(_ url: URL) -> Bool {
        guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems else { return false }
        return items.contains { downloadQueryNames.contains($0.name.lowercased()) }
    }

    public static func decide(segments segs: [String], scope: CanvasScope, phase: CanvasPhase) -> Decision {
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

    public static func entersQuiz(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url), isUnderItem(segs, scope: scope) else {
            return false
        }
        if scope.itemKind == "assignments" { return true }
        return segs.count >= 5 && segs[4] == "take"
    }

    public static func isItemLanding(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url) else { return false }
        return segs.count == 4 && isUnderItem(segs, scope: scope)
    }

    public static func isLanding(url: URL, examHost: String, scope: CanvasScope) -> Bool {
        guard onHost(url, examHost: examHost), let segs = segments(url) else { return false }
        return segs.isEmpty || (segs.count == 2 && segs[0] == "courses" && segs[1] == scope.courseId)
    }

    public static func isSignIn(url: URL, examHost: String) -> Bool {
        if !onHost(url, examHost: examHost) { return true }
        return segments(url)?.first == "login"
    }
}

public struct CanvasRecoveryGate {

    public static let minInterval: TimeInterval = 1
    public static let maxRemembered = 200

    public private(set) var lastRecovery: Date?
    private var seen: Set<String> = []

    public init() {}

    public mutating func shouldRecover(now: Date) -> Bool {
        if let last = lastRecovery, now.timeIntervalSince(last) < CanvasRecoveryGate.minInterval { return false }
        lastRecovery = now
        return true
    }

    public mutating func firstTime(_ key: String) -> Bool {
        if seen.count >= CanvasRecoveryGate.maxRemembered { seen.removeAll() }
        return seen.insert(key).inserted
    }
}

public struct CanvasRecovery: Equatable {
    public var report: String?
    public var notice = false
    public var action: CanvasViewAction?
    public var recheck = false

    public init() {}
}

public struct CanvasSetup {

    public enum Failure: Error, Equatable {
        case notHTTPS
        case badScope
    }

    public let rules: [SiteRule]
    public let scope: CanvasScope
    public let finishRegex: NSRegularExpression?
    public let finishRejected: Bool

    public static func make(startURL url: URL, allowedDomains: [Any], rawScope: Any?,
                            rawRegex: String?) -> Result<CanvasSetup, Failure> {
        guard url.scheme?.lowercased() == "https", let startHost = url.host?.lowercased(), !startHost.isEmpty else {
            return .failure(.notHTTPS)
        }
        var rules = allowedDomains.compactMap { ($0 as? String).flatMap(SiteRule.init) }
        if !rules.contains(where: { !$0.wildcard && $0.host == startHost && $0.pathPrefix.isEmpty }),
           let r = SiteRule(startHost) {
            rules.append(r)
        }
        let regex = CanvasFinish.compile(rawRegex)
        guard let raw = rawScope, !(raw is NSNull), let scope = CanvasScope(json: raw),
              CanvasNavigation.decide(url: url, examHost: startHost, scope: scope, phase: .lobby) == .allow else {
            return .failure(.badScope)
        }
        return .success(CanvasSetup(rules: rules, scope: scope, finishRegex: regex, finishRejected: regex == nil))
    }
}

public struct CanvasBrowser {

    public static let maxRecoveries = 5
    public static let maxBlockReports = 100
    public static let blockedNotice = "This page is not available during the exam."

    public let startURL: URL
    public let rules: [SiteRule]
    public let scope: CanvasScope
    public let finishUsable: Bool
    public private(set) var phase: CanvasPhase = .lobby
    public private(set) var armed = false
    public private(set) var blockedReports = 0

    private var autoOpened = false
    private var signInSeen = false
    private var firstCommitSeen = false
    private var keepArmedOnFirstLanding = false
    private var recovery = CanvasRecoveryGate()
    private var blockLog = CanvasRecoveryGate()
    private var recoveryTries = 0
    private var finalLoadDone = false

    public init(startURL: URL, rules: [SiteRule], scope: CanvasScope, quizStarted: Bool,
                armed: Bool = false, finishUsable: Bool = true) {
        self.startURL = startURL
        self.rules = rules
        self.scope = scope
        self.finishUsable = finishUsable
        if quizStarted {
            phase = .quiz
            self.armed = armed
            keepArmedOnFirstLanding = armed
        }
    }

    public var examHost: String { (startURL.host ?? "").lowercased() }

    public var isLockedIn: Bool { phase == .quiz && finishUsable }

    public func isFinish(_ url: URL, regex: NSRegularExpression?) -> Bool {
        finishArmedNow && CanvasFinish.matches(url, regex: regex, scope: scope)
    }

    public mutating func disarm(keepingOn current: URL?, regex: NSRegularExpression?) {
        if let current, armed, CanvasFinish.matches(current, regex: regex, scope: scope) { return }
        armed = false
    }

    public var finishArmedNow: Bool { armed }

    public func isUnderExamItem(_ url: URL?) -> Bool {
        guard let url else { return false }
        return CanvasNavigation.isUnderItem(url, scope: scope)
    }

    public func allows(_ url: URL) -> Bool { CanvasPolicy.allows(url, rules: rules) }

    public func blocksMainFrame(_ url: URL) -> Bool {
        return CanvasNavigation.decide(url: url, examHost: examHost, scope: scope, phase: phase) == .block
    }

    public func allowsMainFrame(_ url: URL) -> Bool { allows(url) && !blocksMainFrame(url) }

    public func blocksExamHostFrame(_ url: URL) -> Bool {
        url.host?.lowercased() == examHost && !CanvasNavigation.isHelperFrame(url, scope: scope) && blocksMainFrame(url)
    }

    public func childAllows(_ url: URL, mainFrame: Bool = true) -> Bool {
        CanvasPolicy.isBlank(url) || (allows(url) && !(mainFrame ? blocksMainFrame(url) : blocksExamHostFrame(url)))
    }

    public func blockDetail(_ url: URL) -> String {
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath ?? ""
        return "canvas page \(String(path.prefix(120)))"
    }

    public mutating func recordBlocked(_ url: URL) -> String? {
        let detail = blockDetail(url)
        guard blockedReports < CanvasBrowser.maxBlockReports, blockLog.firstTime(detail) else { return nil }
        blockedReports += 1
        return detail
    }

    public func backAllowed(_ previous: URL) -> Bool {
        guard allowsMainFrame(previous) else { return false }
        if phase == .quiz {
            return previous.host?.lowercased() == examHost && !CanvasNavigation.isSignIn(url: previous, examHost: examHost)
                && CanvasNavigation.isUnderItem(previous, scope: scope)
        }
        return true
    }

    public func isOnItem(_ url: URL?) -> Bool {
        guard let url, url.host?.lowercased() == examHost else { return false }
        return CanvasNavigation.isUnderItem(url, scope: scope)
    }

    public mutating func committed(_ url: URL?) -> Bool {
        guard let url, url.scheme?.lowercased() == "https" else { return false }
        let keepArmed = keepArmedOnFirstLanding
        keepArmedOnFirstLanding = false
        if CanvasNavigation.isSignIn(url: url, examHost: examHost) { signInSeen = true }
        if !firstCommitSeen {
            firstCommitSeen = true
            if url != startURL, CanvasNavigation.isLanding(url: url, examHost: examHost, scope: scope) {
                signInSeen = true
            }
        }
        let enters = CanvasNavigation.entersQuiz(url: url, examHost: examHost, scope: scope)
        if enters {
            armed = true
        } else if CanvasNavigation.isItemLanding(url: url, examHost: examHost, scope: scope), !keepArmed {
            armed = false
        }
        if phase == .lobby, enters {
            phase = .quiz
            return true
        }
        return false
    }

    public mutating func autoOpenTarget(_ url: URL?) -> URL? {
        guard !autoOpened, signInSeen, phase == .lobby, let url,
              CanvasNavigation.isLanding(url: url, examHost: examHost, scope: scope) else { return nil }
        autoOpened = true
        return startURL
    }

    public mutating func recover(url: URL?, now: Date, previous: URL?) -> CanvasRecovery {
        var out = CanvasRecovery()
        guard let url else { return out }
        guard allows(url), blocksMainFrame(url) else {
            recoveryTries = 0
            finalLoadDone = false
            return out
        }
        let detail = blockDetail(url)
        if recovery.firstTime(detail) && blockedReports < CanvasBrowser.maxBlockReports {
            blockedReports += 1
            out.report = detail + " (in page)"
        }
        out.notice = true
        guard recovery.shouldRecover(now: now) else {
            out.recheck = true
            return out
        }
        guard recoveryTries < CanvasBrowser.maxRecoveries else {
            if !finalLoadDone {
                finalLoadDone = true
                out.action = .load(startURL)
            }
            return out
        }
        recoveryTries += 1
        let startOK = allowsMainFrame(startURL)
        if phase == .quiz && startOK {
            out.action = .load(startURL)
        } else if let previous, previous != url, backAllowed(previous) {
            out.action = .goBack
        } else if startOK {
            out.action = .load(startURL)
        }
        out.recheck = true
        return out
    }

    public func popupPlan(url: URL?) -> CanvasPopup.Plan {
        guard let url, !CanvasPolicy.isBlank(url) else { return .block }
        if blocksMainFrame(url) { return .block }
        return allows(url) ? .sameView : .block
    }
}

public enum CanvasFinish {

    public static let maxPattern = 300
    public static let maxInput = 512

    public static func compile(_ pattern: String?) -> NSRegularExpression? {
        guard let pattern, !pattern.isEmpty, pattern.count <= maxPattern, isSafe(pattern) else { return nil }
        return try? NSRegularExpression(pattern: pattern)
    }

    private struct GroupInfo {
        var alternation = false
        var quantifier = false
        var nested = false
    }

    public static func isSafe(_ pattern: String) -> Bool {
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

    public static let maxOpenRepeats = 2

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

    public static func repeatsSafe(_ pattern: String) -> Bool {
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
    public static func matches(_ url: URL, regex: NSRegularExpression?, scope: CanvasScope?) -> Bool {
        guard matches(url, regex: regex) else { return false }
        guard let scope else { return true }
        guard let segs = CanvasNavigation.segments(url) else { return false }
        if segs.first == "courses" { return CanvasNavigation.isUnderItem(segs, scope: scope) }
        return true
    }

    public static func matches(_ url: URL, regex: NSRegularExpression?) -> Bool {
        guard let regex else { return false }
        let limited = String(url.path.prefix(maxInput))
        let range = NSRange(limited.startIndex..., in: limited)
        return regex.firstMatch(in: limited, options: [], range: range) != nil
    }
}

public enum CanvasData {

    public static func shouldRemove(domain: String, serverHost: String) -> Bool {
        var d = domain.trimmingCharacters(in: .whitespaces).lowercased()
        if d.hasPrefix(".") { d.removeFirst() }
        let server = serverHost.lowercased()
        guard !d.isEmpty, d != server else { return false }
        if !server.isEmpty && server.hasSuffix("." + d) { return false }
        return true
    }
}

public enum CanvasPopup {

    public enum Plan: Equatable {
        case sameView
        case block
    }

    public static func isCrossHost(_ url: URL?, openerHost: String?) -> Bool {
        guard let host = url?.host?.lowercased(), !host.isEmpty else { return false }
        return host != openerHost?.lowercased()
    }
}

public enum CanvasScripts {

    public static func pageGuard(serverOrigin: String) -> String {
        """
        (function () {
          if (location.protocol !== 'https:' || location.origin === '\(serverOrigin)') return;
          try { navigator.share = undefined; navigator.canShare = undefined; } catch (e) {}
          try { window.print = function () {}; } catch (e) {}
          var top = true;
          try { top = window.top === window.self; } catch (e) { top = false; }
          if (!top) return;
          window.open = function (u) {
            if (u !== undefined && u !== null && String(u) !== '') {
              try { location.assign(new URL(String(u), location.href).href); } catch (e) {}
            }
            return null;
          };
        })();
        """
    }
}
