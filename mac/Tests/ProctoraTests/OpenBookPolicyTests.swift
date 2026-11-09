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
        #expect(n(true, true, nil, nil) == neutral)
        #expect(n(true, true, 404, nil) == "DKU OJ answered HTTP 404.")
        #expect(n(true, true, 404, "<html><head><title>No such contest</title></head></html>") == "DKU OJ answered HTTP 404: No such contest.")
        #expect(n(true, true, 409, "<html><body><h1>Conflict &amp; more</h1></body></html>", conflict: true) == "DKU OJ answered HTTP 409: Conflict & more.")
        #expect(n(true, true, 404, #"{"error":"Not on the roster."}"#) == "Not on the roster.")
        #expect(n(false, true, 404, nil) == nil)
        #expect(n(false, true, 403, "<html><title>Forbidden</title></html>") == "DKU OJ answered HTTP 403: Forbidden.")
        #expect(n(true, false, nil, nil)?.contains("could not confirm") == true)
    }

    @Test func preLockHTTPAnswerShowsStatusAndOJTitleButTransportKeepsAppText() {
        typealias C = ProtectedBrowserController
        let page = Data("<html><title>500 Internal Server Error</title></html>".utf8)
        #expect(C.preLockUnavailableNotice(status: 500, failed: false, data: page)
            == "DKU OJ answered HTTP 500: 500 Internal Server Error. Your Mac is still unlocked.")
        #expect(C.preLockUnavailableNotice(status: 502, failed: false, data: nil) == "DKU OJ answered HTTP 502. Your Mac is still unlocked.")
        #expect(C.preLockUnavailableNotice(status: nil, failed: true, data: nil) == C.transportUnavailableText)
        #expect(C.preLockUnavailableNotice(status: 503, failed: true, data: page) == C.transportUnavailableText)
        #expect(!C.ojReachable(status: 500, failed: false))
    }

    @Test func ojPageSummaryPrefersTitleThenHeadingThenFirstLine() {
        func s(_ t: String) -> String? { OJPage.summary(Data(t.utf8)) }
        #expect(s("<TITLE>\n Not Found </TITLE><h1>x</h1>") == "Not Found")
        #expect(s("<body><h1 class=a>Bad <b>gateway</b></h1></body>") == "Bad gateway")
        #expect(s("<style>p{}</style>\n\nServer exploded\nmore") == "Server exploded")
        #expect(s("") == nil)
        #expect(s(String(repeating: "a", count: 500))?.count == 120)
    }

    @Test func errorPageEscapesTheDescriptionAndShowsTheFailingURL() {
        let html = ProtectedBrowserController.errorPageHTML(description: "bad <b>\"x\"", url: URL(string: "https://oj.example/a?b=1&c=2"))
        #expect(html.contains("bad &lt;b&gt;&quot;x&quot;") && !html.contains("<b>"))
        #expect(html.contains("https://oj.example/a?b=1&amp;c=2"))
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

@Suite struct EditShortcutAndAssetTests {
    private let forbidden = URL(string: "https://fairplay.example.edu")!
    private func chord(_ key: Int, _ flags: CGEventFlags, edit: Bool, reload: Bool = false) -> Bool {
        EventTap.commandChordAllowed(key: key, flags: flags, reloadAllowed: reload, editAllowed: edit)
    }

    @Test func editChordsPassOnlyWhenThePageHasTheFocus() {
        let cmd: CGEventFlags = .maskCommand, cmdShift: CGEventFlags = [.maskCommand, .maskShift]
        #expect(chord(6, cmd, edit: true) && chord(6, cmdShift, edit: true))      // Z, Shift-Z
        #expect(chord(0, cmd, edit: true) && chord(3, cmd, edit: true))           // A, F
        #expect(!chord(6, cmd, edit: false) && !chord(0, cmd, edit: false) && !chord(3, cmd, edit: false))
        #expect(!chord(0, cmdShift, edit: true) && !chord(3, cmdShift, edit: true))
        #expect(!chord(6, [.maskCommand, .maskAlternate], edit: true) && !chord(6, [.maskCommand, .maskControl], edit: true))
    }

    @Test func everyOtherCommandChordStaysBlocked() {
        // C, V, X, Q, W, H, M, Tab, Space, comma, grave, T, N, P, S
        for key in [8, 9, 7, 12, 13, 4, 46, 48, 49, 43, 50, 17, 45, 35, 1] {
            #expect(!chord(key, .maskCommand, edit: true), "key \(key)")
            #expect(!chord(key, [.maskCommand, .maskShift], edit: true), "key \(key)")
        }
        #expect(chord(15, .maskCommand, edit: false, reload: true))   // Reload still follows its own flag
        #expect(!chord(15, .maskCommand, edit: true, reload: false))
    }

    private func policy(_ assets: [String], sites: [String] = []) -> ProtectedBrowserPolicy {
        ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "", forbiddenOrigin: forbidden,
                               referenceSites: sites, assetOrigins: assets)!
    }
    private func url(_ s: String) -> URL { URL(string: s)! }

    @Test func aClosedBookContextAllowsTheCDNsForSubresourcesButNeverAsAPage() {
        let p = policy(["https://cdn.example.org", "https://fonts.example.net:8443"])
        #expect(p.allows(url("https://cdn.example.org/a.js"), mainFrame: false))
        #expect(p.allows(url("https://fonts.example.net:8443/f.woff"), mainFrame: false))
        #expect(!p.allows(url("https://cdn.example.org/"), mainFrame: true))
        #expect(!p.allowsMainTab(url("https://cdn.example.org/")) && !p.opensInOwnTab(url("https://cdn.example.org/")))
        #expect(p.contentRules.contains("cdn\\\\.example\\\\.org"))
    }

    @Test func malformedOrDeniedAssetOriginsAreDropped() {
        let p = policy(["https://chatgpt.com", "https://www.google.com", "http://cdn.a.org", "https://*.a.org", "https://a.org/path",
                        "https://user@a.org", "https://CDN.a.org", "https://ok.example.org"])
        for bad in ["https://chatgpt.com/x.js", "https://www.google.com/x.js", "http://cdn.a.org/x.js", "https://a.org/x.js", "https://cdn.a.org/x.js"] {
            #expect(!p.allows(url(bad), mainFrame: false), "\(bad)")
        }
        #expect(p.allows(url("https://ok.example.org/x.js"), mainFrame: false))
    }

    @Test func noAssetsChangesNothingAndOnlySixteenAreUsed() {
        let none = policy([])
        #expect(!none.allows(url("https://cdn.example.org/a.js"), mainFrame: false))
        let many = policy((0..<20).map { "https://c\($0).example.org" })
        #expect(many.allows(url("https://c15.example.org/a.js"), mainFrame: false))
        #expect(!many.allows(url("https://c16.example.org/a.js"), mainFrame: false))
    }

    @Test func theSignedStateCarriesAtMostSixteenAssets() throws {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "a", count: 64)
        func verify(_ extra: [String: Any]) throws -> NativeDeviceContext? {
            var payload: [String: Any] = ["v": 2, "nonce": "n", "expires": 1030, "session": UUID().uuidString, "device": device,
                "user": "7", "contest": "exam", "participation": "1", "state": "active", "lease_until": 0]
            payload.merge(extra) { $1 }
            let bytes = try JSONSerialization.data(withJSONObject: payload)
            let env: [String: Any] = ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
            return NativeDeviceContext.verify(env, publicKey: key.publicKey.rawRepresentation, nonce: "n", device: device, now: Date(timeIntervalSince1970: 1000))
        }
        #expect(try verify([:])?.assets == nil)
        #expect(try verify(["assets": ["https://cdn.example.org"]])?.assets == ["https://cdn.example.org"])
        #expect(try verify(["assets": (0..<16).map { "https://c\($0).org" }])?.assets?.count == 16)
        #expect(try verify(["assets": (0..<17).map { "https://c\($0).org" }]) == nil)
    }
}

