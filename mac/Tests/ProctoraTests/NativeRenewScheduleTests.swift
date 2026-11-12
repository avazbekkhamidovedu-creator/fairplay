import Foundation
import AppKit
import Testing
import WebKit
@testable import Proctora

@Suite struct NativeRenewScheduleTests {
    private func schedule(_ values: [Double]) -> NativeRenewSchedule {
        var queue = values
        return NativeRenewSchedule(random: { queue.isEmpty ? 0.5 : queue.removeFirst() })
    }

    @Test func constantsAreTheAgreedOnes() {
        #expect(NativeRenewSchedule.renewInterval == 20)
        #expect(NativeRenewSchedule.holdPollInterval == 10)
        #expect(NativeRenewSchedule.backoffSteps == [2, 4, 8, 16])
        #expect(NativeRenewSchedule.backoffCap == 30)
        #expect(NativeRenewSchedule.retryAfterCap == 30)
    }

    @Test func renewIsTwentySecondsWithTwentyPercentJitter() {
        var s = schedule([0, 0.5, 0.999999])
        let low = s.next(after: .success, pace: .renew)
        let mid = s.next(after: .success, pace: .renew)
        let high = s.next(after: .success, pace: .renew)
        #expect(low == 16)
        #expect(mid == 20)
        #expect(high > 23.99 && high < 24)
    }

    @Test func holdPollIsTenSecondsWithJitter() {
        var s = schedule([0, 0.999999])
        #expect(s.next(after: .success, pace: .hold) == 8)
        let high = s.next(after: .success, pace: .hold)
        #expect(high > 11.99 && high < 12)
    }

    @Test func randomnessStaysInRangeForAnySource() {
        for r in stride(from: 0.0, through: 1.0, by: 0.05) {
            var s = NativeRenewSchedule(random: { r })
            let d = s.next(after: .success, pace: .renew)
            #expect(d >= 16 && d <= 24)
        }
    }

    @Test func backoffStepsDoubleThenCapAtThirtyWithEqualJitter() {
        var top = NativeRenewSchedule(random: { 1 })
        let waits = (0..<7).map { _ in top.next(after: .overloaded(retryAfter: nil), pace: .renew) }
        #expect(waits == [2, 4, 8, 16, 30, 30, 30])
        var floor = NativeRenewSchedule(random: { 0 })
        let lows = (0..<6).map { _ in floor.next(after: .overloaded(retryAfter: nil), pace: .renew) }
        #expect(lows == [1, 2, 4, 8, 15, 15])
        var half = NativeRenewSchedule(random: { 0.5 })
        #expect(half.next(after: .overloaded(retryAfter: nil), pace: .hold) == 1.5)
    }

    @Test func nothingEverComesBackBelowHalfTheStepOrHalfTheHoldInterval() {
        for r in stride(from: 0.0, through: 1.0, by: 0.05) {
            var s = NativeRenewSchedule(random: { r })
            for failure in 1...8 {
                let wait = s.next(after: .overloaded(retryAfter: nil), pace: .renew)
                #expect(wait >= NativeRenewSchedule.step(forFailure: failure) / 2)
            }
            var hold = NativeRenewSchedule(random: { r })
            #expect(hold.next(after: .success, pace: .hold) >= NativeRenewSchedule.holdPollInterval / 2)
        }
    }

    @Test func successResetsTheBackoff() {
        var s = NativeRenewSchedule(random: { 1 })
        _ = s.next(after: .overloaded(retryAfter: nil), pace: .renew)
        _ = s.next(after: .overloaded(retryAfter: nil), pace: .renew)
        #expect(s.failures == 2)
        _ = s.next(after: .success, pace: .renew)
        #expect(s.failures == 0)
        #expect(s.next(after: .overloaded(retryAfter: nil), pace: .renew) == 2)
    }

    @Test func otherRefusalsKeepNormalPaceAndDoNotTouchTheBackoff() {
        var s = NativeRenewSchedule(random: { 0.5 })
        _ = s.next(after: .overloaded(retryAfter: nil), pace: .renew)
        #expect(s.next(after: .other, pace: .renew) == 20)
        #expect(s.failures == 1)
    }

