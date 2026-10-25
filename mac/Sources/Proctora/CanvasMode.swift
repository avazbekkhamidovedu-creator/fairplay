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
    static func fillOrigin(page: URL, start: URL) -> String? {
        func host(_ u: URL) -> String? {
            guard u.scheme?.lowercased() == "https", u.user == nil, u.password == nil,
                  let h = u.host?.lowercased(), !h.isEmpty else { return nil }
            if let port = u.port, port != 443 { return nil }
            return h
        }
        guard let pageHost = host(page), let startHost = host(start), pageHost == startHost else { return nil }
        guard isQuizPath(page.path) else { return nil }
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
        return stack.isEmpty
    }

    static func matches(_ url: URL, contains: String?, regex: NSRegularExpression?) -> Bool {
        if let contains, !contains.isEmpty { return url.absoluteString.contains(contains) }
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
    /// cleared after a Canvas exam. Everything but the exam server's own record goes. A parent
    /// domain of the server goes only when it also covers one of the exam's site rules.
    static func shouldRemove(domain: String, serverHost: String, ruleHosts: [String]) -> Bool {
        var d = domain.trimmingCharacters(in: .whitespaces).lowercased()
        if d.hasPrefix(".") { d.removeFirst() }
        let server = serverHost.lowercased()
        guard !d.isEmpty, d != server else { return false }
        if !server.isEmpty && server.hasSuffix("." + d) {
            return ruleHosts.contains { $0 == d || $0.hasSuffix("." + d) }
        }
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

    static func fill(code: String, origin: String) -> String {
        """
        (function () {
          if (location.origin !== \(jsString(origin))) { return 'wrongorigin'; }
          try {
            var el = document.querySelector('\(field)');
            if (!el) { return 'nofield'; }
            var v = \(jsString(code));
            var d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
            if (d && d.set) { d.set.call(el, v); } else { el.value = v; }
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            var form = el.form || el.closest('form');
            var btn = form ? form.querySelector('button[type=submit], input[type=submit], button:not([type]), .btn-primary') : null;
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
