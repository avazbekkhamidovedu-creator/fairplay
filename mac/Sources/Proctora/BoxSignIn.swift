import Foundation

/// Duke Box sign-in for an allowed-site tab that starts on a Box link: Box, then Duke single sign-on, then Duo, then
/// back to Box. The host lists are the ceilings of the Box pane (BoxPolicy), one source of truth, and the same as the
/// Windows BoxSignIn.cs. These hosts are otherwise refused (box.com and its sign-in hosts are on the site deny-list);
/// the controller applies this rule ONLY in a tab opened on a Box link (never the exam tab or any other tab) and only
/// while that tab is on the Box chain. https on the default port, no user name, a plain ASCII host with no punycode
/// label, no hidden separators or dot segments in the path. Downloads are not touched by this.
enum BoxSignIn {
    static let appHosts: Set<String> = ["duke.box.com", "duke.app.box.com"]
    static let signInExact: Set<String> = ["duke.account.box.com", "account.box.com", "sso.services.box.net", "shib.oit.duke.edu"]
    static let loginPathOnly: Set<String> = ["duke.account.box.com", "account.box.com"]
    static let signInSubdomainsOf = ["duosecurity.com"]
    static let assetExact: Set<String> = ["cdn01.boxcdn.net", "e3.boxcdn.net", "api.box.com"]
    static let assetSubdomainsOf = ["boxcloud.com"]

    /// A Box page (an app host): the page a Box tab starts on.
    static func isBoxPage(_ url: URL?) -> Bool {
        guard let url, let host = BoxPolicy.basicHost(url) else { return false }
        return appHosts.contains(host)
    }

    /// A page of the sign-in chain: the sign-in hosts (account hosts only on /login), Duo below duosecurity.com.
    /// The Box app hosts themselves are the approved site's business.
    static func allowsSignIn(_ url: URL?) -> Bool {
        guard let url, let host = BoxPolicy.basicHost(url), let path = BoxPolicy.cleanPath(url) else { return false }
        if loginPathOnly.contains(host) { return path.hasPrefix("/login") }
        return signInExact.contains(host) || signInSubdomainsOf.contains { host.hasSuffix("." + $0) }
    }

    /// On the chain: a Box page or a sign-in page.
    static func onChain(_ url: URL?) -> Bool { isBoxPage(url) || allowsSignIn(url) }

    /// May a top-level navigation of this tab go to `target`? Only in a Box tab, only to the sign-in chain, and only
    /// while the tab is still on the chain (or has not shown a page yet), so leaving for another site ends it.
    static func allowsTopLevel(target: URL?, current: URL?, boxTab: Bool) -> Bool {
        guard boxTab, allowsSignIn(target) else { return false }
        // A popup of a Box tab commits about:blank before it is sent to sign in: that never-navigated page counts as no page.
        let blank = current?.scheme?.lowercased() == "about" && current?.absoluteString.lowercased() == "about:blank"
        return current == nil || blank || onChain(current)
    }

    /// A host a list sent as a plain resource that only Box tabs may use: Box's own hosts (account, sso, api, cdn, boxcloud).
    /// Duke's sign-in (shib.oit.duke.edu, *.duosecurity.com) is not dropped: other allowed sites use it and keep it as before.
    /// The host is read the way the policy reads origins: lowercase, default port and trailing dot removed.
    static func isChainResource(_ raw: String) -> Bool {
        var host = raw.lowercased()
        if host.hasPrefix("https://") { host.removeFirst(8) }
        host = String(host.split(separator: "/").first ?? "")
        if let colon = host.lastIndex(of: ":"), host[host.index(after: colon)...].allSatisfy(\.isNumber) { host = String(host[..<colon]) }
        while host.hasSuffix(".") { host.removeLast() }
        // duke.box.com / duke.app.box.com are the Box page itself (already an approved page); as a plain resource they would be
        // allowed in every tab, so they are dropped too.
        let boxExact = signInExact.subtracting(["shib.oit.duke.edu"]).union(assetExact).union(appHosts)
        if host.hasPrefix("*.") {
            let base = String(host.dropFirst(2))
            return assetSubdomainsOf.contains { base == $0 || base.hasSuffix("." + $0) }
        }
        return boxExact.contains(host) || assetSubdomainsOf.contains { host.hasSuffix("." + $0) }
    }

    /// A subframe of a Box tab: the sign-in chain pages (account hosts on /login only) and the asset hosts.
    static func allowsFrame(_ url: URL?) -> Bool {
        if allowsSignIn(url) { return true }
        guard let url, let host = BoxPolicy.basicHost(url) else { return false }
        return assetExact.contains(host) || assetSubdomainsOf.contains { host.hasSuffix("." + $0) }
    }

    /// May a frame (not a page) of this tab load `target`? Only in a Box tab that is on the chain.
    static func allowsSubframe(target: URL?, current: URL?, boxTab: Bool) -> Bool {
        boxTab && onChain(current) && allowsFrame(target)
    }
}
