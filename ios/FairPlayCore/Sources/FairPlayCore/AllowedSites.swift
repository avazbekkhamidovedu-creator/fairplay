import Foundation

/// 2026-10-23: teacher-allowed websites inside the exam. The exam page shows them in an
/// <iframe> side pane and sends `allowed_sites` with "lock" / "session" (also
/// "openExternal" / "prelock"). A message without the key leaves the list as it is; an
/// explicit [] means none; the unlock clears it. While locked, a SUB-FRAME may load https
/// pages on a listed site, plus about:blank / about:srcdoc. The main frame still shows only
/// the exam pages, no window ever opens and nothing downloads.
///
/// Entry rule (review 2026-10-23): "docs.python.org" is that host only (and the same host with "www."
/// added or removed); "*.python.org" is python.org and every subdomain of it. A wildcard on a
/// public suffix or a shared-hosting domain ("*.github.io", "*.com", "*.co.uk") is ignored:
/// anyone can put a page there.
///
/// Pure decisions only (ExamViewController acts on them), for the tests. Same rules as
/// the Mac's AllowedSites.swift and the Windows NavigationPolicy. iPadOS Assessment Mode: the
/// frames are inside our own web view, so the same rules apply there.
public enum AllowedSites {

    /// Most entries a page may send (more are ignored).
    public static let maxEntries = 200

    /// Entries from a bridge message, normalised (see `normalizedEntry`), without
    /// duplicates. Anything else in the list is dropped. Missing or not a list = [].
    public static func parse(_ raw: Any?) -> [String] {
        guard let list = raw as? [Any] else { return [] }
        var out: [String] = []
        for case let s as String in list {
            guard out.count < maxEntries else { break }
            if let e = normalizedEntry(s), !out.contains(e) { out.append(e) }
        }
        return out
    }

    /// The list after a "lock" / "session" / "prelock" / "openExternal" message: unchanged when
    /// the message has no `allowed_sites` key, otherwise what it sends ([] = none).
    public static func updated(_ current: [String], from body: [String: Any]) -> [String] {
        guard let raw = body["allowed_sites"] else { return current }
        return parse(raw)
    }

    public static let wildcardPrefix = "*."

    /// Wildcard bases that are never allowed, part 1: public suffixes, where anyone can
    /// register a name. Matched exactly ("*.bbc.co.uk" is fine, "*.co.uk" is not). The one list
    /// on the iPad (same as the Mac's and Windows' NavigationPolicy).
    public static let publicSuffixes: Set<String> = [
        "com", "net", "org", "edu", "gov", "mil", "int", "info", "biz", "io", "app", "dev", "page",
        "site", "online", "xyz", "me", "co", "ai", "cn", "uk", "us", "eu", "de", "ru", "uz", "jp",
        "co.uk", "org.uk", "ac.uk", "gov.uk", "com.cn", "net.cn", "org.cn", "edu.cn", "gov.cn", "ac.cn",
    ]

    /// Part 2 (2026-10-23, review): shared hosting, where anyone can get a subdomain. A base
    /// that is one of these OR ends with one ("*.s3.us-east-1.amazonaws.com") is refused.
    /// Same list on all three platforms.
    public static let sharedHosts: Set<String> = [
        "github.io", "githubusercontent.com", "github.com", "github.dev", "gitlab.io", "gitlab.com",
        "bitbucket.io", "gitpod.io", "codesandbox.io", "stackblitz.io", "codepen.io", "jsfiddle.net",
        "vercel.app", "now.sh", "netlify.app", "netlify.com", "pages.dev", "workers.dev", "r2.dev",
        "trycloudflare.com", "web.app", "firebaseapp.com", "appspot.com", "herokuapp.com",
        "blogspot.com", "wordpress.com", "glitch.me", "replit.app", "replit.dev", "repl.co",
        "onrender.com", "fly.dev", "deno.dev", "streamlit.app", "huggingface.co", "hf.space",
        "pythonanywhere.com", "azurewebsites.net", "azurestaticapps.net", "azureedge.net",
        "cloudfront.net", "amazonaws.com", "s3.amazonaws.com", "wixsite.com", "weebly.com",
        "notion.site", "carrd.co", "surge.sh", "neocities.org", "ngrok.io", "ngrok-free.app",
        "loca.lt", "google.com", "googleusercontent.com", "sites.google.com", "docs.google.com",
        "translate.goog",
    ]

