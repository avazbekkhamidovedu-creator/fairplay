import Foundation
import CryptoKit
import Testing
@testable import Proctora

@Suite struct UnlockPathTests {
    private func envelope(state: String, nonce: String = "n1", session: String, device: String, key: Curve25519.Signing.PrivateKey) throws -> [String: Any] {
        let payload: [String: Any] = ["v": 2, "nonce": nonce, "expires": 1030, "session": session, "device": device, "user": "7",
                                      "contest": "exam", "participation": "12", "state": state, "lease_until": 1000]
        let bytes = try JSONSerialization.data(withJSONObject: payload)
        return ["payload": bytes.base64EncodedString(), "signature": try key.signature(for: bytes).base64EncodedString()]
    }

    /// A PC whose clock is an hour off must still get an instructor release (the nonce, not the clock, stops a replay).
    @Test func aReleaseReachesAMacWhoseClockIsFarOff() throws {
        let key = Curve25519.Signing.PrivateKey(), pub = key.publicKey.rawRepresentation
        let device = String(repeating: "a", count: 64), session = UUID().uuidString
        let binding = NativeDeviceContext.Binding(session: session, device: device, user: "7", contest: "exam")
        let env = try envelope(state: "released", session: session, device: device, key: key)
        let skewed = Date(timeIntervalSince1970: 1000 + 3_600)
        #expect(NativeDeviceContext.verify(env, publicKey: pub, nonce: "n1", device: device, now: skewed) == nil)   // the old behaviour: dropped
        let ok = NativeDeviceContext.verifyLive(env, publicKey: pub, nonce: "n1", device: device, binding: binding, now: skewed)
        #expect(ok?.permitsUnlock(for: binding) == true)
        // nothing else gets through the clock fallback
        #expect(NativeDeviceContext.verifyLive(env, publicKey: pub, nonce: "other", device: device, binding: binding, now: skewed) == nil)
        #expect(NativeDeviceContext.verifyLive(env, publicKey: pub, nonce: "n1", device: device, binding: nil, now: skewed) == nil)
        let another = NativeDeviceContext.Binding(session: UUID().uuidString, device: device, user: "7", contest: "exam")
        #expect(NativeDeviceContext.verifyLive(env, publicKey: pub, nonce: "n1", device: device, binding: another, now: skewed) == nil)
        let wrongKey = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation
        #expect(NativeDeviceContext.verifyLive(env, publicKey: wrongKey, nonce: "n1", device: device, binding: binding, now: skewed) == nil)
        for state in ["active", "finishing", "rejected"] {
            let e = try envelope(state: state, session: session, device: device, key: key)
            #expect(NativeDeviceContext.verifyLive(e, publicKey: pub, nonce: "n1", device: device, binding: binding, now: skewed) == nil)
        }
    }

    /// A teacher deleting the attempt makes the beat answer 404; the Windows app asks the signed hand-in check, the Mac did not.
    @Test func aBeatThatSaysGoneOrMovedAsksTheSignedHandInCheck() {
        #expect(AppSession.beatMeansSessionGone(409))
        #expect(AppSession.beatMeansSessionGone(404))
        #expect(!AppSession.beatMeansSessionGone(200))
        #expect(!AppSession.beatMeansSessionGone(500))
        #expect(!AppSession.beatMeansSessionGone(nil))
    }

    /// Documents a limit: the block-screen poller (MainWindowController.startRecoveryRelease) sends cookies: [], so the OJ (which needs the
    /// student's logged-in Django session and a CSRF token for the POST renew) cannot answer it. No remote release before the browser reopens.
    @Test func theBlockScreenPollSendsNoLoginSoTheOJCannotAnswerIt() {
        let request = ProtectedBrowserController.nativeRequest(
            endpoint: URL(string: "https://oj.example/api/fairplay/native/session/x/renew")!,
            origin: URL(string: "https://oj.example/")!, nonce: "n", unclean: false, lockId: nil, cookies: [])
        #expect(request.value(forHTTPHeaderField: "Cookie") == nil)
        #expect(request.value(forHTTPHeaderField: "X-CSRFToken") == nil)
    }
}
