import Foundation

/// 2026-10-30: the rules of the Duke Box pane an open exam can show inside the locked exam window.
/// The server sends the lists (`tools.box`, forwarded by the page as {action:"open_box", box}); they are
/// data, but the app also keeps a built-in ceiling: a list outside it makes the pane fail to open.
///
/// Pure decisions only (BoxOverlay.swift acts on them), for the tests. The same file, rule for
/// rule, is the iPad's FairPlayCore BoxPolicy.swift.
struct BoxPolicy {

    static let notAvailable = "Not available"
    static let maxEntries = 64
    static let maxText = 200

    private static let appCeiling: Set<String> = ["duke.box.com", "duke.app.box.com"]
    private static let sharedBases = ["box.com", "box.net", "boxcdn.net", "boxcloud.com", "duke.edu", "duosecurity.com"]

    struct SignInHost: Equatable {
        let host: String
        let paths: [String]?
    }

    struct BlockRule: Equatable {
        let host: String
        let path: String
        let methods: [String]?
    }

    enum Navigation: Equatable {
        case allow
        case block
    }

    enum Popup: Equatable {
        case loadHere
        case refuse
    }

    let startURL: URL
    let appHosts: [String]
    let pathPrefix: String
    let signInHosts: [SignInHost]
    let assetHosts: [String]
    let blockedRequests: [BlockRule]