    /// Second-level labels under a two-letter country code that are public suffixes too
    /// ("com.au", "co.jp", "ac.kr", "edu.uz").
    private static let countrySecondLevels: Set<String> = [
        "com", "net", "org", "edu", "gov", "mil", "ac", "co", "go", "or", "ne", "gob", "nic", "sch", "gv",
    ]

    /// A wildcard base nobody may use: a public suffix, shared hosting (or under it), a
    /// single label, a country-code second level, or Blogger in any country
    /// ("blogspot.com", "blogspot.co.uk", "x.blogspot.de": a "blogspot" label right above a
    /// top-level domain or a country-code second level).
    public static func isSharedSuffix(_ host: String) -> Bool {
        if publicSuffixes.contains(host) { return true }
        if sharedHosts.contains(where: { host == $0 || host.hasSuffix("." + $0) }) { return true }
        let labels = host.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        if labels.count < 2 { return true }
        if labels.count == 2 && labels[1].count == 2 && countrySecondLevels.contains(labels[0]) { return true }
        for (i, label) in labels.enumerated() where label == "blogspot" {
            let rest = Array(labels[(i + 1)...])
            if rest.count == 1 || (rest.count == 2 && rest[1].count == 2 && countrySecondLevels.contains(rest[0])) {
                return true
            }
        }
        return false
    }

    /// An allowed-site entry in canonical form: a usable host name (`normalizedHost`,
    /// `isUsableEntry`), or "*." + a host name that is neither an IP literal nor a shared
    /// suffix. nil for anything else (a wildcard anywhere else, a scheme, a path, an IP
    /// address...).
    public static func normalizedEntry(_ raw: String) -> String? {
        let t = raw.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix(wildcardPrefix) {
            let rest = String(t.dropFirst(wildcardPrefix.count))
            guard rest == rest.trimmingCharacters(in: .whitespaces), let base = normalizedHost(rest),
                  !isIPLiteral(base), !isSharedSuffix(base) else { return nil }
            return wildcardPrefix + base
        }
        guard let h = normalizedHost(t), isUsableEntry(h) else { return nil }
        return h
    }

    /// Lowercased, without one trailing dot or IPv6 brackets. nil when it is not a plain
    /// host name: a scheme, path, port, "*", spaces, empty labels or non-ASCII
    /// characters. International names come as punycode ("xn--e1afmkfd.xn--p1ai"; the
    /// server sends them so, 2026-10-23) and WebKit reports frame hosts the same way, so they
    /// are compared as they are.
    public static func normalizedHost(_ raw: String) -> String? {
        var h = raw.trimmingCharacters(in: .whitespaces).lowercased()
        if h.hasPrefix("[") && h.hasSuffix("]") && h.count > 2 { h = String(h.dropFirst().dropLast()) }
        if h.hasSuffix(".") { h.removeLast() }
        guard !h.isEmpty, h.count <= 253 else { return nil }
        if h.contains(":") {
            // An IPv6 literal: hex digits, ":" and "." (an embedded IPv4 tail) only, and at
            // least two ":" (so "abc.de:443", a host with a port, is not one).
            return h.allSatisfy({ ipv6Chars.contains($0) }) && h.filter({ $0 == ":" }).count >= 2 ? h : nil
        }
        guard h.allSatisfy({ hostChars.contains($0) }), !h.hasPrefix("."), !h.hasSuffix("."),
              !h.contains("..") else { return nil }
        return h
    }

    private static let hostChars = Set("abcdefghijklmnopqrstuvwxyz0123456789-._")
    private static let ipv6Chars = Set("0123456789abcdef:.")
    private static let digits = Set("0123456789")

    /// An IP address rather than a name: IPv6 (contains ":"), or a last label that is all
    /// digits (no top-level domain is a number). Matched only exactly, never as a parent.
    public static func isIPLiteral(_ host: String) -> Bool {
        if host.contains(":") { return true }
        guard let last = host.split(separator: ".").last, !last.isEmpty else { return false }
        return last.allSatisfy { digits.contains($0) }
    }

