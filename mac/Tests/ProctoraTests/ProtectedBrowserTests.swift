import Testing
import Foundation
import WebKit
import AppKit
@testable import Proctora

@Suite struct ProtectedBrowserPolicyTests {
    private let server = URL(string: "https://fairplay.example.edu")!

    @Test func exactOriginsAndSSO() throws {
        let policy = try #require(ProtectedBrowserPolicy(start: "https://university.example.edu/course?section=1",
            additionalOrigins: "https://login.example.edu, https://cdn.example.edu:443/", forbiddenOrigin: server))
        for raw in ["https://university.example.edu/quiz", "https://login.example.edu/sso", "https://cdn.example.edu:443/file.js"] {
            #expect(policy.allows(URL(string: raw), mainFrame: true))
        }
        for raw in ["https://university.example.edu.evil.test/", "https://x.university.example.edu/",
                    "http://university.example.edu/", "https://user:pw@university.example.edu/",
                    "https://university.example.edu:8443/", "https://university.example.edu./",
                    "https://fairplay.example.edu/student", "javascript:alert(1)", "data:text/html,hi", "file:///tmp/test"] {
            #expect(!policy.allows(URL(string: raw), mainFrame: true), "\(raw)")
            #expect(!policy.allows(URL(string: raw), mainFrame: false), "\(raw)")
        }
        #expect(!policy.allows(nil, mainFrame: true))
        #expect(!policy.allows(URL(string: "about:blank"), mainFrame: true))
        #expect(policy.allows(URL(string: "about:srcdoc"), mainFrame: false))
    }

    @Test func malformedLocalConfigurationIsRejected() {
        for start in ["http://university.example.edu", "https://127.0.0.1", "https://localhost",
                      "https://fairplay.example.edu/", "https://university.example.edu:8443", "https://*.example.edu"] {
            #expect(ProtectedBrowserPolicy(start: start, additionalOrigins: "", forbiddenOrigin: server) == nil)
        }
        for origin in ["https://fairplay.example.edu", "https://login.example.edu/path", "https://*.example.edu",
                       "https://login.example.edu?x=1", "https://login.example.edu#x", "https://a..example.edu",
                       Array(repeating: "https://login.example.edu", count: 33).joined(separator: ",")] {
            #expect(ProtectedBrowserPolicy(start: "https://university.example.edu", additionalOrigins: origin,
                                           forbiddenOrigin: server) == nil)
        }
    }
}

@Suite struct ProtectedBrowserFlowTests {
    @Test func lockAlwaysPrecedesOpeningAndClearingPrecedesUnlock() throws {
        let flow = ProtectedBrowserFlow()
        let token = try #require(flow.prepare())
        var effects: [String] = []
        #expect(flow.activate(token, acquireLock: { effects.append("lock"); return true }, open: { effects.append("open") }))
        #expect(effects == ["lock", "open"])
        #expect(flow.state == .browsing)
        #expect(flow.prepare() == nil)
        flow.close(clear: { effects.append("clear") }, releaseLock: { effects.append("unlock") })
        #expect(effects == ["lock", "open", "clear", "unlock"])
        flow.close(clear: { effects.append("unexpected") }, releaseLock: { effects.append("unexpected") })
        #expect(effects.count == 4)
    }

    @Test func failedLockNeverOpensContentAndCanRetry() throws {
        let flow = ProtectedBrowserFlow()
        let first = try #require(flow.prepare())
        var opened = false
        #expect(!flow.activate(first, acquireLock: { false }, open: { opened = true }))
        #expect(!opened)
        #expect(!flow.ownsLock)
        #expect(flow.state == .setup)
        #expect(flow.prepare() != nil)
    }

    @Test func cancelledAndStalePreparationNeverLocks() throws {
        let flow = ProtectedBrowserFlow()
        let first = try #require(flow.prepare())
        flow.preparationFailed(first)
        let second = try #require(flow.prepare())
        var locked = false
        #expect(!flow.activate(first, acquireLock: { locked = true; return true }, open: {}))
        flow.close(clear: {}, releaseLock: { locked = true })
        #expect(!flow.activate(second, acquireLock: { locked = true; return true }, open: {}))
        #expect(!locked)
    }
}

