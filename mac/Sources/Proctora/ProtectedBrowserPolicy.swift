import Foundation

/// Local client configuration only. No exam, university registration, or server admission.
struct ProtectedBrowserPolicy {
    let startURL: URL
    let origins: Set<String>

    init?(start: String, additionalOrigins: String, forbiddenOrigin: URL) {
        guard start.utf8.count <= 2048, additionalOrigins.utf8.count <= 8192,
              let url = URL(string: start.trimmingCharacters(in: .whitespacesAndNewlines)),
              let first = Self.origin(url), first != Self.origin(forbiddenOrigin) else { return nil }
        var allowed: Set<String> = [first]
        let entries = additionalOrigins.split(whereSeparator: { $0 == "," || $0.isNewline })
        guard entries.count <= 32 else { return nil }
        for entry in entries {
            let raw = entry.trimmingCharacters(in: .whitespacesAndNewlines)
            guard let u = URL(string: raw), let origin = Self.origin(u),
                  u.path.isEmpty || u.path == "/", u.query == nil, u.fragment == nil,
                  origin != Self.origin(forbiddenOrigin) else { return nil }
            allowed.insert(origin)
        }
        startURL = url
        origins = allowed
    }

    static func origin(_ url: URL) -> String? {
        guard url.scheme?.lowercased() == "https", url.user == nil, url.password == nil,
              url.port == nil || url.port == 443, let host = url.host?.lowercased(),
              host.count <= 253, !host.hasSuffix("."), host.contains("."),
              !host.contains("%"), !host.contains(":"),
              host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy({ label in
                  !label.isEmpty && label.count <= 63 && label.first != "-" && label.last != "-"
                      && label.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || $0 == 45 }
              }), !host.allSatisfy({ $0.isNumber || $0 == "." }) else { return nil }
        return "https://" + host
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
            let pattern = "^" + NSRegularExpression.escapedPattern(for: origin) + "(:443)?([/?#]|$)"
            rules.append(["trigger": ["url-filter": pattern], "action": ["type": "ignore-previous-rules"]])
        }
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
