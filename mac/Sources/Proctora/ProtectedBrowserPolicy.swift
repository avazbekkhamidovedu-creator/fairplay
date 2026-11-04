import Foundation
import Darwin

/// Local client configuration only. No exam, university registration, or server admission.
struct ProtectedBrowserPolicy {
    let startURL: URL
    let origins: Set<String>

    init?(start: String, additionalOrigins: String, forbiddenOrigin: URL, allowHTTP: Bool = false) {
        guard start.utf8.count <= 2048, additionalOrigins.utf8.count <= 8192,
              let url = Self.normalizedURL(start),
              let first = Self.origin(url), first != Self.origin(forbiddenOrigin),
              url.scheme?.lowercased() == "https" || (allowHTTP && Self.localHTTP(url)) else { return nil }
        var allowed: Set<String> = [first]
        let entries = additionalOrigins.split(whereSeparator: { $0 == "," || $0.isNewline })
        guard entries.count <= 32 else { return nil }
        for entry in entries {
            guard let u = Self.normalizedURL(String(entry)), let origin = Self.origin(u),
                  u.path.isEmpty || u.path == "/", u.query == nil, u.fragment == nil,
                  origin != Self.origin(forbiddenOrigin),
                  u.scheme?.lowercased() == "https" || (allowHTTP && Self.localHTTP(u)) else { return nil }
            allowed.insert(origin)
        }
        startURL = url
        origins = allowed
    }

    static func normalizedURL(_ raw: String) -> URL? {
        let value = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, !value.contains(where: { $0.isWhitespace || $0.isNewline }) else { return nil }
        let candidate = value.contains("://") ? value : "https://" + value
        guard let url = URL(string: candidate), origin(url) != nil else { return nil }
        return url
    }

    static func origin(_ url: URL) -> String? {
        guard let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme),
              url.user == nil, url.password == nil, let host = url.host?.lowercased(),
              !host.isEmpty, host.count <= 253, !host.hasSuffix("."), !host.contains("%"),
              url.port == nil || (1...65535).contains(url.port!) else { return nil }
        var ipv4 = in_addr()
        var ipv6 = in6_addr()
        let address = host.hasPrefix("[") && host.hasSuffix("]") ? String(host.dropFirst().dropLast()) : host
        let isIPv4 = inet_pton(AF_INET, address, &ipv4) == 1
        let isIPv6 = host.contains(":") && inet_pton(AF_INET6, address, &ipv6) == 1
        let isName = !host.allSatisfy({ $0.isNumber || $0 == "." }) && host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy { label in
            !label.isEmpty && label.count <= 63 && label.first != "-" && label.last != "-"
                && label.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }
        }
        guard isIPv4 || isIPv6 || isName else { return nil }
        let port = url.port ?? (scheme == "https" ? 443 : 80)
        let suffix = port == (scheme == "https" ? 443 : 80) ? "" : ":" + String(port)
        let authority = isIPv6 ? "[" + address + "]" : host
        return scheme + "://" + authority + suffix
    }

    static func localHTTP(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "http", let host = url.host?.lowercased() else { return false }
        return !host.contains(".") || host.hasSuffix(".local") || host.contains(":")
            || host.allSatisfy { $0.isNumber || $0 == "." }
    }

    func allows(_ url: URL?, mainFrame: Bool) -> Bool {
        guard let url else { return false }
        if !mainFrame && ["about:blank", "about:srcdoc"].contains(url.absoluteString.lowercased()) { return true }
        guard let origin = Self.origin(url) else { return false }
        return origins.contains(origin)
    }

    /// WebKit content blocker covers resource requests too, not just page navigations.
    /// No wildcard domains or port expansion. Inline/page-generated content remains site-controlled.
    var contentRules: String {
        var rules: [[String: Any]] = [["trigger": ["url-filter": ".*"], "action": ["type": "block"]]]
        for origin in origins.sorted() {
            // WebKit's content-blocker regex grammar does not support alternation.
            // Network URLs are canonicalized with '/' after the authority. Keep the
            // default and explicit 443 forms separate; never allow a generic ':'.
            let escaped = origin.replacingOccurrences(of: ".", with: "\\.")
                .replacingOccurrences(of: "[", with: "\\[")
                .replacingOccurrences(of: "]", with: "\\]")
            let parsed = URL(string: origin)!
            let suffixes = parsed.port == nil ? ["/", parsed.scheme == "https" ? ":443/" : ":80/"] : ["/"]
            for suffix in suffixes {
                rules.append(["trigger": ["url-filter": "^" + escaped + suffix],
                              "action": ["type": "ignore-previous-rules"]])
            }
        }
        // A network-free initial popup document is needed by opener-based SSO.
        // Native navigation policy still rejects arbitrary top-level about:blank navigation.
        rules.append(["trigger": ["url-filter": "^about:blank$"], "action": ["type": "ignore-previous-rules"]])
        return String(data: try! JSONSerialization.data(withJSONObject: rules), encoding: .utf8)!
    }
}