    @Test func retryAfterIsHonouredUpToThirtySeconds() {
        var s = NativeRenewSchedule(random: { 0 })
        #expect(s.next(after: .overloaded(retryAfter: 25), pace: .renew) == 25)
        #expect(s.next(after: .overloaded(retryAfter: 600), pace: .renew) == 30)
        #expect(s.next(after: .overloaded(retryAfter: 1), pace: .renew) == 4)
        #expect(NativeRenewSchedule.retryAfter("12") == 12)
        #expect(NativeRenewSchedule.retryAfter(" 7 ") == 7)
        #expect(NativeRenewSchedule.retryAfter("-3") == nil)
        #expect(NativeRenewSchedule.retryAfter("soon") == nil)
        #expect(NativeRenewSchedule.retryAfter(nil) == nil)
    }

    @Test func onlyTimeoutsTooManyRequestsAndServerErrorsBackOff() {
        #expect(NativeRenewSchedule.isOverloaded(status: nil, transportFailed: true))
        #expect(NativeRenewSchedule.isOverloaded(status: 429, transportFailed: false))
        #expect(NativeRenewSchedule.isOverloaded(status: 500, transportFailed: false))
        #expect(NativeRenewSchedule.isOverloaded(status: 503, transportFailed: false))
        for status in [200, 400, 401, 403, 404, 409] {
            #expect(!NativeRenewSchedule.isOverloaded(status: status, transportFailed: false), "\(status)")
        }
    }

    @Test func handInPageIsRecognisedByLeavePathOrTitle() {
        let leave = URL(string: "https://oj.example.edu/contest/exam1/leave")
        #expect(NativeRenewSchedule.looksLikeHandIn(url: leave, title: nil, contest: "exam1"))
        #expect(NativeRenewSchedule.looksLikeHandIn(url: URL(string: "https://oj.example.edu/contest/exam1/leave/"), title: "", contest: "exam1"))
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: nil, title: "Exam submitted - DKU OJ", contest: nil), "a title alone is not enough")
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: leave, title: "Other", contest: "exam2"))
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: URL(string: "https://oj.example.edu/contest/exam1/"), title: "Exam", contest: "exam1"))
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: leave, title: nil, contest: nil))
    }
}

