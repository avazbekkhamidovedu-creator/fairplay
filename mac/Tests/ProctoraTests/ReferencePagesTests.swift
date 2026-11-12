import Testing
import Foundation
import AppKit
import WebKit
@testable import Proctora

extension ReferencePages {
    mutating func claimAndOpen(_ pages: [String], openTabs: Int) -> [String] {
        let list = claim(pages, openTabs: openTabs)
        for raw in list { opened(raw) }
        return list
    }
}

@Suite struct ReferencePagesTests {
    @Test func spellingsOfOnePageAreOnePage() {
        #expect(ReferencePages.distinct(["https://a.com", "https://a.com/", "https://A.com/", "https://a.com:443/", "http://a.com:80/"])
            == ["https://a.com", "http://a.com:80/"])
        #expect(ReferencePages.key("https://A.com") == ReferencePages.key("https://a.com:443/"))
    }

    @Test func aPageThatDidNotOpenIsClaimedAgainOnTheNextRenew() {
        var pages = ReferencePages()
        #expect(pages.claim(["https://a.com/"], openTabs: 1) == ["https://a.com/"])
        // The controller could not open it (no lock, stale policy): never reported as opened.
        #expect(pages.claim(["https://a.com/"], openTabs: 1) == ["https://a.com/"])
        pages.opened("https://a.com/")
        #expect(pages.claim(["https://a.com/"], openTabs: 1).isEmpty)
    }

    @Test func aPageAlreadyOpenInATabIsNotOpenedAgainAfterAReset() {
        var pages = ReferencePages()
        pages.reset()
        let open: Set<String> = [ReferencePages.key("https://A.com")]
        #expect(pages.claim(["https://a.com/", "https://b.com/"], openTabs: 2, open: open) == ["https://b.com/"])
        #expect(pages.claim(["https://a.com/"], openTabs: 2, open: []).isEmpty)
    }

    @Test func oneSharedTabLimit() {
        #expect(ReferencePages.maxTabs == 8 && ReferencePages.maxExtraTabs == 5)
    }

    private let server = [
        "https://duke.app.box.com/s/abc123",
        "https://docs.python.org/",
        "https://cppreference.com/",
        "https://docs.python.org/3/library/",
    ]

    @Test func startPageReplacesTheBareRootOfTheSameHost() {
        #expect(ReferencePages.distinct(server) == [
            "https://duke.app.box.com/s/abc123",
            "https://cppreference.com/",
            "https://docs.python.org/3/library/",
        ])
    }

    @Test func rootsStayWhenThereIsNoStartPageAndDuplicatesGo() {
        let list = ["https://a.com/", "https://b.com/", "https://a.com/", "https://B.com/x", "https://b.com/y"]
        #expect(ReferencePages.distinct(list) == ["https://a.com/", "https://B.com/x", "https://b.com/y"])
        #expect(ReferencePages.distinct([]) == [])
    }

    @Test func labelsShowThePathOfAStartPage() {
        #expect(ReferencePages.label("https://docs.python.org/") == "docs.python.org")
        #expect(ReferencePages.label("https://docs.python.org/3/library/") == "docs.python.org/3/library")
    }

    @Test func firstListOpensEverythingInOrderAndAnIdenticalRenewOpensNothing() {
        var pages = ReferencePages()
        let list = ReferencePages.distinct(server)
        #expect(pages.claimAndOpen(list, openTabs: 1) == list)
        #expect(pages.claimAndOpen(list, openTabs: 1).isEmpty)
        #expect(pages.claimAndOpen(list, openTabs: 2).isEmpty)
    }

    @Test func onlyNewPagesOpenWhenTheInstructorAddsOne() {
        var pages = ReferencePages()
        _ = pages.claimAndOpen(["https://a.com/", "https://b.com/"], openTabs: 1)
        #expect(pages.claimAndOpen(["https://a.com/", "https://b.com/", "https://c.com/"], openTabs: 3) == ["https://c.com/"])
    }

    @Test func aRemovedAndReAddedPageOpensAgain() {
        var pages = ReferencePages()
        _ = pages.claimAndOpen(["https://a.com/", "https://b.com/"], openTabs: 1)
        _ = pages.claimAndOpen(["https://a.com/"], openTabs: 2)
        #expect(pages.claimAndOpen(["https://a.com/", "https://b.com/"], openTabs: 2) == ["https://b.com/"])
    }

    @Test func capLeavesTheRestInTheMenuAndTheyDoNotOpenLater() {
        var pages = ReferencePages()
        let ten = (1...10).map { "https://s\($0).com/" }
        let first = pages.claimAndOpen(ten, openTabs: 1)
        #expect(first == Array(ten.prefix(5)))
        #expect(pages.claimAndOpen(ten, openTabs: 4).isEmpty)
    }

    @Test func autoOpenLeavesTwoSlotsFreeForPopupsAndTheStudentsOwnLinks() {
        var pages = ReferencePages()
        let ten = (1...10).map { "https://s\($0).com/" }
        // Exam tab + 5 pages = 6 of 8: two slots stay free.
        #expect(pages.claimAndOpen(ten, openTabs: 1).count == 5)
        var more = ReferencePages()
        #expect(more.claimAndOpen(ten, openTabs: 6).isEmpty)
        #expect(ReferencePages.maxTabs - (1 + ReferencePages.maxExtraTabs) == 2)
    }

    @Test func fullTabBarOpensNothing() {
        var pages = ReferencePages()
        #expect(pages.claimAndOpen(["https://a.com/"], openTabs: 8).isEmpty)
    }

    @Test func resetOpensEverythingAgain() {
        var pages = ReferencePages()
        _ = pages.claimAndOpen(["https://a.com/"], openTabs: 1)
        pages.reset()
        #expect(pages.claimAndOpen(["https://a.com/"], openTabs: 1) == ["https://a.com/"])
    }

    @Test func closedBookStaysEmpty() {
        var pages = ReferencePages()
        #expect(pages.claimAndOpen([], openTabs: 1).isEmpty)
    }
}