    /// nil when the config is not usable or leaves the built-in ceiling. `allowDev` is true only in dev
    /// builds: it admits the fakebox-*.test app hosts and any *.test sign-in or asset host.
    init?(body: [String: Any]?, allowDev: Bool) {
        guard let body,
              let startRaw = body["start_url"] as? String, startRaw.utf8.count <= Self.maxText,
              let start = URL(string: startRaw),
              let prefix = body["path_prefix"] as? String,
              Self.matches(prefix, #"^/s/[A-Za-z0-9]{10,64}$"#),
              let appRaw = Self.strings(body["app_hosts"]), !appRaw.isEmpty, appRaw.count <= 8 else { return nil }
        let apps = appRaw.map { $0.lowercased() }
        guard apps.allSatisfy({ Self.validHost($0) && Self.appCeilingOK($0, allowDev: allowDev) }) else { return nil }

        guard let comps = URLComponents(url: start, resolvingAgainstBaseURL: false),
              comps.scheme?.lowercased() == "https", comps.user == nil, comps.password == nil, comps.port == nil,
              comps.query == nil, comps.fragment == nil,
              let host = comps.host?.lowercased(), apps.contains(host),
              comps.percentEncodedPath == prefix || comps.percentEncodedPath == prefix + "/" else { return nil }

        var signIn: [SignInHost] = []
        guard let signRaw = body["sign_in_hosts"] as? [Any], signRaw.count <= Self.maxEntries else { return nil }
        for item in signRaw {
            guard let d = item as? [String: Any], let h = d["host"] as? String,
                  let pattern = Self.pattern(h.lowercased()),
                  Self.sharedCeilingOK(pattern, allowDev: allowDev) else { return nil }
            var paths: [String]?
            if let p = d["paths"], !(p is NSNull) {
                guard let list = Self.strings(p), list.count <= Self.maxEntries,
                      list.allSatisfy({ $0.hasPrefix("/") && $0.utf8.count <= Self.maxText }) else { return nil }
                paths = list
            }
            signIn.append(SignInHost(host: h.lowercased(), paths: paths))
        }

        var assets: [String] = []
        guard let assetRaw = body["asset_hosts"] as? [Any], assetRaw.count <= Self.maxEntries else { return nil }
        for item in assetRaw {
            guard let h = item as? String, let pattern = Self.pattern(h.lowercased()),
                  Self.sharedCeilingOK(pattern, allowDev: allowDev) else { return nil }
            assets.append(h.lowercased())
        }

        var blocked: [BlockRule] = []
        guard let blockRaw = body["blocked_requests"] as? [Any], blockRaw.count <= Self.maxEntries * 2 else { return nil }
        for item in blockRaw {
            guard let d = item as? [String: Any], let h = d["host"] as? String,
                  h == "*" || Self.pattern(h.lowercased()) != nil,
                  let p = d["path"] as? String, p.hasPrefix("/"), p.utf8.count <= Self.maxText else { return nil }
            var methods: [String]?
            if let m = d["methods"], !(m is NSNull) {
                guard let list = Self.strings(m), !list.isEmpty, list.count <= 8,
                      list.allSatisfy({ Self.matches($0, #"^[A-Z]{3,7}$"#) }) else { return nil }
                methods = list
            }
            blocked.append(BlockRule(host: h.lowercased(), path: p, methods: methods))
        }

        self.startURL = start
        self.appHosts = apps
        self.pathPrefix = prefix
        self.signInHosts = signIn
        self.assetHosts = assets
        self.blockedRequests = blocked
    }

    // MARK: - Lifecycle

    /// The refusal text when the pane may not open now, else nil: only while an exam session is locked
    /// and running, never in the pre-exam lock and never unlocked.
    static func openRefusal(locked: Bool, preLock: Bool, hasAttempt: Bool) -> String? {
        locked && !preLock && hasAttempt ? nil : notAvailable
    }

    /// The pane never outlives the lock.
    static func mustClose(locked: Bool) -> Bool { !locked }

    static func message(_ state: String, error: String? = nil) -> [String: Any] {
        var m: [String: Any] = ["type": "boxResult", "state": state]
        if let error { m["error"] = error }
        return m
    }

    // MARK: - Navigation

    /// A page load (main frame or sub-frame). App hosts only under the shared item's path; sign-in hosts
    /// on their listed paths; asset hosts only in sub-frames. https, default port, no user name, plain
    /// ASCII host with no punycode label. A blocked_requests match always wins.
    func decide(_ url: URL, mainFrame: Bool, method: String = "GET") -> Navigation {
        if !mainFrame, Self.isBlankFrame(url) { return .allow }
        guard let host = Self.basicHost(url), let path = Self.cleanPath(url) else { return .block }
        if isBlocked(host: host, path: path, method: method) { return .block }
        if appHosts.contains(host) { return underPrefix(path) ? .allow : .block }
        if signInAllows(host: host, path: path) { return .allow }
        if !mainFrame, assetHosts.contains(where: { Self.hostMatches($0, host) }) { return .allow }
        return .block
    }

    /// A window.open / target=_blank: only a sign-in page opens, and in the same view.
    func popup(_ url: URL, method: String = "GET") -> Popup {
        guard let host = Self.basicHost(url), let path = Self.cleanPath(url),
              !isBlocked(host: host, path: path, method: method),
              signInAllows(host: host, path: path) else { return .refuse }
        return .loadHere
    }

    private func underPrefix(_ path: String) -> Bool {
        path == pathPrefix || path.hasPrefix(pathPrefix + "/")
    }

    private func signInAllows(host: String, path: String) -> Bool {
        signInHosts.contains { entry in
            guard Self.hostMatches(entry.host, host) else { return false }
            guard let paths = entry.paths else { return true }
            return paths.contains { Self.glob($0, path, caseInsensitive: false) }
        }
    }

    private func isBlocked(host: String, path: String, method: String) -> Bool {
        let trimmed = path.count > 1 && path.hasSuffix("/") ? String(path.dropLast()) : path
        return blockedRequests.contains { rule in
            guard rule.host == "*" || Self.hostMatches(rule.host, host) else { return false }
            guard rule.methods == nil || rule.methods!.contains(method.uppercased()) else { return false }
            return Self.glob(rule.path, path, caseInsensitive: true) || Self.glob(rule.path, trimmed, caseInsensitive: true)
        }
    }

    static func isBlankFrame(_ url: URL) -> Bool {
        let s = url.absoluteString.lowercased()
        return s == "about:blank" || s == "about:srcdoc"
    }

    // MARK: - Event text (host and first path segment only, never a URL, query or token)

    static func blockedDetail(_ url: URL?) -> String {
        "box \(safeHost(url)) \(pathClass(url))"
    }

    static func openedDetail(_ url: URL?) -> String { safeHost(url) }

    static func safeHost(_ url: URL?) -> String {
        guard let h = url?.host?.lowercased(), !h.isEmpty, h.utf8.count <= 100,
              h.utf8.allSatisfy({ ($0 >= 0x61 && $0 <= 0x7A) || ($0 >= 0x30 && $0 <= 0x39) || $0 == 0x2E || $0 == 0x2D }) else { return "?" }
        return h
    }

    /// The first path segment only; "/" for the root; "?" when it is not a short lowercase word (a token, an id).
    static func pathClass(_ url: URL?) -> String {
        guard let url, let comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return "?" }
        let seg = comps.percentEncodedPath.split(separator: "/", maxSplits: 1, omittingEmptySubsequences: true).first.map(String.init) ?? ""
        if seg.isEmpty { return "/" }
        let plain = seg.utf8.count <= 16 && seg.utf8.allSatisfy { ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x2D || $0 == 0x5F }
        return plain ? seg : "?"
    }

    /// The host as the strip shows it (punycode as WebKit reports it, so a look-alike shows).
    static func displayHost(_ url: URL?) -> String { url?.host?.lowercased() ?? "" }

    // MARK: - Content rule list and page script

    /// A WKContentRuleList: everything is blocked, the listed hosts are let through again, then
    /// blocked_requests are blocked on top. WebKit sees no HTTP method, so a rule limited to methods that
    /// do not include GET is left to the page script (and to the navigation check) instead.
    func contentRuleListJSON() -> String {
        var rules: [[String: Any]] = [["trigger": ["url-filter": ".*"], "action": ["type": "block"]]]
        let listed = appHosts + signInHosts.map(\.host) + assetHosts
        for h in listed {
            rules.append(["trigger": ["url-filter": Self.hostRegex(h) + "/"], "action": ["type": "ignore-previous-rules"]])
        }
        for rule in blockedRequests where rule.methods == nil || rule.methods!.contains("GET") {
            let head = rule.host == "*" ? "^https://[a-z0-9.-]+" : Self.hostRegex(rule.host)
            let body = (rule.path.hasSuffix("*") ? String(rule.path.dropLast()) : rule.path)
                .split(separator: "*", omittingEmptySubsequences: false)
                .map { Self.regexEscape(String($0)) }.joined(separator: ".*")
            let tail = rule.path.hasSuffix("*") ? [""] : ["$", "\\?"]
            for t in tail {
                rules.append(["trigger": ["url-filter": head + body + t], "action": ["type": "block"]])
            }
        }
        let data = (try? JSONSerialization.data(withJSONObject: rules)) ?? Data("[]".utf8)
        return String(decoding: data, as: UTF8.self)
    }

    /// Rules the content rule list cannot express (a method that is not GET): the page script refuses
    /// those fetch / XHR / beacon calls and tells the app the host and first path segment.
    private var scriptRules: [[String: Any]] {
        blockedRequests.compactMap { rule in
            guard let m = rule.methods, !m.contains("GET") else { return nil }
            return ["h": rule.host, "p": Self.globRegexSource(rule.path), "m": m]
        }
    }

    func guardScript(handler: String) -> String {
        let data = (try? JSONSerialization.data(withJSONObject: scriptRules)) ?? Data("[]".utf8)
        let rules = String(decoding: data, as: UTF8.self)
        return """
        (function () {
          document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
          document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);
          try { Object.defineProperty(window, 'print', { value: function () {}, writable: false, configurable: false }); } catch (e) {}
          var rules = \(rules);
          var sent = 0;
          function hostOk(h, host) {
            if (h === '*') return true;
            if (h.indexOf('*.') === 0) { var b = h.slice(2); return host.length > b.length && host.slice(-b.length - 1) === '.' + b; }
            return h === host;
          }
          function refused(url, method) {
            var u;
            try { u = new URL(String(url), location.href); } catch (e) { return false; }
            var m = String(method || 'GET').toUpperCase();
            for (var i = 0; i < rules.length; i++) {
              var r = rules[i];
              if (r.m.indexOf(m) >= 0 && hostOk(r.h, u.hostname.toLowerCase()) && new RegExp(r.p, 'i').test(u.pathname)) {
                if (sent < 20) {
                  sent++;
                  try { window.webkit.messageHandlers.\(handler).postMessage({ host: u.hostname, path: u.pathname }); } catch (e) {}
                }
                return true;
              }
            }
            return false;
          }
          var f = window.fetch;
          if (f) {
            window.fetch = function (input, init) {
              var url = input && input.url ? input.url : input;
              var method = (init && init.method) || (input && input.method) || 'GET';
              if (refused(url, method)) return Promise.reject(new TypeError('Failed to fetch'));
              return f.apply(this, arguments);
            };
          }
          var open = XMLHttpRequest.prototype.open;
          XMLHttpRequest.prototype.open = function (method, url) {
            if (refused(url, method)) { this.__fpBlocked = true; }
            return open.apply(this, arguments);
          };
          var send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.send = function () {
            if (this.__fpBlocked) { throw new DOMException('Blocked', 'NetworkError'); }
            return send.apply(this, arguments);
          };
          if (navigator.sendBeacon) {
            var beacon = navigator.sendBeacon.bind(navigator);
            navigator.sendBeacon = function (url, data) { return refused(url, 'POST') ? false : beacon(url, data); };
          }
          document.addEventListener('click', function (e) {
            var t = e.target;
            if (t && t.tagName === 'INPUT' && String(t.type).toLowerCase() === 'file') { e.preventDefault(); e.stopImmediatePropagation(); }
          }, true);
        })();
        """
    }

    // MARK: - Host, path and glob helpers

    /// "host" or "*.base" -> the match test. A "*.base" is every subdomain of base, not base itself.
    static func hostMatches(_ entry: String, _ host: String) -> Bool {
        if entry.hasPrefix("*.") {
            let base = String(entry.dropFirst(2))
            return host.count > base.count && host.hasSuffix("." + base)
        }
        return entry == host
    }

    private static func hostRegex(_ entry: String) -> String {
        if entry.hasPrefix("*.") {
            return "^https://[a-z0-9.-]+\\." + regexEscape(String(entry.dropFirst(2)))
        }
        return "^https://" + regexEscape(entry)
    }

    static func regexEscape(_ s: String) -> String {
        var out = ""
        for ch in s {
            if "\\.+?()[]{}^$|*".contains(ch) { out.append("\\") }
            out.append(ch)
        }
        return out
    }

    private static func globRegexSource(_ glob: String) -> String {
        "^" + glob.split(separator: "*", omittingEmptySubsequences: false).map { regexEscape(String($0)) }.joined(separator: ".*")
            + (glob.hasSuffix("*") ? "" : "/?$")
    }

    /// `*` matches any run of characters (also "/").
    static func glob(_ pattern: String, _ s: String, caseInsensitive: Bool) -> Bool {
        let p = Array((caseInsensitive ? pattern.lowercased() : pattern).utf8)
        let t = Array((caseInsensitive ? s.lowercased() : s).utf8)
        var pi = 0, ti = 0, star = -1, mark = 0
        while ti < t.count {
            if pi < p.count, p[pi] == 0x2A {
                star = pi; mark = ti; pi += 1
            } else if pi < p.count, p[pi] == t[ti] {
                pi += 1; ti += 1
            } else if star >= 0 {
                pi = star + 1; mark += 1; ti = mark
            } else {
                return false
            }
        }
        while pi < p.count, p[pi] == 0x2A { pi += 1 }
        return pi == p.count
    }

    /// The host of an https URL with the default port and no user name; nil otherwise, or when the host
    /// is not plain ASCII or has a punycode label.
    static func basicHost(_ url: URL) -> String? {
        guard url.scheme?.lowercased() == "https",
              let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.user == nil, c.password == nil, c.port == nil || c.port == 443,
              let host = c.host?.lowercased(), validHost(host) else { return nil }
        return host
    }

    /// The decoded path, or nil for one that hides a separator or a dot segment.
    static func cleanPath(_ url: URL) -> String? {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        let raw = c.percentEncodedPath.lowercased()
        if raw.contains("%2f") || raw.contains("%5c") || raw.contains("%00") || raw.contains("\\") || raw.contains("//") { return nil }
        let path = c.path
        if path.split(separator: "/").contains(where: { $0 == ".." || $0 == "." }) { return nil }
        return path.isEmpty ? "/" : path
    }

    static func validHost(_ h: String) -> Bool {
        guard h.utf8.count <= 253, h.utf8.allSatisfy({ ($0 >= 0x61 && $0 <= 0x7A) || ($0 >= 0x30 && $0 <= 0x39) || $0 == 0x2E || $0 == 0x2D }) else { return false }
        let labels = h.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.count >= 2 else { return false }
        return labels.allSatisfy { l in
            !l.isEmpty && l.utf8.count <= 63 && !l.hasPrefix("-") && !l.hasSuffix("-") && !l.hasPrefix("xn--")
        }
    }

    private struct Pattern {
        let wildcard: Bool
        let base: String
    }

    private static func pattern(_ h: String) -> Pattern? {
        if h.hasPrefix("*.") {
            let base = String(h.dropFirst(2))
            return validHost(base) ? Pattern(wildcard: true, base: base) : nil
        }
        return validHost(h) ? Pattern(wildcard: false, base: h) : nil
    }

    private static func appCeilingOK(_ h: String, allowDev: Bool) -> Bool {
        if appCeiling.contains(h) { return true }
        return allowDev && h.hasPrefix("fakebox-") && h.hasSuffix(".test")
    }

    private static func sharedCeilingOK(_ p: Pattern, allowDev: Bool) -> Bool {
        if allowDev, p.base.hasSuffix(".test") { return true }
        return sharedBases.contains { p.base.hasSuffix("." + $0) || (p.wildcard && p.base == $0) }
    }

    private static func strings(_ v: Any?) -> [String]? {
        guard let a = v as? [Any] else { return nil }
        let s = a.compactMap { $0 as? String }
        return s.count == a.count ? s : nil
    }

    private static func matches(_ s: String, _ regex: String) -> Bool {
        s.range(of: regex, options: .regularExpression) != nil
    }
}
