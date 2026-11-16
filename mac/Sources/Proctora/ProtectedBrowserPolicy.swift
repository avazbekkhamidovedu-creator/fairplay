import Foundation
import Darwin

/// Local client configuration only. No exam, university registration, or server admission.
struct ProtectedBrowserPolicy {
    let startURL: URL
    let origins: Set<String>
    private let referenceRules: [ExternalExamController.SiteRule]
    private let baseOrigins: Set<String>
    private let resourceOrigins: Set<String>
    private let resourceSuffixes: Set<String>
    private let assetOrigins: Set<String>
    /// The signed sites and resources stop working at this time unless a newer signed context extends it.
    var sitesExpire: Date?
    var clock: () -> Date = Date.init
    private var sitesLive: Bool { sitesExpire.map { clock() < $0 } ?? true }
    /// The signed sites that passed validation, in order (the picker lists these).
    let acceptedSites: [String]
    /// True when an allowed page is a Duke Box page: the Box sign-in chain is available to Box tabs.
    let hasBoxPage: Bool

    init?(start: String, additionalOrigins: String, forbiddenOrigin: URL, allowHTTP: Bool = false, referenceSites: [String] = [], resourceOrigins resources: [String] = [], assetOrigins assets: [String] = []) {
        guard start.utf8.count <= 2048, additionalOrigins.utf8.count <= 8192,
              let url = Self.normalizedURL(start),
              let first = Self.origin(url), first != Self.origin(forbiddenOrigin),
              url.scheme?.lowercased() == "https" || (allowHTTP && url.scheme?.lowercased() == "http") else { return nil }
        var allowed: Set<String> = [first]
        let entries = additionalOrigins.split(whereSeparator: { $0 == "," || $0.isNewline })
        guard entries.count <= 32 else { return nil }
        for entry in entries {
            guard let u = Self.normalizedURL(String(entry)), let origin = Self.origin(u),
                  u.path.isEmpty || u.path == "/", u.query == nil, u.fragment == nil,
                  origin != Self.origin(forbiddenOrigin),
                  u.scheme?.lowercased() == "https" || (allowHTTP && u.scheme?.lowercased() == "http") else { return nil }
            allowed.insert(origin)
        }
        // Reviewed institution profile: exact destinations, never a university-wide wildcard.
        if ["https://canvas.duke.edu", "https://go.canvas.duke.edu", "http://canvas.duke.edu", "http://go.canvas.duke.edu"].contains(first) {
            allowed.formUnion(["https://canvas.duke.edu", "https://go.canvas.duke.edu", "https://shib.oit.duke.edu"])
        }
        // Explicit HTTP sites may upgrade to TLS; HTTPS sites never silently downgrade.
        for origin in Array(allowed) where origin.hasPrefix("http://") {
            var secure = URLComponents(string: origin)!
            secure.scheme = "https"
            if secure.port == 80 { secure.port = nil }
            if let upgraded = secure.url.flatMap(Self.origin) { allowed.insert(upgraded) }
        }
        guard !allowed.contains(Self.origin(forbiddenOrigin) ?? "") else { return nil }
        baseOrigins = allowed
        var references: [ExternalExamController.SiteRule] = []
        var accepted: [String] = []
        guard referenceSites.count <= 32 else { return nil }
        for raw in referenceSites {
            guard raw.utf8.count <= 2048, let link = Self.normalizedURL(raw),
                  let origin = Self.origin(link), origin != Self.origin(forbiddenOrigin),
                  link.scheme == "https" || (allowHTTP && link.scheme == "http"),
                  !SiteDenyList.isDenied(host: link.host ?? ""),
                  let rule = ExternalExamController.SiteRule(link.absoluteString) else { continue }
            allowed.insert(origin)
            references.append(rule)
            accepted.append(link.absoluteString)
        }
        referenceRules = references
        acceptedSites = accepted
        // Subresource-only origins (CDNs, asset hosts): never a page of their own.
        let assetList = assets
        var assets: Set<String> = []
        var suffixes: Set<String> = []
        guard resources.count <= 64 else { return nil }
        for raw in (accepted.isEmpty ? [] : resources) {
            if BoxSignIn.isChainResource(raw) { continue }
            if raw.contains("*") {
                if let suffix = Self.resourceSuffix(raw) { suffixes.insert(suffix) }
                continue
            }
            guard raw.utf8.count <= 2048, let link = Self.normalizedURL(raw), let origin = Self.origin(link),
                  link.path.isEmpty || link.path == "/", link.query == nil, link.fragment == nil,
                  origin != Self.origin(forbiddenOrigin),
                  link.scheme == "https" || (allowHTTP && link.scheme == "http"),
                  !SiteDenyList.isDenied(host: link.host ?? "") else { continue }
            assets.insert(origin)
        }
        // A Box link among the allowed pages enables the sign-in chain, but only as frames and subresources of a page that is itself
        // on the chain (Box tab, see contentRules and the controller's verdict), never as resources of every tab. A chain host a
        // list sends as a plain resource is dropped above for the same reason.
        hasBoxPage = accepted.contains { BoxSignIn.isBoxPage(URL(string: $0)) }
        resourceOrigins = assets
        resourceSuffixes = suffixes
        // The OJ's own subresource hosts: always allowed for non-main-frame loads, with or without sites.
        var cdns: Set<String> = []
        for raw in assetList.prefix(16) {
            if let origin = Self.assetOrigin(raw), origin != Self.origin(forbiddenOrigin) { cdns.insert(origin) }
        }
        assetOrigins = cdns
        startURL = url
        origins = allowed
    }

