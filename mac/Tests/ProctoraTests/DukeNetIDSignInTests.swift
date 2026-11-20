import Testing
import Foundation
@testable import Proctora

@Suite struct DukeNetIDSignInTests {
    private func u(_ s: String) -> URL { URL(string: s)! }
    private let ojPage = URL(string: "http://10.200.31.64/accounts/login/")!
    private let chain = ["https://oj.dukekunshan.edu.cn:4431/auth/redirect",
                         "https://oj.dukekunshan.edu.cn:4431/Shibboleth.sso/SAML2/POST",
                         "https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO?SAMLRequest=x",
                         "https://shib.oit.duke.edu/idp/authn/external?conversation=e1s1",
                         "https://api-1a2b3c.duosecurity.com/frame/web/v1/auth?tx=1",
                         "https://duosecurity.com.evil.com.duosecurity.com/x"]
    private let refused = ["https://oj.dukekunshan.edu.cn:4432/auth/redirect", "https://oj.dukekunshan.edu.cn/", "https://oj.dukekunshan.edu.cn/problem/1",
                           "http://shib.oit.duke.edu/idp", "http://oj.dukekunshan.edu.cn:4431/auth/redirect", "https://shib.oit.duke.edu:8443/idp",
                           "https://evil-duosecurity.com/", "https://duosecurity.com.evil.com/", "https://duosecurity.com/",
                           "https://user@shib.oit.duke.edu/idp", "https://shib.oit.duke.edu.evil.com/", "https://xn--shib.oit.duke.edu/",
                           "https://shib.oit.duke.edu/a/../b", "https://shib.oit.duke.edu/a%2fb", "https://canvas.duke.edu/",
                           "https://duke.box.com/s/abc", "https://shib.oit.duke.edu./idp"]

    @Test func chainPagesAreAllowedFromTheOJTab() {
        for raw in chain { #expect(DukeNetIDSignIn.allowsTopLevel(target: u(raw), current: ojPage, currentIsOJ: true), Comment(rawValue: raw)) }
        // Each hop follows from the previous chain page.
        for (a, b) in zip(chain, chain.dropFirst()) {
            #expect(DukeNetIDSignIn.allowsTopLevel(target: u(b), current: u(a), currentIsOJ: false))
        }
        #expect(DukeNetIDSignIn.allowsSubframe(target: u("https://api-1.duosecurity.com/frame"), current: u(chain[3])))
        #expect(DukeNetIDSignIn.allowsSubframe(target: u("https://shib.oit.duke.edu/idp/x"), current: u(chain[3])))
    }

    @Test func otherPortsHostsAndSchemesAreRefused() {
        for raw in refused where raw != "https://oj.dukekunshan.edu.cn/problem/1" || true {
            #expect(!DukeNetIDSignIn.allowsTopLevel(target: u(raw), current: ojPage, currentIsOJ: true), Comment(rawValue: raw))
            #expect(!DukeNetIDSignIn.allowsSubframe(target: u(raw), current: u(chain[3])), Comment(rawValue: raw))
        }
    }

    @Test func theChainStartsOnlyFromTheOJOrTheChain() {
        // A blank tab, an unknown page or no page never starts it.
        #expect(!DukeNetIDSignIn.allowsTopLevel(target: u(chain[0]), current: nil, currentIsOJ: false))
        #expect(!DukeNetIDSignIn.allowsTopLevel(target: u(chain[0]), current: u("about:blank"), currentIsOJ: false))
        #expect(!DukeNetIDSignIn.allowsTopLevel(target: u(chain[2]), current: u("https://docs.python.org/"), currentIsOJ: false))
        // A frame needs a chain page on top: never in an OJ page.
        #expect(!DukeNetIDSignIn.allowsSubframe(target: u(chain[4]), current: ojPage))
    }

    @Test func theRealOJHostIsOnlyTheReturnStep() {
        for raw in ["https://oj.dukekunshan.edu.cn/accounts/login_netid/?token=abc", "https://oj.dukekunshan.edu.cn/accounts/login_netid_redirect/"] {
            #expect(DukeNetIDSignIn.allowsTopLevel(target: u(raw), current: u(chain[1]), currentIsOJ: false), Comment(rawValue: raw))
            #expect(!DukeNetIDSignIn.allowsSubframe(target: u(raw), current: u(chain[3])))
        }
        for raw in ["https://oj.dukekunshan.edu.cn/", "https://oj.dukekunshan.edu.cn/accounts/login/", "https://oj.dukekunshan.edu.cn/accounts/login_netidx/../x",
                    "https://oj.dukekunshan.edu.cn/accounts/loginnetid", "https://oj.dukekunshan.edu.cn:4432/accounts/login_netid/"] {
            #expect(!DukeNetIDSignIn.allowsTopLevel(target: u(raw), current: u(chain[1]), currentIsOJ: false), Comment(rawValue: raw))
        }
    }

    @Test func denyListAndPlainPolicyAreUntouched() {
        let p = ProtectedBrowserPolicy(start: "http://10.200.31.64/", additionalOrigins: "", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!, allowHTTP: true)!
        // The policy itself still refuses the chain: only the OJ tab's verdict adds it, never other tabs or opensInOwnTab.
        for raw in chain { #expect(!p.allows(u(raw), mainFrame: true) && !p.opensInOwnTab(u(raw)) && !p.allowsMainTab(u(raw)), Comment(rawValue: raw)) }
        #expect(p.isOJPage(ojPage))
        // Denied hosts are never on the chain.
        for raw in ["https://duke.box.com/", "https://dropbox.com/", "https://chat.openai.com/"] { #expect(!DukeNetIDSignIn.onChain(u(raw))) }
        // Box chain unchanged.
        #expect(BoxSignIn.allowsSignIn(u(chain[2])) && BoxSignIn.allowsSignIn(u(chain[4])) && !BoxSignIn.allowsSignIn(u(chain[0])))
        #expect(BoxSignIn.signInExact.contains("shib.oit.duke.edu") && BoxSignIn.signInSubdomainsOf == ["duosecurity.com"])
    }

    @Test func contentRulesScopeTheChainToChainPages() throws {
        let p = ProtectedBrowserPolicy(start: "http://10.200.31.64/", additionalOrigins: "", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!, allowHTTP: true)!
        let rules = try #require(JSONSerialization.jsonObject(with: Data(p.contentRules.utf8)) as? [[String: Any]])
        let chainRules = rules.filter { (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("4431") == true
            || (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("shib") == true
            || (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("duosecurity") == true }
        #expect(!chainRules.isEmpty)
        for r in chainRules where ((r["action"] as? [String: Any])?["type"] as? String) == "ignore-previous-rules" {
            let t = r["trigger"] as? [String: Any]
            #expect((t?["if-top-url"] as? [String])?.isEmpty == false, "\(t ?? [:])")
        }
        #expect(p.contentRules.contains("oj\\\\.dukekunshan\\\\.edu\\\\.cn:4431"))
        #expect(!p.contentRules.contains("|") && !p.contentRules.contains("(:443)"))
    }
}
