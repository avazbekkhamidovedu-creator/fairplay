import Testing
import Foundation
@testable import FairPlayCore

@Suite struct GoneCheckTests {
    @Test func atMostOncePerTenSeconds() {
        var g = GoneCheck()
        let t0 = Date(timeIntervalSince1970: 5_000)
        let r = [g.shouldCheck(now: t0), g.shouldCheck(now: t0.addingTimeInterval(3)),
                 g.shouldCheck(now: t0.addingTimeInterval(10)), g.shouldCheck(now: t0.addingTimeInterval(10.5)),
                 g.shouldCheck(now: t0.addingTimeInterval(12))]
        #expect(r == [true, false, false, true, false])
    }

    @Test func statusNameMatchesTheOtherPlatforms() {
        #expect(GoneCheck.sessionGone == "session_gone")
    }
}
