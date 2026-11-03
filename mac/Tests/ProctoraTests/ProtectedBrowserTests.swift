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
        await withCheckedContinuation { continuation in
            WKContentRuleListStore.default().removeContentRuleList(forIdentifier: id) { _ in continuation.resume() }
        }
    }
}
