import Testing
import Foundation
import WebKit
import AppKit
import Network
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

    @Test func exactLocalAddressesPortsAndHttpConsent() throws {
        let secure = try #require(ProtectedBrowserPolicy(start: "intranet:8443/exam", additionalOrigins: "https://localhost, https://127.0.0.1:9443, https://[::1]:9443", forbiddenOrigin: server))
        #expect(secure.startURL.absoluteString == "https://intranet:8443/exam")
        for raw in ["https://intranet:8443/x", "https://localhost:443/", "https://127.0.0.1:9443/", "https://[::1]:9443/"] { #expect(secure.allows(URL(string: raw), mainFrame: true)) }
        for raw in ["http://intranet:8443/", "https://intranet/", "https://intranet:8444/", "https://[::1]:9444/", "https://intranet.evil:8443/"] { #expect(!secure.allows(URL(string: raw), mainFrame: true)) }
        #expect(ProtectedBrowserPolicy(start: "http://localhost:8765", additionalOrigins: "", forbiddenOrigin: server) == nil)
        #expect(ProtectedBrowserPolicy(start: "http://public.example.edu", additionalOrigins: "", forbiddenOrigin: server, allowHTTP: true) != nil)
        let local = try #require(ProtectedBrowserPolicy(start: "http://localhost:8765/page", additionalOrigins: "http://intranet, http://[::1]:9000", forbiddenOrigin: server, allowHTTP: true))
        #expect(local.allows(URL(string: "http://intranet:80/"), mainFrame: true))
        for raw in ["http://localhost:8766/", "http://user:pass@localhost:8765/", "http://intranet:81/"] { #expect(!local.allows(URL(string: raw), mainFrame: true)) }
        #expect(ProtectedBrowserPolicy.normalizedURL("example.edu")?.scheme == "https")
    }

    @Test func malformedLocalConfigurationIsRejected() {
        for start in ["http://university.example.edu", "https://fairplay.example.edu/", "https://*.example.edu", "https://bad..host", "https://127.1", "https://host:0", "https://host:65536"] {
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
            additionalOrigins: "https://login.example.edu, https://[::1]:9443, https://intranet:8080", forbiddenOrigin: URL(string: "https://fairplay.example.edu")!))
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
        #expect(state.message == "https://login.example.edu")
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


    @Test(.enabled(if: ProcessInfo.processInfo.environment["FAIRPLAY_CANVAS_NETWORK_TEST"] == "1"))
    func publicCanvasNavigationWithSimulatedLock() async throws {
        for (label, start, extra) in [
            ("bare", "canvas.duke.edu", ""),
            ("oj", "http://10.200.31.64/", ""),
            ("portal", "https://canvas.duke.edu/", "https://go.canvas.duke.edu, https://shib.oit.duke.edu"),
            ("saml", "https://canvas.duke.edu/login/saml", "https://go.canvas.duke.edu, https://shib.oit.duke.edu")
        ] {
            let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
            var locked = false
            var events: [String] = []
            var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true }, ownsLock: { locked }, release: { locked = false }, load: { view, url in view.load(URLRequest(url: url)) })
            effects.diagnostic = { event in events.append(event) }
            let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
            controller.show(in: root)
            let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
            fields[0].stringValue = start
            fields[1].stringValue = extra
            if start.hasPrefix("http://") { descendants(root).compactMap { $0 as? NSButton }.first { $0.title.hasPrefix("Allow HTTP") }?.state = .on }
            try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
            for _ in 0..<1000 {
                let view = descendants(root).compactMap { $0 as? WKWebView }.first
                if events.contains(where: { $0.contains("navigation-failed") }) || (view?.isLoading == false && view?.title?.isEmpty == false) { break }
                try await Task.sleep(for: .milliseconds(20))
            }
            for event in events { print("Canvas " + label + " " + event) }
            let view = descendants(root).compactMap { $0 as? WKWebView }.first
            print("Canvas " + label + " final-origin=" + (view?.url.flatMap(ProtectedBrowserPolicy.origin) ?? "unavailable") + " has-title=" + String(view?.title?.isEmpty == false))
            #expect(!events.isEmpty)
            #expect(view?.title?.isEmpty == false)
            #expect(!events.contains { $0.contains("policy-denied") })
            controller.lockEnded()
        }
    }

    @Test(.enabled(if: Config.isDevBuild)) func actualLoopbackHTTPAndBlockedTargetStayExact() async throws {
        let parameters = NWParameters.tcp
        parameters.requiredLocalEndpoint = .hostPort(host: "127.0.0.1", port: .any)
        let listener = try NWListener(using: parameters, on: .any)
        let queue = DispatchQueue(label: "protected-browser.loopback-test")
        listener.newConnectionHandler = { connection in
            connection.start(queue: queue)
            connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { _, _, _, _ in
                let body = "<html><title>Loopback HTTP fixture</title><body>Local fixture only</body></html>"
                let response = "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n" + body
                connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in connection.cancel() })
            }
        }
        listener.start(queue: queue)
        defer { listener.cancel() }
        #expect(await waitUntil { (listener.port?.rawValue ?? 0) > 0 })
        let port = try #require(listener.port)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var locked = false
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true }, ownsLock: { locked }, release: { locked = false }, load: { view, url in view.load(URLRequest(url: url)) })
        let store = InMemoryNativeLockStore()
        effects.setup = NativeLockSetup.dev.with(store: store)
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        defer { controller.lockEnded() }
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        fields[0].stringValue = "http://127.0.0.1:\(port.rawValue)/"
        let buttons = descendants(root).compactMap { $0 as? NSButton }
        let consent = try #require(buttons.first { $0.title.hasPrefix("Allow HTTP websites") })
        #expect(consent.state == .off)
        consent.state = .on
        try #require(buttons.first { $0.title == "Lock and open" }).performClick(nil)
        let loaded = await waitUntil { self.descendants(root).compactMap { $0 as? WKWebView }.first?.title == "Loopback HTTP fixture" }
        let status = descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue).joined(separator: " | ")
        #expect(loaded, "Local fixture status: \(status)")
        let view = try #require(descendants(root).compactMap { $0 as? WKWebView }.first)
        #expect(view.url?.scheme == "http")
        #expect(view.url?.port == Int(port.rawValue))
        #expect(locked)
        let blockedPort = port.rawValue == 65535 ? 65534 : port.rawValue + 1
        _ = try await view.evaluateJavaScript("location.href = 'http://127.0.0.1:\(blockedPort)/secret?token=hidden'; true")
        #expect(await waitUntil { self.descendants(root).compactMap { $0 as? NSTextField }.contains { $0.stringValue.contains("Blocked site: http://127.0.0.1:\(blockedPort)") } })
        let messages = descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue).joined()
        #expect(!messages.contains("secret"))
        #expect(!messages.contains("hidden"))
        controller.submitLeaveCode(store.exitCode)
        #expect(!locked)
    }

    @Test func firstLoadFailureRetriesAllowedRequestWithoutCommittedDocument() async throws {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var locked = false
        var requests: [URL] = []
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true },
            ownsLock: { locked }, release: { locked = false }, load: { _, url in requests.append(url) })
        let store = InMemoryNativeLockStore()
        effects.setup = NativeLockSetup.dev.with(store: store)
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
        let reload = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.identifier?.rawValue == "browser.reload" })
        #expect(reload.isEnabled)
        let messages = descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue).joined()
        #expect(messages.contains("Could not load this page. Try Reload."))
        #expect(!messages.contains("never-use"))
        controller.webView(view, didFailProvisionalNavigation: nil,
                           withError: NSError(domain: "WebKitErrorDomain", code: 102))
        #expect(descendants(root).compactMap { $0 as? NSTextField }.contains { $0.stringValue == "Could not load this page. Try Reload." })
        reload.performClick(nil)
        #expect(requests.count == 2)
        #expect(requests.allSatisfy { $0.absoluteString == "https://university.example.edu/initial" })
        #expect(!reload.isEnabled)
        controller.webViewWebContentProcessDidTerminate(view)
        controller.webView(view, didFailProvisionalNavigation: nil, withError: failure)
        #expect(reload.isEnabled)
        #expect(requests.count == 2)
        controller.submitLeaveCode(store.exitCode)
        #expect(!locked)
    }

    @Test func manualReloadCancelsBackoffReloadsCurrentPageAndKeepsLock() async throws {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var locked = false
        var requests: [URL] = []
        var reloads: [Bool] = []
        var cancelled = 0
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { locked = true; return true },
            ownsLock: { locked }, release: { locked = false }, load: { _, url in requests.append(url) })
        effects.reload = { _, hard in reloads.append(hard) }
        effects.schedule = { _, _ in { cancelled += 1 } }
        let store = InMemoryNativeLockStore()
        effects.setup = NativeLockSetup.dev.with(store: store)
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        defer { controller.lockEnded() }
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        fields[0].stringValue = "https://university.example.edu/initial"
        descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }?.performClick(nil)
        #expect(await waitUntil { requests.count == 1 })
        let view = try #require(descendants(root).compactMap { $0 as? WKWebView }.first)
        #expect(controller.reloadFromKeyboard(hard: false))
        let failure = NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut,
            userInfo: [NSURLErrorFailingURLErrorKey: URL(string: "https://university.example.edu/initial")!])
        controller.webView(view, didFailProvisionalNavigation: nil, withError: failure)
        let before = cancelled
        controller.reloadFromKeyboard(hard: true)
        #expect(cancelled == before + 1)
        #expect(requests.count >= 2)
        #expect(requests.allSatisfy { $0.absoluteString == "https://university.example.edu/initial" })
        #expect(locked)
        #expect(EventTap.reloadShortcutAllowed)
        controller.webView(view, didFailProvisionalNavigation: nil, withError: failure)
        #expect(cancelled == before + 1)
        controller.submitLeaveCode(store.exitCode)
        #expect(!locked)
        #expect(!EventTap.reloadShortcutAllowed)
        #expect(!controller.reloadFromKeyboard(hard: false))
    }

    @Test func actualCoordinatorDestroysPagesBeforeReleasingSimulatedLock() async throws {
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let window = NSWindow(contentRect: root.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = root
        defer { window.orderOut(nil) }
        var locked = false
        var released = false
        let store = InMemoryNativeLockStore()
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
        effects.setup = NativeLockSetup.dev.with(store: store)
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
        controller.webView(child, didFailProvisionalNavigation: nil, withError: NSError(domain: "WebKitErrorDomain", code: 102))
        #expect(descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue) == statusBefore)
        _ = try await view.evaluateJavaScript("window.open('about:blank', 'second'); true")
        #expect(await waitUntil { self.descendants(root).filter { $0 is WKWebView }.count == 2 })
        let second = try #require(descendants(root).compactMap { $0 as? WKWebView }.first { $0 !== view })
        controller.webViewWebContentProcessDidTerminate(second)
        #expect(locked)
        #expect(descendants(root).filter { $0 is WKWebView }.count == 2)
        #expect(!released)
        let buttons = descendants(root).compactMap { $0 as? NSButton }
        #expect(buttons.first { $0.identifier?.rawValue == "browser.reload" }?.isEnabled == true)
        #expect(!buttons.contains { ["Leave and unlock", "Leave exam", "Finish exam"].contains($0.title) })
        // Exercise coordinator teardown directly; students finish through DKU OJ,
        // so there is deliberately no native finish button in the header.
        controller.submitLeaveCode(store.exitCode)
        #expect(released)
        #expect(!locked)
        #expect(root.subviews.isEmpty)
    }
}

