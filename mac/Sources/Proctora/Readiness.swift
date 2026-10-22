import Foundation
import IOKit.ps

/// Plan M5 (2026-10-20): the server's clock, carried forward with the monotonic clock.
///
/// Every server response has `X-Server-Time`. We keep the last one together with
/// `systemUptime` at that moment, so "now" for deadlines and sealing is
/// last server time + time elapsed since -- changing the Mac's clock changes nothing.
final class ServerClock {
    static let shared = ServerClock()
    static let maxSkew: TimeInterval = 120

    private let lock = NSLock()
    private var server: Date?
    private var uptimeAt: TimeInterval = 0
    /// server time - Mac time at the last response (positive: the Mac is behind).
    private(set) var offset: TimeInterval?

    func update(serverTime text: String) {
        guard let t = ServerClock.parse(text) else { return }
        lock.lock(); defer { lock.unlock() }
        server = t
        uptimeAt = ProcessInfo.processInfo.systemUptime
        offset = t.timeIntervalSince(Date())
    }

    /// Server-based now when we have heard from the server, else the Mac's clock.
    func now() -> Date {
        lock.lock(); defer { lock.unlock() }
        guard let server else { return Date() }
        return server.addingTimeInterval(ProcessInfo.processInfo.systemUptime - uptimeAt)
    }

    var known: Bool { lock.lock(); defer { lock.unlock() }; return server != nil }

    /// nil when unknown (no server reply yet).
    var skewOK: Bool? {
        lock.lock(); defer { lock.unlock() }
        return offset.map { ServerClock.skewOK($0) }
    }

    static func skewOK(_ offset: TimeInterval) -> Bool { abs(offset) <= maxSkew }

    /// ISO-8601 with or without fractional seconds.
    static func parse(_ text: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: text) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: text)
    }

    /// "2026-10-20T11:00:00.123Z" (UTC, milliseconds) -- the format sealed files use.
    static func iso(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: date)
    }
}

/// Dotted version numbers compared part by part: 0.10 > 0.9, 1.0 == 1.0.0.
enum AppVersion {
    static func compare(_ a: String, _ b: String) -> ComparisonResult {
        func parts(_ s: String) -> [Int] {
            s.split(separator: ".").map { Int($0.prefix { $0.isNumber }) ?? 0 }
        }
        let x = parts(a), y = parts(b)
        for i in 0..<max(x.count, y.count) {
            let l = i < x.count ? x[i] : 0, r = i < y.count ? y[i] : 0
            if l != r { return l < r ? .orderedAscending : .orderedDescending }
        }
        return .orderedSame
    }

    /// True when `version` is at least `minimum` (an empty minimum means no minimum).
    static func meets(_ version: String, minimum: String?) -> Bool {
        guard let minimum, !minimum.trimmingCharacters(in: .whitespaces).isEmpty else { return true }
        return compare(version, minimum) != .orderedAscending
    }
}

/// Plan M2: one row of "Check this Mac". `info` rows never block the exam.
struct CheckRow: Equatable {
    let id: String
    let ok: Bool
    let message: String
    var info: Bool = false

    var dict: [String: Any] { ["id": id, "ok": ok, "message": message, "info": info] }
}

/// Facts about the Mac that are not lock checks: macOS, disk, battery, VPN.
enum Readiness {
    static let minFreeBytes: Int64 = 500 * 1024 * 1024

