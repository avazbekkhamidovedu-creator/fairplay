import Testing
import Foundation
@testable import Proctora

@Suite struct HomeworkRememberTests {

    private final class Gate {
        var result: RememberGateResult = .passed
        var asked: [String] = []
        func run(_ reason: String, _ done: @escaping (RememberGateResult) -> Void) { asked.append(reason); done(result) }
    }

    private func make(canGate: Bool = true, store: MemoryRememberStore = MemoryRememberStore(), gate: Gate = Gate())
        -> (HomeworkRemember, MemoryRememberStore, Gate) {
        (HomeworkRemember(store: store, canGate: { canGate }, gate: gate.run), store, gate)
    }

    private func unlock(_ r: HomeworkRemember, busy: Bool = false) -> [String: Any] {
        var out: [String: Any] = [:]
        r.handle(action: "rememberUnlock", body: [:], busy: busy) { out = $0 }
        return out
    }

    @Test func stateShowsAvailabilityAndNameNeverTheToken() {
        let (r, _, _) = make()
        #expect(r.stateValue["available"] as? Bool == true)
        #expect(r.stateValue["name"] is NSNull)
        r.handle(action: "rememberSave", body: ["token": "secret-token-value", "name": " Ava "], busy: false) { _ in }
        #expect(r.stateValue["name"] as? String == "Ava")
        #expect("\(r.stateValue)".contains("secret-token-value") == false)
    }

    @Test func unavailableWithoutGateOrStore() {
        let (a, _, _) = make(canGate: false)
        #expect(a.stateValue["available"] as? Bool == false)
        #expect(unlock(a)["error"] as? String == HomeworkRemember.unavailable)
        let store = MemoryRememberStore(); store.isWorking = false
        let (b, _, _) = make(store: store)
        #expect(b.stateValue["available"] as? Bool == false)
        #expect(b.stateValue["name"] is NSNull)
    }

    @Test func saveIsIgnoredWhenUnavailableOrMalformed() {
        let (a, sa, _) = make(canGate: false)
        a.handle(action: "rememberSave", body: ["token": "t", "name": "n"], busy: false) { _ in }
        #expect(sa.name() == nil)
        let (b, sb, _) = make()
        b.handle(action: "rememberSave", body: ["token": "", "name": "n"], busy: false) { _ in }
        b.handle(action: "rememberSave", body: ["token": String(repeating: "x", count: 5000), "name": "n"], busy: false) { _ in }
        b.handle(action: "rememberSave", body: ["token": 5, "name": "n"], busy: false) { _ in }
        #expect(sb.name() == nil)
    }

    @Test func saveReplacesTheOlderItem() throws {
        let (r, s, _) = make()
        r.handle(action: "rememberSave", body: ["token": "one", "name": "A"], busy: false) { _ in }
        r.handle(action: "rememberSave", body: ["token": "two", "name": "B"], busy: false) { _ in }
        #expect(try s.load() == "two")
        #expect(s.name() == "B")
    }

    @Test func unlockAnswersTokenAfterThePrompt() {
        let (r, _, g) = make()
        r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
        let out = unlock(r)
        #expect(out["type"] as? String == "rememberResult")
        #expect(out["token"] as? String == "tok")
        #expect(g.asked == ["Sign in to your homework"])
    }

    @Test func cancelledOrFailedPromptNeverReadsTheToken() {
        for result in [RememberGateResult.cancelled, .failed] {
            let g = Gate(); g.result = result
            let s = MemoryRememberStore(); s.failLoad = true
            let (r, _, _) = make(store: s, gate: g)
            r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
            let out = unlock(r)
            #expect(out["cancelled"] as? Bool == true)
            #expect(out["token"] == nil && out["error"] == nil)
        }
    }

    @Test func refusedDuringAnExamWithoutPrompting() {
        let (r, _, g) = make()
        r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
        #expect(unlock(r, busy: true)["error"] as? String == "Not available during an exam")
        #expect(g.asked.isEmpty)
    }

    @Test func nothingStoredAndStoreErrorsAnswerAnError() {
        let (r, s, _) = make()
        #expect(unlock(r)["error"] as? String == HomeworkRemember.nothingStored)
        r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
        s.failLoad = true
        #expect(unlock(r)["error"] as? String == HomeworkRemember.storeFailed)
    }

    @Test func forgetAlwaysWorksEvenWhenBusy() {
        let (r, s, _) = make()
        r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
        r.handle(action: "rememberForget", body: [:], busy: true) { _ in }
        #expect(s.name() == nil)
        #expect(r.stateValue["name"] is NSNull)
    }

    @Test func otherActionsAreNotHandled() {
        let (r, _, _) = make()
        #expect(r.handle(action: "hello", body: [:], busy: false) { _ in } == false)
    }

    @Test func savedResultAndFailedSaveForgetsTheOlderItem() throws {
        let (r, s, _) = make()
        var out: [String: Any] = [:]
        r.handle(action: "rememberSave", body: ["token": "one", "name": "A"], busy: false) { out = $0 }
        #expect(out["type"] as? String == "rememberResult")
        #expect(out["saved"] as? Bool == true)
        r.handle(action: "rememberSave", body: ["token": "", "name": "A"], busy: false) { out = $0 }
        #expect(out["saved"] as? Bool == false)
        #expect(try s.load() == nil)
        #expect(s.name() == nil)
    }

    @Test func throwingSaveForgetsTheOlderItem() throws {
        let s = FailingSaveStore()
        let r = HomeworkRemember(store: s, canGate: { true }, gate: { _, done in done(.passed) })
        s.preload("old", "Old")
        var out: [String: Any] = [:]
        r.handle(action: "rememberSave", body: ["token": "new", "name": "A"], busy: false) { out = $0 }
        #expect(out["saved"] as? Bool == false)
        #expect(try s.load() == nil)
    }

    @Test func unavailableSaveForgetsAndAnswersNotSaved() throws {
        let (r, s, _) = make(canGate: false)
        try s.save(token: "old", name: "Old")
        var out: [String: Any] = [:]
        r.handle(action: "rememberSave", body: ["token": "new", "name": "A"], busy: false) { out = $0 }
        #expect(out["saved"] as? Bool == false)
        #expect(s.name() == nil)
    }

    @Test func unlockRefusesWhenAnExamStartedDuringThePrompt() {
        var busy = false
        let g = Gate()
        let s = MemoryRememberStore()
        let r = HomeworkRemember(store: s, canGate: { true }, gate: { reason, done in busy = true; g.asked.append(reason); done(.passed) })
        r.handle(action: "rememberSave", body: ["token": "tok", "name": "A"], busy: false) { _ in }
        var out: [String: Any] = [:]
        r.handle(action: "rememberUnlock", body: [:], busy: busy) { out = $0 }
        #expect(out["token"] == nil)
        #expect(out["error"] as? String == HomeworkRemember.notDuringExam)
    }

    private final class FailingSaveStore: RememberStore {
        private var token: String?
        private var savedName: String?
        var isWorking = true
        func preload(_ t: String, _ n: String) { token = t; savedName = n }
        func save(token: String, name: String) throws { throw NSError(domain: "x", code: 1) }
        func load() throws -> String? { token }
        func forget() throws { token = nil; savedName = nil }
        func name() -> String? { token == nil ? nil : savedName }
    }
}
