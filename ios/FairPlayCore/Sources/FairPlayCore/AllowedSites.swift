import Foundation

/// 2026-10-23: teacher-allowed websites inside the exam. The exam page shows them in an
/// <iframe> side pane and sends `allowed_sites` (host names such as "docs.python.org")
/// with "lock" / "session" (also "openExternal" / "prelock"; missing = none). While
/// locked, a SUB-FRAME may load https pages on a listed host or any subdomain of it,
/// plus about:blank / about:srcdoc. The main frame still shows only the exam pages, no
/// window ever opens and nothing downloads.
///
/// Pure decisions only (ExamViewController acts on them), for the tests. Same rules as
/// the Mac's AllowedSites.swift. iPadOS Assessment Mode: the frames are inside our own web
/// view, so the same rules apply there.
public enum AllowedSites {

    /// Most entries a page may send (more are ignored).
    public static let maxEntries = 200

    /// Host names from a bridge message, normalised (see `normalizedHost`), without
    /// duplicates. Anything else in the list is dropped. Missing or not a list = [].
    public static func parse(_ raw: Any?) -> [String] {
        guard let list = raw as? [Any] else { return [] }
        var out: [String] = []
        for case let s as String in list {
            guard out.count < maxEntries else { break }
            if let h = normalizedHost(s), isUsableEntry(h), !out.contains(h) { out.append(h) }
        }
        return out
    }

    /// Lowercased, without one trailing dot or IPv6 brackets. nil when it is not a plain
    /// host name: a scheme, path, port, wildcard, spaces, empty labels or non-ASCII
    /// characters (WebKit gives hosts in punycode, "xn--...", and they are compared as is).
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

    /// A list entry must name a site, not a whole top-level domain: "com" or "edu" would
    /// open every site under it. IP literals are taken as they are.
    public static func isUsableEntry(_ host: String) -> Bool {
        isIPLiteral(host) || host.contains(".")
    }

    /// `host` equals an allowed entry or is a subdomain of one ("x.docs.python.org" for
    /// "docs.python.org"; never "evildocs.python.org" or "docs.python.org.attacker.com").
    /// An IP literal only matches the same literal.
    public static func hostAllowed(_ host: String, allowed: [String]) -> Bool {
        guard let h = normalizedHost(host) else { return false }
        let entries = allowed.compactMap(normalizedHost).filter(isUsableEntry)
        if isIPLiteral(h) { return entries.contains(h) }
        return entries.contains { a in !isIPLiteral(a) && (h == a || h.hasSuffix("." + a)) }
    }

    /// May a sub-frame of the locked exam page load `url`? about:blank / about:srcdoc, or
    /// https on the default port (no user name or password in the URL) on an allowed host.
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