@MainActor @Suite struct ReferenceAutoOpenTests {
    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }
    private func context(_ state: String, sites: [String], session: String = "p") throws -> BrowserExamContext {
        let list = sites.map { "\"\($0)\"" }.joined(separator: ",")
        let json = #"{"v":1,"nonce":"n","expires":1,"user":"u","contest":"c","participation":"\#(session)","state":"\#(state)","sites":[\#(list)]}"#
        return try JSONDecoder().decode(BrowserExamContext.self, from: Data(json.utf8))
    }
    private final class Probe {
        var locked = false
        var loads: [URL] = []
        let store = InMemoryNativeLockStore()
    }
    private func open(_ probe: Probe, root: NSView) async throws -> ProtectedBrowserController {
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false }, load: { _, url in probe.loads.append(url) })
        var setup = NativeLockSetup.dev
        setup.store = probe.store
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        return controller
    }
    private func tabs(_ root: NSView) -> [WKWebView] { descendants(root).compactMap { $0 as? WKWebView } }

    @Test func allowedPagesOpenOnTheFirstListAndNotAgainOnARenew() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let before = probe.loads.count
        let sites = ["https://docs.python.org/", "https://cppreference.com/", "https://docs.python.org/3/library/"]
        controller.applyExamContext(try context("active", sites: sites), url: nil)
        #expect(await waitUntil { tabs(root).count == 3 })
        #expect(Array(probe.loads.dropFirst(before)) == [URL(string: "https://cppreference.com/")!, URL(string: "https://docs.python.org/3/library/")!])
        // Exactly one tab is visible: the exam tab keeps the screen.
        #expect(tabs(root).filter { !$0.isHidden }.count == 1)
        controller.applyExamContext(try context("active", sites: sites), url: nil)
        try await Task.sleep(for: .milliseconds(300))
        #expect(tabs(root).count == 3)
        #expect(probe.loads.count == before + 2)
    }

    @Test func closedBookOpensNoExtraTab() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let before = probe.loads.count
        controller.applyExamContext(try context("active", sites: []), url: nil)
        try await Task.sleep(for: .milliseconds(300))
        #expect(tabs(root).count == 1 && probe.loads.count == before)
    }

    private final class FakeAction: WKNavigationAction {
        let urlRequest: URLRequest, download: Bool
        init(_ url: String, download: Bool = false) { urlRequest = URLRequest(url: URL(string: url)!); self.download = download; super.init() }
        override var request: URLRequest { urlRequest }
        override var shouldPerformDownload: Bool { download }
        override var targetFrame: WKFrameInfo? { nil }
    }

    private func openThree(_ probe: Probe, _ root: NSView) async throws -> ProtectedBrowserController {
        let controller = try await open(probe, root: root)
        controller.applyExamContext(try context("active", sites: ["https://docs.python.org/", "https://cppreference.com/"]), url: nil)
        #expect(await waitUntil { tabs(root).count == 3 })
        return controller
    }

    @Test func aBindingChangeDoesNotDuplicateTabsAlreadyOpen() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        let loads = probe.loads.count
        controller.applyExamContext(try context("active", sites: ["https://docs.python.org/", "https://cppreference.com/", "https://A.com/"], session: "q"), url: nil)
        #expect(await waitUntil { tabs(root).count == 4 })
        try await Task.sleep(for: .milliseconds(200))
        #expect(tabs(root).count == 4 && probe.loads.count == loads + 1)
    }

    @Test func aBackgroundTabCannotDownloadOrOpenWindowsUntilSelected() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        let background = try #require(tabs(root).first { $0.isHidden })
        var decisions: [WKNavigationActionPolicy] = []
        controller.webView(background, decidePolicyFor: FakeAction("https://docs.python.org/f.zip", download: true)) { decisions.append($0) }
        #expect(decisions == [.cancel])
        let before = tabs(root).count
        let made = controller.webView(background, createWebViewWith: WKWebViewConfiguration(), for: FakeAction("https://docs.python.org/x"), windowFeatures: WKWindowFeatures())
        #expect(made == nil && tabs(root).count == before)
        // An ordinary navigation of the loading tab is still allowed.
        var normal: [WKNavigationActionPolicy] = []
        controller.webView(background, decidePolicyFor: FakeAction("https://docs.python.org/3/")) { normal.append($0) }
        #expect(normal == [.allow])
    }

    @Test func aBackgroundTabThatFailedLoadsAgainWhenSelected() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        for background in tabs(root).filter({ $0.isHidden }) {
            controller.webView(background, didFailProvisionalNavigation: nil, withError: NSError(domain: NSURLErrorDomain, code: NSURLErrorNotConnectedToInternet))
        }
        let loads = probe.loads.count
        let button = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.tag == 1 && ($0.accessibilityLabel() ?? "").hasPrefix("Tab:") })
        button.performClick(nil)
        #expect(probe.loads.count == loads + 1)
        #expect(probe.loads.last.map { ["https://docs.python.org/", "https://cppreference.com/"].contains($0.absoluteString) } == true)
    }

    private final class Sender: NSObject, URLAuthenticationChallengeSender {
        func use(_ credential: URLCredential, for challenge: URLAuthenticationChallenge) {}
        func continueWithoutCredential(for challenge: URLAuthenticationChallenge) {}
        func cancel(_ challenge: URLAuthenticationChallenge) {}
    }
    private func trustChallenge() -> URLAuthenticationChallenge {
        let space = URLProtectionSpace(host: "docs.python.org", port: 443, protocol: "https", realm: nil, authenticationMethod: NSURLAuthenticationMethodServerTrust)
        return URLAuthenticationChallenge(protectionSpace: space, proposedCredential: nil, previousFailureCount: 0, failureResponse: nil, error: nil, sender: Sender())
    }

    @Test func aBackgroundTabAnswersAServerTrustChallengeLikeAnyTabAndAStrangerIsCancelled() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        let background = try #require(tabs(root).first { $0.isHidden })
        var got: [URLSession.AuthChallengeDisposition] = []
        controller.webView(background, didReceive: trustChallenge()) { d, _ in got.append(d) }
        #expect(got == [.performDefaultHandling])
        let exam = try #require(tabs(root).first { !$0.isHidden })
        controller.webView(exam, didReceive: trustChallenge()) { d, _ in got.append(d) }
        #expect(got.last == .performDefaultHandling)
        controller.webView(WKWebView(), didReceive: trustChallenge()) { d, _ in got.append(d) }
        #expect(got.last == .cancelAuthenticationChallenge)
    }

    @Test func everyWayOfRemovingATabForgetsItsReferenceUrl() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        #expect(controller.rememberedReferenceTabs == 2)
        func select(_ tag: Int) throws {
            try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.tag == tag && ($0.accessibilityLabel() ?? "").hasPrefix("Tab:") }).performClick(nil)
        }
        // Select tab 1, then tab 2: tab 1 is now a hidden parent. Its popup closing must not leave its URL behind.
        try select(1)
        let parent = try #require(tabs(root).first { !$0.isHidden })
        try select(2)
        #expect(parent.isHidden)
        controller.webViewDidClose(parent)
        #expect(controller.rememberedReferenceTabs == 1)
    }

    @Test func theAllowedPagesOpenOneAfterTheOtherAndNeverUseEverySlot() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let sites = (1...8).map { "https://s\($0).example.org/" }
        controller.applyExamContext(try context("active", sites: sites), url: nil)
        #expect(await waitUntil { tabs(root).count >= 2 })
        #expect(tabs(root).count < 6, "the pages are staggered, not opened together")
        #expect(await waitUntil { tabs(root).count == 6 })
        try await Task.sleep(for: .milliseconds(800))
        #expect(tabs(root).count == 6)
    }

    @Test func theEmergencyExitButtonIsOnTopAndTheTabBarEndsBeforeIt() async throws {
        for width in [900.0, 1024.0, 1600.0] {
            let probe = Probe()
            let root = NSView(frame: NSRect(x: 0, y: 0, width: width, height: 768))
            let controller = try await open(probe, root: root)
            let leave = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Emergency exit" })
            let bar = try #require(descendants(root).first { $0 is SafariTabBar })
            #expect(bar.frame.maxX <= leave.frame.minX, "width \(width)")
            let siblings = try #require(leave.superview?.subviews)
            #expect(bar.superview === leave.superview)
            #expect(try #require(siblings.firstIndex(of: leave)) > #require(siblings.firstIndex(of: bar)))
            #expect(leave.accessibilityLabel() == NativeLeaveText.title)
            controller.lockEnded()
        }
    }

    // MARK: Duke Box sign-in in a Box tab

    private let boxLink = "https://duke.box.com/s/abcdefghij1234"
    private let chain = ["https://duke.account.box.com/login?x=1", "https://sso.services.box.net/sp/acs", "https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO",
                         "https://api-1a2b3c.duosecurity.com/frame/web/v1/auth", "https://duke.app.box.com/s/abcdefghij1234"]

    @Test func theBoxSignInChainOpensInABoxTabAndOnlyThere() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        controller.applyExamContext(try context("active", sites: [boxLink, "https://docs.python.org/"]), url: nil)
        #expect(await waitUntil { tabs(root).count == 3 })
        let exam = try #require(tabs(root).first { !$0.isHidden })
        let background = tabs(root).filter { $0 !== exam }
        func decision(_ tab: WKWebView, _ url: String) -> WKNavigationActionPolicy? {
            var out: [WKNavigationActionPolicy] = []
            controller.webView(tab, decidePolicyFor: FakeAction(url)) { out.append($0) }
            return out.first
        }
        for url in chain.dropLast() {
            // Exactly one background tab started on the Box link: it alone may open Duke sign-in.
            #expect(background.filter { decision($0, url) == .allow }.count == 1, "\(url)")
            #expect(decision(exam, url) == .cancel, "exam tab: \(url)")
        }
    }

    @Test func theBoxChainFollowsTheRulesOfTheBoxPane() {
        let box = URL(string: boxLink)!
        func u(_ s: String) -> URL { URL(string: s)! }
        for good in chain.dropLast() { #expect(BoxSignIn.allowsTopLevel(target: u(good), current: box, boxTab: true), "\(good)") }
        // Not a Box tab, or not on the chain any more.
        #expect(!BoxSignIn.allowsTopLevel(target: u(chain[2]), current: box, boxTab: false))
        #expect(!BoxSignIn.allowsTopLevel(target: u(chain[2]), current: u("https://docs.python.org/"), boxTab: true))
        // A fresh Box tab has no page yet.
        #expect(BoxSignIn.allowsTopLevel(target: u(chain[1]), current: nil, boxTab: true))
        // Account hosts only on /login; https only; no user name, port, punycode, hidden separators; other hosts never.
        #expect(!BoxSignIn.allowsSignIn(u("https://account.box.com/settings")))
        #expect(!BoxSignIn.allowsSignIn(u("http://shib.oit.duke.edu/idp")))
        #expect(!BoxSignIn.allowsSignIn(u("https://user@shib.oit.duke.edu/idp")))
        #expect(!BoxSignIn.allowsSignIn(u("https://shib.oit.duke.edu:8443/idp")))
        #expect(!BoxSignIn.allowsSignIn(u("https://xn--shib.oit.duke.edu/idp")))
        #expect(!BoxSignIn.allowsSignIn(u("https://duosecurity.com/")))
        #expect(!BoxSignIn.allowsSignIn(u("https://evil-duosecurity.com/")))
        #expect(!BoxSignIn.allowsSignIn(u("https://app.box.com/login")))
        #expect(!BoxSignIn.allowsSignIn(u("https://shib.oit.duke.edu/a%2fb")))
        #expect(BoxSignIn.isBoxPage(box) && !BoxSignIn.isBoxPage(u("https://app.box.com/s/abc")))
    }

    @Test func theBoxHostListIsTheOneTheBoxPaneTrusts() {
        let body: [String: Any] = [
            "start_url": "https://duke.app.box.com/s/abcdefghij1234", "path_prefix": "/s/abcdefghij1234", "app_hosts": ["duke.app.box.com"],
            "sign_in_hosts": [["host": "duke.account.box.com", "paths": ["/login*"]], ["host": "sso.services.box.net"], ["host": "shib.oit.duke.edu"], ["host": "*.duosecurity.com"]],
            "asset_hosts": ["cdn01.boxcdn.net", "*.boxcloud.com", "api.box.com"], "blocked_requests": []]
        #expect(BoxPolicy(body: body, allowDev: false) != nil)
        // A Box link enables the chain for Box tabs only: the policy itself never lists its hosts as frames or pages, so the exam tab
        // and every other tab cannot load them; a chain host a list sends as a plain resource is dropped.
        let policy = ProtectedBrowserPolicy(start: "https://dkuoj.example.edu/", additionalOrigins: "", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!,
                                            referenceSites: [boxLink],
                                            resourceOrigins: ["https://api.box.com", "https://api.box.com:443", "https://API.BOX.COM.", "https://*.boxcloud.com", "https://cdn.example.org"])
        #expect(policy?.hasBoxPage == true)
        for url in ["https://api.box.com/2.0/x", "https://duke.account.box.com/login", "https://dl.boxcloud.com/x"] {
            #expect(policy?.allows(URL(string: url), mainFrame: false) == false, Comment(rawValue: url))
            #expect(policy?.allows(URL(string: url), mainFrame: true) == false, Comment(rawValue: url))
        }
        #expect(policy?.allows(URL(string: "https://cdn.example.org/a.js"), mainFrame: false) == true)
        // Content rules: every rule that lets a chain host load is scoped to a chain page and never lets /settings of an account host in.
        let rules = try? JSONSerialization.jsonObject(with: Data((policy?.contentRules ?? "[]").utf8)) as? [[String: Any]]
        let chainRules = (rules ?? []).filter { (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("shib") == true
            || (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("duosecurity") == true
            || (($0["trigger"] as? [String: Any])?["url-filter"] as? String)?.contains("account") == true }
        #expect(!chainRules.isEmpty)
        for rule in chainRules where ((rule["action"] as? [String: Any])?["type"] as? String) == "ignore-previous-rules" {
            let trigger = rule["trigger"] as? [String: Any]
            #expect((trigger?["if-top-url"] as? [String])?.isEmpty == false, "\(trigger ?? [:])")
        }
        let account = chainRules.compactMap { (($0["trigger"] as? [String: Any])?["url-filter"] as? String) }.filter { $0.contains("account\\.box") }
        #expect(!account.isEmpty && account.allSatisfy { $0.hasSuffix("/login") })
        // No Box page in the list: no chain at all.
        let none = ProtectedBrowserPolicy(start: "https://dkuoj.example.edu/", additionalOrigins: "", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!, referenceSites: ["https://docs.python.org/"])
        #expect(none?.hasBoxPage == false && !(none?.contentRules.contains("shib") ?? true))
    }

    @Test func frameRulesOfTheChainAreForABoxTabOnTheChainOnly() {
        func u(_ s: String) -> URL { URL(string: s)! }
        let box = u(boxLink)
        #expect(BoxSignIn.allowsSubframe(target: u("https://api-1.duosecurity.com/frame"), current: u(chain[2]), boxTab: true))
        #expect(BoxSignIn.allowsSubframe(target: u("https://cdn01.boxcdn.net/a"), current: box, boxTab: true))
        #expect(!BoxSignIn.allowsSubframe(target: u("https://api-1.duosecurity.com/frame"), current: box, boxTab: false))
        #expect(!BoxSignIn.allowsSubframe(target: u("https://api-1.duosecurity.com/frame"), current: u("https://docs.python.org/"), boxTab: true))
        #expect(!BoxSignIn.allowsSubframe(target: u("https://duke.account.box.com/settings"), current: box, boxTab: true))
        for raw in ["https://api.box.com", "https://api.box.com:443", "https://API.box.com.", "api.box.com", "https://*.boxcloud.com", "https://duke.account.box.com/"] {
            #expect(BoxSignIn.isChainResource(raw), Comment(rawValue: raw))
        }
        // Duke SSO and Duo are not Box's: other allowed sites keep them as before.
        #expect(!BoxSignIn.isChainResource("https://shib.oit.duke.edu") && !BoxSignIn.isChainResource("https://*.duosecurity.com"))
        #expect(!BoxSignIn.isChainResource("https://cdn.example.org"))
        let plain = ProtectedBrowserPolicy(start: "https://dkuoj.example.edu/", additionalOrigins: "", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!,
                                           referenceSites: ["https://docs.python.org/"], resourceOrigins: ["https://shib.oit.duke.edu", "https://*.duosecurity.com"])
        #expect(plain?.allows(URL(string: "https://shib.oit.duke.edu/x"), mainFrame: false) == true)
        #expect(plain?.allows(URL(string: "https://api-1.duosecurity.com/x"), mainFrame: false) == true)
    }

    @Test func aBoxTabLosesTheChainWhenTheBoxLinkIsRemoved() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        controller.applyExamContext(try context("active", sites: [boxLink, "https://docs.python.org/"]), url: nil)
        #expect(await waitUntil { tabs(root).count == 3 })
        let exam = try #require(tabs(root).first { !$0.isHidden })
        let background = tabs(root).filter { $0 !== exam }
        #expect(background.contains { controller.boxChainAllowed(URL(string: chain[2]), in: $0) })
        controller.applyExamContext(try context("active", sites: ["https://docs.python.org/"], session: "q"), url: nil)
        #expect(await waitUntil { tabs(root).count == 2 })
        for tab in tabs(root) { #expect(!controller.boxChainAllowed(URL(string: chain[2]), in: tab)) }
    }

    @Test func anyTabThatGoesToAnAllowedBoxPageMayThenSignIn() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        controller.applyExamContext(try context("active", sites: [boxLink, "https://docs.python.org/"]), url: nil)
        #expect(await waitUntil { tabs(root).count == 3 })
        let exam = try #require(tabs(root).first { !$0.isHidden })
        let plain = try #require(tabs(root).first { $0 !== exam && !controller.boxChainAllowed(URL(string: chain[2]), in: $0) })
        #expect(!controller.boxChainAllowed(URL(string: chain[2]), in: plain))
        var d: [WKNavigationActionPolicy] = []
        controller.webView(plain, decidePolicyFor: FakeAction(boxLink)) { d.append($0) }
        #expect(d == [.allow] && controller.boxChainAllowed(URL(string: chain[2]), in: plain))
        // The exam tab is sent to its own tab for a Box page and never becomes one.
        controller.webView(exam, decidePolicyFor: FakeAction(boxLink)) { _ in }
        #expect(!controller.boxChainAllowed(URL(string: chain[2]), in: exam))
    }

    @Test func aHoldTearsDownEveryTabIncludingTheBackgroundOnes() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        let all = tabs(root)
        #expect(all.count == 3 && all.contains { $0.isHidden })
        controller.applyHold(true)
        for tab in all {
            #expect(tab.superview == nil && tab.navigationDelegate == nil && tab.uiDelegate == nil)
        }
        #expect(controller.rememberedReferenceTabs == 0)
        #expect(tabs(root).isEmpty)
    }

    @Test func aHoldOrABindingChangeStopsTheStaggeredOpening() async throws {
        for hold in [true, false] {
            let probe = Probe()
            let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
            var fires: [() -> Void] = []
            var cancelled = 0
            var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
                ownsLock: { probe.locked }, release: { probe.locked = false }, load: { _, url in probe.loads.append(url) })
            effects.schedule = { delay, fire in
                guard delay == ProtectedBrowserController.staggerDelay else { return {} }
                fires.append(fire); return { cancelled += 1 }
            }
            var setup = NativeLockSetup.dev
            setup.store = probe.store
            effects.setup = setup
            let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
            controller.show(in: root)
            descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
            try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
            #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
            controller.applyExamContext(try context("active", sites: ["https://a.example.org/", "https://b.example.org/", "https://c.example.org/"]), url: nil)
            #expect(await waitUntil { tabs(root).count == 2 && fires.count == 1 })
            let loads = probe.loads.count
            if hold { controller.applyHold(true) }
            else { controller.applyExamContext(try context("active", sites: ["https://a.example.org/", "https://b.example.org/", "https://c.example.org/"], session: "other"), url: nil) }
            #expect(cancelled >= 1, "hold=\(hold): the pending timer is cancelled")
            if hold {
                // Even a timer that still fires opens nothing behind the hold.
                fires.removeFirst()()
                #expect(tabs(root).isEmpty && probe.loads.count == loads)
            }
            controller.lockEnded()
        }
    }

    @Test func aSecondBatchJoinsTheRunningQueueAndAFailedPageIsTriedAgainOnTheNextRenew() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var fires: [() -> Void] = []
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false }, load: { _, url in probe.loads.append(url) })
        effects.schedule = { delay, fire in
            guard delay == ProtectedBrowserController.staggerDelay else { return {} }
            fires.append(fire); return {}
        }
        var setup = NativeLockSetup.dev
        setup.store = probe.store
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        defer { controller.lockEnded() }
        let sites = ["https://a.example.org/", "https://b.example.org/", "https://c.example.org/", "https://d.example.org/"]
        controller.applyExamContext(try context("active", sites: Array(sites.prefix(2))), url: nil)
        #expect(await waitUntil { tabs(root).count == 2 && fires.count == 1 })
        // A second list while the first chain waits: its page joins the same queue, nothing is orphaned.
        controller.applyExamContext(try context("active", sites: Array(sites.prefix(3))), url: nil)
        #expect(await waitUntil { tabs(root).count == 2 })
        var guardCount = 0
        while !fires.isEmpty, guardCount < 10 { fires.removeFirst()(); guardCount += 1 }
        #expect(await waitUntil { tabs(root).count == 4 }, "all three pages open exactly once")
        // The same list again on the next renew claims nothing new (dedupe-safe).
        controller.applyExamContext(try context("active", sites: Array(sites.prefix(3))), url: nil)
        try await Task.sleep(for: .milliseconds(200))
        #expect(tabs(root).count == 4 && fires.isEmpty)
    }

    @Test func endingTheLeaveSheetEndsOnlyTheLeaveSheet() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
        let other = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled], backing: .buffered, defer: false)
        window.beginSheet(other) { _ in }
        NativeLeaveSheet.end(in: window)
        #expect(window.attachedSheet === other, "a sheet that is not the Emergency exit one stays")
        window.endSheet(other)
    }

    @Test func theStaggerKeepsOnePendingTimerAndNoneAfterItFires() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var fires: [() -> Void] = []
        var cancelled = 0
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false }, load: { _, url in probe.loads.append(url) })
        effects.schedule = { delay, fire in
            guard delay == ProtectedBrowserController.staggerDelay else { return {} }
            fires.append(fire); return { cancelled += 1 }
        }
        var setup = NativeLockSetup.dev
        setup.store = probe.store
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        controller.applyExamContext(try context("active", sites: ["https://a.example.org/", "https://b.example.org/", "https://c.example.org/"]), url: nil)
        #expect(await waitUntil { tabs(root).count == 2 && fires.count == 1 })
        fires.removeFirst()()
        #expect(tabs(root).count == 3 && fires.count == 1)
        fires.removeFirst()()
        #expect(tabs(root).count == 4 && fires.isEmpty)
        controller.lockEnded()
        #expect(cancelled == 0, "a timer that fired is not kept to be cancelled later")
    }

    // MARK: Safari-style tab bar

    @Test func tabsShareTheRowEquallyAndShrinkLikeSafari() {
        let w = SafariTabBar.tabWidth
        #expect(w(1, 1000) == SafariTabBar.maxTabWidth)
        #expect(w(3, 1000) == SafariTabBar.maxTabWidth)
        #expect(w(8, 800) < w(4, 800))
        #expect(w(8, 300) == SafariTabBar.minTabWidth)
        let bounds = NSRect(x: 0, y: 0, width: 600, height: 40)
        let frames = (0..<4).map { SafariTabBar.tabFrame(index: $0, count: 4, bounds: bounds) }
        #expect(Set(frames.map(\.width)).count == 1)
        #expect(frames.last!.maxX <= bounds.width)
        for pair in zip(frames, frames.dropFirst()) { #expect(pair.0.maxX <= pair.1.minX) }
    }

    @Test func theTabBarShowsFaviconTitleCloseOnHoverAndKeepsTheLabels() {
        let bar = SafariTabBar(frame: NSRect(x: 0, y: 0, width: 600, height: 40))
        let a = NSView(), b = NSView()
        bar.update([SafariTabItem(id: ObjectIdentifier(a), title: "DKU OJ", selected: true, closable: false, useExamIcon: true),
                    SafariTabItem(id: ObjectIdentifier(b), title: "Python docs", selected: false, closable: true)])
        let buttons = bar.tabButtons
        #expect(buttons.count == 2)
        #expect(buttons[0].accessibilityLabel() == "Tab: DKU OJ" && buttons[1].accessibilityLabel() == "Tab: Python docs")
        #expect(buttons[0].tag == 0 && buttons[1].tag == 1)
        #expect(buttons[0].label.stringValue == "DKU OJ" && buttons[0].iconView.image != nil && buttons[1].iconView.image != nil)
        // Exam tab: never closable. Other tabs: the close button shows on hover only.
        #expect(buttons[0].closeButton.isHidden && !buttons[0].item.closable)
        #expect(buttons[1].closeButton.isHidden)
        buttons[1].mouseEntered(with: NSEvent())
        #expect(!buttons[1].closeButton.isHidden)
        buttons[1].mouseExited(with: NSEvent())
        #expect(buttons[1].closeButton.isHidden)
        #expect(buttons[0].frame.width == buttons[1].frame.width)
    }

    @Test func anUnchangedUpdateMovesNothing() {
        let bar = SafariTabBar(frame: NSRect(x: 0, y: 0, width: 600, height: 40))
        let a = NSView(), b = NSView()
        let items = [SafariTabItem(id: ObjectIdentifier(a), title: "One", selected: true, closable: false),
                     SafariTabItem(id: ObjectIdentifier(b), title: "Two", selected: false, closable: true)]
        bar.update(items)
        let marker = NSRect(x: 5, y: 5, width: 50, height: 20)
        bar.tabButtons[1].frame = marker
        bar.update(items)
        #expect(bar.tabButtons[1].frame == marker, "the same event twice does not relayout or re-animate")
        var changed = items; changed[0].title = "One!"
        bar.update(changed)
        #expect(bar.tabButtons[0].label.stringValue == "One!")
    }

    @Test func aTabThatIsLeavingTakesNoMoreClicks() {
        let bar = SafariTabBar(frame: NSRect(x: 0, y: 0, width: 600, height: 40))
        let a = NSView(), b = NSView()
        var selected: [Int] = []
        bar.onSelect = { selected.append($0) }
        bar.update([SafariTabItem(id: ObjectIdentifier(a), title: "One", selected: true, closable: false),
                    SafariTabItem(id: ObjectIdentifier(b), title: "Two", selected: false, closable: true)])
        let leaving = bar.tabButtons[1]
        bar.update([SafariTabItem(id: ObjectIdentifier(a), title: "One", selected: true, closable: false)])
        leaving.performClick(nil)
        #expect(selected.isEmpty && !leaving.isEnabled && !leaving.closeButton.isEnabled)
    }

    @Test func aNewTabAppearsAtTheEndAndATitleChangeKeepsTheSameTab() {
        let bar = SafariTabBar(frame: NSRect(x: 0, y: 0, width: 600, height: 40))
        let a = NSView(), b = NSView(), c = NSView()
        let first = SafariTabItem(id: ObjectIdentifier(a), title: "One", selected: true, closable: false)
        bar.update([first, SafariTabItem(id: ObjectIdentifier(b), title: "docs.python.org", selected: false, closable: true)])
        let second = bar.tabButtons[1]
        bar.update([first, SafariTabItem(id: ObjectIdentifier(b), title: "Python 3 documentation", selected: false, closable: true),
                    SafariTabItem(id: ObjectIdentifier(c), title: "New tab", selected: false, closable: true)])
        #expect(bar.tabButtons.count == 3 && bar.tabButtons[1] === second && second.label.stringValue == "Python 3 documentation")
        #expect(bar.tabButtons[2].label.stringValue == "New tab" && bar.tabButtons[2].frame.minX > second.frame.minX)
        bar.update([first])
        #expect(bar.tabButtons.count == 1 && bar.subviews.count == 1)
    }

    @Test func closingATabFromTheBarClosesIt() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openThree(probe, root)
        defer { controller.lockEnded() }
        let bar = try #require(descendants(root).compactMap { $0 as? SafariTabBar }.first)
        #expect(bar.tabButtons.count == 3 && bar.tabButtons[0].closeButton.isHidden)
        bar.onClose(2)
        #expect(await waitUntil { tabs(root).count == 2 })
        #expect(bar.tabButtons.count == 2)
        bar.onClose(0)
        #expect(tabs(root).count == 2, "the exam tab is never closed")
        #expect(controller.rememberedReferenceTabs == 1)
    }
}
