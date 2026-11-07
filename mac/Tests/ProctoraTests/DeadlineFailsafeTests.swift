import Foundation
import CryptoKit
import Testing
@testable import Proctora

@Suite struct DeadlineFailsafeTests {
    private func context(deadline: Int?, expires: Int = 1_000_000, state: String = "active", hold: Bool? = nil) throws -> NativeDeviceContext {
        var json: [String: Any] = ["v": 2, "nonce": "n", "expires": expires, "session": UUID().uuidString,
            "device": String(repeating: "a", count: 64), "user": "7", "contest": "exam", "participation": "1",
            "state": state, "lease_until": 0]
        if let deadline { json["deadline"] = deadline }
        if let hold { json["hold"] = hold }
        return try JSONDecoder().decode(NativeDeviceContext.self, from: JSONSerialization.data(withJSONObject: json))
    }
    private func mono(_ s: Double, boot: String = "b") -> NativeExit.Monotonic { .init(boot: boot, seconds: s) }
    private let grace = DeadlineFailsafe.grace

    @Test func dueOnlyAfterTheDeadlinePlusGraceOnTheMonotonicClock() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600), at: mono(500))
        #expect(!f.isDue(at: mono(500 + 600 + grace - 1)))
        #expect(f.isDue(at: mono(500 + 600 + grace)))
    }

    @Test func aNewerContextThatMovesTheDeadlinePostponesIt() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600), at: mono(0))
        f.accept(try context(deadline: 1_000_600 + 1800, expires: 1_000_020), at: mono(20))
        #expect(!f.isDue(at: mono(600 + grace + 100)))
        #expect(f.isDue(at: mono(20 + 2380 + grace)))
    }

    @Test func aNewerDeadlineReplacesAnOlderOneEvenWhenEarlier() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_005_000), at: mono(0))
        f.accept(try context(deadline: 1_000_100, expires: 1_000_020), at: mono(20))
        #expect(f.isDue(at: mono(20 + 80 + grace)))
    }

    @Test func theWallClockIsNeverRead() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600), at: mono(0))
        let before = f
        // Only the injected continuous clock moves it; there is no Date anywhere in its state.
        #expect(f == before)
        #expect(!f.isDue(at: mono(10)))
        #expect(f.isDue(at: mono(10_000)))
    }

    @Test func aDifferentBootSessionNeverFires() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600), at: mono(0, boot: "one"))
        #expect(!f.isDue(at: mono(1_000_000, boot: "two")))
    }

    @Test func noDeadlineOrAHoldOrAnotherStateIsOff() throws {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600), at: mono(0))
        f.accept(try context(deadline: nil), at: mono(20))
        #expect(!f.isArmed)
        f.accept(try context(deadline: 1_000_600), at: mono(0))
        f.accept(try context(deadline: 1_000_600, hold: true), at: mono(20))
        #expect(!f.isArmed && !f.isDue(at: mono(1e9)))
        f.accept(try context(deadline: 1_000_600, state: "finishing"), at: mono(0))
        #expect(!f.isArmed)
    }

    @Test func anUnsignedOrUnverifiedContextNeverReachesIt() throws {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "a", count: 64)
        let payload: [String: Any] = ["v": 2, "nonce": "n", "expires": 1030, "session": UUID().uuidString, "device": device,
            "user": "7", "contest": "exam", "participation": "1", "state": "active", "lease_until": 0, "deadline": 900]
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        let good: [String: Any] = ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
        let now = Date(timeIntervalSince1970: 1000), pub = key.publicKey.rawRepresentation
        #expect(NativeDeviceContext.verify(good, publicKey: pub, nonce: "n", device: device, now: now)?.deadline == 900)
        let other = Curve25519.Signing.PrivateKey()
        #expect(NativeDeviceContext.verify(good, publicKey: other.publicKey.rawRepresentation, nonce: "n", device: device, now: now) == nil)
        #expect(NativeDeviceContext.verify(good, publicKey: pub, nonce: "x", device: device, now: now) == nil)
        let unsigned: [String: Any] = ["payload": bytes.base64EncodedString()]
        #expect(NativeDeviceContext.verify(unsigned, publicKey: pub, nonce: "n", device: device, now: now) == nil)
        let absurd = try JSONSerialization.data(withJSONObject: payload.merging(["deadline": 9_999_999_999_999]) { $1 })
        let signedAbsurd: [String: Any] = ["payload": absurd.base64EncodedString(), "signature": try key.signature(for: absurd).base64EncodedString()]
        #expect(NativeDeviceContext.verify(signedAbsurd, publicKey: pub, nonce: "n", device: device, now: now) == nil)
    }
}