@Suite struct ProtectedBrowserPolicyDenialTests {
    @Test func onlySameViewAndCancelledNavigationMatch() {
        let view = NSObject(), otherView = NSObject(), navigation = NSObject(), next = NSObject()
        let target = URL(string: "https://blocked.example.edu/path?not-logged")!
        let denial = ProtectedBrowserPolicyDenial(view: view, navigation: navigation, target: target)
        #expect(denial.matches(view: view, navigation: navigation, failedURL: nil))
        #expect(!denial.matches(view: view, navigation: next, failedURL: target))
        #expect(!denial.matches(view: otherView, navigation: navigation, failedURL: target))
        let early = ProtectedBrowserPolicyDenial(view: view, navigation: nil, target: target)
        #expect(early.matches(view: view, navigation: nil, failedURL: target))
        #expect(!early.matches(view: view, navigation: nil, failedURL: nil))
        #expect(!early.matches(view: view, navigation: nil, failedURL: URL(string: "https://other.example.edu")))
    }
}

@Suite struct UniversityCompatibilityTests {
    @Test func dukeProfileAndTransportBoundaries() throws {
        let server = URL(string: "https://fairplay.example.edu")!
        let policy = try #require(ProtectedBrowserPolicy(start: " canvas.duke.edu ", additionalOrigins: "https://api-example.duosecurity.com", forbiddenOrigin: server))
        #expect(policy.startURL.absoluteString == "https://canvas.duke.edu")
        for host in ["canvas.duke.edu", "go.canvas.duke.edu", "shib.oit.duke.edu", "api-example.duosecurity.com"] {
            #expect(policy.allows(URL(string: "https://" + host + "/login"), mainFrame: true))
        }
        for raw in ["http://canvas.duke.edu", "https://go.canvas.duke.edu.evil.test", "https://other.duosecurity.com", "https://go.canvas.duke.edu:8443"] {
            #expect(!policy.allows(URL(string: raw), mainFrame: true))
        }
        let http = try #require(ProtectedBrowserPolicy(start: "http://university.example.edu/exam", additionalOrigins: "", forbiddenOrigin: server, allowHTTP: true))
        #expect(http.allows(URL(string: "https://university.example.edu/exam"), mainFrame: true))
        #expect(!http.allows(URL(string: "http://other.example.edu"), mainFrame: true))
        #expect(ProtectedBrowserPolicy(start: "canvas.duke.edu", additionalOrigins: "", forbiddenOrigin: URL(string: "https://shib.oit.duke.edu")!) == nil)
    }
}


enum TestExit {
    static let key = Data(repeating: 9, count: 32)
}

extension NativeLockSetup {
    func with(store: NativeLockStoring) -> NativeLockSetup {
        var copy = self
        copy.store = store
        copy.newKey = { TestExit.key }
        return copy
    }
}

extension InMemoryNativeLockStore {
    var exitCode: String { load().map { NativeExit.code(key: TestExit.key, lockId: $0.lockId) } ?? "" }
}