    /// A host or suffix as a content-rule regex literal.
    static func escapeDots(_ host: String) -> String { host.replacingOccurrences(of: ".", with: "\\.") }

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

    /// True when the page (top frame) is on the OJ or one of its login origins, not a signed site.
    func isOJPage(_ top: URL?) -> Bool {
        guard let top, let origin = Self.origin(top) else { return false }
        return baseOrigins.contains(origin)
    }

    /// Frame (document) navigations only; subresources of an OJ page are covered by `contentRules`, which
    /// never applies the OJ-page allowance to documents, so an iframe keeps these strict rules.
    func allows(_ url: URL?, mainFrame: Bool) -> Bool {
        guard let url else { return false }
        if !mainFrame && ["about:blank", "about:srcdoc"].contains(url.absoluteString.lowercased()) { return true }
        guard let origin = Self.origin(url) else { return false }
        if baseOrigins.contains(origin) { return true }
        if !mainFrame, assetOrigins.contains(origin), !SiteDenyList.isDenied(host: url.host ?? "") { return true }
        guard sitesLive else { return false }
        if !mainFrame, resourceOrigins.contains(origin) || matchesResourceSuffix(url) { return true }
        guard origins.contains(origin) else { return false }
        let encodedPath = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath ?? url.path
        return referenceRules.contains { $0.matches(host: url.host?.lowercased() ?? "", encodedPath: encodedPath) }
    }