/// Testable sequencing. Effects are supplied by the native coordinator; tests never lock a Mac.
final class ProtectedBrowserFlow {
    enum State: Equatable { case setup, preparing, browsing, closed }
    private(set) var state: State = .setup
    private var generation: UUID?
    private(set) var ownsLock = false

    func prepare() -> UUID? {
        guard state == .setup else { return nil }
        let token = UUID()
        generation = token
        state = .preparing
        return token
    }

    func activate(_ token: UUID, acquireLock: () -> Bool, open: () -> Void) -> Bool {
        guard state == .preparing, generation == token else { return false }
        generation = nil
        guard acquireLock() else { state = .setup; return false }
        ownsLock = true
        state = .browsing
        open()
        return true
    }

    func preparationFailed(_ token: UUID) {
        guard state == .preparing, generation == token else { return }
        generation = nil
        state = .setup
    }

    func close(clear: () -> Void, releaseLock: () -> Void) {
        guard state != .closed else { return }
        let release = ownsLock
        ownsLock = false
        generation = nil
        state = .closed
        clear() // Content is removed before any requested unlock.
        if release { releaseLock() }
    }
}

struct ProtectedBrowserNavigation {
    enum Phase: Equatable { case loading, ready, failed, unavailable }
    private(set) var phase: Phase = .loading
    private(set) var currentURL: URL
    private(set) var requestedURL: URL
    private(set) var failureDiagnostic: String?

    init(startURL: URL) { currentURL = startURL; requestedURL = startURL }

    mutating func requested(_ url: URL) { if phase != .unavailable { requestedURL = url } }

    mutating func started() { if phase != .unavailable { phase = .loading } }
    mutating func committed(_ url: URL) { if phase != .unavailable { currentURL = url; requestedURL = url } }
    mutating func finished() { if phase != .unavailable { phase = .ready } }
    mutating func failed(diagnostic: String? = nil) { if phase != .unavailable { phase = .failed; failureDiagnostic = diagnostic } }
    mutating func discarded() { phase = .unavailable }

    var canReload: Bool { phase == .ready || phase == .failed }
    var message: String {
        let displayedURL = (phase == .loading || phase == .failed) ? requestedURL : currentURL
        let origin = ProtectedBrowserPolicy.origin(displayedURL) ?? "unavailable site"
        switch phase {
        case .loading: return "Locked browser · Loading " + origin
        case .ready: return "Locked browser · " + origin
        case .failed: return "Locked browser · " + origin + " · Page failed to load" + (failureDiagnostic.map { " · " + $0 } ?? "") + ". Reload or leave and unlock."
        case .unavailable: return "The website stopped. The Mac is still locked. Leave and unlock, then start again."
        }
    }
}

struct ProtectedBrowserPolicyDenial {
    let viewID: ObjectIdentifier
    let navigationID: ObjectIdentifier?
    let target: URL?

    init(view: AnyObject, navigation: AnyObject?, target: URL?) {
        viewID = ObjectIdentifier(view)
        navigationID = navigation.map(ObjectIdentifier.init)
        self.target = target
    }

    func matches(view: AnyObject, navigation: AnyObject?, failedURL: URL?) -> Bool {
        guard ObjectIdentifier(view) == viewID else { return false }
        if let navigationID { return navigation.map(ObjectIdentifier.init) == navigationID }
        return target != nil && target == failedURL
    }
}
