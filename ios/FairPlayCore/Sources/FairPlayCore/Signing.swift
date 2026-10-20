import Foundation
import CryptoKit

/// Signed message formats shared with the server (server/app/app_client.py) and the Mac
/// app (mac/Sources/Proctora/AppSession.swift). Same strings, same HMAC, same checks.
public enum Signing {

    /// Lowercase hex HMAC-SHA256 (same as Python hmac.hexdigest()).
    public static func hmacHex(key: String, _ message: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data(message.utf8), using: SymmetricKey(data: Data(key.utf8)))
        return mac.map { String(format: "%02x", $0) }.joined()
    }

    public static func startMessage(nonce: String, deviceId: String) -> String { "start|\(nonce)|\(deviceId)" }

    public static func beatMessage(aid: String, session: String, deviceId: String, ts: Int64,
                                   locked: Bool, checks: String) -> String {
        "beat|\(aid)|\(session)|\(deviceId)|\(ts)|\(locked ? 1 : 0)|\(checks)"
    }

    public static func offkeyMessage(aid: String, session: String, deviceId: String, ts: Int64) -> String {
        "offkey|\(aid)|\(session)|\(deviceId)|\(ts)"
    }

    public static func handinMessage(aid: String, session: String, status: String, nonce: String) -> String {
        "handin|\(aid)|\(session)|\(status)|\(nonce)"
    }

    public static func exitMessage(aid: String, session: String, nonce: String) -> String {
        "exit|\(aid)|\(session)|ok|\(nonce)"
    }

    public static func offkeyResponseMessage(aid: String, session: String, keyHex: String, ts: Int64) -> String {
        "offkey-resp|\(aid)|\(session)|\(keyHex)|\(ts)"
    }

    /// ASCII letters, digits, "-" and "_" only (no "|" so signed messages stay unambiguous).
    public static func isSafeToken(_ s: String, min: Int, max: Int) -> Bool {
        guard s.count >= min, s.count <= max else { return false }
        return s.unicodeScalars.allSatisfy {
            ($0.value >= 48 && $0.value <= 57) || ($0.value >= 65 && $0.value <= 90)
                || ($0.value >= 97 && $0.value <= 122) || $0 == "-" || $0 == "_"
        }
    }

    public static func constantTimeEqual(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<x.count { diff |= x[i] ^ y[i] }
        return diff == 0
    }

    public static func data(hex: String) -> Data? {
        let chars = Array(hex.utf8)
        guard chars.count % 2 == 0 else { return nil }
        guard chars.allSatisfy({ ($0 >= 48 && $0 <= 57) || ($0 >= 65 && $0 <= 70) || ($0 >= 97 && $0 <= 102) })
        else { return nil }
        var out = Data(capacity: chars.count / 2)
        var i = 0
        while i < chars.count {
            guard let b = UInt8(String(decoding: chars[i..<i + 2], as: UTF8.self), radix: 16) else { return nil }
            out.append(b)
            i += 2
        }
        return out
    }

    public static func hex(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }

    /// The account id inside a FairPlay token ("<uid>:<role>:..." base64url, then "."
    /// signature). Not verified here: the server checks every request.
    public static func tokenUserId(_ token: String) -> String? {
        guard let payload = token.split(separator: ".").first else { return nil }
        var b64 = payload.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64), let text = String(data: data, encoding: .utf8) else { return nil }
        let uid = text.split(separator: ":").first.map(String.init) ?? ""
        return !uid.isEmpty && uid.allSatisfy(\.isNumber) ? uid : nil
    }

    /// While locked: same attempt and a token of the same student (Mac review 2026-10-14).
    public static func mayReplace(currentAttempt: String, currentToken: String, attemptId: String, token: String) -> Bool {
        guard currentAttempt == attemptId else { return false }
        // Review L11: an unreadable current token only accepts the very same token.
        guard let was = tokenUserId(currentToken) else { return constantTimeEqual(currentToken, token) }
        return tokenUserId(token) == was
    }

    /// Statuses that end the lock: handed in, attempt deleted, or moved to another device.
    public static func unlockAllowed(status: String) -> Bool { ["submitted", "gone", "moved"].contains(status) }

    public static func handInReplyIsGenuine(key: String, proofs: [String], attemptId: String, session: String,
                                            status: String, nonce: String) -> Bool {
        let expected = hmacHex(key: key, handinMessage(aid: attemptId, session: session, status: status, nonce: nonce))
        return proofs.contains { constantTimeEqual($0, expected) }
    }

    public static func exitReplyIsGenuine(key: String, proofs: [String], attemptId: String, session: String,
                                          nonce: String) -> Bool {
        let expected = hmacHex(key: key, exitMessage(aid: attemptId, session: session, nonce: nonce))
        return proofs.contains { constantTimeEqual($0, expected) }
    }

    /// Offline-key reply (review M2): required, never optional. Accepts the per-key
    /// `proofs` list, or the single `proof` older servers send.
    public static func offkeyReplyIsGenuine(key: String, json: [String: Any]?, attemptId: String, session: String,
                                            keyHex: String, ts: Int64) -> Bool {
        var proofs = (json?["proofs"] as? [String]) ?? []
        if let one = json?["proof"] as? String { proofs.append(one) }
        let expected = hmacHex(key: key, offkeyResponseMessage(aid: attemptId, session: session, keyHex: keyHex, ts: ts))
        return proofs.contains { constantTimeEqual($0, expected) }
    }

    public enum ExitResult: Equatable { case ok, wrong, tooMany, unreachable, noSession, serverError(Int) }

    /// The server's answer to an invigilator code.
    public static func exitResult(key: String, status: Int?, json: [String: Any]?, attemptId: String,
                                  session: String, nonce: String) -> ExitResult {
        switch status {
        case 200?:
            guard (json?["ok"] as? Bool) == true else { return .wrong }
            let proofs = (json?["proofs"] as? [String]) ?? []
            return exitReplyIsGenuine(key: key, proofs: proofs, attemptId: attemptId, session: session, nonce: nonce)
                ? .ok : .serverError(200)
        case 403?: return .wrong
        case 429?: return .tooMany
        case nil: return .unreachable
        case let other?: return .serverError(other)
        }
    }

    /// No answer, a server error or "busy": try again. 4xx is a definite no.
    public static func offlineKeyRetryable(_ status: Int?) -> Bool {
        guard let status else { return true }
        return status == 429 || status >= 500
    }

    /// iPad device id: "ipad-" + 32 hex of SHA-256("fairplay-device|<secret>|<app key>").
    /// `secret` is a random value kept in this iPad's Keychain (ThisDeviceOnly: not in
    /// backups, never restored onto another iPad), so copying the app does not carry the
    /// device over. Matches the server's device_id pattern ^[A-Za-z0-9-]+$, 8...64.
    public static func deviceId(secret: String, key: String) -> String {
        let digest = SHA256.hash(data: Data("fairplay-device|\(secret)|\(key)".utf8))
        return "ipad-" + digest.map { String(format: "%02x", $0) }.joined().prefix(32)
    }
}