    /// Registry-controlled suffixes a wildcard may never sit on ("https://*.com").
    private static let publicSuffixes: Set<String> = [
        "ac", "ac.cn", "ac.in", "ac.jp", "ac.kr", "ac.uk", "ai", "amazonaws.com", "app", "appspot.com",
        "azurestaticapps.net", "azurewebsites.net", "bitbucket.io", "biz", "blogspot.com", "carrd.co",
        "cloudfront.net", "cn", "co", "co.in", "co.jp", "co.kr", "co.nz", "co.uk", "co.za", "codepen.io",
        "codesandbox.io", "com", "com.ar", "com.au", "com.br", "com.cn", "com.hk", "com.mx", "com.my", "com.ru",
        "com.sg", "com.tr", "com.tw", "com.uz", "de", "dev", "docs.google.com", "edu", "edu.au", "edu.cn", "edu.hk",
        "edu.my", "edu.sg", "edu.tr", "edu.tw", "edu.uz", "eu", "firebaseapp.com", "fly.dev", "github.com",
        "github.io", "githubusercontent.com", "gitlab.com", "gitlab.io", "glitch.me", "google.com",
        "googleusercontent.com", "gov", "gov.cn", "gov.hk", "gov.sg", "gov.tw", "gov.uk", "herokuapp.com",
        "hf.space", "info", "int", "io", "jp", "jsfiddle.net", "me", "me.uk", "mil", "neocities.org", "net",
        "net.au", "net.cn", "net.hk", "net.tw", "netlify.app", "netlify.com", "ngrok-free.app", "ngrok.io",
        "notion.site", "now.sh", "online", "onrender.com", "org", "org.au", "org.cn", "org.hk", "org.sg", "org.tr",
        "org.tw", "org.uk", "page", "pages.dev", "r2.dev", "repl.co", "replit.app", "replit.dev", "ru",
        "s3.amazonaws.com", "site", "sites.google.com", "stackblitz.io", "surge.sh", "translate.goog",
        "trycloudflare.com", "uk", "us", "uz", "vercel.app", "web.app", "weebly.com", "wixsite.com", "wordpress.com",
        "workers.dev", "xyz"
    ]

    /// Second-level labels under a two-letter country code are public suffixes too (com.au, edu.tw).
    private static let countrySecondLevels: Set<String> = ["com", "net", "org", "edu", "gov", "mil", "ac", "co", "go", "or", "ne", "gob", "nic", "sch", "gv"]

    static func isSharedSuffix(_ suffix: String) -> Bool {
        if publicSuffixes.contains(suffix) { return true }
        let labels = suffix.split(separator: ".", omittingEmptySubsequences: false)
        return labels.count == 2 && labels[1].count == 2 && countrySecondLevels.contains(String(labels[0]))
    }

    /// "https://*.suffix" -> "suffix": one leading wildcard label, a suffix of at least two labels that is
    /// not a public suffix, no port, path, query or fragment, and not covering a denied host.
    static func resourceSuffix(_ raw: String) -> String? {
        let prefix = "https://*."
        guard raw.utf8.count <= 253 + prefix.utf8.count, raw.hasPrefix(prefix) else { return nil }
        let suffix = String(raw.dropFirst(prefix.count))
        guard !suffix.isEmpty, !suffix.contains(where: { "/:?#*@%[]".contains($0) }), !suffix.hasSuffix("."),
              suffix == suffix.lowercased(), !isSharedSuffix(suffix),
              let url = URL(string: "https://" + suffix), origin(url) == "https://" + suffix,
              suffix.split(separator: ".").count >= 2, !SiteDenyList.isDenied(host: "probe." + suffix), !SiteDenyList.exactHostIsUnder(suffix) else { return nil }
        return suffix
    }

    /// "https://host[:port]" exactly: lowercase, no userinfo, path, query, fragment or wildcard, not a denied host.
    static func assetOrigin(_ raw: String) -> String? {
        guard raw.utf8.count <= 300, raw == raw.lowercased(), raw.hasPrefix("https://"), !raw.contains("*"),
              let url = URL(string: raw), url.path.isEmpty, url.query == nil, url.fragment == nil, url.user == nil,
              let origin = origin(url), origin == raw || origin + ":443" == raw,
              !SiteDenyList.isDenied(host: url.host ?? "") else { return nil }
        return origin
    }

    private func matchesResourceSuffix(_ url: URL) -> Bool {
        guard url.scheme?.lowercased() == "https", url.port == nil || url.port == 443,
              let host = url.host?.lowercased(), !SiteDenyList.isDenied(host: host) else { return false }
        return resourceSuffixes.contains { host.hasSuffix("." + $0) }
    }

