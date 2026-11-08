import Foundation
import CryptoKit
import Testing
import WebKit
@testable import Proctora

@Suite struct OpenBookPolicyTests {
    private let oj = URL(string: "https://oj.example.edu")!
    private let forbidden = URL(string: "https://fairplay.example.edu")!

    private func policy(_ sites: [String] = [], resources: [String] = []) throws -> ProtectedBrowserPolicy {
        try #require(ProtectedBrowserPolicy(start: oj.absoluteString, additionalOrigins: "", forbiddenOrigin: forbidden,
                                            referenceSites: sites, resourceOrigins: resources))
    }

    @Test func anInvalidEntryIsSkippedAlone() throws {
        let p = try policy(["Python docs https://docs.python.org/3/", "*.cppreference.com", "https://en.cppreference.com/w/", "ftp://x.org"])
        #expect(p.acceptedSites == ["https://en.cppreference.com/w/"])
        #expect(p.allows(URL(string: "https://en.cppreference.com/w/c")!, mainFrame: true))
        #expect(!p.allows(URL(string: "https://docs.python.org/3/")!, mainFrame: true))
    }

    @Test func aWildcardEntryIsNotAcceptedBecauseThePolicyHasNoWildcards() throws {
        let p = try policy(["*.cppreference.com"])
        #expect(p.acceptedSites.isEmpty)
        #expect(!p.allows(URL(string: "https://en.cppreference.com/")!, mainFrame: true))
    }

    @Test func aDeniedHostIsRefusedEvenWhenSigned() throws {
        let p = try policy(["https://chatgpt.com/", "https://sub.claude.ai/x", "https://github.com/", "https://docs.github.com/",
                            "https://app.box.com/", "https://duke.app.box.com/folder"],
                           resources: ["https://cdn.openai.com"])
        #expect(!p.allows(URL(string: "https://chatgpt.com/")!, mainFrame: true))
        #expect(!p.allows(URL(string: "https://sub.claude.ai/x")!, mainFrame: true))
        #expect(!p.allows(URL(string: "https://github.com/")!, mainFrame: true))
        #expect(!p.allows(URL(string: "https://app.box.com/")!, mainFrame: true))
        #expect(!p.allows(URL(string: "https://cdn.openai.com/a.js")!, mainFrame: false))
        #expect(p.allows(URL(string: "https://docs.github.com/")!, mainFrame: true))
        #expect(p.allows(URL(string: "https://duke.app.box.com/folder")!, mainFrame: true))
    }

    @Test func aResourceOnlyOriginLoadsSubresourcesButIsNeverAPage() throws {
        let p = try policy(["https://duke.app.box.com/"], resources: ["https://cdn.boxcloud.com", "https://bad host", "https://cdn.boxcloud.com/path"])
        let asset = URL(string: "https://cdn.boxcloud.com/app.js")!
        #expect(p.allows(asset, mainFrame: false))
        #expect(!p.allows(asset, mainFrame: true))
        #expect(!p.allowsMainTab(asset) && !p.opensInOwnTab(asset))
        #expect(p.contentRules.contains("cdn\\\\.boxcloud\\\\.com"))
    }

    @Test func theMainTabRefusesAnAllowedSiteAndItOpensInItsOwnTab() throws {
        let p = try policy(["https://docs.python.org/3/"])
        let site = URL(string: "https://docs.python.org/3/")!
        #expect(p.allows(site, mainFrame: true))
        #expect(!p.allowsMainTab(site))
        #expect(p.opensInOwnTab(site))
        let page = URL(string: "https://oj.example.edu/contest/1")!
        #expect(p.allowsMainTab(page))
        #expect(!p.opensInOwnTab(page))
    }

    @Test func noSitesIsTheClosedBookLock() throws {
        let p = try policy()
        #expect(p.acceptedSites.isEmpty)
        #expect(!p.allows(URL(string: "https://docs.python.org/")!, mainFrame: true))
    }

    @Test func thirtyTwoSitesWork() throws {
        let sites = (0..<32).map { "https://site\($0).example.org/" }
        let p = try policy(sites)
        #expect(p.acceptedSites.count == 32)
        #expect(ProtectedBrowserPolicy(start: oj.absoluteString, additionalOrigins: "", forbiddenOrigin: forbidden,
                                       referenceSites: sites + ["https://x.org/"]) == nil)
    }

    private func envelope(_ extra: [String: Any]) throws -> (NativeDeviceContext?, Bool) {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "a", count: 64)
        var payload: [String: Any] = ["v": 2, "nonce": "n", "expires": 1030, "session": UUID().uuidString,
            "device": device, "user": "7", "contest": "exam", "participation": "1", "state": "active", "lease_until": 1000]
        payload.merge(extra) { $1 }
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        let env: [String: Any] = ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
        let c = NativeDeviceContext.verify(env, publicKey: key.publicKey.rawRepresentation, nonce: "n", device: device,
                                           now: Date(timeIntervalSince1970: 1000))
        return (c, c != nil)
    }

    @Test func aSignedPayloadWithAndWithoutResourcesVerifies() throws {
        let without = try envelope(["sites": ["https://docs.python.org/3/"]]).0
        #expect(without?.resources == nil && without?.sites?.count == 1)
        let with = try envelope(["sites": ["https://docs.python.org/3/"], "resources": ["https://cdn.example.org"]]).0
        #expect(with?.resources == ["https://cdn.example.org"])
        #expect(try envelope(["sites": (0..<32).map { "https://s\($0).org/" }]).0?.sites?.count == 32)
        #expect(try envelope(["sites": (0..<33).map { "https://s\($0).org/" }]).0 == nil)
        #expect(try envelope(["resources": (0..<65).map { "https://s\($0).org" }]).0 == nil)
    }
}