@Suite struct DeadlineProbeTests {
    private func context(deadline: Int, expires: Int) throws -> NativeDeviceContext {
        let json: [String: Any] = ["v": 2, "nonce": "n", "expires": expires, "session": UUID().uuidString,
            "device": String(repeating: "a", count: 64), "user": "7", "contest": "exam", "participation": "1",
            "state": "active", "lease_until": 0, "deadline": deadline]
        return try JSONDecoder().decode(NativeDeviceContext.self, from: JSONSerialization.data(withJSONObject: json))
    }
    private func mono(_ s: Double) -> NativeExit.Monotonic { .init(boot: "b", seconds: s) }
    private let grace = DeadlineFailsafe.grace
    // Server time = monotonic seconds + 1_000_000; the deadline is 600 s after the first context.
    private func armed() throws -> DeadlineFailsafe {
        var f = DeadlineFailsafe()
        f.accept(try context(deadline: 1_000_600, expires: 1_000_000), at: mono(0))
        return f
    }

    @Test func anUnverifiedAnswerNeverReleasesAndTheProbeRepeats() throws {
        var f = try armed()
        let due = 600 + grace
        #expect(f.decide(at: mono(due - 1)) == .wait)
        #expect(f.decide(at: mono(due + 1)) == .probe)
        // Transport errors and unverifiable replies never reach accept(): nothing changes, it keeps probing.
        for step in 1...20 { #expect(f.decide(at: mono(due + 1 + Double(step) * 60)) == .probe) }
    }

    @Test func aVerifiedAnswerThatMovedTheDeadlinePostpones() throws {
        var f = try armed()
        let due = 600 + grace
        #expect(f.decide(at: mono(due + 1)) == .probe)
        f.accept(try context(deadline: 1_000_600 + 1800, expires: 1_000_000 + Int(due) + 2), at: mono(due + 2))
        #expect(f.decide(at: mono(due + 3)) == .wait)
        #expect(f.decide(at: mono(due + 1790)) == .wait)
        #expect(f.decide(at: mono(due + 1800 + 10)) == .probe)
    }

    @Test func aVerifiedNoExtensionAnswerReleasesAtGrace() throws {
        var f = try armed()
        let due = 600 + grace
        #expect(f.decide(at: mono(due + 1)) == .probe)
        f.accept(try context(deadline: 1_000_600, expires: 1_000_000 + Int(due) + 2), at: mono(due + 2))
        #expect(f.decide(at: mono(due + 3)) == .release)
    }

    @Test func aVerifiedAnswerBeforeAnyProbeDoesNotReleaseByItself() throws {
        var f = try armed()
        let due = 600 + grace
        f.accept(try context(deadline: 1_000_600, expires: 1_000_000 + 100), at: mono(100))
        #expect(f.decide(at: mono(due + 1)) == .probe)
    }

    @Test func continuousFailureReleasesOnlyAfterTheHardBound() throws {
        var f = try armed()
        let due = 600 + grace
        #expect(f.decide(at: mono(due + 1)) == .probe)
        #expect(f.decide(at: mono(due + DeadlineFailsafe.hardBound - 1)) == .probe)
        #expect(f.decide(at: mono(due + DeadlineFailsafe.hardBound)) == .release)
        #expect(DeadlineFailsafe.hardBound == 7200)
    }
}