    static var macOSVersion: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return "\(v.majorVersion).\(v.minorVersion).\(v.patchVersion)"
    }

    /// Free space where FairPlay keeps offline answers (Application Support).
    static func freeBytes() -> Int64? {
        let url = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory())
        let v = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return v?.volumeAvailableCapacityForImportantUsage
    }

    struct Battery: Equatable { let percent: Int; let charging: Bool }

    /// nil on a Mac without a battery.
    static func battery() -> Battery? {
        guard let r = batteryReading() else { return nil }
        return Battery(percent: r.current * 100 / r.max, charging: r.charging)
    }

    /// 2026-10-23: the exam top bar's battery, 0...1 (BatteryScript). nil without a battery.
    static func batteryLevel() -> (level: Double, charging: Bool)? {
        guard let r = batteryReading() else { return nil }
        return (Double(r.current) / Double(r.max), r.charging)
    }

    /// The internal battery's capacity (max > 0) and whether it is on the charger.
    private static func batteryReading() -> (current: Int, max: Int, charging: Bool)? {
        guard let blob = IOPSCopyPowerSourcesInfo()?.takeRetainedValue(),
              let list = IOPSCopyPowerSourcesList(blob)?.takeRetainedValue() as? [CFTypeRef] else { return nil }
        for ps in list {
            guard let d = IOPSGetPowerSourceDescription(blob, ps)?.takeUnretainedValue() as? [String: Any],
                  (d[kIOPSTypeKey] as? String) == kIOPSInternalBatteryType,
                  let cur = d[kIOPSCurrentCapacityKey] as? Int, let max = d[kIOPSMaxCapacityKey] as? Int, max > 0
            else { continue }
            let onAC = (d[kIOPSPowerSourceStateKey] as? String) == kIOPSACPowerValue
            return (cur, max, onAC || (d[kIOPSIsChargingKey] as? Bool) == true)
        }
        return nil
    }

    private static var powerChanged: (() -> Void)?
    private static var powerSource: CFRunLoopSource?

    /// 2026-10-23: calls `handler` on the main run loop whenever a power source changes
    /// (plugged in, unplugged, level). Once per app; a later call replaces the handler.
    static func observePowerChanges(_ handler: @escaping () -> Void) {
        powerChanged = handler
        guard powerSource == nil,
              let src = IOPSNotificationCreateRunLoopSource({ _ in Readiness.powerChanged?() }, nil)?.takeRetainedValue()
        else { return }
        CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
        powerSource = src
    }

    static func batteryOK(_ b: Battery?) -> Bool {
        guard let b else { return true }
        return b.charging || b.percent >= 50
    }

    /// Tunnel interfaces that are up with an IPv4/IPv6 address (utun*, ipsec*, ppp*).
    /// Informational: campus networks may require a VPN. macOS keeps a few idle utun
    /// interfaces (iCloud, Handoff) with only link-local IPv6, so those don't count.
    static func vpnInterfaces() -> [String] {
        var out = Set<String>()
        var head: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&head) == 0, let first = head else { return [] }
        defer { freeifaddrs(head) }
        var p: UnsafeMutablePointer<ifaddrs>? = first
        while let cur = p {
            let name = String(cString: cur.pointee.ifa_name)
            let up = (cur.pointee.ifa_flags & UInt32(IFF_UP)) != 0
            if up, isTunnelName(name), let sa = cur.pointee.ifa_addr {
                if sa.pointee.sa_family == UInt8(AF_INET) {
                    out.insert(name)
                } else if sa.pointee.sa_family == UInt8(AF_INET6) {
                    let v6 = UnsafeRawPointer(sa).assumingMemoryBound(to: sockaddr_in6.self).pointee
                    let b = v6.sin6_addr.__u6_addr.__u6_addr8
                    let linkLocal = b.0 == 0xfe && (b.1 & 0xc0) == 0x80
                    if !linkLocal { out.insert(name) }
                }
            }
            p = cur.pointee.ifa_next
        }
        return out.sorted()
    }

    static func isTunnelName(_ name: String) -> Bool {
        ["utun", "ipsec", "ppp", "tun", "tap"].contains { name.hasPrefix($0) && name.dropFirst($0.count).allSatisfy(\.isNumber) }
    }

    /// The rows for "Check this Mac" from what the server told us (nil: not reached).
    /// `problems` are the existing blocking preflight texts (lock checks).
    static func rows(problems: [(id: String, message: String)], serverReached: Bool,
                     minVersion: String?, clockOffset: TimeInterval?, freeBytes: Int64?,
                     battery: Battery?, vpn: [String], accessibility: Bool) -> [CheckRow] {
        var rows: [CheckRow] = []
        rows.append(CheckRow(id: "macos_version", ok: true, message: "macOS \(macOSVersion)"))
        let versionOK = AppVersion.meets(Config.version, minimum: minVersion)
        rows.append(CheckRow(id: "app_version", ok: versionOK, message: versionOK
            ? "FairPlay \(Config.version)"
            : "FairPlay \(Config.version) is too old. Install version \(minVersion ?? "") or newer."))
        rows.append(CheckRow(id: "server", ok: serverReached, message: serverReached
            ? "The exam server answers"
            : "Cannot reach the exam server. Check the Wi-Fi (and the campus VPN if you need one)."))
        if let off = clockOffset {
            let ok = ServerClock.skewOK(off)
            rows.append(CheckRow(id: "clock", ok: ok, message: ok
                ? "Date and time are correct"
                : "This Mac's clock is \(Int(abs(off) / 60)) min off. Set the date and time to automatic (System Settings > General > Date & Time)."))
        } else {
            rows.append(CheckRow(id: "clock", ok: true, message: "Clock not checked (no server reply)", info: true))
        }
        rows.append(CheckRow(id: "accessibility", ok: accessibility, message: accessibility
            ? "FairPlay may lock this Mac"
            : "Turn on FairPlay in System Settings > Privacy & Security > Accessibility."))
        let diskOK = (freeBytes ?? minFreeBytes) >= minFreeBytes
        rows.append(CheckRow(id: "disk", ok: diskOK, message: diskOK
            ? "Enough free disk space"
            : "Less than 500 MB free. Free up some disk space so answers can be kept offline."))
        if let b = battery {
            let ok = batteryOK(b)
            rows.append(CheckRow(id: "battery", ok: ok, message: ok
                ? "Battery \(b.percent)%\(b.charging ? ", charging" : "")"
                : "Battery \(b.percent)% and not charging. Bring your charger.", info: true))
        }
        rows.append(CheckRow(id: "vpn", ok: true, message: vpn.isEmpty
            ? "No VPN connected"
            : "VPN connected (\(vpn.joined(separator: ", "))). Fine if your campus needs it.", info: true))
        // Several problems may share an id ("blocked_apps" per service): one row each id.
        var order: [String] = [], texts: [String: [String]] = [:]
        for p in problems {
            if texts[p.id] == nil { order.append(p.id) }
            texts[p.id, default: []].append(p.message)
        }
        for id in order {
            let message = texts[id]!.joined(separator: " ")
            if let i = rows.firstIndex(where: { $0.id == id }) {
                rows[i] = CheckRow(id: id, ok: false, message: message, info: false)
            } else {
                rows.append(CheckRow(id: id, ok: false, message: message))
            }
        }
        for id in ["displays", "blocked_apps", "input_source", "vm", "remote"]
        where !rows.contains(where: { $0.id == id }) {
            rows.append(CheckRow(id: id, ok: true, message: okText(id)))
        }
        return rows
    }

    private static func okText(_ id: String) -> String {
        switch id {
        case "displays": return "One screen"
        case "blocked_apps": return "No sharing or remote-control services on"
        case "input_source": return "Keyboard input is allowed"
        case "vm": return "Not a virtual machine"
        case "remote": return "Used at the Mac itself"
        default: return "OK"
        }
    }
}
