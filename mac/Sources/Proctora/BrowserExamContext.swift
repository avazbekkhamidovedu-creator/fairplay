import Foundation
import CryptoKit

struct BrowserExamContext: Decodable, Equatable {
    let v: Int
    let nonce: String
    let expires: TimeInterval
    let user: String
    let contest: String
    let participation: String
    let state: String
    let sites: [String]
    var binding: String { user + ":" + contest + ":" + participation }
    static func verify(_ envelope: [String: Any], publicKey: Data, nonce: String, now: Date = Date()) -> Self? {
        guard let encoded = envelope["payload"] as? String, encoded.utf8.count <= 100_000,
              let bytes = Data(base64Encoded: encoded), let signatureText = envelope["signature"] as? String,
              let signature = Data(base64Encoded: signatureText),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: bytes),
              let value = try? JSONDecoder().decode(Self.self, from: bytes),
              value.v == 1, value.nonce == nonce,
              value.expires > now.timeIntervalSince1970, value.expires <= now.timeIntervalSince1970 + 60,
              !value.user.isEmpty, value.user.count <= 128,
              ["idle", "active", "completed"].contains(value.state),
              value.sites.count <= 32, value.sites.allSatisfy({ $0.utf8.count <= 2048 }),
              value.state == "idle" || (!value.contest.isEmpty && !value.participation.isEmpty),
              value.contest.count <= 128, value.participation.count <= 128 else { return nil }
        return value
    }
}