@MainActor @Suite struct ProtectedBrowserWebKitTests {
    @Test func firstScreenDoesNotCreateAWebViewOrLock() {
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { false }, didClose: {})
        let wasLocked = LockdownController.shared.isLocked
        controller.show(in: container)
        #expect(controller.isOpen)
        func containsBrowser(_ view: NSView) -> Bool {
            view is WKWebView || view.subviews.contains(where: containsBrowser)
        }
        #expect(!containsBrowser(container))
        #expect(LockdownController.shared.isLocked == wasLocked)
        controller.lockEnded()
        #expect(!controller.isOpen)
        #expect(container.subviews.isEmpty)
    }

    @Test func busyExamCannotOpenTheNewScreen() {
        let container = NSView(frame: .zero)
        let controller = ProtectedBrowserController(busy: { true }, didClose: {})
        controller.show(in: container)
        #expect(!controller.isOpen)
        #expect(container.subviews.isEmpty)
    }

    @Test func actualWebKitCompilesResourceRestrictions() async throws {
        let policy = try #require(ProtectedBrowserPolicy(start: "https://university.example.edu/",
            additionalOrigins: "https://login.example.edu", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!))
        let id = "fairplay.protected-browser.test." + UUID().uuidString
        let result: WKContentRuleList = try await withCheckedThrowingContinuation { continuation in
            WKContentRuleListStore.default().compileContentRuleList(forIdentifier: id, encodedContentRuleList: policy.contentRules) { rules, error in
                if let rules { continuation.resume(returning: rules) }
                else { continuation.resume(throwing: error ?? NSError(domain: "ProtectedBrowserTest", code: 1)) }
            }
        }
        #expect(result.identifier == id)
        let configuration = ProtectedBrowserController.websiteConfiguration(rules: result)
        let webView = WKWebView(frame: .zero, configuration: configuration)
        #expect(!webView.configuration.websiteDataStore.isPersistent)
        #expect(webView.configuration.userContentController.userScripts.isEmpty)
        #expect(!webView.configuration.preferences.javaScriptCanOpenWindowsAutomatically)
        #expect(webView.url == nil)
        #expect(!webView.isLoading)
        await withCheckedContinuation { continuation in
            WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in continuation.resume() }
        }
    }
}

@Suite struct ProtectedBrowserNavigationTests {
    @Test func committedOriginAndFailedPageOfferReload() {
        var state = ProtectedBrowserNavigation(startURL: URL(string: "https://university.example.edu/exam")!)
        #expect(!state.canReload)
        state.committed(URL(string: "https://login.example.edu/sso?secret=hidden")!)
        state.finished()
        #expect(state.message == "Locked browser · https://login.example.edu")
        #expect(state.canReload)
        state.started()
        #expect(!state.canReload)
        state.failed()
        #expect(state.canReload)
        #expect(state.message.contains("Reload or leave and unlock"))
        #expect(!state.message.contains("secret"))
    }
    @Test func discardedRendererCannotBeRevivedByLateCallbacks() {
        var state = ProtectedBrowserNavigation(startURL: URL(string: "https://university.example.edu")!)
        state.discarded()
        state.requested(URL(string: "https://login.example.edu")!)
        state.started()
        state.committed(URL(string: "https://login.example.edu")!)
        state.finished()
        state.failed()
        #expect(state.phase == .unavailable)
        #expect(!state.canReload)
        #expect(state.message.contains("Leave and unlock"))
        #expect(state.currentURL.host == "university.example.edu")
        #expect(state.requestedURL.host == "university.example.edu")
    }
}

