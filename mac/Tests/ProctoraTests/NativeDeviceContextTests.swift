import Foundation
import CryptoKit
import Testing
@testable import Proctora

@Suite struct NativeDeviceContextTests {
    @Test func teacherUnlockCannotCrossSessionOrDevice() throws {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "a", count: 64)
        let payload: [String: Any] = ["v": 2, "nonce": "one", "expires": 1030, "session": UUID().uuidString,
            "device": device, "user": "7", "contest": "exam", "participation": "12", "state": "released", "lease_until": 1000]
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        let envelope: [String: Any] = ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
        let now = Date(timeIntervalSince1970: 1000)
        let verified = try #require(NativeDeviceContext.verify(envelope, publicKey: key.publicKey.rawRepresentation, nonce: "one", device: device, now: now))
        #expect(verified.permitsUnlock(for: verified.binding))
        #expect(!verified.permitsUnlock(for: .init(session: UUID().uuidString, device: device, user: "7", contest: "exam")))
        #expect(NativeDeviceContext.verify(envelope, publicKey: key.publicKey.rawRepresentation, nonce: "two", device: device, now: now) == nil)
        #expect(NativeDeviceContext.verify(envelope, publicKey: key.publicKey.rawRepresentation, nonce: "one", device: String(repeating: "b", count: 64), now: now) == nil)
        #expect(NativeDeviceContext.verify(envelope, publicKey: key.publicKey.rawRepresentation, nonce: "one", device: device, now: now.addingTimeInterval(151)) == nil)
    }

    @Test func aMacClockOffByUpToTwoMinutesIsAcceptedAndBeyondThatIsNamed() throws {
        let key = Curve25519.Signing.PrivateKey()
        let pub = key.publicKey.rawRepresentation
        let device = String(repeating: "a", count: 64)
        let payload: [String: Any] = ["v": 2, "nonce": "one", "expires": 1030, "session": UUID().uuidString,
            "device": device, "user": "7", "contest": "exam", "participation": "12", "state": "active", "lease_until": 1000]
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        let envelope: [String: Any] = ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
        let server = Date(timeIntervalSince1970: 1000)
        for offset in [-100.0, -30, 0, 30, 100] {
            let mac = server.addingTimeInterval(offset)
            #expect(NativeDeviceContext.verify(envelope, publicKey: pub, nonce: "one", device: device, now: mac) != nil)
            #expect(!NativeDeviceContext.clockIsOff(envelope, publicKey: pub, nonce: "one", device: device, now: mac))
        }
        for offset in [-400.0, 200, 3_600] {
            let mac = server.addingTimeInterval(offset)
            #expect(NativeDeviceContext.verify(envelope, publicKey: pub, nonce: "one", device: device, now: mac) == nil)
            #expect(NativeDeviceContext.clockIsOff(envelope, publicKey: pub, nonce: "one", device: device, now: mac))
        }
        #expect(!NativeDeviceContext.clockIsOff(envelope, publicKey: pub, nonce: "two", device: device, now: server.addingTimeInterval(3_600)))
        let other = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation
        #expect(!NativeDeviceContext.clockIsOff(envelope, publicKey: other, nonce: "one", device: device, now: server.addingTimeInterval(3_600)))
        #expect(NativeDeviceContext.clockOffMessage == "Your Mac clock is off by more than 2 minutes. Ask your instructor for help.")
        #expect(!NativeDeviceContext.clockOffMessage.contains("Settings"))
    }
}
