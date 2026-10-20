import Foundation
import CryptoKit

/// Offline invigilator exit (CONTRACT.md "Offline exit"), same rule as the Mac app:
/// the locked iPad shows a random 8-char code; exit code = first 4 bytes of
/// HMAC-SHA256(K, "offline-exit|<CODE>") big-endian, mod 10^8, 8 digits.
/// K = this attempt's offline key from the server (memory only), never in the binary.
public struct OfflineExitCode {

    public static let alphabet = Array("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    public static let maxWrong = 5
    public static let waitAfterWrong: TimeInterval = 5 * 60

    public private(set) var challenge: String
    private var wrong = 0
    private var lockedUntil = Date.distantPast

    public init(challenge: String? = nil) {
        self.challenge = challenge ?? OfflineExitCode.randomChallenge()
    }

    public static func randomChallenge() -> String {
        var c = ""
        for _ in 0..<8 { c.append(alphabet[Int.random(in: 0..<alphabet.count)]) }
        return c
    }

    /// "K7QM-3XPA".
    public var challengeForDisplay: String { String(challenge.prefix(4)) + "-" + String(challenge.suffix(4)) }

    public static func expectedCode(for challenge: String, key: Data) -> String {
        let mac = Array(HMAC<SHA256>.authenticationCode(for: Data("offline-exit|\(challenge)".utf8),
                                                        using: SymmetricKey(data: key)))
        let n = (UInt32(mac[0]) << 24) | (UInt32(mac[1]) << 16) | (UInt32(mac[2]) << 8) | UInt32(mac[3])
        return String(format: "%08u", n % 100_000_000)
    }

    public enum Result: Equatable { case ok, wrong, tooMany, unavailable }

    /// 5 wrong codes -> wait `waitAfterWrong`, during which even the right code is refused.
    public mutating func check(_ typed: String, key: Data?, now: Date = Date()) -> Result {
        if now < lockedUntil { return .tooMany }
        let digits = typed.filter { !$0.isWhitespace && $0 != "-" }
        guard let key else { return .unavailable }
        if Signing.constantTimeEqual(digits, OfflineExitCode.expectedCode(for: challenge, key: key)) { return .ok }
        wrong += 1
        if wrong >= OfflineExitCode.maxWrong {
            wrong = 0
            lockedUntil = now.addingTimeInterval(OfflineExitCode.waitAfterWrong)
            return .tooMany
        }
        return .wrong
    }

    public func isWaiting(now: Date = Date()) -> Bool { now < lockedUntil }
}
