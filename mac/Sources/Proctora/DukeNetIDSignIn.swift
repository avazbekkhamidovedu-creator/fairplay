import Foundation

/// Duke NetID sign-in of the main DKU OJ tab: "Log in with NetID" on the OJ goes to the OJ's Shibboleth service provider, then Duke
/// single sign-on, then Duo, then back to the OJ. One source of truth for the hosts, the same as the Windows DukeNetIDSignIn.cs.
/// The chain is only for the first (OJ) tab and only while that tab is on the OJ or already on the chain; other tabs never get it, and
/// the site deny-list still wins in the content rules. https only, no user name, no hidden separators or dot segments in the path.
enum DukeNetIDSignIn {
    /// The only Duke-registered service provider: exact host and port, any path.
    static let authHost = "oj.dukekunshan.edu.cn"
    static let authPort = 4431
    static let ssoHost = "shib.oit.duke.edu"
    /// Duo (Universal Prompt top level and its assets): any subdomain, never the bare suffix or a look-alike.
    static let duoSuffix = "duosecurity.com"
    /// The real OJ (port 443) is NOT an exam page. Only where the auth service returns the browser after sign-in, as a document.
    static let returnHost = authHost
    static let returnPathPrefix = "/accounts/login_netid"

    private static func parts(_ url: URL?) -> (host: String, port: Int, path: String)? {
        guard let url, url.scheme?.lowercased() == "https",
              let c = URLComponents(url: url, resolvingAgainstBaseURL: false),
              c.user == nil, c.password == nil, let host = c.host?.lowercased(), BoxPolicy.validHost(host),
              let path = BoxPolicy.cleanPath(url) else { return nil }
        return (host, c.port ?? 443, path)
    }

    /// A page or frame of the chain: the auth service (port 4431), Duke SSO, Duo (default port).
    static func allowsChain(_ url: URL?) -> Bool {
        guard let p = parts(url) else { return false }
        if p.host == authHost { return p.port == authPort }
        guard p.port == 443 else { return false }
        return p.host == ssoHost || p.host.hasSuffix("." + duoSuffix)
    }

    /// The page the auth service sends the browser back to on the real OJ host: only /accounts/login_netid..., top level only.
    static func isReturnPage(_ url: URL?) -> Bool {
        guard let p = parts(url), p.host == returnHost, p.port == 443 else { return false }
        return p.path == returnPathPrefix || p.path.hasPrefix(returnPathPrefix + "/") || p.path.hasPrefix(returnPathPrefix + "_")
    }

    static func onChain(_ url: URL?) -> Bool { allowsChain(url) || isReturnPage(url) }

    /// May the main tab go to `target` as a page? Only from the OJ itself or from a chain page (never from a blank or other page).
    static func allowsTopLevel(target: URL?, current: URL?, currentIsOJ: Bool) -> Bool {
        guard allowsChain(target) || isReturnPage(target) else { return false }
        return currentIsOJ || onChain(current)
    }

    /// May a frame of the main tab load `target`? Only chain hosts, only while the tab is on the chain (Duo iframe, shib widgets).
    static func allowsSubframe(target: URL?, current: URL?) -> Bool {
        allowsChain(target) && allowsChain(current)
    }

    /// Content-blocker rules: the chain hosts load (as pages and as subresources of chain pages) only when the top page is on the
    /// chain, or, for the first hop, as documents. Deny rules still follow these in the list; the return page is a document only.
    static func contentRules() -> [[String: Any]] {
        func esc(_ s: String) -> String { ProtectedBrowserPolicy.escapeDots(s) }
        // WebKit's grammar has no groups or alternation: the default-port and explicit :443 forms are separate filters.
        let tops = ["^https://" + esc(authHost) + ":" + String(authPort) + "/",
                    "^https://" + esc(ssoHost) + "/", "^https://" + esc(ssoHost) + ":443/",
                    "^https://[a-z0-9.-]+\\." + esc(duoSuffix) + "/", "^https://[a-z0-9.-]+\\." + esc(duoSuffix) + ":443/"]
        var rules: [[String: Any]] = []
        for filter in tops {
            rules.append(["trigger": ["url-filter": filter, "if-top-url": tops], "action": ["type": "ignore-previous-rules"]])
        }
        rules.append(["trigger": ["url-filter": "^https://" + esc(returnHost) + returnPathPrefix, "resource-type": ["document"]],
                      "action": ["type": "ignore-previous-rules"]])
        return rules
    }
}
