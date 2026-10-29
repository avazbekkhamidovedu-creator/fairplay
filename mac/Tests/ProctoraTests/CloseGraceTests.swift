import Testing
import Foundation
@testable import Proctora

@Suite struct CloseGraceTests {
    @Test func freshlyAskedAppIsNotYetAProblem() {
        let t = Date()
        #expect(!LockdownController.closeGraceOver(askedAt: t, now: t))
        #expect(!LockdownController.closeGraceOver(askedAt: t, now: t.addingTimeInterval(1.9)))
        #expect(!LockdownController.closeGraceOver(askedAt: t, now: t.addingTimeInterval(2)))
    }

    @Test func appStillAliveAfterGraceIsAProblem() {
        let t = Date()
        #expect(LockdownController.closeGraceOver(askedAt: t, now: t.addingTimeInterval(2.1)))
    }
}