@MainActor @Suite struct NativeRenewControllerTests {
    private final class Clock {
        struct Item { let id: Int; let at: TimeInterval; let fire: () -> Void }
        var now: TimeInterval = 0
        var items: [Item] = []
        var delays: [TimeInterval] = []
        var next = 0
        var released = 0
        func schedule(_ delay: TimeInterval, _ fire: @escaping () -> Void) -> () -> Void {
            next += 1
            let id = next
            delays.append(delay)
            items.append(Item(id: id, at: now + delay, fire: fire))
            return { [weak self] in self?.items.removeAll { $0.id == id } }
        }
        func advance(_ seconds: TimeInterval) {
            let target = now + seconds
            while let due = items.filter({ $0.at <= target }).min(by: { $0.at < $1.at }) {
                items.removeAll { $0.id == due.id }
                now = due.at
                due.fire()
            }
            now = target
        }
    }

    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }

    private func resumed(_ clock: Clock, contest: String?, fakeNow: Bool = false) async -> (ProtectedBrowserController, NSView) {
        let binding = NativeLockState.Binding(session: "s1", device: "d", user: "7", contest: contest ?? "exam")
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(), contest: contest, binding: contest == nil ? nil : binding)
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { false }, ownsLock: { true },
                                                         release: { clock.released += 1 }, load: { _, _ in })
        effects.setup = NativeLockSetup.dev.with(store: InMemoryNativeLockStore(saved))
        effects.random = { 0.5 }
        effects.schedule = { clock.schedule($0, $1) }
        if fakeNow { effects.now = { Date(timeIntervalSince1970: 1_000_000 + clock.now) } }
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { true }, didClose: {}, effects: effects)
        controller.resumeNativeLock(saved, in: root)
        _ = await waitUntil { descendants(root).contains { $0 is WKWebView } }
        return (controller, root)
    }

    @Test func handInPageAsksNowThenAtTwoAndFiveSecondsAndNeverUnlocks() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam")
        defer { controller.lockEnded() }
        clock.items.removeAll(); clock.delays.removeAll()
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/leave"), title: "Exam submitted - DKU OJ")
        #expect(Array(clock.delays.prefix(2)) == [2, 5])
        let asked = clock.delays.count
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/leave"), title: nil)
        #expect(clock.delays.count == asked, "a burst of hand-in pages must not queue more asks")
        clock.advance(2)
        clock.advance(3)
        #expect(clock.released == 0)
        #expect(controller.isOpen)
    }

    @Test func appliedContextKeepsSitesForNinetySecondsOnTheFakeClock() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam")
        defer { controller.lockEnded() }
        let json = #"{"v":1,"nonce":"n","expires":1,"user":"u","contest":"exam","participation":"p","state":"active","sites":[]}"#
        let context = try JSONDecoder().decode(BrowserExamContext.self, from: Data(json.utf8))
        controller.applyExamContext(context, url: nil)
        let expiry = try #require(controller.approvedSitesExpiry)
        #expect(expiry.timeIntervalSince(Date()) > 85 && expiry.timeIntervalSince(Date()) <= 90)
    }

    private func state(_ name: String, session: String = "s1") throws -> NativeDeviceContext {
        let json: [String: Any] = ["v": 2, "nonce": "n", "expires": 1, "session": session, "device": "d", "user": "7",
            "contest": "exam", "participation": "p", "state": name, "lease_until": 0]
        return try JSONDecoder().decode(NativeDeviceContext.self, from: JSONSerialization.data(withJSONObject: json))
    }

    @Test func aVerifiedReleasedStateEndsTheLockAndLeavesNoPagesOrTimers() async throws {
        let clock = Clock()
        let (controller, root) = await resumed(clock, contest: "exam")
        defer { controller.lockEnded() }
        controller.receiveVerifiedState(try state("released"))
        #expect(clock.released == 1)
        #expect(!descendants(root).contains { $0 is WKWebView })
        #expect(!controller.isOpen)
        #expect(clock.items.isEmpty)
    }

    @Test func aStateThatDoesNotUnlockOrBelongsToAnotherSessionKeepsTheLock() async throws {
        let clock = Clock()
        let (controller, root) = await resumed(clock, contest: "exam")
        defer { controller.lockEnded() }
        controller.receiveVerifiedState(try state("active"))
        controller.receiveVerifiedState(try state("rejected"))
        controller.receiveVerifiedState(try state("released", session: "other"))
        #expect(clock.released == 0)
        #expect(descendants(root).contains { $0 is WKWebView })
    }

    @Test func ordinaryPagesAndOtherContestsTriggerNothing() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam")
        defer { controller.lockEnded() }
        clock.items.removeAll(); clock.delays.removeAll()
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/"), title: "Exam")
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/other/leave"), title: "Other")
        #expect(clock.delays.isEmpty)
    }

    @Test func closingTheLockCancelsEveryPendingAsk() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam")
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/leave"), title: nil)
        #expect(!clock.items.isEmpty)
        controller.lockEnded()
        #expect(clock.items.isEmpty)
    }
}

final class FakeEscrowClock {
    var now: TimeInterval = 0
    var waits: [TimeInterval] = []
    func install(in setup: inout NativeLockSetup) {
        setup.random = { 0.5 }
        setup.background = { $0() }
        setup.onMain = { $0() }
        setup.uptime = { self.now }
        setup.wait = { delay, fire in self.waits.append(delay); self.now += delay; fire() }
    }
}