@Suite struct WildcardResourceTests {
    private let forbidden = URL(string: "https://fairplay.example.edu")!
    private func policy(_ resources: [String], sites: [String] = ["https://docs.python.org/3/"]) -> ProtectedBrowserPolicy {
        ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "", forbiddenOrigin: forbidden,
                               referenceSites: sites, resourceOrigins: resources)!
    }
    private func url(_ s: String) -> URL { URL(string: s)! }

    @Test func aWildcardCoversSubdomainsForSubresourcesOnly() {
        let p = policy(["https://*.boxcloud.com"])
        #expect(p.allows(url("https://dl.boxcloud.com/a.js"), mainFrame: false))
        #expect(p.allows(url("https://a.b.boxcloud.com:443/a.js"), mainFrame: false))
        #expect(!p.allows(url("https://boxcloud.com/a.js"), mainFrame: false))
        #expect(!p.allows(url("https://evilboxcloud.com/a.js"), mainFrame: false))
        #expect(!p.allows(url("http://dl.boxcloud.com/a.js"), mainFrame: false))
        #expect(!p.allows(url("https://dl.boxcloud.com:8443/a.js"), mainFrame: false))
        #expect(!p.allows(url("https://dl.boxcloud.com/"), mainFrame: true))
        #expect(!p.allowsMainTab(url("https://dl.boxcloud.com/")) && !p.opensInOwnTab(url("https://dl.boxcloud.com/")))
    }

    @Test func malformedAndTooBroadPatternsAreRefused() {
        for bad in ["https://*.com", "https://*.co.uk", "https://*.edu.cn", "https://*.github.io", "https://*.x", "https://*.",
                    "https://*.a.com:8443", "https://*.a.com/path", "https://*.a.com?x=1", "http://*.a.com", "https://a.*.com",
                    "https://**.a.com", "https://*.*.a.com", "*.a.com", "https://*.A.com"] {
            #expect(ProtectedBrowserPolicy.resourceSuffix(bad) == nil, "\(bad)")
        }
        #expect(ProtectedBrowserPolicy.resourceSuffix("https://*.duosecurity.com") == "duosecurity.com")
        let p = policy(["https://*.com"])
        #expect(!p.allows(url("https://a.com/x.js"), mainFrame: false))
    }

    @Test func aDeniedHostIsBlockedEvenUnderAnAllowedWildcard() {
        let p = policy(["https://*.microsoft.com", "https://*.box.com", "https://*.claude.ai"])
        #expect(p.allows(url("https://static.microsoft.com/x.css"), mainFrame: false))
        #expect(!p.allows(url("https://copilot.microsoft.com/x"), mainFrame: false))
        #expect(!p.allows(url("https://teams.microsoft.com/x"), mainFrame: false))
        #expect(!p.allows(url("https://a.box.com/x"), mainFrame: false))
        #expect(!p.allows(url("https://x.claude.ai/x"), mainFrame: false))
        let rules = p.contentRules
        #expect(rules.contains("copilot\\\\.microsoft\\\\.com"))
        #expect(!rules.contains("box\\\\.com"))
    }

    @Test func wildcardsInSitesAreNotPages() {
        let p = policy([], sites: ["https://*.boxcloud.com"])
        #expect(p.acceptedSites.isEmpty)
        #expect(!p.allows(url("https://dl.boxcloud.com/"), mainFrame: true))
    }

    @Test func theCapOfSixtyFourStaysAndRulesCompile() {
        #expect(ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "", forbiddenOrigin: forbidden,
                resourceOrigins: (0..<65).map { "https://*.s\($0).org" }) == nil)
        let p = policy(["https://*.duosecurity.com", "https://*.microsoft.com"])
        #expect((try? JSONSerialization.jsonObject(with: Data(p.contentRules.utf8))) != nil)
    }
}

