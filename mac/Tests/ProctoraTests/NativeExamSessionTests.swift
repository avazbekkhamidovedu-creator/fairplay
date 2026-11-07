import Foundation
import Testing
@testable import Proctora

@Suite struct NativeExamSessionTests {
    private func context(_ state: String, session: String = "s1", user: String = "7") -> NativeDeviceContext {
        NativeDeviceContext(v: 2, nonce: "test", expires: 1030, session: session, device: "device",
                            user: user, contest: "exam", participation: "12", state: state, lease_until: 1000)
    }
    @Test func ambiguousClaimCannotUnlockOrSwitchExam() {
        var session = NativeExamSession()
        let result1 = session.beginClaim("exam")
        #expect(result1)
        #expect(session.requiresTerminalReceipt)
        #expect(!session.permitsUnlock)
        let result2 = !session.beginClaim("other-exam")
        #expect(result2)
        let result3 = !session.accept(context("completed"))
        #expect(result3)
    }
    @Test func terminalCommandMustMatchEstablishedSessionAndAccount() {
        var session = NativeExamSession()
        let result4 = session.beginClaim("exam")
        #expect(result4)
        let result5 = session.accept(context("active"))
        #expect(result5)
        let result6 = !session.accept(context("released", session: "another"))
        #expect(result6)
        let result7 = !session.accept(context("released", user: "8"))
        #expect(result7)
        let result8 = session.accept(context("finishing"))
        #expect(result8)
        #expect(!session.permitsUnlock)
        session.rejectedClaim()
        #expect(session.requiresTerminalReceipt)
        let result9 = session.accept(context("completed"))
        #expect(result9)
        #expect(session.permitsUnlock)
    }
    @Test func terminalStateWithoutAKnownBindingIsRefused() {
        var session = NativeExamSession()
        let claimed = session.beginClaim("exam")
        #expect(claimed)
        for state in ["finishing", "completed", "released", "rejected"] {
            let accepted = session.accept(context(state))
            #expect(!accepted, "\(state)")
        }
        #expect(!session.permitsUnlock)
    }

    @Test func restoredBindingResolvesALostClaimResponse() {
        let saved = NativeLockState.Binding(session: "s1", device: "device", user: "7", contest: "exam")
        var session = NativeExamSession(restoring: "exam", binding: saved)
        #expect(session.requiresTerminalReceipt)
        #expect(!session.permitsUnlock)
        let otherSession = session.accept(context("released", session: "other"))
        let otherUser = session.accept(context("released", user: "8"))
        #expect(!otherSession && !otherUser)
        #expect(!session.permitsUnlock)
        let matching = session.accept(context("released"))
        #expect(matching)
        #expect(session.permitsUnlock)
    }

    @Test func restoredBindingAcceptsOnlyAMatchingRejection() {
        let saved = NativeLockState.Binding(session: "s1", device: "device", user: "7", contest: "exam")
        var session = NativeExamSession(restoring: "exam", binding: saved)
        let other = session.accept(context("rejected", session: "other"))
        #expect(!other)
        #expect(!session.permitsUnlock)
    }

    @Test func rejectedRecoveryCannotReleaseAnEstablishedAttempt() {
        var session = NativeExamSession()
        let began = session.beginClaim("exam")
        #expect(began)
        let admitted = session.accept(context("active"))
        #expect(admitted)
        let rejected = session.accept(context("rejected"))
        #expect(!rejected)
        #expect(!session.permitsUnlock)
        #expect(session.requiresTerminalReceipt)
    }

}
