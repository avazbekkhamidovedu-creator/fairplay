import Foundation
import CryptoKit

/// S5 (CONTRACT.md "Sealed offline submit"): answers frozen on the iPad when the hand-in at
/// time-up can't reach the server, uploaded later. Same format as the Mac (Sealed.swift).
public enum SealedFormat {
    public static let maxAnswersBytes = 5 * 1024 * 1024

    public static func sha256Hex(_ text: String) -> String { Signing.hex(Data(SHA256.hash(data: Data(text.utf8)))) }

    /// `attempt|session|device|client_seq|sealed_at|sha256(answers_json)`.
    public static func payload(attemptId: String, session: String, deviceId: String, clientSeq: Int64,
                               sealedAt: String, answersJSON: String) -> String {
        "\(attemptId)|\(session)|\(deviceId)|\(clientSeq)|\(sealedAt)|\(sha256Hex(answersJSON))"
    }

    /// HMAC-SHA256 with the attempt's offline key (the server recomputes it).
    public static func proof(payload: String, key: Data) -> String {
        Signing.hex(Data(HMAC<SHA256>.authenticationCode(for: Data(payload.utf8), using: SymmetricKey(data: key))))
    }

    /// The answers the page sends must be a JSON object (an answer map).
    public static func answersAreValid(_ json: String) -> Bool {
        json.utf8.count <= maxAnswersBytes
            && (try? JSONSerialization.jsonObject(with: Data(json.utf8))) is [String: Any]
    }

    public enum Outcome: Equatable { case done, keep, reject }

    /// After an upload answer: done (delete), keep (retry later), reject (set aside).
    public static func outcome(status: Int?) -> Outcome {
        switch status {
        case 200?: return .done
        case nil, 401?, 403?, 408?, 429?: return .keep      // offline, token expired, busy
        case let s? where s >= 500: return .keep
        default: return .reject
        }
    }
}

/// Server time from `X-Server-Time` plus monotonic time since, so moving the iPad's clock
/// back doesn't make sealed answers look on time (S2/S5). Thread-safe.
public final class ServerClock: @unchecked Sendable {
    public static let shared = ServerClock()
    public static let maxSkew: TimeInterval = 120

    private let lock = NSLock()
    private var server: Date?
    private var uptimeAt: TimeInterval = 0
    private var _offset: TimeInterval?

    public init() {}

    /// server time - iPad time at the last response (positive: the iPad is behind).
    public var offset: TimeInterval? { lock.lock(); defer { lock.unlock() }; return _offset }

    public func update(serverTime text: String, uptime: TimeInterval = ProcessInfo.processInfo.systemUptime,
                       now: Date = Date()) {
        guard let t = ServerClock.parse(text) else { return }
        lock.lock(); defer { lock.unlock() }
        server = t
        uptimeAt = uptime
        _offset = t.timeIntervalSince(now)
    }

    public func now(uptime: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Date {
        lock.lock(); defer { lock.unlock() }
        guard let server else { return Date() }
        return server.addingTimeInterval(uptime - uptimeAt)
    }

    public static func skewOK(_ offset: TimeInterval) -> Bool { abs(offset) <= maxSkew }

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    public static func parse(_ s: String) -> Date? { isoFrac.date(from: s) ?? ISO8601DateFormatter().date(from: s) }

    /// "2026-10-20T10:00:00.000Z" (UTC, ms, Z), as the server writes and expects.
    public static func iso(_ d: Date) -> String { isoFrac.string(from: d) }
}

/// S2: numeric compare by dot parts, same as server/app/app_version.py.
public enum AppVersion {
    public static func parts(_ v: String?) -> [Int] {
        var out = (v ?? "").trimmingCharacters(in: .whitespaces).split(separator: ".", omittingEmptySubsequences: false).map { raw -> Int in
            Int(raw.trimmingCharacters(in: .whitespaces).prefix { $0.isNumber }) ?? 0
        }
        while let last = out.last, last == 0 { out.removeLast() }
        return out
    }

    public static func meets(_ version: String?, minimum: String?) -> Bool {
        guard let minimum, !minimum.isEmpty else { return true }
        return !parts(version).lexicographicallyPrecedes(parts(minimum))
    }
}

/// S1 device check: one row of the preflight reply / POST /api/student/device-check.
public struct CheckRow: Equatable {
    public let id: String
    public let ok: Bool
    public let message: String
    public let info: Bool

    public init(id: String, ok: Bool, message: String, info: Bool = false) {
        self.id = id; self.ok = ok; self.message = String(message.prefix(300)); self.info = info
    }

    public var dict: [String: Any] {
        var d: [String: Any] = ["id": id, "ok": ok, "message": message]
        if info { d["info"] = true }
        return d
    }

    /// The iPad's rows: OS and app version, server, clock, the lock problems, battery, VPN.
    public static func ipadRows(problems: [(id: String, message: String)], appVersion: String, minVersion: String?,
                                osVersion: String, serverReached: Bool, clockOffset: TimeInterval?,
                                batteryPercent: Int?, charging: Bool, vpn: Bool) -> [CheckRow] {
        var rows: [CheckRow] = [CheckRow(id: "ipados_version", ok: true, message: "iPadOS \(osVersion)", info: true)]
        let versionOK = AppVersion.meets(appVersion, minimum: minVersion)
        rows.append(CheckRow(id: "app_version", ok: versionOK, message: versionOK
            ? "FairPlay \(appVersion)"
            : "FairPlay \(appVersion) is too old. Install version \(minVersion ?? "") or newer."))
        rows.append(CheckRow(id: "server", ok: serverReached, message: serverReached
            ? "The exam server answers"
            : "Cannot reach the exam server. Check the Wi-Fi (and the campus VPN if you need one)."))
        if let off = clockOffset {
            let ok = ServerClock.skewOK(off)
            rows.append(CheckRow(id: "clock", ok: ok, message: ok
                ? "Date and time are correct"
                : "This iPad's clock is \(Int(abs(off) / 60)) min off. Turn on Settings > General > Date & Time > Set Automatically."))
        } else {
            rows.append(CheckRow(id: "clock", ok: true, message: "Clock not checked (no server reply)", info: true))
        }
        rows.append(CheckRow(id: "assessment", ok: true, message: "Assessment Mode starts with the exam (tap Yes when iPadOS asks)", info: true))
        for p in problems { rows.append(CheckRow(id: p.id, ok: false, message: p.message)) }
        if let pct = batteryPercent {
            let ok = charging || pct >= 50
            rows.append(CheckRow(id: "battery", ok: ok, message: ok
                ? "Battery \(pct)%\(charging ? ", charging" : "")"
                : "Battery \(pct)% and not charging. Bring your charger.", info: true))
        }
        rows.append(CheckRow(id: "vpn", ok: true, message: vpn ? "VPN connected. Fine if your campus needs it." : "No VPN connected", info: true))
        return Array(rows.prefix(40))
    }
}