@Suite struct OpenBookHardeningTests {
    private let forbidden = URL(string: "https://fairplay.example.edu")!
    private func make(sites: [String], resources: [String] = []) -> ProtectedBrowserPolicy {
        ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "", forbiddenOrigin: forbidden,
                               referenceSites: sites, resourceOrigins: resources)!
    }

    @Test func sitesAndResourcesStopWorkingWhenTheExpiryPasses() {
        var p = make(sites: ["https://docs.python.org/3/"], resources: ["https://cdn.example.org", "https://*.duosecurity.com"])
        var now = Date(timeIntervalSince1970: 1000)
        p.clock = { now }
        p.sitesExpire = Date(timeIntervalSince1970: 1090)
        let site = URL(string: "https://docs.python.org/3/x")!, cdn = URL(string: "https://cdn.example.org/a.js")!
        let duo = URL(string: "https://api.duosecurity.com/a.js")!, oj = URL(string: "https://oj.example.edu/c")!
        #expect(p.allows(site, mainFrame: true) && p.allows(cdn, mainFrame: false) && p.allows(duo, mainFrame: false))
        now = Date(timeIntervalSince1970: 1091)
        #expect(!p.allows(site, mainFrame: true) && !p.allows(cdn, mainFrame: false) && !p.allows(duo, mainFrame: false))
        #expect(p.allows(oj, mainFrame: true))
        p.sitesExpire = Date(timeIntervalSince1970: 1200)
        #expect(p.allows(site, mainFrame: true))
    }

    @Test func resourcesAreIgnoredWhenNoSiteIsAccepted() {
        let p = make(sites: ["*.bad.org"], resources: ["https://cdn.example.org", "https://*.duosecurity.com"])
        #expect(!p.allows(URL(string: "https://cdn.example.org/a.js")!, mainFrame: false))
        #expect(!p.allows(URL(string: "https://x.duosecurity.com/a.js")!, mainFrame: false))
    }

    @Test func newDeniedHostsAndPublicSuffixesAreRefused() {
        for host in ["deepl.com", "www.duckduckgo.com", "a.reddit.com", "zoom.us", "godbolt.org", "www.google.com", "google.com",
                     "translate.google.com", "meet.google.com", "search.brave.com", "api.chegg.com", "quark.cn"] {
            #expect(SiteDenyList.isDenied(host: host), "\(host)")
        }
        #expect(!SiteDenyList.isDenied(host: "docs.python.org"))
        #expect(!SiteDenyList.isDenied(host: "fonts.google.com"))
        #expect(!SiteDenyList.isDenied(host: "duke.app.box.com"))
        for pattern in ["https://*.vercel.app", "https://*.s3.amazonaws.com", "https://*.githubusercontent.com", "https://*.repl.co",
                        "https://*.pages.dev", "https://*.notion.site", "https://*.glitch.me"] {
            #expect(ProtectedBrowserPolicy.resourceSuffix(pattern) == nil, "\(pattern)")
        }
    }

    @Test @MainActor func leavingTheExamTabClearsTheClipboardOnlyOnTheWayOut() {
        let exam = WKWebView(), site = WKWebView(), other = WKWebView()
        #expect(ProtectedBrowserController.clearsClipboard(from: exam, to: site, examTab: exam))
        #expect(!ProtectedBrowserController.clearsClipboard(from: site, to: exam, examTab: exam))
        #expect(!ProtectedBrowserController.clearsClipboard(from: site, to: other, examTab: exam))
        #expect(!ProtectedBrowserController.clearsClipboard(from: nil, to: site, examTab: exam))
    }
}

@Suite struct ParityRulesTests {
    @Test func aWildcardOverAnExactDeniedHostIsRefused() {
        for pattern in ["https://*.google.com", "https://*.brave.com", "https://*.github.com"] {
            #expect(ProtectedBrowserPolicy.resourceSuffix(pattern) == nil, "\(pattern)")
        }
        #expect(SiteDenyList.exactHostIsUnder("google.com") && SiteDenyList.exactHostIsUnder("brave.com"))
        #expect(!SiteDenyList.exactHostIsUnder("duosecurity.com"))
        for host in ["www.google.com", "translate.google.com", "meet.google.com", "search.brave.com", "google.com"] {
            #expect(SiteDenyList.isDenied(host: host))
        }
    }