@Suite struct OJSubresourcePolicyTests {
    private let forbidden = URL(string: "https://fairplay.example.edu")!
    private let oj = URL(string: "https://oj.example.edu/problem/1")!
    private func policy() -> ProtectedBrowserPolicy {
        ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "", forbiddenOrigin: forbidden)!
    }

    private func rules(_ p: ProtectedBrowserPolicy) throws -> [[String: Any]] {
        try #require(JSONSerialization.jsonObject(with: Data(p.contentRules.utf8)) as? [[String: Any]])
    }
    private func trigger(_ r: [String: Any]) -> [String: Any] { r["trigger"] as? [String: Any] ?? [:] }
    private func action(_ r: [String: Any]) -> String { (r["action"] as? [String: Any])?["type"] as? String ?? "" }

    @Test func framesKeepTheStrictRulesEvenInsideAnOJPage() {
        let p = policy()
        for raw in ["https://cdn.random-host.net/", "https://img.example.org/x", "http://plain.example.org/", "https://example.org/embed"] {
            #expect(!p.allows(URL(string: raw), mainFrame: false), "\(raw) iframe")
            #expect(!p.allows(URL(string: raw), mainFrame: true), "\(raw)")
        }
        #expect(p.allows(URL(string: "https://oj.example.edu/frame"), mainFrame: false))
    }

    @Test func theOJAllowanceNeverCoversDocuments() throws {
        let p = policy()
        let all = try rules(p)
        let open = try #require(all.first { action($0) == "ignore-previous-rules" && trigger($0)["if-top-url"] != nil })
        let types = try #require(trigger(open)["resource-type"] as? [String])
        #expect(!types.contains("document"))
        #expect(!types.contains("popup"))
        for t in ["image", "script", "style-sheet", "font", "raw", "media", "fetch"] { #expect(types.contains(t), "\(t)") }
        for r in all where action(r) == "ignore-previous-rules" && trigger(r)["if-top-url"] != nil {
            #expect(trigger(r)["resource-type"] != nil, "\(trigger(r)["url-filter"] ?? "")")
        }
    }

    @Test func localNetworkHostsAreBlockedForTheOJPageExceptTheOJsOwn() throws {
        let p = ProtectedBrowserPolicy(start: "https://10.200.31.64:8443", additionalOrigins: "", forbiddenOrigin: forbidden)!
        let all = try rules(p)
        let filters = all.filter { trigger($0)["if-top-url"] != nil }.map { (trigger($0)["url-filter"] as? String ?? "", action($0)) }
        let blocks = filters.filter { $0.1 == "block" }.map(\.0)
        for probe in ["https://127.0.0.1/", "http://127.9.9.9:8888/", "http://localhost:8888/", "http://[::1]:8000/", "http://10.1.2.3/",
                      "http://172.16.0.1/", "http://172.31.255.1/", "http://192.168.1.5/", "http://169.254.1.1/", "http://[fd12::1]/",
                      "http://[fe80::1]/", "http://[::ffff:7f00:1]/", "http://printer.local/", "http://ai.internal:11434/", "http://nas.lan/"] {
            #expect(blocks.contains { probe.range(of: $0, options: .regularExpression) != nil }, "\(probe)")
        }
        for probe in ["http://172.32.0.1/", "http://172.15.0.1/", "https://10.example.com/", "https://cdn.example.org/", "https://[2001:db8::1]/"] {
            #expect(!blocks.contains { probe.range(of: $0, options: .regularExpression) != nil }, "\(probe)")
        }
        let reopen = filters.filter { $0.1 == "ignore-previous-rules" }.map(\.0)
        #expect(reopen.contains { "https://10.200.31.64:8443/a.png".range(of: $0, options: .regularExpression) != nil })
        let lastLocalBlock = try #require(all.lastIndex { trigger($0)["url-filter"] as? String == "^https?://10\\.[0-9]+\\.[0-9]+\\.[0-9]+[:/]" })
        let reopenIndex = try #require(all.firstIndex { trigger($0)["url-filter"] as? String == "^https://10\\.200\\.31\\.64:8443/" && trigger($0)["if-top-url"] != nil })
        #expect(reopenIndex > lastLocalBlock)
    }

    @Test func userinfoAndTrailingDotHostsAreBlockedForTheOJPage() throws {
        let p = policy()
        let blocks = try rules(p).filter { action($0) == "block" && trigger($0)["if-top-url"] != nil }.compactMap { trigger($0)["url-filter"] as? String }
        for probe in ["https://u@chatgpt.com/", "https://user:pw@chat.openai.com:443/x", "https://chatgpt.com./a.js", "http://example.org./x", "https://example.org.:8080/"] {
            #expect(blocks.contains { probe.range(of: $0, options: .regularExpression) != nil }, "\(probe)")
        }
        for probe in ["https://cdn.example.org/a@b.js", "https://cdn.example.org/a.b./c"] {
            #expect(!blocks.contains { probe.range(of: $0, options: .regularExpression) != nil }, "\(probe)")
        }
    }

    @Test func mainFrameRulesAreUnchanged() {
        let p = policy()
        #expect(!p.allows(URL(string: "https://cdn.random-host.net/"), mainFrame: true))
        #expect(p.allows(URL(string: "https://oj.example.edu/x"), mainFrame: true))
    }

    @Test func actualWebKitCompilesTheOJPageRules() async throws {
        for start in ["https://oj.example.edu", "https://10.200.31.64:8443"] {
            let p = ProtectedBrowserPolicy(start: start, additionalOrigins: "https://login.example.edu", forbiddenOrigin: forbidden,
                                           assetOrigins: ["https://cdn.example.edu"])!
            let id = "fairplay.oj-rules.test." + UUID().uuidString
            let list: WKContentRuleList = try await withCheckedThrowingContinuation { c in
                WKContentRuleListStore.default().compileContentRuleList(forIdentifier: id, encodedContentRuleList: p.contentRules) { list, error in
                    if let list { c.resume(returning: list) } else { c.resume(throwing: error ?? NSError(domain: "rules", code: 1)) }
                }
            }
            #expect(list.identifier == id)
            await withCheckedContinuation { c in WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in c.resume() } }
        }
    }

    @Test func contentRulesScopeTheAllowanceToOJTopPagesAndKeepTheDenyList() throws {
        let p = policy()
        let rules = try #require(JSONSerialization.jsonObject(with: Data(p.contentRules.utf8)) as? [[String: Any]])
        #expect((rules.first?["trigger"] as? [String: Any])?["url-filter"] as? String == ".*")
        let open = try #require(rules.first { ($0["action"] as? [String: Any])?["type"] as? String == "ignore-previous-rules"
            && ($0["trigger"] as? [String: Any])?["if-top-url"] != nil })
        let tops = try #require((open["trigger"] as? [String: Any])?["if-top-url"] as? [String])
        #expect(tops.contains { $0.contains("oj\\.example\\.edu") })
        #expect(p.contentRules.contains("chatgpt\\\\.com"))
        #expect(p.contentRules.contains("www\\\\.google\\\\.com"))
    }
}
