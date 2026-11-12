import Testing
import Foundation
import Darwin
import AppKit
import WebKit
@testable import Proctora

@Suite struct OJFlowTests {
    private let server = URL(string: "https://fairplay.example.edu")!

    @Test func httpOJOriginIsExactAndNeverDowngraded() throws {
        let http = try #require(ProtectedBrowserPolicy(start: "http://10.200.31.64/", additionalOrigins: "", forbiddenOrigin: server, allowHTTP: true))
        #expect(http.allows(URL(string: "http://10.200.31.64/accounts/login/"), mainFrame: true))
        #expect(http.allows(URL(string: "https://10.200.31.64/"), mainFrame: true))
        for raw in ["http://10.200.31.64:8080/", "http://10.200.31.65/", "https://10.200.31.64:8443/", "http://10.200.31.64.evil.test/"] {
            #expect(!http.allows(URL(string: raw), mainFrame: true), "\(raw)")
        }
    }

    @Test func httpsOJOriginNeverFallsBackToHttp() throws {
        let https = try #require(ProtectedBrowserPolicy(start: "https://10.200.31.64:8443/", additionalOrigins: "", forbiddenOrigin: server, allowHTTP: false))
        #expect(https.allows(URL(string: "https://10.200.31.64:8443/contest/1"), mainFrame: true))
        for raw in ["http://10.200.31.64:8443/", "http://10.200.31.64/", "https://10.200.31.64/", "https://10.200.31.64:9443/"] {
            #expect(!https.allows(URL(string: raw), mainFrame: true), "\(raw)")
        }
        #expect(ProtectedBrowserPolicy(start: "http://10.200.31.64/", additionalOrigins: "", forbiddenOrigin: server, allowHTTP: false) == nil)
    }

    @Test func destinationNoteMatchesTheScheme() {
        let note = UniversityDestination.dkuOJ.note
        #expect(note.contains(UniversityDestination.dkuOJ.url.scheme == "http" ? "HTTP" : "secure"))
    }

    @Test func releaseAppTransportSecurityHasNoBlanketExceptions() throws {
        let script = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("build.sh")
        let text = try String(contentsOf: script, encoding: .utf8)
        let atsLines = text.split(separator: "\n").filter { $0.trimmingCharacters(in: .whitespaces).hasPrefix("ATS=") }
        #expect(atsLines.count == 3)
        #expect(!atsLines.contains { $0.contains("NSExceptionDomains") || $0.contains("NSExceptionAllowsInsecureHTTPLoads") || $0.contains("10.200.31.64") })
        let profile = atsLines[1]
        #expect(profile.trimmingCharacters(in: .whitespaces) == "ATS=''")
        let classic = try #require(atsLines.last)
        #expect(classic.trimmingCharacters(in: .whitespaces) == "ATS=''")
        let exempt = atsLines.filter { $0.contains("NSAllowsArbitraryLoadsInWebContent") || $0.contains("NSAllowsLocalNetworking") }
        #expect(exempt.count == 1 && exempt[0].contains("<dict><key>NSAllowsLocalNetworking</key>"))
    }

    @Test func startIsRefusedWhenNoReceiptKeyCanUnlockTheMac() {
        typealias C = ProtectedBrowserController
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: false, isDevBuild: false) != nil)
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: true, isDevBuild: false) == nil)
        #expect(C.startBlocker(customSetup: true, policyKeyPresent: false, isDevBuild: false) == nil)
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: false, isDevBuild: true) == nil)
    }

    @Test func releaseBuildLocksOnlyOverHTTPSWithTheBundledProfile() {
        typealias C = ProtectedBrowserController
        let msg = "This exam needs a secure connection (https). Contact your instructor. Your Mac was not locked."
        #expect(C.secureConnectionMessage == msg)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: false, scheme: "http", profileBundled: true) == msg)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: false, scheme: "https", profileBundled: false) == msg)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: false, scheme: nil, profileBundled: true) == msg)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: false, scheme: "https", profileBundled: true) == nil)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: false, scheme: "HTTPS", profileBundled: true) == nil)
        #expect(C.secureBlocker(customSetup: false, isDevBuild: true, scheme: "http", profileBundled: false) == nil)
        #expect(C.secureBlocker(customSetup: true, isDevBuild: false, scheme: "http", profileBundled: false) == nil)
    }

    @Test func sharingPortsCoverSSHScreenSharingAndRemoteManagement() {
        #expect(SystemChecks.sharingPorts.map { $0.0 }.sorted() == [22, 3283, 5900])
        var open: Set<UInt16> = []
        #expect(SystemChecks.listeningSharingServices(isOpen: { open.contains($0) }).isEmpty)
        open = [22]
        #expect(SystemChecks.listeningSharingServices(isOpen: { open.contains($0) }) == ["Remote Login (SSH)"])
        open = [22, 5900, 3283]
        #expect(SystemChecks.listeningSharingServices(isOpen: { open.contains($0) }).count == 3)
    }

    @Test func localPortProbeSeesARealListenerAndAClosedPort() throws {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        try #require(fd >= 0)
        defer { close(fd) }
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_addr.s_addr = inet_addr("127.0.0.1")
        let bound = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        try #require(bound == 0 && listen(fd, 1) == 0)
        var len = socklen_t(MemoryLayout<sockaddr_in>.size)
        _ = withUnsafeMutablePointer(to: &addr) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(fd, $0, &len) } }
        let port = UInt16(bigEndian: addr.sin_port)
        #expect(SystemChecks.localPortOpen(port))
        let closedFD = socket(AF_INET, SOCK_STREAM, 0)
        var other = addr
        other.sin_port = 0
        _ = withUnsafePointer(to: &other) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(closedFD, $0, len) } }
        var l2 = len
        _ = withUnsafeMutablePointer(to: &other) { $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(closedFD, $0, &l2) } }
        let freePort = UInt16(bigEndian: other.sin_port)
        close(closedFD)
        #expect(!SystemChecks.localPortOpen(freePort))
    }
}

