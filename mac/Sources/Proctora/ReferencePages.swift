import Foundation

/// Which allowed pages of an open-book exam get a tab, and which are new since the last time.
struct ReferencePages {
    /// Tabs the protected browser allows in all, the exam tab included.
    static let maxTabs = 8
    /// Most extra tabs the app opens by itself next to the exam tab. The rest of the slots stay free for OJ popups
    /// and the student's own links (problem PDFs, statements); more allowed pages stay in the menu.
    static let maxExtraTabs = maxTabs - 3

    /// One page identity: scheme and host lowercased, default port removed, an empty path equal to "/".
    static func key(_ url: URL) -> String? {
        guard var c = URLComponents(url: url, resolvingAgainstBaseURL: false), let host = c.host?.lowercased() else { return nil }
        let scheme = (c.scheme ?? "").lowercased()
        c.scheme = scheme; c.host = host
        if (scheme == "https" && c.port == 443) || (scheme == "http" && c.port == 80) { c.port = nil }
        if c.path.isEmpty { c.path = "/" }
        return c.string
    }

    static func key(_ raw: String) -> String {
        ProtectedBrowserPolicy.normalizedURL(raw).flatMap { key($0) } ?? raw
    }

    /// One page per link in the server's order. A bare site root is dropped when the same host also has a start page.
    static func distinct(_ accepted: [String]) -> [String] {
        func parts(_ raw: String) -> (host: String, root: Bool)? {
            guard let url = ProtectedBrowserPolicy.normalizedURL(raw), let host = url.host?.lowercased() else { return nil }
            let root = (url.path.isEmpty || url.path == "/") && url.query == nil && url.fragment == nil
            return (host, root)
        }
        var hostsWithStartPage: Set<String> = []
        for raw in accepted { if let p = parts(raw), !p.root { hostsWithStartPage.insert(p.host) } }
        var seen: Set<String> = []
        var result: [String] = []
        for raw in accepted {
            guard let p = parts(raw), !(p.root && hostsWithStartPage.contains(p.host)), seen.insert(key(raw)).inserted else { continue }
            result.append(raw)
        }
        return result
    }

    /// Menu text: the host, plus the path when the link is a start page.
    static func label(_ raw: String) -> String {
        guard let url = ProtectedBrowserPolicy.normalizedURL(raw), let host = url.host else { return raw }
        let path = url.path == "/" ? "" : url.path
        return host + path
    }

    /// Keys of pages that need no further attention: opened, already open, or left out by the tab limit.
    private(set) var handled: Set<String> = []

    mutating func reset() { handled = [] }

    /// Pages to try opening now, in order: not handled before, not already `open` (keys of the tabs' URLs), within the room
    /// left. Pages already open, and pages cut by the limit, count as handled, so closed tabs never bring one back.
    /// A returned page is handled only once the caller reports `opened` (it may fail to open; a later renew retries).
    /// Pages no longer listed are forgotten.
    mutating func claim(_ pages: [String], openTabs: Int, open: Set<String> = []) -> [String] {
        let keys = Set(pages.map(Self.key))
        handled.formIntersection(keys)
        var fresh: [String] = []
        for raw in pages {
            let k = Self.key(raw)
            guard !handled.contains(k), !fresh.contains(where: { Self.key($0) == k }) else { continue }
            if open.contains(k) { handled.insert(k) } else { fresh.append(raw) }
        }
        let room = max(0, Self.maxExtraTabs + 1 - openTabs)
        for raw in fresh.dropFirst(room) { handled.insert(Self.key(raw)) }
        return Array(fresh.prefix(room))
    }

    mutating func opened(_ raw: String) { handled.insert(Self.key(raw)) }
}
