import Testing
import Foundation
import AppKit
import WebKit
@testable import Proctora

@Suite @MainActor struct LocalDKUSmokeTests {
    @Test(.enabled(if: ProcessInfo.processInfo.environment["FAIRPLAY_LOCAL_SMOKE"] == "1"))
    func localLoginLoadsInsideRestrictedWebKit() async throws {
        let url = URL(string: "http://127.0.0.1:18081/accounts/login/")!
        let policy = try #require(ProtectedBrowserPolicy(start: url.absoluteString, additionalOrigins: "", forbiddenOrigin: URL(string: "https://exam.example.edu")!, allowHTTP: true))
        let rules = try await WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "local-dku-smoke", encodedContentRuleList: policy.contentRules)
        let config = ProtectedBrowserController.websiteConfiguration(rules: try #require(rules))
        let view = WKWebView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700), configuration: config)
        let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.contentView = view
        defer { window.orderOut(nil) }
        view.load(URLRequest(url: url))
        var found = false
        for _ in 0..<80 {
            try await Task.sleep(for: .milliseconds(100))
            if let ready = try? await view.evaluateJavaScript("!!document.querySelector('input[name=username]') && !!document.querySelector('input[type=password]')") as? Bool, ready {
                found = true; break
            }
        }
        #expect(found, "The actual local DKU login form must load under native resource restrictions")
        let styled = try await view.evaluateJavaScript("Array.from(document.querySelectorAll('link[rel=stylesheet]')).filter(x => x.href.includes('/static/')).every(x => !!x.sheet) && typeof window.jQuery === 'function'") as? Bool
        #expect(styled == true, "DKU stylesheets and jQuery must load, not only the login HTML")
        view.stopLoading()
    }
}