@Suite struct EscrowRetryTests {
    private let key = Data(repeating: 7, count: 32)
    private func setup(_ replies: [Int?], clock: FakeEscrowClock, sent: @escaping () -> Void = {},
                       progress: @escaping () -> Void = {}) -> (NativeLockSetup, InMemoryNativeLockStore) {
        let store = InMemoryNativeLockStore()
        var remaining = replies
        var s = NativeLockSetup(store: store, publicKey: key, isDev: false, device: { String(repeating: "a", count: 64) },
                                transport: { _, done in
                                    sent()
                                    let status = remaining.count > 1 ? remaining.removeFirst() : remaining.first ?? nil
                                    done(status, status == 201 ? Data(#"{"ok":true}"#.utf8) : nil)
                                }, loginItem: NoLoginItem(), iterations: 2_000)
        clock.install(in: &s)
        return (s, store)
    }
    private func run(_ s: NativeLockSetup, progress: @escaping () -> Void = {}) -> Result<NativeLockState, NativeLockSetupFailure> {
        var out: Result<NativeLockState, NativeLockSetupFailure>?
        s.prepare(progress: progress) { out = $0 }
        return out!
    }

    @Test func overloadedThenOkLocksOnlyAfterTheKeyIsStored() throws {
        let clock = FakeEscrowClock()
        var sends = 0, shown = 0
        let (s, store) = setup([429, 503, nil, 201], clock: clock, sent: { sends += 1 })
        let state = try run(s, progress: { shown += 1 }).get()
        #expect(sends == 4 && shown == 4)
        #expect(store.load() == state)
        #expect(clock.waits == [3, 1.5, 3])
    }

    @Test func firstWaitIsOneToFiveSecondsThenTheBackoffSteps() {
        var s = NativeLockSetup.dev
        for (r, lo) in [(0.0, 1.0), (1.0, 5.0)] {
            s.random = { r }
            #expect(s.retryWait(afterFailure: 1) == lo)
        }
        s.random = { 1 }
        #expect([2, 3, 4, 5, 6].map { s.retryWait(afterFailure: $0) } == [2, 4, 8, 16, 30])
    }

    @Test func persistentOverloadGivesUpAroundSixtySecondsWithTheFailClosedResult() {
        let clock = FakeEscrowClock()
        var sends = 0
        let (s, store) = setup([503], clock: clock, sent: { sends += 1 })
        #expect(run(s) == .failure(.escrow))
        #expect(store.load() == nil)
        #expect(sends > 3)
        #expect(clock.now <= NativeLockSetup.retryBudget)
        #expect(clock.now > NativeLockSetup.retryBudget - 20)
    }

    @Test func refusalsAreNeverRetried() {
        for status in [403, 409, 422, 400, 200] {
            let clock = FakeEscrowClock()
            var sends = 0
            let (s, store) = setup([status], clock: clock, sent: { sends += 1 })
            #expect(run(s) == .failure(.escrow), "\(status)")
            #expect(sends == 1 && clock.waits.isEmpty && store.load() == nil, "\(status)")
        }
    }

    @Test func preparingIsShownOnceWhenTheFirstTryWorks() throws {
        let clock = FakeEscrowClock()
        var shown = 0
        let (s, _) = setup([201], clock: clock)
        _ = try run(s, progress: { shown += 1 }).get()
        #expect(shown == 1)
    }

    @Test func derivationRunsOffTheCallerAndNothingIsStoredBeforeThe201() throws {
        let clock = FakeEscrowClock()
        var sends = 0
        var (s, store) = setup([201], clock: clock, sent: { sends += 1 })
        var workerJobs: [() -> Void] = [], mainJobs: [() -> Void] = []
        s.background = { workerJobs.append($0) }
        s.onMain = { mainJobs.append($0) }
        var out: Result<NativeLockState, NativeLockSetupFailure>?
        var shown = 0
        s.prepare(progress: { shown += 1 }) { out = $0 }
        #expect(shown == 1 && workerJobs.count == 1 && mainJobs.isEmpty && sends == 0 && store.load() == nil && out == nil)
        workerJobs.removeFirst()()
        #expect(sends == 0 && store.load() == nil && mainJobs.count == 1)
        mainJobs.removeFirst()()
        let state = try #require(try out?.get())
        #expect(sends == 1 && store.load() == state)
        let text = String(decoding: try JSONEncoder().encode(state), as: UTF8.self)
        #expect(!text.contains("\"key\""))
    }

    @Test func failedEscrowAfterBackgroundDerivationStoresNothing() {
        let clock = FakeEscrowClock()
        var (s, store) = setup([403], clock: clock)
        var workerJobs: [() -> Void] = []
        s.background = { workerJobs.append($0) }
        var out: Result<NativeLockState, NativeLockSetupFailure>?
        s.prepare { out = $0 }
        workerJobs.removeFirst()()
        #expect(out == .failure(.escrow) && store.load() == nil)
    }
}

@Suite struct PolicyWindowTests {
    @Test func approvedSitesLiveNinetySecondsFromTheReceiptOrTheSignedExpiryIfLater() {
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        #expect(NativeRenewSchedule.policyWindow == 90)
        #expect(NativeRenewSchedule.policyExpiry(signedExpires: t.timeIntervalSince1970 + 30, receivedAt: t) == t.addingTimeInterval(90))
        #expect(NativeRenewSchedule.policyExpiry(signedExpires: t.timeIntervalSince1970 + 300, receivedAt: t) == t.addingTimeInterval(300))
        let late = t.addingTimeInterval(60)
        #expect(late < NativeRenewSchedule.policyExpiry(signedExpires: t.timeIntervalSince1970 + 30, receivedAt: t))
        #expect(t.addingTimeInterval(91) > NativeRenewSchedule.policyExpiry(signedExpires: t.timeIntervalSince1970 + 30, receivedAt: t))
    }
}

@Suite struct StudentMessageTests {
    @Test func beginPromptStatesTheLockPlainly() {
        #expect(ProtectedBrowserController.beginText == "Continue locks this Mac until you finish the exam or your instructor releases this Mac from DKU OJ. Start only when your exam is about to begin.")
    }
}

@Suite struct RepeatedFailureLogTests {
    @Test func firstFailureLogsThenOneSummaryPerMinuteThenRecovery() {
        var log = RepeatedFailureLog()
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        let line = "native-request-failed http=503 transport=0"
        #expect(log.failure(line, now: t) == line)
        for second in stride(from: 5.0, to: 60.0, by: 5.0) { #expect(log.failure(line, now: t.addingTimeInterval(second)) == nil) }
        #expect(log.failure(line, now: t.addingTimeInterval(60)) == line + " (13 times so far)")
        #expect(log.failure(line, now: t.addingTimeInterval(65)) == nil)
        #expect(log.success() == "native-request-recovered after 14 failures")
        #expect(log.success() == nil)
        #expect(log.failure(line, now: t.addingTimeInterval(70)) == line)
    }

    @Test func aDifferentFailureLogsAtOnce() {
        var log = RepeatedFailureLog()
        let t = Date(timeIntervalSince1970: 1_800_000_000)
        _ = log.failure("native-request-failed http=503 transport=0", now: t)
        #expect(log.failure("native-request-failed http=0 transport=-1001", now: t.addingTimeInterval(1)) != nil)
    }
}

// MARK: - Fast release poll and the hand-in check

extension NativeRenewControllerTests {
    @Test func releasePollIsAFewSecondsAndHandInPollFasterAndRenewStaysTwenty() {
        #expect(NativeRenewSchedule.watchInterval == 5)
        #expect(NativeRenewSchedule.handInPollInterval == 2)
        #expect(NativeRenewSchedule.renewInterval == 20)
        var s = NativeRenewSchedule(random: { 0.5 })
        #expect(s.next(after: .success, pace: .watch) == 5)
        #expect(s.next(after: .success, pace: .handIn) == 2)
        // 300 students: light reads plus renews stay near 60 requests per second.
        let perSecond = 300.0 * (1 / NativeRenewSchedule.watchInterval)
        #expect(perSecond <= 60)
    }

    @Test func stateReadIsAGetWithTheNonceAndNoBody() {
        let endpoint = URL(string: "https://oj.example.edu/api/fairplay/native/session/abc")!
        let request = ProtectedBrowserController.nativeStateRequest(endpoint: endpoint, origin: URL(string: "https://oj.example.edu")!,
                                                                    nonce: "n0nce", cookies: [])
        #expect(request.httpMethod == "GET")
        #expect(request.httpBody == nil)
        #expect(request.url?.absoluteString == "https://oj.example.edu/api/fairplay/native/session/abc?nonce=n0nce")
    }

    @Test func verificationIsProgressUntilOneSixtySecondTimeoutAndNeverAsksTheOJ() {
        var v = HandInVerification()
        let b1 = v.begin(at: 100); #expect(b1)
        let b2 = v.begin(at: 101); #expect(!b2, "a second hand-in page keeps the running clock")
        let early = v.tick(at: 140); #expect(early == .none)
        #expect(v.phase == .checking && v.text == HandInVerification.checkingText)
        #expect(HandInVerification.window == 60)
        let late = v.tick(at: 160); #expect(late == .failed)
        #expect(v.phase == .failed && v.text == HandInVerification.timeoutText)
        v.restart(at: 300)
        #expect(v.phase == .checking)
        v.pageRefused()
        #expect(v.phase == .failed && v.text == HandInVerification.refusedText)
        v.reset()
        #expect(v.phase == .idle)
    }

    @Test func onlyTheLeaveAddressOnTheOJOriginForTheBoundContestIsAHandInPage() {
        let oj = URL(string: "https://oj.example.edu")!
        func page(_ s: String, contest: String = "exam") -> Bool {
            HandInVerification.isHandInPage(url: URL(string: s), origin: oj, contest: contest)
        }
        #expect(page("https://oj.example.edu/contest/exam/leave"))
        #expect(page("https://oj.example.edu/contest/exam/leave/"))
        #expect(!page("https://forum.example.org/contest/exam/leave"), "another origin")
        #expect(!page("https://oj.example.edu/contest/other/leave"))
        #expect(!page("https://oj.example.edu/contest/exam/"))
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: URL(string: "https://oj.example.edu/post/1"), title: "Exam submitted", contest: "exam"),
                "a title alone is never a hand-in")
        #expect(!NativeRenewSchedule.looksLikeHandIn(url: nil, title: "Checking your answers", contest: "exam"))
    }

    @Test func checkingPageIsProgressTheFinalNotCompletePageIsAFailureAndBackIsQuiet() async throws {
        let clock = Clock()
        let (controller, root) = await resumed(clock, contest: "exam", fakeNow: true)
        defer { controller.lockEnded() }
        let leave = URL(string: "https://oj.example.edu/contest/exam/leave")
        let texts = { self.descendants(root).compactMap { ($0 as? NSTextField)?.stringValue } }
        let retry = { self.descendants(root).first { $0.identifier?.rawValue == "browser.handin.retry" } }
        controller.handInPageFinished(url: leave, title: "Checking your answers")
        #expect(controller.handInPhase == .checking)
        #expect(texts().contains(HandInVerification.checkingText))
        #expect(descendants(root).contains { $0 is NSProgressIndicator })
        #expect(retry()?.isHidden == true)
        clock.advance(50)
        #expect(controller.handInPhase == .checking)
        clock.advance(15)
        #expect(controller.handInPhase == .failed)
        #expect(texts().contains(HandInVerification.timeoutText))
        #expect(retry()?.isHidden == false)
        let leaveButton = try #require(descendants(root).first { $0.identifier?.rawValue == "browser.leave" })
        let panel = try #require(descendants(root).first { $0.identifier?.rawValue == "browser.handin" })
        let siblings = try #require(leaveButton.superview).subviews
        #expect(siblings.firstIndex(of: leaveButton)! > siblings.firstIndex(of: panel)!)
        (retry() as? NSButton)?.performClick(nil)
        #expect(controller.handInPhase == .checking)
        // The OJ's FINAL page: failure at once.
        controller.handInPageFinished(url: leave, title: "Hand-in not complete")
        #expect(controller.handInPhase == .failed)
        #expect(texts().contains(HandInVerification.refusedText))
        // Back to the exam: the panel goes and the page's own re-post is ignored for a few seconds.
        let back = try #require(descendants(root).first { $0.identifier?.rawValue == "browser.handin.back" } as? NSButton)
        back.performClick(nil)
        #expect(controller.handInPhase == .idle)
        controller.handInPageFinished(url: leave, title: "Checking your answers")
        #expect(controller.handInPhase == .idle)
        clock.advance(6)
        controller.handInPageFinished(url: leave, title: "Checking your answers")
        #expect(controller.handInPhase == .checking)
    }

    @Test func aSignedReleaseDuringTheCheckUnlocksAndRemovesEverything() async throws {
        let clock = Clock()
        let (controller, root) = await resumed(clock, contest: "exam", fakeNow: true)
        defer { controller.lockEnded() }
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/leave"), title: nil)
        #expect(controller.handInPhase == .checking)
        controller.receiveVerifiedState(try state("completed"))
        #expect(clock.released == 1)
        #expect(!controller.isOpen)
        #expect(!descendants(root).contains { $0.identifier?.rawValue == "browser.handin" })
        #expect(clock.items.isEmpty)
    }
}

// MARK: - Box popup sign-in and the staggered auto-open

extension NativeRenewControllerTests {
    @Test func aBoxPopupThatCommittedAboutBlankMayStillGoToDukeSignIn() {
        let blank = URL(string: "about:blank")!
        for login in ["https://shib.oit.duke.edu/idp/profile/SAML2/Redirect/SSO", "https://duke.account.box.com/login",
                      "https://api-1a2b3c.duosecurity.com/frame/web/v1/auth"] {
            let url = URL(string: login)!
            #expect(BoxSignIn.allowsTopLevel(target: url, current: blank, boxTab: true), "\(login)")
            #expect(!BoxSignIn.allowsTopLevel(target: url, current: blank, boxTab: false), "not a Box tab: \(login)")
            #expect(!BoxSignIn.allowsTopLevel(target: url, current: URL(string: "https://docs.python.org/"), boxTab: true),
                    "a tab that left the chain cannot come back: \(login)")
        }
        #expect(!BoxSignIn.allowsTopLevel(target: URL(string: "https://evil.example/")!, current: blank, boxTab: true))
    }

