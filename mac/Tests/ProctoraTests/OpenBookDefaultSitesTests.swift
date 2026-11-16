import Testing
import Foundation
@testable import Proctora

/// The default allowed sites of an open-book DKU OJ exam (server: open_book_defaults.py) and the sign-in flows they need.
/// What the server hands the lock for those defaults: the OJ, Duke Shibboleth and the two Duke Box app hosts as pages; the
/// Box account / SSO hosts only as the Box sign-in chain; all of boxcdn.net as a Box-tab resource. Final proof is a real login
/// on a real Mac (docs/open-book-default-sites.md).
@Suite struct OpenBookDefaultSitesTests {
    static let pages = ["https://oj.dukekunshan.edu.cn/", "https://shib.oit.duke.edu/", "https://duke.app.box.com/", "https://duke.box.com/"]
    static let resources = ["https://duke.account.box.com", "https://sso.services.box.net", "https://api.box.com", "https://cdn01.boxcdn.net",
                            "https://e3.boxcdn.net", "https://*.boxcloud.com", "https://*.boxcdn.net"]

    private func policy() -> ProtectedBrowserPolicy? {
        ProtectedBrowserPolicy(start: "https://oj.dukekunshan.edu.cn/", additionalOrigins: "",
                               forbiddenOrigin: URL(string: "https://fairplay.example.edu")!,
                               referenceSites: Self.pages, resourceOrigins: Self.resources)
    }

    private func u(_ s: String) -> URL { URL(string: s)! }

    @Test func everyDefaultPageIsAllowedForAnyPathAndHostCase() throws {
        let p = try #require(policy())
        #expect(p.acceptedSites.count == 4 && p.hasBoxPage)
        for url in ["https://oj.dukekunshan.edu.cn/", "https://OJ.DukeKunshan.edu.cn/contest/x?a=1", "https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO",
                    "https://SHIB.oit.duke.edu/x", "https://duke.app.box.com/folder/123", "https://DUKE.app.box.com/s/abcdefghij1234", "https://duke.box.com/", "https://duke.box.com/a/b/c"] {
            #expect(p.allows(u(url), mainFrame: true), Comment(rawValue: url))
        }
    }

    @Test func httpOtherPortsAndLookAlikesAreNotAllowed() throws {
        let p = try #require(policy())
        for url in ["http://shib.oit.duke.edu/", "http://duke.box.com/", "https://duke.box.com:8443/", "https://shib.oit.duke.edu:444/x",
                    "https://duke.box.com.evil.example/", "https://evilduke.box.com/", "https://box.com/", "https://app.box.com/", "https://other.app.box.com/",
                    "https://oj.dukekunshan.edu.cn.evil.example/", "https://dukekunshan.edu.cn/", "https://user@duke.box.com/"] {
            #expect(!p.allows(u(url), mainFrame: true), Comment(rawValue: url))
        }
    }

    @Test func theBoxAccountAndSsoHostsAreNeverPagesOfTheirOwn() throws {
        let p = try #require(policy())
        for url in ["https://duke.account.box.com/", "https://duke.account.box.com/login", "https://sso.services.box.net/", "https://sso.services.box.net/sp/acs",
                    "https://account.box.com/login"] {
            #expect(!p.allows(u(url), mainFrame: true), Comment(rawValue: url))
            #expect(!p.allows(u(url), mainFrame: false), Comment(rawValue: url))
        }
    }

    @Test func theBoxLoginFlowIsOpenInABoxTabFromBoxToDukeToDuoAndBack() {
        let box = u("https://duke.app.box.com/")
        let flow = ["https://duke.account.box.com/login?redirect_url=%2F", "https://sso.services.box.net/sp/startSSO.ping", "https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x",
                    "https://api-1a2b3c.duosecurity.com/frame/web/v1/auth?tx=1", "https://sso.services.box.net/sp/ACS.saml2"]
        var current = box
        for step in flow {
            #expect(BoxSignIn.allowsTopLevel(target: u(step), current: current, boxTab: true), Comment(rawValue: step))
            current = u(step)
        }
        // The way back to the folder is the approved page itself.
        #expect(BoxSignIn.isBoxPage(u("https://duke.app.box.com/folder/0")) && BoxSignIn.isBoxPage(u("https://duke.box.com/")))
        // Not outside a Box tab.
        for step in flow { #expect(!BoxSignIn.allowsTopLevel(target: u(step), current: box, boxTab: false), Comment(rawValue: step)) }
    }

    @Test func embeddedContentOfTheLoginFlowsLoadsInABoxTab() {
        let account = u("https://duke.account.box.com/login")
        for frame in ["https://api-1a2b3c.duosecurity.com/frame/web/v1/auth", "https://shib.oit.duke.edu/idp/x", "https://sso.services.box.net/sp/x",
                      "https://cdn01.boxcdn.net/a.js", "https://static.boxcdn.net/fonts/x.woff2", "https://e3.boxcdn.net/a", "https://public.boxcloud.com/x", "https://dl.boxcloud.com/x",
                      "https://api.box.com/2.0/x"] {
            #expect(BoxSignIn.allowsSubframe(target: u(frame), current: account, boxTab: true), Comment(rawValue: frame))
            #expect(!BoxSignIn.allowsSubframe(target: u(frame), current: account, boxTab: false), Comment(rawValue: frame))
        }
        // Not widened beyond Box's own bases.
        for frame in ["https://boxcdn.net/x", "https://boxcdn.net.evil.example/x", "https://evilboxcdn.net/x", "http://static.boxcdn.net/x", "https://duke.account.box.com/settings"] {
            #expect(!BoxSignIn.allowsSubframe(target: u(frame), current: account, boxTab: true), Comment(rawValue: frame))
        }
    }

    @Test func boxcdnIsAChainResourceAndGetsScopedContentRules() throws {
        for raw in ["https://*.boxcdn.net", "https://static.boxcdn.net", "*.boxcdn.net"] { #expect(BoxSignIn.isChainResource(raw), Comment(rawValue: raw)) }
        #expect(!BoxSignIn.isChainResource("https://evilboxcdn.net") && !BoxSignIn.isChainResource("https://*.com"))
        let p = try #require(policy())
        // As a plain resource it would open in every tab; it is dropped there and only the Box tab's chain rules carry it.
        #expect(!p.allows(u("https://static.boxcdn.net/x.js"), mainFrame: false))
        let rules = try #require(try JSONSerialization.jsonObject(with: Data(p.contentRules.utf8)) as? [[String: Any]])
        let boxcdn = rules.filter { (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("boxcdn") == true }
        #expect(!boxcdn.isEmpty)
        for rule in boxcdn {
            #expect((((rule["trigger"] as? [String: Any])?["if-top-url"]) as? [String])?.isEmpty == false)
        }
    }

    @Test func theBoxPaneKeepsItsNarrowAssetCeiling() {
        let body: [String: Any] = [
            "start_url": "https://duke.app.box.com/s/abcdefghij1234", "path_prefix": "/s/abcdefghij1234", "app_hosts": ["duke.app.box.com"],
            "sign_in_hosts": [["host": "duke.account.box.com", "paths": ["/login*"]]],
            "asset_hosts": ["*.boxcdn.net"], "blocked_requests": []]
        #expect(BoxPolicy(body: body, allowDev: false) == nil)
    }
}
