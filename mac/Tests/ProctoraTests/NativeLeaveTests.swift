import Testing
import Foundation
import AppKit
import WebKit
import CryptoKit
@testable import Proctora

@Suite struct NativeLeaveTextTests {
    @Test func wrongCodeShowsTheRemainingAttempts() {
        let now = Date()
        #expect(NativeLeaveText.message(for: .wrong(remaining: 4), now: now) == "That code is not right. 4 attempts left.")
        #expect(NativeLeaveText.message(for: .wrong(remaining: 1), now: now) == "That code is not right. 1 attempt left.")
    }

    @Test func lockoutShowsTheWaitTime() {
        let now = Date()
        #expect(NativeLeaveText.message(for: .wait(until: now.addingTimeInterval(300)), now: now)?.contains("5 min") == true)
        #expect(NativeLeaveText.message(for: .wait(until: now.addingTimeInterval(45)), now: now)?.contains("45 s") == true)
    }

    @Test func acceptedHasNoMessageAndMalformedAsksForSixDigits() {
        #expect(NativeLeaveText.message(for: .accepted, now: Date()) == nil)
        #expect(NativeLeaveText.message(for: .malformed, now: Date()) == "Enter the 6-digit code.")
        #expect(NativeLeaveText.hint == "Ask your instructor for an exit code")
    }
}

@Suite struct NativeHoldContextTests {
    private func envelope(hold: Bool?, state: String) throws -> ([String: Any], Data, String) {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "a", count: 64)
        var payload: [String: Any] = ["v": 2, "nonce": "one", "expires": 1030, "session": UUID().uuidString,
            "device": device, "user": "7", "contest": "exam", "participation": "12", "state": state, "lease_until": 1000]
        if let hold { payload["hold"] = hold }
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        return (["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()],
                key.publicKey.rawRepresentation, device)
    }
    private let now = Date(timeIntervalSince1970: 1000)

    @Test func holdIsReadFromTheSignedStateAndAbsentMeansFalse() throws {
        let (held, key, device) = try envelope(hold: true, state: "active")
        let heldContext = try #require(NativeDeviceContext.verify(held, publicKey: key, nonce: "one", device: device, now: now))
        #expect(heldContext.hold == true)
        let (plain, key2, device2) = try envelope(hold: nil, state: "active")
        let plainContext = try #require(NativeDeviceContext.verify(plain, publicKey: key2, nonce: "one", device: device2, now: now))
        #expect(plainContext.hold != true)
    }

    @Test func holdKeepsTheLockButASignedReleaseUnlocks() throws {
        let (held, key, device) = try envelope(hold: true, state: "active")
        let context = try #require(NativeDeviceContext.verify(held, publicKey: key, nonce: "one", device: device, now: now))
        var session = NativeExamSession()
        _ = session.beginClaim("exam")
        let accepted = session.accept(context)
        #expect(accepted)
        #expect(session.onHold && !session.permitsUnlock)
        var released = context
        released = NativeDeviceContext(v: 2, nonce: context.nonce, expires: context.expires, session: context.session,
                                       device: context.device, user: context.user, contest: context.contest,
                                       participation: context.participation, state: "released", lease_until: context.lease_until, hold: true)
        let again = session.accept(released)
        #expect(again)
        #expect(session.permitsUnlock && !session.onHold)
    }
}

