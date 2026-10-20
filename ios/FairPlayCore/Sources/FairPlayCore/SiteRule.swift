import Foundation

/// One allowed-site entry for website exams: [*.]host[/path-prefix] (CONTRACT.md
/// "Website mode"; same rules as the Mac's ExternalExamController.SiteRule).
public struct SiteRule: Equatable {
    public let wildcard: Bool
    public let host: String
    public let pathPrefix: String

    public init?(_ raw: String) {
        var s = raw.trimmingCharacters(in: .whitespaces)
        if let r = s.range(of: "://") { s = String(s[r.upperBound...]) }
        if let q = s.firstIndex(where: { $0 == "?" || $0 == "#" }) { s = String(s[..<q]) }
        var host = s
        var path = ""
        if let slash = s.firstIndex(of: "/") {
            host = String(s[..<slash])
            path = String(s[slash...])
        }
        var wild = false
        if host.hasPrefix("*.") { wild = true; host = String(host.dropFirst(2)) }
        if let colon = host.firstIndex(of: ":") { host = String(host[..<colon]) }
        host = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard !host.isEmpty, !host.contains("*") else { return nil }
        wildcard = wild
        self.host = host
        pathPrefix = path == "/" ? "" : path
    }

    public func matches(host h: String, path p: String) -> Bool {
        let hostOK = h == host || (wildcard && h.hasSuffix("." + host))
        guard hostOK else { return false }
        if pathPrefix.isEmpty { return true }
        // Whole path segments only: "/forms/abc" allows "/forms/abc/..." but not "/forms/abcdef".
        let path = p.isEmpty ? "/" : p
        if pathPrefix.hasSuffix("/") { return path.hasPrefix(pathPrefix) }
        return path == pathPrefix || path.hasPrefix(pathPrefix + "/")
    }

    /// Rules for an openExternal message. When the page sent none for the exam address,
    /// allow exactly its host + folder, never the whole domain tree.
    public static func rules(from raw: [Any], startURL url: URL) -> [SiteRule] {
        var rules = raw.compactMap { ($0 as? String).flatMap(SiteRule.init) }
        guard let host = url.host?.lowercased() else { return rules }
        if !rules.contains(where: { $0.matches(host: host, path: url.path) }) {
            let folder = url.path.contains("/") ? String(url.path[...url.path.lastIndex(of: "/")!]) : ""
            if let r = SiteRule(host + folder) { rules.append(r) }
        }
        return rules
    }
}