    @Test func aQueuedPageTheStudentAlreadyOpenedIsNotOpenedTwice() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam", fakeNow: true)
        defer { controller.lockEnded() }
        let json = #"{"v":1,"nonce":"n","expires":4102444800,"user":"u","contest":"exam","participation":"p","state":"active","sites":["https://docs.python.org/3/","https://example.org/ref/"]}"#
        controller.applyExamContext(try JSONDecoder().decode(BrowserExamContext.self, from: Data(json.utf8)), url: nil)
        _ = await waitUntil { controller.pickerSiteCount >= 2 }
        let before = controller.rememberedReferenceTabs
        // The student opens the second page by hand while it still waits in the queue.
        controller.openReferenceTab(URL(string: "https://example.org/ref/")!, select: true)
        clock.advance(5)
        let opened = controller.openReferenceKeys.filter { $0.contains("example.org") }
        #expect(opened.count == 1)
        #expect(controller.rememberedReferenceTabs <= before + 2)
    }
}

extension NativeRenewControllerTests {
    @Test func anActiveAnswerIsNotAReopenUnlessTheDeadlineMovedLater() {
        #expect(!HandInVerification.isReopen(state: "active", deadline: 1000, startDeadline: 1000))
        #expect(!HandInVerification.isReopen(state: "active", deadline: nil, startDeadline: 1000))
        #expect(HandInVerification.isReopen(state: "active", deadline: 1600, startDeadline: 1000))
        #expect(!HandInVerification.isReopen(state: "finishing", deadline: 1600, startDeadline: 1000))
    }

