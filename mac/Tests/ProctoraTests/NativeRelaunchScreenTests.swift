import Foundation
import Testing
@testable import Proctora

@Suite struct NativeBlockScreenTests {
    private let start = Date(timeIntervalSince1970: 1_000)

    @Test func rebootWithNoSavedLockStartsNormallyAndShowsNothing() {
        #expect(NativeRelaunch.decide(store: InMemoryNativeLockStore()) == .normal)
        #expect(NativeBlockScreen.look(problems: [:], nativeLock: false, since: nil, now: start) == nil)
        #expect(NativeBlockScreen.look(problems: [:], nativeLock: true, since: nil, now: start) == nil)
    }

    @Test func savedLockShowsACalmScreenThatNeverAsksForACode() throws {
        let problems = ["proc": "These apps must be closed for the exam:\nMail"]
        let look = try #require(NativeBlockScreen.look(problems: problems, nativeLock: true, since: start,
                                                       now: start.addingTimeInterval(NativeBlockScreen.grace)))
        #expect(look.calm)
        #expect(!look.text.lowercased().contains("code"))
        #expect(!look.text.lowercased().contains("invigilator"))
        #expect(look.text.contains("Mail"))
    }

    @Test func savedLockShowsNothingDuringTheGraceSoAFlashCannotHappen() {
        let problems = ["proc": "Mail"]
        #expect(NativeBlockScreen.look(problems: problems, nativeLock: true, since: nil, now: start) == nil)
        #expect(NativeBlockScreen.look(problems: problems, nativeLock: true, since: start,
                                       now: start.addingTimeInterval(NativeBlockScreen.grace - 1)) == nil)
    }

    @Test func anExamLockKeepsItsRedScreenWithTheCodeHint() throws {
        let look = try #require(NativeBlockScreen.look(problems: ["proc": "Mail"], nativeLock: false, since: start, now: start))
        #expect(!look.calm)
        #expect(look.text.contains("exit code"))
    }
}

@Suite struct NativeRecoveryReleaseTests {
    private final class World {
        var mono = NativeExit.Monotonic(boot: "b", seconds: 0)
        var paths: [String] = []
        var answers: [(NativeDeviceContext?)] = []
        var scheduled: [() -> Void] = []
        var released = 0
        func deps() -> NativeRecoveryRelease.Deps {
            .init(request: { [self] path, done in
                paths.append(path)
                done(answers.isEmpty ? nil : answers.removeFirst())
                return true
            }, schedule: { [self] _, fire in scheduled.append(fire); return {} },
                  monotonic: { [self] in mono }, release: { [self] in released += 1 }, random: { 0.5 })
        }
        func fire() { let f = scheduled.removeLast(); scheduled.removeAll(); f() }
    }

    private func state(contest: String? = "exam", session: String = "s1") -> NativeLockState {
        var s = NativeLockState(lockId: NativeExit.newLockId(), created: Date(), unclean: true)
        s.contest = contest
        if contest != nil { s.binding = .init(session: session, device: "device", user: "7", contest: "exam") }
        return s
    }

    private func context(_ state: String, session: String = "s1", expires: Int = 1000, deadline: Int? = nil) -> NativeDeviceContext {
        NativeDeviceContext(v: 2, nonce: "n", expires: expires, session: session, device: "device", user: "7",
                            contest: "exam", participation: "1", state: state, lease_until: 1000, deadline: deadline)
    }

    @Test func aSignedReleaseUnlocksWithoutTheBrowser() {
        let w = World()
        w.answers = [context("released")]
        let poller = NativeRecoveryRelease(state: state(), deps: w.deps())
        poller.start()
        defer { poller.stop() }
        #expect(w.paths == ["session/s1/renew"])
        #expect(w.released == 1)
    }

    @Test func aStateForAnotherSessionNeverUnlocks() {
        let w = World()
        w.answers = [context("released", session: "other")]
        let poller = NativeRecoveryRelease(state: state(), deps: w.deps())
        poller.start()
        #expect(w.released == 0)
        #expect(poller.isRunning)
        #expect(w.scheduled.count == 1)
    }

    @Test func anActiveExamKeepsPollingAndStaysLocked() {
        let w = World()
        w.answers = [context("active", deadline: 5000), context("active", deadline: 5000)]
        let poller = NativeRecoveryRelease(state: state(), deps: w.deps())
        poller.start()
        defer { poller.stop() }
        w.fire()
        #expect(w.paths.count == 2)
        #expect(w.released == 0)
    }

    @Test func theDeadlineFailsafeReleasesWhenTheOJConfirmsAfterTheDeadline() {
        let w = World()
        w.answers = [context("active", deadline: 1000)]
        let poller = NativeRecoveryRelease(state: state(), deps: w.deps())
        poller.start()
        defer { poller.stop() }
        w.mono.seconds = 1300
        w.answers = [context("active", expires: 2300, deadline: 1000), context("active", expires: 2400, deadline: 1000)]
        w.fire()
        #expect(w.released == 0)
        w.mono.seconds = 1400
        w.fire()
        #expect(w.released == 1)
    }

    @Test func anUnreachableOJStillReleasesAfterTheHardBound() {
        let w = World()
        w.answers = [context("active", deadline: 1000)]
        let poller = NativeRecoveryRelease(state: state(), deps: w.deps())
        poller.start()
        defer { poller.stop() }
        w.mono.seconds = 1200 + DeadlineFailsafe.hardBound + 1
        w.fire()
        #expect(w.released == 1)
    }

    @Test func aSavedLockWithNoExamSendsNothing() {
        let w = World()
        let poller = NativeRecoveryRelease(state: state(contest: nil), deps: w.deps())
        poller.start()
        #expect(w.paths.isEmpty)
        #expect(w.released == 0)
    }
}
