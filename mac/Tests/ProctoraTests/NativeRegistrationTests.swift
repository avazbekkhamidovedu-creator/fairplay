import Testing
import Foundation
import CryptoKit
@testable import Proctora

@Suite struct NativeRegistrationTests {
    private let serverPrivate = Curve25519.KeyAgreement.PrivateKey()
    private let device = String(repeating: "a", count: 64)

    private struct Reply { var status: Int?; var retryAfter: TimeInterval? = nil }

    private final class Harness {
        var sent: [NativeExit.EscrowRequest] = []
        var waits: [TimeInterval] = []
        var pending: [() -> Void] = []
        var replies: [Reply]
        var holdWaits: Bool
        init(replies: [Reply], holdWaits: Bool = false) { self.replies = replies; self.holdWaits = holdWaits }
    }

    private func make(store: NativeLockStoring, harness h: Harness, key: Data? = nil) -> NativeLockSetup {
        var s = NativeLockSetup(store: store, publicKey: key ?? serverPrivate.publicKey.rawRepresentation, isDev: false,
                                device: { device }, transport: { _, done in done(nil, nil) }, loginItem: NoLoginItem(), iterations: 2_000)
        s.registerTransport = { body, done in
            h.sent.append(try! JSONDecoder().decode(NativeExit.EscrowRequest.self, from: body))
            let r = h.replies.count > 1 ? h.replies.removeFirst() : h.replies[0]
            done(r.status, r.status == 201 ? Data(#"{"ok":true}"#.utf8) : nil, r.retryAfter)
        }
        s.random = { 0.5 }
        s.background = { $0() }
        s.onMain = { $0() }
        s.wait = { delay, fire in
            h.waits.append(delay)
            if h.holdWaits { h.pending.append(fire) } else { fire() }
        }
        return s
    }

    private func lost(_ id: String = "K7QM3XPA") -> NativeLockState {
        NativeLockState(lockId: id, created: Date(timeIntervalSince1970: 1_000), unclean: true)
    }

    @Test func acceptedRegistrationSavesTheVerifierForTheSameId() throws {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 201)])
        let s = make(store: store, harness: h)
        s.registerMissingVerifier(for: lost())
        let saved = try #require(store.load())
        #expect(saved.lockId == "K7QM3XPA")
        #expect(saved.verifier != nil)
        #expect(saved.unclean)
        #expect(h.sent.count == 1 && h.sent[0].lock_id == "K7QM3XPA")
        let key = try NativeExit.open(blob: h.sent[0].blob, lockId: "K7QM3XPA", serverPrivate: serverPrivate.rawRepresentation)
        #expect(NativeExit.verifies(NativeExit.code(key: key, lockId: "K7QM3XPA"), lockId: "K7QM3XPA", verifier: saved.verifier))
        #expect(!s.registrar.isRetrying)
    }

    @Test func nothingIsSavedBeforeTheServerAccepts() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 503)], holdWaits: true)
        let s = make(store: store, harness: h)
        s.registerMissingVerifier(for: lost())
        #expect(store.load()?.verifier == nil)
        #expect(s.registrar.isRetrying)
        #expect(h.sent.count == 1)
    }

    @Test func conflictTakesAFreshIdAndKeyAndShowsTheNewCode() throws {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 409), Reply(status: 201)])
        let s = make(store: store, harness: h)
        s.registerMissingVerifier(for: lost())
        let saved = try #require(store.load())
        #expect(h.sent.count == 2)
        #expect(saved.lockId == h.sent[1].lock_id && saved.lockId != "K7QM3XPA")
        let key = try NativeExit.open(blob: h.sent[1].blob, lockId: saved.lockId, serverPrivate: serverPrivate.rawRepresentation)
        #expect(NativeExit.verifies(NativeExit.code(key: key, lockId: saved.lockId), lockId: saved.lockId, verifier: saved.verifier))
        #expect(!NativeExit.leaveCodeText(saved).contains("release this Mac"))
    }

    @Test func retriesOnlyOnNoAnswerAnd429And502And503And504WithLongWaits() {
        for status in [nil, 429, 502, 503, 504] as [Int?] {
            let store = InMemoryNativeLockStore(lost())
            let h = Harness(replies: [Reply(status: status), Reply(status: status), Reply(status: 201)])
            make(store: store, harness: h).registerMissingVerifier(for: lost())
            #expect(h.sent.count == 3, "\(String(describing: status))")
            #expect(h.sent[0].blob == h.sent[2].blob)
            #expect(h.waits.count == 2 && h.waits[0] >= 45 && h.waits[1] > h.waits[0])
            #expect(store.load()?.verifier != nil)
        }
        for status in [403, 422] as [Int?] {
            let store = InMemoryNativeLockStore(lost())
            let h = Harness(replies: [Reply(status: status)])
            let s = make(store: store, harness: h)
            s.registerMissingVerifier(for: lost())
            #expect(h.sent.count == 1 && h.waits.isEmpty)
            #expect(store.load()?.verifier == nil)
            #expect(s.registrar.status == .refused)
            s.registerMissingVerifier(for: lost())
            #expect(h.sent.count == 1)
            #expect(NativeExit.leaveCodeText(lost(), registration: .refused).contains("refused"))
        }
        for status in [500, 400, 408] as [Int?] {
            let store = InMemoryNativeLockStore(lost())
            let h = Harness(replies: [Reply(status: status), Reply(status: 201)])
            make(store: store, harness: h).registerMissingVerifier(for: lost())
            #expect(h.sent.count == 2 && h.waits.count == 1 && h.waits[0] >= 45, "\(String(describing: status))")
            #expect(store.load()?.verifier != nil)
        }
    }

    @Test func aRunIsBoundedThenPausesAndTheNextLeaveSheetOrTimerStartsItAgain() throws {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 500)], holdWaits: true)
        var s = make(store: store, harness: h)
        let clock = Clock(Date(timeIntervalSince1970: 50_000))
        s.now = { clock.date }
        s.registerMissingVerifier(for: lost())
        for _ in 0..<(NativeLockRegistrar.attemptsPerRun - 1) {
            let due = h.pending
            h.pending = []
            due.forEach { $0() }
        }
        #expect(h.sent.count == NativeLockRegistrar.attemptsPerRun)
        #expect(s.registrar.status == .paused)
        #expect(h.waits.last == NativeLockRegistrar.rearmInterval)
        #expect(NativeExit.leaveCodeText(lost(), registration: .paused).contains("try again later"))
        clock.date = clock.date.addingTimeInterval(100_000)
        h.replies = [Reply(status: 201)]
        h.holdWaits = false
        let snapshot = s.leaveSnapshot()
        #expect(store.load()?.verifier != nil)
        #expect(snapshot.lock?.lockId == "K7QM3XPA")
        #expect(s.registrar.status == .idle)
    }

    @Test func theRearmTimerRegistersAPausedLock() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 503)], holdWaits: true)
        var s = make(store: store, harness: h)
        let clock = Clock(Date(timeIntervalSince1970: 50_000))
        s.now = { clock.date }
        s.registerMissingVerifier(for: lost())
        for _ in 0..<(NativeLockRegistrar.attemptsPerRun - 1) {
            let due = h.pending
            h.pending = []
            due.forEach { $0() }
        }
        #expect(s.registrar.status == .paused && h.pending.count == 1)
        clock.date = clock.date.addingTimeInterval(100_000)
        h.replies = [Reply(status: 201)]
        h.pending.removeFirst()()
        #expect(store.load()?.verifier != nil)
    }

    @Test func aMissingServerKeyIsFinalAndSaidSo() {
        let h = Harness(replies: [Reply(status: 201)])
        let s = make(store: InMemoryNativeLockStore(lost()), harness: h, key: Data())
        s.registerMissingVerifier(for: lost())
        #expect(s.registrar.status == .noKey && h.sent.isEmpty)
        #expect(NativeExit.leaveCodeText(lost(), registration: .noKey).contains("no emergency exit key"))
        s.clear()
        #expect(s.registrar.status == .idle)
    }

    @Test func retryAfterIsHonouredAndTheBackoffIsCapped() {
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 0, retryAfter: 3600) == 3600)
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 0, retryAfter: 99_999) == 86_400)
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 0, retryAfter: 7_200) == 7_200)
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 1, retryAfter: nil) == 60)
        #expect(NativeLockRegistrar.wait(afterFailure: 30, random: 1, retryAfter: nil) == 900)
        #expect(NativeLockRegistrar.wait(afterFailure: 30, random: 0, retryAfter: 10) == 450)
    }

    @Test func aClearedLockNeverRegistersLater() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 503), Reply(status: 201)], holdWaits: true)
        let s = make(store: store, harness: h)
        s.registerMissingVerifier(for: lost())
        s.clear()
        try? store.save(lost("ABCDEFGH"))
        h.pending.forEach { $0() }
        #expect(h.sent.count == 1)
        #expect(store.load()?.verifier == nil)
        #expect(!s.registrar.isRetrying)
    }

    @Test func aReplyForAnotherSavedLockIsDropped() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 201)])
        var s = make(store: store, harness: h)
        let inner = s.registerTransport!
        s.registerTransport = { body, done in
            try? store.save(lost("ABCDEFGH"))
            inner(body, done)
        }
        s.registerMissingVerifier(for: lost())
        #expect(store.load()?.lockId == "ABCDEFGH")
        #expect(store.load()?.verifier == nil)
    }

    @Test func aLockWithAVerifierOrNoServerKeyIsNotRegistered() {
        let h = Harness(replies: [Reply(status: 201)])
        var withVerifier = lost()
        withVerifier.verifier = NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPA", iterations: 2_000)
        make(store: InMemoryNativeLockStore(withVerifier), harness: h).registerMissingVerifier(for: withVerifier)
        make(store: InMemoryNativeLockStore(lost()), harness: h, key: Data()).registerMissingVerifier(for: lost())
        #expect(h.sent.isEmpty)
    }

    @Test func keychainLostLockGetsItsVerifierBackAndIsNotLostAgain() throws {
        var state = lost()
        state.keychainLost = true
        let store = InMemoryNativeLockStore(state)
        let h = Harness(replies: [Reply(status: 201)])
        make(store: store, harness: h).registerMissingVerifier(for: state)
        let saved = try #require(store.load())
        #expect(saved.verifier != nil && !saved.keychainLost)
    }

    @Test func aSaveFailureAfter201RetriesWithTheSameBody() {
        final class FlakyStore: NativeLockStoring {
            var state: NativeLockState?
            var failures = 1
            struct Failure: Error {}
            init(_ s: NativeLockState) { state = s }
            func load() -> NativeLockState? { state }
            func save(_ s: NativeLockState) throws { if failures > 0 { failures -= 1; throw Failure() }; state = s }
            func delete() { state = nil }
        }
        let store = FlakyStore(lost())
        let h = Harness(replies: [Reply(status: 201)])
        make(store: store, harness: h).registerMissingVerifier(for: lost())
        #expect(h.sent.count == 2 && h.sent[0].blob == h.sent[1].blob)
        #expect(store.state?.verifier != nil)
    }

    @Test func leaveTextSaysKeepsTryingOnlyWhileRetrying() {
        let state = lost()
        #expect(NativeExit.leaveCodeText(state, registration: .retrying).contains("keeps trying"))
        #expect(!NativeExit.leaveCodeText(state).contains("keeps trying"))
        #expect(NativeExit.leaveCodeText(state).contains("release this Mac"))
    }

    @Test func retryAfterAcceptsSecondsAndHttpDatesAndNeverRoundsDown() {
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        func date(_ offset: TimeInterval, _ format: String) -> String {
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "GMT"); f.dateFormat = format
            return f.string(from: now.addingTimeInterval(offset))
        }
        #expect(NativeRenewSchedule.retryAfter("7200", now: now) == 7200)
        #expect(NativeRenewSchedule.retryAfter(date(7200, "EEE, dd MMM yyyy HH:mm:ss 'GMT'"), now: now) == 7200)
        #expect(NativeRenewSchedule.retryAfter(date(5400, "EEEE, dd-MMM-yy HH:mm:ss 'GMT'"), now: now) == 5400)
        #expect(NativeRenewSchedule.retryAfter(date(600, "EEE MMM d HH:mm:ss yyyy"), now: now) == 600)
        #expect(NativeRenewSchedule.retryAfter(date(-60, "EEE, dd MMM yyyy HH:mm:ss 'GMT'"), now: now) == 0)
        #expect(NativeRenewSchedule.retryAfter("Wed, 99 Foo 2026", now: now) == nil)
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 0, retryAfter: NativeRenewSchedule.retryAfter("7200", now: now)) == 7200)
        #expect(NativeLockRegistrar.wait(afterFailure: 1, random: 0, retryAfter: 10 * 86_400) == 86_400)
    }

    private final class KeychainDown: NativeLockStoring {
        var inner: NativeLockState?
        var down = true
        struct Failure: Error {}
        func load() -> NativeLockState? { inner }
        func save(_ state: NativeLockState) throws { if down { throw Failure() }; inner = state }
        func delete() { inner = nil }
    }

    @Test func aKeychainWriteThatFailsIsNotARegistrationAndTheLockStaysAsItWas() throws {
        var lostState = lost()
        lostState.keychainLost = true
        lostState.attempts = 7
        let keychain = KeychainDown()
        let file = InMemoryNativeLockStore(lostState)
        let dual = DualNativeLockStore(primary: keychain, secondary: file)
        let h = Harness(replies: [Reply(status: 201)], holdWaits: true)
        let s = make(store: dual, harness: h)
        s.registerMissingVerifier(for: lostState)
        #expect(h.sent.count == 1)
        #expect(keychain.load() == nil)
        #expect(dual.loadTrusted()?.verifier == nil)
        #expect(file.load() == lostState.withoutVerifier)
        #expect(s.registrar.isRetrying && h.pending.count == 1)
        keychain.down = false
        h.pending.removeFirst()()
        let saved = try #require(dual.loadTrusted())
        #expect(saved.verifier != nil && !saved.keychainLost && saved.lockId == "K7QM3XPA")
        #expect(h.sent.count == 2 && h.sent[0].blob == h.sent[1].blob)
        #expect(!s.registrar.isRetrying)
    }

    @Test func aFreshRegistrationLeavesAUsableBudgetWithoutResettingASameIdWait() {
        var s = lost()
        s.attempts = NativeExit.maxTotalAttempts
        s.until = Date(timeIntervalSince1970: 5_000)
        s.waitBoot = "b"; s.waitMono = 77
        s.guessLog = [.init(at: Date(timeIntervalSince1970: 1), boot: "b", mono: 1)]
        let kept = NativeLockSetup.budgetAfterRegistration(s)
        #expect(kept.attempts == NativeExit.maxTotalAttempts - NativeExit.maxWrong)
        #expect(kept.until == s.until && kept.waitBoot == "b" && kept.waitMono == 77 && kept.guessLog == s.guessLog)
    }

    @Test func afterKeychainLossAtTheCapTheCorrectCodeStillWorksOnceRegistered() throws {
        var capped = lost()
        capped.keychainLost = true
        capped.attempts = NativeExit.maxTotalAttempts
        let store = InMemoryNativeLockStore(capped)
        let h = Harness(replies: [Reply(status: 201)])
        make(store: store, harness: h).registerMissingVerifier(for: capped)
        let key = try NativeExit.open(blob: h.sent[0].blob, lockId: "K7QM3XPA", serverPrivate: serverPrivate.rawRepresentation)
        let code = NativeExit.code(key: key, lockId: "K7QM3XPA")
        let now = Date(timeIntervalSince1970: 2_000)
        #expect(NativeExit.attempt(typed: code, store: store, now: now, monotonic: { .init(boot: "b", seconds: 1) }) == .accepted)
    }

    @Test func aConflictReplacementKeepsTheWaitLeavesOneRoundAndBumpsTheCodeVersion() throws {
        var capped = lost()
        capped.keychainLost = true
        capped.attempts = NativeExit.maxTotalAttempts
        let store = InMemoryNativeLockStore(capped)
        let h = Harness(replies: [Reply(status: 409), Reply(status: 201)])
        let s = make(store: store, harness: h)
        let before = s.registrar.lockIdChanges
        s.registerMissingVerifier(for: capped)
        let saved = try #require(store.load())
        #expect(saved.lockId != "K7QM3XPA" && saved.attempts == NativeExit.maxTotalAttempts - NativeExit.maxWrong)
        #expect(s.registrar.lockIdChanges == before + 1)
    }

    @Test func aRequestIsOnlySentWhileTheSessionIsCurrentAndALateReplyAfterClearIsDiscarded() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 201)])
        var s = make(store: store, harness: h)
        let inner = s.registerTransport!
        var currentAtSend = false
        let probe = s
        s.registerTransport = { body, done in
            currentAtSend = probe.registrar.isRetrying
            inner(body) { status, data, after in
                probe.clear()
                done(status, data, after)
            }
        }
        s.registerMissingVerifier(for: lost())
        #expect(currentAtSend)
        #expect(store.load() == nil)
        #expect(!s.registrar.isRetrying)
    }

    private final class Clock { var date: Date; init(_ d: Date) { date = d } }

    private func verified(_ id: String, code: String, at t: Date) -> NativeLockState {
        NativeLockState(lockId: id, verifier: NativeExit.makeVerifier(code: code, lockId: id, iterations: 2_000), created: t)
    }

    @Test func deletingTheKeychainAndReRegisteringAfterAConflictKeepsTheWaitAndTheLedger() throws {
        let t0 = Date(timeIntervalSince1970: 100_000)
        let primary = InMemoryNativeLockStore(), secondary = InMemoryNativeLockStore()
        let store = DualNativeLockStore(primary: primary, secondary: secondary, now: { t0 }, monotonic: { .init(boot: "b", seconds: 10) })
        try store.save(verified("K7QM3XPA", code: "123456", at: t0))
        for n in 1...5 {
            _ = NativeExit.attempt(typed: String(format: "%06d", n), store: store, now: t0, monotonic: { .init(boot: "b", seconds: 10) })
        }
        primary.delete()
        let loaded = try #require(store.load())
        #expect(loaded.verifier == nil)
        let h = Harness(replies: [Reply(status: 409), Reply(status: 201)])
        make(store: store, harness: h).registerMissingVerifier(for: loaded)
        let saved = try #require(store.load())
        #expect(saved.lockId != "K7QM3XPA" && saved.verifier != nil)
        #expect(saved.guessLog.count == 5)
        let key = try NativeExit.open(blob: h.sent[1].blob, lockId: saved.lockId, serverPrivate: serverPrivate.rawRepresentation)
        let code = NativeExit.code(key: key, lockId: saved.lockId)
        let soon = t0.addingTimeInterval(20)
        guard case .wait = NativeExit.attempt(typed: "999999", store: store, now: soon, monotonic: { .init(boot: "b", seconds: 30) }) else {
            Issue.record("the wait was reset by the fresh id"); return
        }
        guard case .wait = NativeExit.attempt(typed: code, store: store, now: soon, monotonic: { .init(boot: "b", seconds: 30) }) else {
            Issue.record("the right code was tried during the wait"); return
        }
        let later = t0.addingTimeInterval(700)
        #expect(NativeExit.attempt(typed: code, store: store, now: later, monotonic: { .init(boot: "b", seconds: 710) }) == .accepted)
    }

    @Test func theHourlyLedgerCapsGuessesAcrossFreshIds() throws {
        let t0 = Date(timeIntervalSince1970: 100_000)
        var st = verified("K7QM3XPA", code: "123456", at: t0)
        st.guessLog = (0..<NativeExit.hourlyGuessCap).map { _ in .init(at: t0, boot: "b", mono: 10) }
        let store = InMemoryNativeLockStore(st)
        let r = NativeExit.attempt(typed: "123456", store: store, now: t0.addingTimeInterval(1_000), monotonic: { .init(boot: "b", seconds: 1_010) })
        #expect(r == .wait(until: t0.addingTimeInterval(3_600)))
        let wallMovedForward = NativeExit.attempt(typed: "123456", store: store, now: t0.addingTimeInterval(7_200), monotonic: { .init(boot: "b", seconds: 1_010) })
        guard case .wait = wallMovedForward else { Issue.record("a forward wall clock ended the window"); return }
        #expect(NativeExit.attempt(typed: "123456", store: store, now: t0.addingTimeInterval(3_700), monotonic: { .init(boot: "b", seconds: 4_000) }) == .accepted)
    }

    @Test func aRegistrationUnderTheSameIdBumpsTheSheetVersionAndFreesARefusedSheet() throws {
        var capped = lost()
        capped.keychainLost = true
        capped.attempts = NativeExit.maxTotalAttempts
        let store = InMemoryNativeLockStore(capped)
        let h = Harness(replies: [Reply(status: 201)])
        let s = make(store: store, harness: h)
        let before = s.registrar.lockIdChanges
        s.registerMissingVerifier(for: capped)
        #expect(store.load()?.lockId == "K7QM3XPA" && store.load()?.verifier != nil)
        #expect(s.registrar.lockIdChanges == before + 1)
        #expect(!NativeExit.isRefused(store.load()))
    }

    @Test func aPersistedHoldOffSchedulesItsOwnRetryAndClearCancelsIt() throws {
        let clock = Clock(Date(timeIntervalSince1970: 50_000))
        var held = lost()
        held.retryNotBefore = clock.date.addingTimeInterval(600)
        let store = InMemoryNativeLockStore(held)
        let h = Harness(replies: [Reply(status: 201)], holdWaits: true)
        var s = make(store: store, harness: h)
        s.now = { clock.date }
        s.registerMissingVerifier(for: held)
        #expect(h.sent.isEmpty && h.pending.count == 1)
        #expect(abs(h.waits[0] - 600) < 1)
        clock.date = clock.date.addingTimeInterval(601)
        h.pending.removeFirst()()
        #expect(h.sent.count == 1 && store.load()?.verifier != nil)

        var again = lost("ABCDEFGH")
        again.retryNotBefore = clock.date.addingTimeInterval(600)
        let other = InMemoryNativeLockStore(again)
        let h2 = Harness(replies: [Reply(status: 201)], holdWaits: true)
        var t = make(store: other, harness: h2)
        t.now = { clock.date }
        t.registerMissingVerifier(for: again)
        t.clear()
        clock.date = clock.date.addingTimeInterval(601)
        h2.pending.removeFirst()()
        #expect(h2.sent.isEmpty)
    }

    @Test func aLockClearedAfterTheSnapshotIsNotRegistered() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 201)])
        let s = make(store: store, harness: h)
        let stale = store.load()!
        s.clear()
        s.registerMissingVerifier(for: stale)
        #expect(h.sent.isEmpty && !s.registrar.isRetrying)
        try? store.save(lost("ZZZZZZZZ"))
        s.registerMissingVerifier(for: stale)
        #expect(h.sent.isEmpty)
    }

    @Test func theSnapshotVersionIsReadBeforeTheLockSoAChangeInsideItIsVisible() {
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 409), Reply(status: 201)])
        let s = make(store: store, harness: h)
        let snapshot = s.leaveSnapshot()
        #expect(snapshot.version == 0 && s.registrar.lockIdChanges == 1)
    }

    @Test func aRetryAfterHoldsBackEveryLaterRunUntilItsTime() throws {
        let clock = Clock(Date(timeIntervalSince1970: 50_000))
        let store = InMemoryNativeLockStore(lost())
        let h = Harness(replies: [Reply(status: 503, retryAfter: 7_200)], holdWaits: true)
        var s = make(store: store, harness: h)
        s.now = { clock.date }
        s.registerMissingVerifier(for: lost())
        while h.sent.count < NativeLockRegistrar.attemptsPerRun, !h.pending.isEmpty { h.pending.removeFirst()() }
        #expect(h.sent.count == NativeLockRegistrar.attemptsPerRun)
        #expect(!s.registrar.isRetrying)
        let until = try #require(store.load()?.retryNotBefore)
        #expect(until >= clock.date.addingTimeInterval(7_200))
        _ = s.leaveSnapshot()
        var again = make(store: store, harness: h)
        again.now = { clock.date }
        again.registerMissingVerifier(for: store.load()!)
        #expect(h.sent.count == NativeLockRegistrar.attemptsPerRun)
        clock.date = until.addingTimeInterval(1)
        _ = s.leaveSnapshot()
        #expect(h.sent.count > NativeLockRegistrar.attemptsPerRun)
    }
}