    /// A plain list entry must name a site with a dot, never a bare label ("com",
    /// "localhost"). 2026-10-23 (review): never an IP address either, as on Windows.
    public static func isUsableEntry(_ host: String) -> Bool {
        !isIPLiteral(host) && host.contains(".")
    }

    /// Does a normalised `host` match one normalised entry? "*.a.com": a.com or any
    /// subdomain ("x.a.com"; never "xa.com" or "a.com.evil.net"). A plain entry: that host,
    /// or the same host with "www." added or removed. An IP literal never matches a name.
    public static func entryMatches(_ host: String, entry: String) -> Bool {
        if entry.hasPrefix(wildcardPrefix) {
            let base = String(entry.dropFirst(wildcardPrefix.count))
            return !isIPLiteral(host) && (host == base || host.hasSuffix("." + base))
        }
        if host == entry { return true }
        if isIPLiteral(host) || isIPLiteral(entry) { return false }
        if host == "www." + entry { return true }
        let bare = String(entry.dropFirst(4))
        return entry.hasPrefix("www.") && bare.contains(".") && host == bare
    }

    /// `host` matches one of the allowed entries (`entryMatches`). Entries that are not
    /// valid (`normalizedEntry`), e.g. "*.github.io", match nothing.
    public static func hostAllowed(_ host: String, allowed: [String]) -> Bool {
        guard let h = normalizedHost(host) else { return false }
        return allowed.compactMap(normalizedEntry).contains { entryMatches(h, entry: $0) }
    }

    /// May a sub-frame of the locked exam page load `url`? about:blank / about:srcdoc, or
    /// https on the default port (no user name or password in the URL) on an allowed site.
    /// Plain http never.
    public static func frameAllowed(_ url: URL, allowed: [String]) -> Bool {
        let scheme = url.scheme?.lowercased()
        if scheme == "about" {
            // Foundation may keep "#x" or percent-encode it ("%23x"): accept both.
            let s = url.absoluteString.lowercased()
            return ["about:blank", "about:srcdoc"].contains { page in
                s == page || s.hasPrefix(page + "#") || s.hasPrefix(page + "%23")
            }
        }
        guard scheme == "https", let host = url.host, url.user == nil, url.password == nil else { return false }
        if let port = url.port, port != 443 { return false }
        return hostAllowed(host, allowed: allowed)
    }

    /// A window.open / target=_blank from an allowed site: never a window. True when it is
    /// an allowed https page (ignored quietly), false when it should count as blocked.
    public static func isAllowedPopup(_ url: URL, allowed: [String]) -> Bool {
        url.scheme?.lowercased() == "https" && frameAllowed(url, allowed: allowed)
    }

    /// A response sent as a download ("Content-Disposition: attachment").
    public static func isAttachment(contentDisposition: String?) -> Bool {
        guard let d = contentDisposition?.trimmingCharacters(in: .whitespaces).lowercased() else { return false }
        return d == "attachment" || d.hasPrefix("attachment;") || d.hasPrefix("attachment ")
    }
}

/// 2026-10-23: the exam top bar's battery. The page reads
/// `window.fairplayBattery = {level: 0..1, charging: Bool}` and listens for the
/// "fairplay-battery" event. Only number and boolean literals go into the script.
public enum BatteryScript {

    /// nil for an unknown or invalid level (negative, NaN, infinite; -1 is "unknown").
    /// Above 1 is taken as 1. Rounded to 2 decimals.
    public static func make(level: Double, charging: Bool) -> String? {
        guard level.isFinite, level >= 0 else { return nil }
        let hundredths = Int((min(level, 1) * 100).rounded())
        let rest = hundredths % 100
        let text = "\(hundredths / 100)." + (rest < 10 ? "0\(rest)" : "\(rest)")
        return "window.fairplayBattery = {level: \(text), charging: \(charging ? "true" : "false")};"
            + " window.dispatchEvent(new Event(\"fairplay-battery\"));"
    }
}