    var downloadOrigins: Set<String> {
        var set = assetOrigins.filter { !SiteDenyList.isDenied(host: URL(string: $0)?.host ?? "") }
        if let first = Self.origin(startURL) { set.insert(first) }
        return set
    }

    /// The first tab stays inside DKU OJ and its login hosts; allowed sites open in their own tabs.
    func allowsMainTab(_ url: URL?) -> Bool {
        guard let url, allows(url, mainFrame: true), let origin = Self.origin(url) else { return false }
        return baseOrigins.contains(origin)
    }

    /// True for an allowed site that must open in a separate tab instead of the main one.
    func opensInOwnTab(_ url: URL?) -> Bool {
        guard let url, allows(url, mainFrame: true) else { return false }
        return !allowsMainTab(url)
    }

    static let subresourceTypes = ["image", "style-sheet", "script", "font", "raw", "svg-document", "media", "fetch", "websocket", "ping", "other"]

    /// Loopback, link-local, private-range and local-name hosts. No alternation or groups in WebKit's grammar.
    static let localNetworkFilters: [String] = {
        var f = ["127\\.[0-9]+\\.[0-9]+\\.[0-9]+", "10\\.[0-9]+\\.[0-9]+\\.[0-9]+", "0\\.[0-9]+\\.[0-9]+\\.[0-9]+",
                 "192\\.168\\.[0-9]+\\.[0-9]+", "169\\.254\\.[0-9]+\\.[0-9]+"].map { "^https?://" + $0 + "[:/]" }
        f += ["1[6-9]", "2[0-9]", "3[01]"].map { "^https?://172\\." + $0 + "\\.[0-9]+\\.[0-9]+[:/]" }
        f += ["^https?://localhost[:/]", "^https?://\\[::1\\]", "^https?://\\[::\\]", "^https?://\\[::ffff:",
              "^https?://\\[f[cd][0-9a-f][0-9a-f]:", "^https?://\\[fe[89ab][0-9a-f]:"]
        for suffix in ["local", "localhost", "internal", "lan"] { f.append("^https?://[a-z0-9.-]+\\." + suffix + "[:/]") }
        return f
    }()

