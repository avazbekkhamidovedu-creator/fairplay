import Foundation
import CryptoKit
import Testing
@testable import Proctora

@Suite struct BrowserExamContextTests {
    @Test func requiresAuthenticFreshResponseForCurrentRequest() throws {
        let key = Curve25519.Signing.PrivateKey()
        let now = Date(timeIntervalSince1970: 1000)
        let payload: [String: Any] = ["v": 1, "nonce": "request-1", "expires": 1030,
            "user": "7", "contest": "exam", "participation": "12", "state": "completed", "sites": []]
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        var envelope: [String: Any] = ["payload": bytes.base64EncodedString(),
            "signature": try key.signature(for: bytes).base64EncodedString()]
        let publicKey = key.publicKey.rawRepresentation
        let verified = BrowserExamContext.verify(envelope, publicKey: publicKey, nonce: "request-1", now: now)
        #expect(verified?.binding == "7:exam:12")
        #expect(BrowserExamContext.verify(envelope, publicKey: publicKey, nonce: "other", now: now) == nil)
        #expect(BrowserExamContext.verify(envelope, publicKey: publicKey, nonce: "request-1", now: now.addingTimeInterval(31)) == nil)
        #expect(BrowserExamContext.verify(envelope, publicKey: Curve25519.Signing.PrivateKey().publicKey.rawRepresentation, nonce: "request-1", now: now) == nil)
        envelope["payload"] = Data("{}".utf8).base64EncodedString()
        #expect(BrowserExamContext.verify(envelope, publicKey: publicKey, nonce: "request-1", now: now) == nil)
    }
}