@MainActor @Suite struct ProtectedBrowserCoordinatorTests {
    private func descendants(_ root: NSView) -> [NSView] {
        [root] + root.subviews.flatMap { descendants($0) }
    }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<200 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }


    @Test func firstLoadFailureRetriesAllowedRequestWithoutCommittedDocument() async throws {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var locked = false
        var requests: [URL] = []
        let effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true },
            ownsLock: { locked }, release: { locked = false }, load: { _, url in requests.append(url) })
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        defer { controller.lockEnded() }
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        try #require(fields.count == 2)
        fields[0].stringValue = "https://university.example.edu/initial"
        let start = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" })
        start.performClick(nil)
        #expect(await waitUntil { requests.count == 1 })
        let view = try #require(descendants(root).compactMap { $0 as? WKWebView }.first)
        #expect(view.url == nil)
        #expect(view.reload() == nil) // Actual WebKit has no committed request to reload.
        // Inject only the failure callback; no HTTP request or native lock is performed.
        let failure = NSError(domain: NSURLErrorDomain, code: NSURLErrorCannotConnectToHost,
            userInfo: [NSURLErrorFailingURLErrorKey: URL(string: "https://unlisted.example.test/never-use")!])
        controller.webView(view, didFailProvisionalNavigation: nil, withError: failure)
        let reload = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Reload" })
        #expect(reload.isEnabled)
        reload.performClick(nil)
        #expect(requests.count == 2)
        #expect(requests.allSatisfy { $0.absoluteString == "https://university.example.edu/initial" })
        #expect(!reload.isEnabled)
        controller.webViewWebContentProcessDidTerminate(view)
        controller.webView(view, didFailProvisionalNavigation: nil, withError: failure)
        #expect(!reload.isEnabled)
        #expect(requests.count == 2)
        let leave = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Leave and unlock" })
        leave.performClick(nil)
        #expect(!locked)
    }

    @Test func actualCoordinatorDestroysPagesBeforeReleasingSimulatedLock() async throws {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let window = NSWindow(contentRect: root.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = root
        defer { window.orderOut(nil) }
        var locked = false
        var released = false
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true },
            ownsLock: { locked }, release: {
                #expect(!self.descendants(root).contains { $0 is WKWebView })
                locked = false; released = true
            }, load: { view, url in
                #expect(locked)
                view.loadHTMLString("<html><title>Isolated fixture</title><body>Local test only</body></html>", baseURL: url)
            })
        // Script evaluation has no physical user gesture. Enable automatic popups only
        // before constructing this injected inline-HTML fixture, never in product configuration.
        effects.configuration = { rules in
            let config = ProtectedBrowserController.websiteConfiguration(rules: rules)
            config.preferences.javaScriptCanOpenWindowsAutomatically = true
            return config
        }
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        try #require(fields.count == 2)
        fields[0].stringValue = "https://university.example.edu/exam"
        let start = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" })
        start.performClick(nil)
        #expect(await waitUntil { self.descendants(root).contains { $0 is WKWebView } })
        let view = try #require(descendants(root).compactMap { $0 as? WKWebView }.first)
        #expect(!view.configuration.websiteDataStore.isPersistent)
        #expect(view.configuration.userContentController.userScripts.isEmpty)
        #expect(await waitUntil { !view.isLoading && view.url?.host == "university.example.edu" })
        let denied = try await view.evaluateJavaScript("!!window.open('https://unlisted.example.test/', 'denied')")
        #expect(denied as? Bool == false)
        #expect(descendants(root).filter { $0 is WKWebView }.count == 1)
        let opened = try await view.evaluateJavaScript("window.testPopup = window.open('about:blank', 'signin'); !!window.testPopup")
        #expect(opened as? Bool == true)
        #expect(await waitUntil { self.descendants(root).filter { $0 is WKWebView }.count == 2 })
        let child = try #require(descendants(root).compactMap { $0 as? WKWebView }.first { $0 !== view })
        #expect(child.configuration.websiteDataStore === view.configuration.websiteDataStore)
        #expect(!child.configuration.websiteDataStore.isPersistent)
        #expect(child.configuration.userContentController.userScripts.isEmpty)
        #expect(child.configuration.userContentController === view.configuration.userContentController)
        #expect(view.isHidden)
        #expect(await waitUntil { child.url?.absoluteString == "about:blank" })
        let opener = try await child.evaluateJavaScript("window.opener !== null")
        #expect(opener as? Bool == true)
        let bridge = try await child.evaluateJavaScript("typeof window.webkit?.messageHandlers?.proctora")
        #expect(bridge as? String == "undefined")
        _ = try await child.evaluateJavaScript("window.close(); true")
        #expect(await waitUntil { self.descendants(root).filter { $0 is WKWebView }.count == 1 })
        #expect(!view.isHidden)
        #expect(locked)
        // Detached child callbacks must not change the current parent's presentation.
        let statusBefore = descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue)
        controller.webView(child, didFinish: nil)
        #expect(descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue) == statusBefore)
        _ = try await view.evaluateJavaScript("window.open('about:blank', 'second'); true")
        #expect(await waitUntil { self.descendants(root).filter { $0 is WKWebView }.count == 2 })
        let second = try #require(descendants(root).compactMap { $0 as? WKWebView }.first { $0 !== view })
        controller.webViewWebContentProcessDidTerminate(second)
        #expect(locked)
        #expect(!descendants(root).contains { $0 is WKWebView })
        let buttons = descendants(root).compactMap { $0 as? NSButton }
        #expect(buttons.first { $0.title == "Reload" }?.isEnabled == false)
        let leave = try #require(buttons.first { $0.title == "Leave and unlock" })
        #expect(leave.isEnabled)
        leave.performClick(nil)
        #expect(released)
        #expect(!locked)
        #expect(root.subviews.isEmpty)
    }
}