@MainActor @Suite struct NativeLeaveControllerTests {
    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }
    private final class Probe {
        var locked = false, released = 0
        var now = Date(timeIntervalSince1970: 1_800_000_000)
        let store = InMemoryNativeLockStore()
        var code: String { store.exitCode }
    }
    private func open(_ probe: Probe, root: NSView) async throws -> ProtectedBrowserController {
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false; probe.released += 1 }, load: { _, _ in })
        effects.setup = NativeLockSetup.dev.with(store: probe.store)
        effects.now = { probe.now }
        effects.monotonic = { .init(boot: "test-boot", seconds: probe.now.timeIntervalSince1970 - 1_800_000_000 + 5_000) }
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        return controller
    }

    @Test func leaveSitsAtTheTopRightForEveryStudentWhileLocked() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let leave = try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.identifier?.rawValue == "browser.leave" })
        #expect(leave.title == "Leave" && !leave.isHidden)
        #expect(leave.superview?.superview === root)
        #expect(leave.frame.maxX > root.bounds.width - 40)
        #expect(leave.frame.maxY > root.bounds.height - 60)
        #expect(leave.autoresizingMask.contains(.minXMargin) && leave.autoresizingMask.contains(.minYMargin))
    }

    @Test func fiveWrongCodesLockOutEvenTheRightOneAndThePersistedStateKeepsIt() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let right = probe.code
        let wrong = right == "111111" ? "222222" : "111111"
        for left in [4, 3, 2, 1] { #expect(controller.submitLeaveCode(wrong) == .wrong(remaining: left)) }
        guard case .wait = controller.submitLeaveCode(wrong) else { Issue.record("expected a lockout"); return }
        #expect(probe.store.load()?.until != nil)
        guard case .wait = controller.submitLeaveCode(right) else { Issue.record("right code during lockout must wait"); return }
        #expect(probe.locked && probe.released == 0 && probe.store.load() != nil)
        // The lockout lives in the store: a fresh attempt object sees the same wait.
        guard case .wait = NativeExit.attempt(typed: right, store: probe.store, now: probe.now.addingTimeInterval(10),
                                              monotonic: { .init(boot: "test-boot", seconds: 5_010) }) else {
            Issue.record("lockout lost"); return
        }
        probe.now = probe.now.addingTimeInterval(NativeExit.waitSeconds + 1)
        #expect(controller.submitLeaveCode(right) == .accepted)
        #expect(!probe.locked && probe.released == 1 && probe.store.load() == nil)
    }

    @Test func backgroundCheckKeepsEveryRuleAndAnswersOnTheMainThread() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        let right = probe.code
        let wrong = right == "111111" ? "222222" : "111111"
        func check(_ typed: String) async -> (NativeExitAttempt, Bool) {
            await withCheckedContinuation { continuation in
                controller.submitLeaveCodeInBackground(typed) { continuation.resume(returning: ($0, Thread.isMainThread)) }
            }
        }
        let first = await check(wrong)
        #expect(first.0 == .wrong(remaining: 4) && first.1)
        #expect(probe.store.load()?.attempts == 1)
        let malformed = await check("12ab")
        #expect(malformed.0 == .malformed)
        #expect(probe.locked && probe.released == 0)
        let accepted = await check(right)
        #expect(accepted.0 == .accepted && accepted.1)
        #expect(!probe.locked && probe.released == 1 && probe.store.load() == nil)
    }

    @Test func malformedAndNonDigitCodesNeverUnlock() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        for typed in ["", "12345", "1234567", "12345a", "١٢٣٤٥٦"] {
            #expect(controller.submitLeaveCode(typed) == .malformed, "\(typed)")
        }
        #expect(probe.locked && probe.released == 0)
    }

    @Test func holdScreenReplacesThePageAndKeepsTheLock() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        controller.applyHold(true)
        #expect(controller.isOnHold)
        #expect(!descendants(root).contains { $0 is WKWebView })
        let hold = try #require(descendants(root).compactMap { $0 as? NSTextField }.first { $0.identifier?.rawValue == "browser.hold" })
        #expect(hold.stringValue == "Session on hold. Your instructor can unlock and reopen your exam.")
        #expect(probe.locked && probe.released == 0)
        #expect(descendants(root).contains { ($0 as? NSButton)?.identifier?.rawValue == "browser.leave" })
        #expect(controller.submitLeaveCode("12ab") == .malformed)
        controller.applyHold(false)
        #expect(!controller.isOnHold)
        #expect(!descendants(root).contains { ($0 as? NSTextField)?.identifier?.rawValue == "browser.hold" })
    }

    @Test func recoverRequestCarriesUncleanOnlyWhenAskedFor() {
        let endpoint = URL(string: "https://oj.example.edu/api/fairplay/native/recover/exam")!
        let origin = URL(string: "https://oj.example.edu/")!
        let plain = ProtectedBrowserController.nativeRequest(endpoint: endpoint, origin: origin, nonce: "n", unclean: false, cookies: [])
        #expect(String(decoding: plain.httpBody ?? Data(), as: UTF8.self) == "nonce=n&confirm=1")
        let unclean = ProtectedBrowserController.nativeRequest(endpoint: endpoint, origin: origin, nonce: "n", unclean: true, cookies: [])
        #expect(String(decoding: unclean.httpBody ?? Data(), as: UTF8.self) == "nonce=n&confirm=1&unclean=1")
    }

    @Test func claimAndRecoverRequestsCarryTheMacCodeInTheServerFormat() {
        let endpoint = URL(string: "https://oj.example.edu/api/fairplay/native/claim/exam")!
        let origin = URL(string: "https://oj.example.edu/")!
        let id = NativeExit.newLockId()
        let body = { (r: URLRequest) in String(decoding: r.httpBody ?? Data(), as: UTF8.self) }
        let claim = ProtectedBrowserController.nativeRequest(endpoint: endpoint, origin: origin, nonce: "n", unclean: false, lockId: id, cookies: [])
        #expect(body(claim) == "nonce=n&confirm=1&lock=\(id)")
        #expect(id.count == 8 && id.allSatisfy { NativeExit.alphabet.contains($0) })
        let recover = ProtectedBrowserController.nativeRequest(endpoint: endpoint, origin: origin, nonce: "n", unclean: true, lockId: id, cookies: [])
        #expect(body(recover) == "nonce=n&confirm=1&unclean=1&lock=\(id)")
        let bad = ProtectedBrowserController.nativeRequest(endpoint: endpoint, origin: origin, nonce: "n", unclean: false, lockId: "bad&x=1", cookies: [])
        #expect(body(bad) == "nonce=n&confirm=1")
    }

    @Test func aBoundLockConflictIsRecognisedOnlyFor409WithTheMacCodeMessage() {
        let conflict = Data(#"{"error": "This Mac code belongs to another exam or student."}"#.utf8)
        #expect(ProtectedBrowserController.isLockConflict(status: 409, data: conflict))
        #expect(!ProtectedBrowserController.isLockConflict(status: 200, data: conflict))
        #expect(!ProtectedBrowserController.isLockConflict(status: 409, data: Data(#"{"error": "Another device is active."}"#.utf8)))
        #expect(!ProtectedBrowserController.isLockConflict(status: 409, data: nil))
    }

    @Test func anUnsignedOJRefusalIsShownOnlyWhenWellFormed() {
        func parse(_ json: String, status: Int = 403) -> ProtectedBrowserController.OJRefusal? {
            ProtectedBrowserController.ojRefusal(status: status, data: Data(json.utf8))
        }
        #expect(parse(#"{"error":"You are the instructor.","reason":"instructor"}"#)?.text == "You are the instructor.")
        #expect(parse(#"{"error":"x","reason":"instructor"}"#)?.retryAfter == 30)
        #expect(parse(#"{"error":"x","reason":"not_fairplay"}"#)?.retryAfter == 30)
        #expect(parse(#"{"error":"Not open yet.","reason":"not_open"}"#)?.retryAfter == 15)
        for reason in ["other_device", "handed_in", "on_hold"] {
            #expect(parse(#"{"error":"Moved.","reason":"\#(reason)"}"#) == .init(text: "Moved.", retryAfter: 30))
            #expect(parse(#"{"error":"Moved.","reason":"\#(reason)"}"#, status: 409) == nil)
        }
        #expect(parse(#"{"error":"x","reason":"other"}"#) == nil)
        #expect(parse(#"{"error":5,"reason":"instructor"}"#) == nil)
        #expect(parse(#"{"error":"x"}"#) == nil)
        #expect(parse(#"{"error":"x","reason":"instructor"}"#, status: 409) == nil)
        #expect(parse(#"{"error":"x","reason":"instructor"}"#, status: 200) == nil)
        #expect(parse(#"{"error":"a\u0000b\nc\u001bd","reason":"not_open"}"#)?.text == "abcd")
        #expect(parse(#"{"error":"\#(String(repeating: "a", count: 500))","reason":"not_open"}"#)?.text.count == 200)
        let big = #"{"error":"x","reason":"instructor","pad":"\#(String(repeating: "p", count: 5000))"}"#
        #expect(parse(big) == nil)
        #expect(ProtectedBrowserController.ojRefusal(status: 403, data: nil) == nil)
    }

    @Test func theVersionLabelNamesTheBuildAndNothingElse() {
        #expect(Config.versionLabelText(version: "0.8.0", dev: false) == "FairPlay 0.8.0 · Mac")
        #expect(Config.versionLabelText(version: "0.8.0", dev: true) == "FairPlay 0.8.0 · Mac · dev")
    }

    @Test func aRefusalPacesTheRetryAndNeverAdvancesTheBackoff() {
        var schedule = NativeRenewSchedule(random: { 0.5 })
        #expect(schedule.next(after: .refused(after: 30), pace: .renew) == 30)
        #expect(schedule.failures == 0)
    }

    @Test func relaunchedWithAClaimStartsWithAnUncleanRecover() async throws {
        let binding = NativeLockState.Binding(session: UUID().uuidString, device: "d", user: "7", contest: "exam")
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(),
                                    contest: "exam", binding: binding, unclean: true)
        let store = InMemoryNativeLockStore(saved)
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { false }, ownsLock: { true }, release: {}, load: { _, _ in })
        effects.setup = NativeLockSetup.dev.with(store: store)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { true }, didClose: {}, effects: effects)
        controller.resumeNativeLock(saved, in: root)
        defer { controller.lockEnded() }
        #expect(controller.pendingUncleanRecover)
        let fresh = ProtectedBrowserController(busy: { true }, didClose: {}, effects: effects)
        fresh.resumeNativeLock(NativeLockState(lockId: saved.lockId, verifier: saved.verifier, created: saved.created, unclean: true), in: NSView(frame: root.frame))
        defer { fresh.lockEnded() }
        #expect(!fresh.pendingUncleanRecover)
    }
}
