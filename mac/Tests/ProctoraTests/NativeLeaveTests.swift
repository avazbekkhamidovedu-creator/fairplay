import Testing
import Foundation
import AppKit
import WebKit
import CryptoKit
@testable import Proctora

@MainActor @Suite struct NativeLeaveTextTests {
    @Test func theSheetTellsOfTheInstructorReleaseAndOfferingTheOfflineCode() {
        #expect(NativeLeaveText.title == "Emergency exit")
        #expect(NativeLeaveText.notice.contains("release this Mac from DKU OJ"))
        #expect(NativeLeaveText.offlineNotice.contains("without Wi-Fi"))
        #expect(NativeLeaveText.unlockTitle == "Unlock")
    }

    @Test func theSheetShowsTheLockCodeAndASixDigitField() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
        let panel = NativeLeavePanel(prompt: NativeLeavePrompt(lockCode: "ABCD-2345"), window: window)
        #expect(panel.info.stringValue.contains("ABCD-2345"))
        #expect(panel.info.stringValue.contains(NativeLeaveText.notice))
        #expect(panel.field.placeholderString == NativeLeaveText.hint)
        #expect(panel.leave.title == "Unlock")
    }

    @Test func aLockWithoutAVerifierShowsTheReleaseOnlyMessageAndTakesNoCode() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
        var state = NativeLeaveAvailability.releaseOnly
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.availability = { $0(state) }
        let panel = NativeLeavePanel(prompt: prompt, window: window)
        panel.syncAvailability()
        #expect(!panel.field.isEnabled && !panel.leave.isEnabled)
        #expect(panel.status.stringValue == NativeLeaveText.releaseOnlyMessage)
        state = .open
        panel.syncAvailability()
        #expect(panel.field.isEnabled && panel.leave.isEnabled)
    }

    private func panel(_ prompt: NativeLeavePrompt) -> NativeLeavePanel {
        NativeLeavePanel(prompt: prompt, window: NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false))
    }

    @Test func theCodeChangedHintStaysUntilTheStudentTypesOrSubmits() {
        var version = 0
        var state = NativeLeaveAvailability.open
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345", lockVersion: { version }, shownVersion: 0)
        prompt.availability = { $0(state) }
        prompt.reload = { $0("WXYZ-9999", nil) }
        let p = panel(prompt)
        version = 1
        _ = p.refreshIfCodeChanged()
        #expect(p.status.stringValue == NativeLeavePanel.codeChangedText)
        state = .refused; p.syncAvailability()
        state = .open; p.syncAvailability()
        #expect(p.field.isEnabled && p.status.stringValue == NativeLeavePanel.codeChangedText, "going back to open keeps the hint")
        p.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification))
        state = .refused; p.syncAvailability()
        state = .open; p.syncAvailability()
        #expect(p.status.stringValue.isEmpty)
    }

    private final class CountingStore: NativeLockStoring {
        var inner: InMemoryNativeLockStore
        var fullLoads = 0, trustedLoads = 0
        init(_ state: NativeLockState?) { inner = InMemoryNativeLockStore(state) }
        func load() -> NativeLockState? { fullLoads += 1; return inner.load() }
        func loadTrusted() -> NativeLockState? { trustedLoads += 1; return inner.load() }
        func save(_ state: NativeLockState) throws { try inner.save(state) }
        func delete() { inner.delete() }
    }

    @Test func thePollReadsTheTrustedCopyWithoutTheWritingFullLoad() {
        let id = NativeExit.newLockId()
        let store = CountingStore(NativeLockState(lockId: id, verifier: NativeExit.makeVerifier(code: "123456", lockId: id, iterations: 1000), created: Date()))
        var setup = NativeLockSetup.dev
        setup.store = store
        setup.background = { $0() }
        setup.onMain = { $0() }
        let prompt = setup.leavePrompt(NativeLockSetup.LeaveSnapshot(lock: nil, text: "T", development: nil)) { _, done in done(.malformed) }
        var got: NativeLeaveAvailability?
        prompt.availability { got = $0 }
        #expect(got == .open && store.trustedLoads == 1 && store.fullLoads == 0)
        // A lost Keychain (no trusted copy) falls back to the full load, which knows the lock has no verifier.
        let lost = CountingStore(nil)
        setup.store = lost
        let p2 = setup.leavePrompt(NativeLockSetup.LeaveSnapshot(lock: nil, text: "T", development: nil)) { _, done in done(.malformed) }
        p2.availability { got = $0 }
        #expect(lost.fullLoads == 1)
    }

    @Test func aSheetWithNoSavedLockSaysSoAndTakesNoCode() {
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.availability = { $0(.noLock) }
        let p = panel(prompt)
        p.syncAvailability()
        #expect(!p.field.isEnabled && !p.leave.isEnabled && p.status.stringValue == NativeLeaveText.noLockMessage)
    }

    @Test func eachSyncReadsTheAvailabilityOnce() {
        var reads = 0
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.availability = { done in reads += 1; done(.open) }
        let p = panel(prompt)
        p.syncAvailability(); p.syncAvailability()
        #expect(reads == 2)
    }

    @Test func theTimerLoadsTheLockOnlyEveryFifthSecond() {
        #expect(NativeLeavePanel.availabilityEvery == 5)
    }

    @Test func aStatusMessageIsKeptWhileTheAvailabilityDoesNotChange() {
        var state = NativeLeaveAvailability.open
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.availability = { $0(state) }
        let p = panel(prompt)
        p.status.stringValue = NativeLeavePanel.codeChangedText
        p.syncAvailability(); p.syncAvailability()
        #expect(p.status.stringValue == NativeLeavePanel.codeChangedText)
        state = .refused
        p.syncAvailability()
        #expect(p.status.stringValue == NativeLeaveText.refusedMessage && !p.field.isEnabled)
        p.status.stringValue = "other"
        p.syncAvailability()
        #expect(p.status.stringValue == "other")
        state = .open
        p.syncAvailability()
        #expect(p.field.isEnabled && p.leave.isEnabled && p.status.stringValue.isEmpty)
    }

    @Test func nothingIsReenabledOrOverwrittenWhileACodeIsBeingCheckedAndASecondSubmitIsIgnored() {
        var state = NativeLeaveAvailability.open
        var submits = 0
        var finish: ((NativeExitAttempt) -> Void)?
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.submitAsync = { _, done in submits += 1; finish = done }
        prompt.availability = { $0(state) }
        let p = panel(prompt)
        p.field.stringValue = "123456"
        p.submit()
        #expect(p.checking && !p.field.isEnabled && submits == 1)
        p.submit()
        #expect(submits == 1)
        state = .refused
        p.syncAvailability()
        #expect(p.status.stringValue == "Checking the code..." && !p.field.isEnabled)
        state = .open
        p.syncAvailability()
        #expect(!p.field.isEnabled && p.status.stringValue == "Checking the code...")
        finish?(.wrong(remaining: 3))
        #expect(!p.checking && p.field.isEnabled && p.status.stringValue.contains("3 attempts left"))
    }

    @Test func availabilityComesFromOneLoadedState() {
        let id = NativeExit.newLockId()
        let registered = NativeLockState(lockId: id, verifier: NativeExit.makeVerifier(code: "123456", lockId: id, iterations: 1000), created: Date())
        #expect(NativeLeaveAvailability.of(registered) == .open)
        #expect(NativeLeaveAvailability.of(NativeLockState(lockId: id, created: Date())) == .releaseOnly)
        var spent = registered
        spent.attempts = NativeExit.maxTotalAttempts
        let now = Date(), mono = NativeExit.Monotonic(boot: "b", seconds: 100)
        spent.guessLog = (0..<NativeExit.hourlyGuessCap).map { _ in .init(at: now, boot: "b", mono: 100) }
        #expect(NativeLeaveAvailability.of(spent, now: now, mono: mono) == .refused)
        #expect(NativeLeaveAvailability.of(nil) == .noLock)
    }

    @Test func everyOfflineExitResultMapsToTheSameAuditEventsAsTheInvigilatorPath() {
        #expect(NativeExit.auditEvent(for: .accepted)?.pair.0 == "offline_exit")
        for rejected in [NativeExitAttempt.wrong(remaining: 2), .wait(until: Date()), .refused] {
            #expect(NativeExit.auditEvent(for: rejected)?.pair.0 == "offline_exit_rejected")
        }
        for none in [NativeExitAttempt.malformed, .stale, .noVerifier] { #expect(NativeExit.auditEvent(for: none) == nil) }
    }

    @Test func settleReleasesOnlyForTheOwnerOfTheLockAndThatLockOnly() {
        let id = NativeExit.newLockId()
        let verifier = NativeExit.makeVerifier(code: "123456", lockId: id, iterations: 1000)
        let store = InMemoryNativeLockStore(NativeLockState(lockId: id, verifier: verifier, created: Date()))
        let ticket = NativeExit.Ticket(lockId: id, verifier: verifier)
        var released = 0
        // The screen no longer owns the lock (the browser took it over): stale, nothing released.
        #expect(NativeExit.settle(.accepted, ticket: ticket, store: store, canRelease: false) { released += 1 } == .stale && released == 0)
        // A ticket for another lock is stale too.
        let other = NativeExit.Ticket(lockId: NativeExit.newLockId(), verifier: verifier)
        #expect(NativeExit.settle(.accepted, ticket: other, store: store, canRelease: true) { released += 1 } == .stale && released == 0)
        #expect(NativeExit.settle(.accepted, ticket: ticket, store: store, canRelease: true) { released += 1 } == .accepted && released == 1)
        #expect(NativeExit.settle(.wrong(remaining: 2), ticket: nil, store: store, canRelease: true) { released += 1 } == .wrong(remaining: 2) && released == 1)
    }

    @Test func aNeverRegisteredLockCountsNothingButALostKeychainLockStillDoes() {
        let id = NativeExit.newLockId()
        let never = InMemoryNativeLockStore(NativeLockState(lockId: id, created: Date()))
        #expect(NativeExit.attempt(typed: "000000", store: never, now: Date()) == .noVerifier)
        #expect(never.load()?.attempts == 0)
        var lost = NativeLockState(lockId: id, created: Date())
        lost.keychainLost = true
        let store = InMemoryNativeLockStore(lost)
        #expect(NativeExit.attempt(typed: "000000", store: store, now: Date()) == .wrong(remaining: NativeExit.maxWrong - 1))
        #expect(store.load()?.attempts == 1)
    }

    @Test func theAttemptCapShowsTheRefusedMessage() {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 400, height: 300), styleMask: [.titled], backing: .buffered, defer: false)
        var prompt = NativeLeavePrompt(lockCode: "ABCD-2345")
        prompt.availability = { $0(.refused) }
        let panel = NativeLeavePanel(prompt: prompt, window: window)
        panel.syncAvailability()
        #expect(!panel.field.isEnabled && panel.status.stringValue == NativeLeaveText.refusedMessage)
    }

    @Test func everyAttemptResultHasAClearMessage() {
        let now = Date(timeIntervalSince1970: 1000)
        #expect(NativeLeaveText.message(for: .accepted, now: now) == nil)
        #expect(NativeLeaveText.message(for: .wrong(remaining: 3), now: now) == "That code is not right. 3 attempts left.")
        #expect(NativeLeaveText.message(for: .wrong(remaining: 1), now: now) == "That code is not right. 1 attempt left.")
        #expect(NativeLeaveText.message(for: .wait(until: now.addingTimeInterval(60)), now: now)?.hasPrefix("Too many wrong codes. Try again in") == true)
        #expect(NativeLeaveText.message(for: .refused, now: now) == NativeLeaveText.refusedMessage)
        #expect(NativeLeaveText.message(for: .noVerifier, now: now) == NativeLeaveText.releaseOnlyMessage)
    }

    @Test func aLockWithoutAVerifierIsReleaseOnly() {
        let store = InMemoryNativeLockStore(NativeLockState(lockId: NativeExit.newLockId(), created: Date()))
        #expect(NativeLeaveAvailability.of(store.load()) == .releaseOnly)
        #expect(NativeLeaveAvailability.of(InMemoryNativeLockStore().load()) == .noLock)
    }

    @Test func aDeviceTestPreviewLeavesAtOnceWithoutACode() {
        #expect(PreLockState.emergencyExitAction(locked: false, deviceTestRunning: true) == .leaveUnlockedPractice)
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
        #expect(leave.title == "Emergency exit" && !leave.isHidden)
        #expect(leave.bezelColor == .systemRed)
        #expect(leave.superview?.superview === root)
        #expect(leave.frame.maxX > root.bounds.width - 40)
        #expect(leave.frame.maxY > root.bounds.height - 60)
        #expect(leave.autoresizingMask.contains(.minXMargin) && leave.autoresizingMask.contains(.minYMargin))
    }

    @Test func aLockWithoutAVerifierRefusesTypedCodesWithoutCountingThem() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = try await open(probe, root: root)
        defer { controller.lockEnded() }
        var state = try #require(probe.store.load())
        state.verifier = nil
        try probe.store.save(state)
        #expect(controller.submitLeaveCode("123456") == .noVerifier)
        #expect(probe.store.load()?.attempts == 0 && probe.locked && probe.released == 0)
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

    @Test func nativeLockNeverSelectsTheCodePrompt() {
        for locked in [true, false] {
            for practice in [true, false] {
                #expect(PreLockState.emergencyExitAction(locked: locked, deviceTestRunning: practice, nativeLock: true) == .nativeLeave)
            }
        }
        #expect(PreLockState.emergencyExitAction(locked: true, deviceTestRunning: false) == .requireCode)
    }

    @Test func theCalmBlockScreenAndConflictTextsStayFreeOfExitCodeTalk() {
        var texts = [NativeBlockScreen.calmFooter, ProtectedBrowserController.lockConflictText]
        let look = NativeBlockScreen.look(problems: ["x": "Close the app."], nativeLock: true,
                                          since: Date(timeIntervalSinceNow: -60), now: Date())
        texts.append(look?.text ?? "")
        for text in texts {
            let lower = text.lowercased()
            #expect(!lower.contains("code") && !lower.contains("digit"), "\(text)")
        }
    }

    @Test func theEmergencyExitSheetOpensWithTheLockCodeAndUnlocksOfflineWithTheRightCode() async throws {
        let probe = Probe()
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        var shown: NativeLeavePrompt?
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false; probe.released += 1 }, load: { _, _ in })
        // No network at all: registration and every report fail.
        var setup = NativeLockSetup.dev.with(store: probe.store)
        setup.transport = { _, done in done(nil, Data()) }
        effects.setup = setup
        effects.now = { probe.now }
        effects.monotonic = { .init(boot: "test-boot", seconds: 5_000) }
        effects.presentLeave = { prompt, _ in shown = prompt }
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }).performClick(nil)
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        defer { controller.lockEnded() }
        try #require(descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Emergency exit" }).performClick(nil)
        #expect(await waitUntil { shown != nil })
        let prompt = try #require(shown)
        let id = try #require(probe.store.load()?.lockId)
        #expect(prompt.lockCode.hasPrefix(NativeExit.display(id)))
        var availability: NativeLeaveAvailability?
        prompt.availability { availability = $0 }
        #expect(await waitUntil { availability != nil } && availability == .open)
        let right = probe.code
        let wrong = right == "111111" ? "222222" : "111111"
        func submit(_ typed: String) async -> NativeExitAttempt {
            var answer: NativeExitAttempt?
            prompt.submitAsync(typed) { answer = $0 }
            #expect(await waitUntil { answer != nil })
            return answer ?? .malformed
        }
        #expect(await submit(wrong) == .wrong(remaining: 4))
        #expect(await submit(right) == .accepted)
        #expect(!probe.locked && probe.released == 1 && probe.store.load() == nil)
    }

    @Test func lockConflictIsRecognisedInTheOldAndNewWording() {
        for message in ["This Mac code belongs to another student or exam", "This lock belongs to another exam or student."] {
            let data = Data("{\"error\": \"\(message)\"}".utf8)
            #expect(ProtectedBrowserController.isLockConflict(status: 409, data: data))
        }
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
