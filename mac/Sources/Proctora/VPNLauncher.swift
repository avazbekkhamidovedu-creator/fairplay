import AppKit
import Network

/// Campus VPN clients FairPlay may open over the lock when the exam site cannot be reached.
/// Only these two Cisco apps.
enum VPNLauncher {
    struct Client: Equatable {
        let name: String
        let path: String
        let bundleId: String
    }

    static let clients = [
        Client(name: "Cisco Secure Client", path: "/Applications/Cisco/Cisco Secure Client.app", bundleId: "com.cisco.secureclient.gui"),
        Client(name: "Cisco AnyConnect", path: "/Applications/Cisco/Cisco AnyConnect Secure Mobility Client.app", bundleId: "com.cisco.Cisco-AnyConnect-Secure-Mobility-Client"),
    ]

    /// Cisco's Apple Team ID, read with codesign -dv from an installed Cisco Secure Client 5.x.
    /// The owner must confirm it (and any older AnyConnect Team ID) before release.
    static let ciscoTeamIDs: [String] = ["DE8Y96K9QP"]

    static var requirement: String {
        let teams = ciscoTeamIDs.map { "certificate leaf[subject.OU] = \"\($0)\"" }.joined(separator: " or ")
        return "anchor apple generic and (\(teams))"
    }

    static func isCiscoSigned(pid: pid_t) -> Bool { CodeSign.satisfies(pid: pid, requirement: requirement) }

    /// Only the two GUI apps, by exact id at their exact path, may sit in front of the lock.
    /// DART, the uninstallers and helpers have file dialogs and never qualify.
    static func mayHoldFront(bundleId: String?, bundleURL: URL?, signedByCisco: () -> Bool) -> Bool {
        guard let bundleId, let bundleURL else { return false }
        guard clients.contains(where: { $0.bundleId == bundleId && $0.path == bundleURL.standardizedFileURL.path }) else { return false }
        return signedByCisco()
    }

    static func isTrusted(path: String) -> Bool {
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(URL(fileURLWithPath: path) as CFURL, [], &code) == errSecSuccess, let code else { return false }
        var req: SecRequirement?
        guard SecRequirementCreateWithString(requirement as CFString, [], &req) == errSecSuccess, let req else { return false }
        return SecStaticCodeCheckValidity(code, SecCSFlags(rawValue: kSecCSCheckAllArchitectures), req) == errSecSuccess
    }

    static func installed(exists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) },
                          trusted: (String) -> Bool = VPNLauncher.isTrusted(path:)) -> Client? {
        clients.first { exists($0.path) && trusted($0.path) }
    }

    @discardableResult
    static func open(_ client: Client, workspace: NSWorkspace = .shared,
                     trusted: (String) -> Bool = VPNLauncher.isTrusted(path:)) -> Bool {
        guard trusted(client.path) else { return false }
        return workspace.open(URL(fileURLWithPath: client.path))
    }

    /// One VPN window may stay in front of the lock this long; all windows of one lock share `totalCap`.
    static let frontGrace: TimeInterval = 300
    static let totalCap: TimeInterval = 900
}

/// Time the VPN client may spend in front during one lock. Never renewed while a window is open,
/// and the total cannot be raised by pressing the button again.
struct VPNWindowBudget {
    private(set) var used: TimeInterval = 0

    init(used: TimeInterval = 0) { self.used = min(max(0, used), VPNLauncher.totalCap) }

    /// Used time plus what the open window may still use; this is what survives a relaunch.
    var committed: TimeInterval {
        guard let start = openedAt, let until = openUntil else { return used }
        return used + max(0, until.timeIntervalSince(start))
    }
    private(set) var openUntil: Date?
    private var openedAt: Date?

    mutating func request(now: Date, grace: TimeInterval = VPNLauncher.frontGrace, cap: TimeInterval = VPNLauncher.totalCap) -> Date? {
        if let until = openUntil, until <= now { close(now: now) }
        guard openUntil == nil else { return nil }
        let allowed = min(grace, cap - used)
        guard allowed > 0 else { return nil }
        openedAt = now
        openUntil = now.addingTimeInterval(allowed)
        return openUntil
    }

    mutating func isOpen(now: Date) -> Bool {
        if let until = openUntil, until <= now { close(now: now) }
        return openUntil != nil
    }

    mutating func close(now: Date) {
        guard let start = openedAt, let until = openUntil else { return }
        used += max(0, min(now, until).timeIntervalSince(start))
        openedAt = nil; openUntil = nil
    }

    func canOpen(now: Date, cap: TimeInterval = VPNLauncher.totalCap) -> Bool {
        if let until = openUntil, until > now { return false }
        return cap - used > 0
    }
}

/// Retry timing for a failed load of the exam site while the Mac is locked: quick at first, never slower than 30 s.
struct LoadRetryPolicy {
    static let delays: [TimeInterval] = [2, 4, 8, 15, 30]
    /// Failures in a row after which the VPN prompt is offered once.
    static let promptAfter = 2

    private(set) var failures = 0
    private(set) var prompted = false

    mutating func failed() -> TimeInterval {
        failures += 1
        return Self.delays[min(failures, Self.delays.count) - 1]
    }

    mutating func succeeded() { failures = 0 }

    /// True once per lock, when the site stays unreachable and a VPN client is installed.
    mutating func shouldPrompt(vpnInstalled: Bool) -> Bool {
        guard vpnInstalled, !prompted, failures >= Self.promptAfter else { return false }
        prompted = true
        return true
    }
}

/// Calls `onSatisfied` each time the Mac gets a usable network path (Wi-Fi joined, VPN connected).
final class NetworkWatch {
    private let monitor = NWPathMonitor()
    private var wasSatisfied: Bool?

    func start(onSatisfied: @escaping () -> Void) {
        monitor.pathUpdateHandler = { [weak self] path in
            let ok = path.status == .satisfied
            DispatchQueue.main.async {
                guard let self else { return }
                let changed = self.wasSatisfied != ok
                self.wasSatisfied = ok
                if ok && changed { onSatisfied() }
            }
        }
        monitor.start(queue: DispatchQueue(label: "fairplay.network-watch"))
    }

    func stop() { monitor.cancel() }
}