    /// WebKit content blocker covers resource requests too, not just page navigations.
    /// No wildcard domains or port expansion. Inline/page-generated content remains site-controlled.
    var contentRules: String {
        var rules: [[String: Any]] = [["trigger": ["url-filter": ".*"], "action": ["type": "block"]]]
        // Wildcard resource hosts first; denied hosts under them are blocked again; exact origins last.
        for suffix in resourceSuffixes.sorted() {
            let escaped = suffix.replacingOccurrences(of: ".", with: "\\.")
            for tail in ["/", ":443/"] {
                rules.append(["trigger": ["url-filter": "^https://[a-z0-9.-]+\\." + escaped + tail],
                              "action": ["type": "ignore-previous-rules"]])
            }
            for denied in SiteDenyList.deniedDomains(under: suffix) {
                let d = denied.replacingOccurrences(of: ".", with: "\\.")
                rules.append(["trigger": ["url-filter": "^https?://" + d + "[:/]"], "action": ["type": "block"]])
                rules.append(["trigger": ["url-filter": "^https?://[a-z0-9.-]+\\." + d + "[:/]"], "action": ["type": "block"]])
            }
        }
        for origin in (origins.union(resourceOrigins).union(assetOrigins)).sorted() {
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
        // Duke Box sign-in chain: its hosts load only inside a page that is itself on the chain (a Box tab), never in the exam
        // tab or another site; account hosts only on /login and /sso/ping_federate. WebKit has no per-tab lists, so the top page scopes it.
        if hasBoxPage {
            var tops: [String] = []
            for host in BoxSignIn.appHosts.union(BoxSignIn.signInExact).sorted() { tops.append("^https://" + Self.escapeDots(host) + "/") }
            for base in BoxSignIn.signInSubdomainsOf { tops.append("^https://[a-z0-9.-]+\\." + Self.escapeDots(base) + "/") }
            func chainRule(_ filter: String) {
                rules.append(["trigger": ["url-filter": filter, "if-top-url": tops], "action": ["type": "ignore-previous-rules"]])
            }
            for host in BoxSignIn.signInExact.union(BoxSignIn.assetExact).sorted() {
                let escaped = Self.escapeDots(host)
                if BoxSignIn.loginPathOnly.contains(host) {
                    for prefix in BoxSignIn.accountPathPrefixes { chainRule("^https://" + escaped + prefix) }
                } else {
                    chainRule("^https://" + escaped + "/")
                }
            }
            for base in BoxSignIn.signInSubdomainsOf + BoxSignIn.tabAssetSubdomainsOf {
                chainRule("^https://[a-z0-9.-]+\\." + Self.escapeDots(base) + "/")
            }
        }
        // Pages on an OJ origin may load anything but denied hosts; signed sites keep the rules above.
        var topURLs: [String] = []
        for origin in baseOrigins.sorted() {
            let escaped = origin.replacingOccurrences(of: ".", with: "\\.")
                .replacingOccurrences(of: "[", with: "\\[").replacingOccurrences(of: "]", with: "\\]")
            let parsed = URL(string: origin)!
            let tails = parsed.port == nil ? ["/", parsed.scheme == "https" ? ":443/" : ":80/"] : ["/"]
            topURLs += tails.map { "^" + escaped + $0 }
        }
        // Never "document": an iframe on an OJ page keeps the strict rules above.
        let types = Self.subresourceTypes
        func scoped(_ filter: String, _ action: String, typed: Bool = true) -> [String: Any] {
            var trigger: [String: Any] = ["url-filter": filter, "if-top-url": topURLs]
            if typed { trigger["resource-type"] = types }
            return ["trigger": trigger, "action": ["type": action]]
        }
        rules.append(scoped("^https?://", "ignore-previous-rules"))
        // Userinfo and trailing-dot hosts dodge the host-anchored deny rules below.
        rules.append(scoped("^https?://[^/]*@", "block", typed: false))
        rules.append(scoped("^https?://[^/:]*\\.[:/]", "block", typed: false))
        // The OJ page may not reach this Mac's own network; only the OJ's configured hosts stay reachable.
        for filter in Self.localNetworkFilters { rules.append(scoped(filter, "block")) }
        for origin in baseOrigins.union(assetOrigins).union(resourceOrigins).sorted() {
            let escaped = origin.replacingOccurrences(of: ".", with: "\\.")
                .replacingOccurrences(of: "[", with: "\\[").replacingOccurrences(of: "]", with: "\\]")
            let parsed = URL(string: origin)!
            let tails = parsed.port == nil ? ["/", parsed.scheme == "https" ? ":443/" : ":80/"] : ["/"]
            for tail in tails { rules.append(scoped("^" + escaped + tail, "ignore-previous-rules")) }
        }
        func escape(_ host: String) -> String { Self.escapeDots(host) }
        for denied in SiteDenyList.allSuffixes {
            for prefix in ["^https?://", "^https?://[a-z0-9.-]+\\."] {
                rules.append(scoped(prefix + escape(denied) + "[:/]", "block", typed: false))
            }
        }
        for denied in SiteDenyList.allExactHosts {
            rules.append(scoped("^https?://" + escape(denied) + "[:/]", "block", typed: false))
        }
        for host in SiteDenyList.allowedExceptions {
            rules.append(scoped("^https?://" + escape(host) + "[:/]", "ignore-previous-rules"))
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
        case .loading: return "Loading " + origin
        case .ready: return origin
        case .failed: return origin + " · Page failed to load" + (failureDiagnostic.map { " · " + $0 } ?? "") + ". Reload or leave and unlock."
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