@Suite struct OJReachabilityTests {
    @Test func anyResponseBelow500IsReachable() {
        for status in [200, 302, 399, 403, 404, 429, 499] { #expect(ProtectedBrowserController.ojReachable(status: status, failed: false), "\(status)") }
    }

    @Test func serverErrorsAndConnectionFailuresAreUnavailable() {
        for status in [500, 502, 503, 599] { #expect(!ProtectedBrowserController.ojReachable(status: status, failed: false), "\(status)") }
        #expect(!ProtectedBrowserController.ojReachable(status: nil, failed: false))
        #expect(!ProtectedBrowserController.ojReachable(status: 200, failed: true))
    }
}

@MainActor @Suite struct OJLeaveAndPauseTests {
    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }
    private func context(_ state: String) throws -> BrowserExamContext {
        let json = #"{"v":1,"nonce":"n","expires":1,"user":"u","contest":"c","participation":"p","state":"\#(state)","sites":[]}"#
        return try JSONDecoder().decode(BrowserExamContext.self, from: Data(json.utf8))
    }
    private final class Probe {
        var locked = false, released = 0
        var prompts = 0
        let store = InMemoryNativeLockStore()
        var code: String? { store.load().map { NativeExit.code(key: TestExit.key, lockId: $0.lockId) } }
    }
    private func openController(_ probe: Probe, root: NSView) async throws -> ProtectedBrowserController {
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false; probe.released += 1 }, load: { _, _ in })
        effects.presentLeave = { _, _ in probe.prompts += 1 }
        var setup = NativeLockSetup.dev
        setup.store = probe.store
        setup.newKey = { TestExit.key }
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        fields[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        return controller
    }

    @Test func bindingSurvivesASecurityPauseAndAPageCompletedContextStillDoesNotUnlock() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openController(probe, root: root)
        controller.applyExamContext(try context("active"), url: nil)
        #expect(controller.examBinding == "u:c:p")
        controller.setHeld(true)
        #expect(!descendants(root).contains { $0 is WKWebView })
        #expect(controller.examBinding == "u:c:p")
        controller.applyExamContext(try context("completed"), url: nil)
        #expect(probe.released == 0)
        #expect(probe.locked)
        #expect(controller.isOpen)
        controller.lockEnded()
    }

    @Test func aNativeLockWithNoSavedClaimIgnoresThePageCompletedContext() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openController(probe, root: root)
        #expect(probe.store.load()?.contest == nil && probe.store.load()?.binding == nil)
        controller.applyExamContext(try context("completed"), url: nil)
        controller.applyExamContext(try context("active"), url: nil)
        controller.applyExamContext(try context("completed"), url: nil)
        #expect(probe.released == 0 && probe.locked && controller.isOpen)
        controller.lockEnded()
    }

    @Test func leaveButtonOnlyShowsTheNoticeAndNeverUnlocks() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openController(probe, root: root)
        let leave = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.identifier?.rawValue == "browser.leave" })
        leave.performClick(nil)
        #expect(await waitUntil { probe.prompts == 1 })
        #expect(probe.locked && probe.released == 0 && controller.isOpen)
        #expect(probe.locked && probe.released == 0 && controller.isOpen && probe.store.load() != nil)
        controller.lockEnded()
    }

    @Test func aValidCodeTearsTheWebViewDownThenReleasesAndDeletesTheState() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var webViewGoneAtRelease = false
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: {
                webViewGoneAtRelease = !self.descendants(root).contains { $0 is WKWebView }
                probe.locked = false; probe.released += 1
            }, load: { _, _ in })
        var setup = NativeLockSetup.dev
        setup.store = probe.store
        setup.newKey = { TestExit.key }
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        let wrong = controller.submitLeaveCode(probe.code == "000000" ? "000001" : "000000")
        #expect(wrong != .accepted && probe.locked && probe.store.load() != nil)
        let accepted = controller.submitLeaveCode(try #require(probe.code))
        #expect(accepted == .accepted)
        #expect(webViewGoneAtRelease)
        #expect(!probe.locked && probe.released == 1 && !controller.isOpen)
        #expect(probe.store.load() == nil)
    }

    @Test func leaveDuringAClaimedExamOnlyShowsTheNotice() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await openController(probe, root: root)
        controller.applyExamContext(try context("active"), url: nil)
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.identifier?.rawValue == "browser.leave" }).performClick(nil)
        #expect(await waitUntil { probe.prompts == 1 })
        #expect(probe.locked && probe.released == 0 && controller.isOpen)
        controller.lockEnded()
    }
}