    @Test func aPollOnlyStartsACheckOnEntryIntoFinishingAndNeverFromFailedOrDismissed() {
        #expect(HandInVerification.startsOnState(previous: "active", current: "finishing", phase: .idle))
        #expect(!HandInVerification.startsOnState(previous: "finishing", current: "finishing", phase: .idle), "dismissed stays dismissed")
        #expect(!HandInVerification.startsOnState(previous: "active", current: "finishing", phase: .failed))
        #expect(!HandInVerification.startsOnState(previous: "active", current: "finishing", phase: .checking))
    }

    @Test func theCheckingPageTitleIsNotAFailure() {
        #expect(HandInVerification.isCheckingPage(title: "Checking your answers"))
        #expect(!HandInVerification.isIncompletePage(title: "Checking your answers"))
    }

    @Test func tearingDownDuringTheCheckLeavesNoPanelForTheNextExam() async throws {
        let clock = Clock()
        let (controller, _) = await resumed(clock, contest: "exam", fakeNow: true)
        controller.handInPageFinished(url: URL(string: "https://oj.example.edu/contest/exam/leave"), title: "Checking your answers")
        #expect(controller.hasHandInPanel)
        controller.receiveVerifiedState(try state("released"))
        #expect(!controller.hasHandInPanel)
        #expect(controller.handInPhase == .idle)
    }
}