    @Test func theSharedSuffixListCoversTheServerPatterns() {
        for suffix in ["com.hk", "edu.tw", "ac.cn", "info", "biz", "ac", "co.kr", "ac.kr", "me.uk", "com.sg", "edu.my",
                       "org.au", "net.au", "co.za", "co.nz", "com.br", "com.mx", "edu.uz", "com.uz", "r2.dev", "hf.space",
                       "page", "site", "xyz", "ngrok.io", "translate.goog", "now.sh", "stackblitz.io", "com.xx"] {
            #expect(ProtectedBrowserPolicy.isSharedSuffix(suffix), "\(suffix)")
            #expect(ProtectedBrowserPolicy.resourceSuffix("https://*." + suffix) == nil, "\(suffix)")
        }
        #expect(!ProtectedBrowserPolicy.isSharedSuffix("duosecurity.com"))
        #expect(ProtectedBrowserPolicy.resourceSuffix("https://*.duosecurity.com") == "duosecurity.com")
    }

    @Test func aTrailingSlashEntryMatchesItsPathWithAndWithoutTheSlashAndChildren() {
        let slash = ExternalExamController.SiteRule("https://x.org/docs/")!
        #expect(slash.matches(host: "x.org", path: "/docs/") && slash.matches(host: "x.org", path: "/docs"))
        #expect(slash.matches(host: "x.org", path: "/docs/a/b"))
        #expect(!slash.matches(host: "x.org", path: "/docsx") && !slash.matches(host: "x.org", path: "/other"))
        let bare = ExternalExamController.SiteRule("https://x.org/docs")!
        #expect(bare.matches(host: "x.org", path: "/docs") && bare.matches(host: "x.org", path: "/docs/") && bare.matches(host: "x.org", path: "/docs/a"))
        #expect(!bare.matches(host: "x.org", path: "/docsx"))
    }

    @Test func aBareHTTPRefusalOfClaimOrRecoverGetsTheNeutralSentence() {
        let neutral = ProtectedBrowserController.startNotAcceptedText
        #expect(!neutral.contains("could not confirm"))
        func n(_ lock: Bool, _ http: Bool, _ status: Int?, _ json: String?, conflict: Bool = false) -> String? {
            ProtectedBrowserController.nativeFailureNotice(sendsLock: lock, httpAnswered: http, status: status,
                                                           data: json.map { Data($0.utf8) }, recoverAfterConflict: conflict)
        }
        #expect(n(true, true, 404, nil) == neutral)
        #expect(n(true, true, 409, "<html>", conflict: true) == neutral)
        #expect(n(true, true, 404, #"{"error":"Not on the roster."}"#) == "Not on the roster.")
        #expect(n(false, true, 404, nil) == nil)
        #expect(n(true, false, nil, nil)?.contains("could not confirm") == true)
    }

    private func context(_ state: String, deadline: Int?, hold: Bool? = nil) throws -> NativeDeviceContext {
        var json: [String: Any] = ["v": 2, "nonce": "n", "expires": 1_000_000, "session": UUID().uuidString,
            "device": String(repeating: "a", count: 64), "user": "7", "contest": "exam", "participation": "1",
            "state": state, "lease_until": 0]
        if let deadline { json["deadline"] = deadline }
        if let hold { json["hold"] = hold }
        return try JSONDecoder().decode(NativeDeviceContext.self, from: JSONSerialization.data(withJSONObject: json))
    }

    @Test func theFailsafeStaysArmedWhileFinishing() throws {
        let m = NativeExit.Monotonic(boot: "b", seconds: 0)
        var f = DeadlineFailsafe()
        f.accept(try context("active", deadline: 1_000_600), at: m)
        let armed = f
        f.accept(try context("finishing", deadline: nil), at: m)
        #expect(f == armed)
        f.accept(try context("finishing", deadline: 1_000_900), at: m)
        #expect(f.isArmed && f != armed)
        f.accept(try context("finishing", deadline: 1_000_900, hold: true), at: m)
        #expect(!f.isArmed)
        f.accept(try context("finishing", deadline: nil), at: m)
        #expect(!f.isArmed)
        f.accept(try context("active", deadline: 1_000_600), at: m)
        f.accept(try context("completed", deadline: 1_000_600), at: m)
        #expect(!f.isArmed)
        var g = DeadlineFailsafe()
        g.accept(try context("finishing", deadline: 1_000_600), at: m)
        let last = NativeExit.Monotonic(boot: "b", seconds: 600 + DeadlineFailsafe.grace + DeadlineFailsafe.hardBound)
        #expect(g.decide(at: NativeExit.Monotonic(boot: "b", seconds: 600 + DeadlineFailsafe.grace + 1)) == .probe)
        #expect(g.decide(at: last) == .release)
    }
}
