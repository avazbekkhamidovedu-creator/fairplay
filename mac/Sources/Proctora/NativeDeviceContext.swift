import Foundation
import CryptoKit

/// A server command is scoped to one certificate, account, exam, and immutable session.
struct NativeDeviceContext: Decodable, Equatable {
    let v: Int
    let nonce: String
    let expires: Int
    let session: String
    let device: String
    let user: String
    let contest: String
    let participation: String
    let state: String
    let lease_until: Int
    var sites: [String]? = nil
    var resources: [String]? = nil
    /// Origins the OJ's own pages load subresources from (script, font and style CDNs).
    var assets: [String]? = nil
    /// This student's own deadline (unix seconds, extra time included); absent on an older OJ.
    var deadline: Int? = nil
    var hold: Bool? = nil

    struct Binding: Equatable {
        let session: String
        let device: String
        let user: String
        let contest: String
    }
    var binding: Binding { Binding(session: session, device: device, user: user, contest: contest) }
    func permitsUnlock(for expected: Binding) -> Bool {
        binding == expected && (state == "completed" || state == "released" || state == "rejected")
    }
    /// How far this Mac's clock may differ from the server's before a signed context is refused.
    static let allowedClockSkew = 120

    /// True when the envelope is genuine for this request and only the clock keeps it from being accepted.
    static func clockIsOff(_ envelope: [String: Any], publicKey: Data, nonce: String,
                           device: String, now: Date = Date()) -> Bool {
        verify(envelope, publicKey: publicKey, nonce: nonce, device: device, now: now) == nil
            && verify(envelope, publicKey: publicKey, nonce: nonce, device: device, now: now, skew: 86_400 * 365) != nil
    }

    static let clockOffMessage = "Your Mac clock is off by more than 2 minutes. Ask your instructor for help."

    static func verify(_ envelope: [String: Any], publicKey: Data, nonce: String,
                       device: String, now: Date = Date(), skew: Int = allowedClockSkew) -> Self? {
        let time = Int(now.timeIntervalSince1970)
        guard let encoded = envelope["payload"] as? String, encoded.utf8.count <= 16_384,
              let bytes = Data(base64Encoded: encoded), let signatureText = envelope["signature"] as? String,
              signatureText.utf8.count <= 128, let signature = Data(base64Encoded: signatureText), signature.count == 64,
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: bytes),
              let value = try? JSONDecoder().decode(Self.self, from: bytes), value.v == 2,
              value.nonce == nonce, value.device == device, device.count == 64,
              device.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              UUID(uuidString: value.session) != nil,
              value.expires > time - skew, value.expires <= time + 60 + skew,
              !value.user.isEmpty, value.user.count <= 128, !value.contest.isEmpty, value.contest.count <= 128,
              value.participation.count <= 128,
              (value.sites ?? []).count <= 32, (value.sites ?? []).allSatisfy({ $0.utf8.count <= 2048 }),
              (value.resources ?? []).count <= 64, (value.resources ?? []).allSatisfy({ $0.utf8.count <= 2048 }),
              ["active", "finishing", "completed", "released", "rejected"].contains(value.state),
              (value.assets ?? []).count <= 16, (value.assets ?? []).allSatisfy({ $0.utf8.count <= 300 }),
              (value.deadline ?? 0) >= 0, (value.deadline ?? 0) <= value.expires + 3_153_600_000,
              value.lease_until >= 0, value.lease_until <= value.expires else { return nil }
        return value
    }
}
