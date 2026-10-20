import Foundation
import UIKit
import GameController
import MachO
import FairPlayCore

/// Layer 4: what the app measures every 3 s while locked (and once before locking).
/// The results go into the signed beats (DeviceState.summary), the blocking screen
/// and the integrity events the teacher sees.
@MainActor
enum IntegrityChecks {

    static func collect(window: UIWindow?) -> DeviceState {
        var s = DeviceState()
        s.guidedAccess = SingleAppMode.anyOn     // informational; LockdownController decides `singleApp`
        s.screens = screenCount()
        s.captured = isCaptured(window)
        #if FAIRPLAY_DEV
        // Dev builds only: the simulator can't be screen-recorded from inside iPadOS, so
        // this pretends it is, to test the red screen (-DevForceCapture YES).
        if UserDefaults.standard.bool(forKey: "DevForceCapture") { s.captured = true }
        #endif
        s.multitasking = isMultitasking(window)
        s.foreground = UIApplication.shared.applicationState == .active
        s.jailbreak = jailbreakSigns()
        s.debugger = debuggerAttached()
        #if targetEnvironment(simulator)
        s.simulator = true
        #endif
        s.hardwareKeyboard = GCKeyboard.coalesced != nil
        let net = networkPaths()
        s.vpn = net.vpn
        s.proxy = net.proxy
        UIDevice.current.isBatteryMonitoringEnabled = true
        let level = UIDevice.current.batteryLevel
        if level >= 0 { s.batteryPercent = Int((level * 100).rounded()) }
        s.charging = [.charging, .full].contains(UIDevice.current.batteryState)
        return s
    }

    /// This iPad's screen plus any external display (cable, AirPlay, Sidecar-like receivers).
    static func screenCount() -> Int {
        // Review L8: connected scenes only (openSessions keeps disconnected ones around).
        let external = UIApplication.shared.connectedScenes.filter {
            $0.session.role == .windowExternalDisplayNonInteractive && $0.activationState != .unattached
        }.count
        return max(1 + external, UIScreen.screens.count)
    }

    /// Screen recording, QuickTime capture over the cable, AirPlay or cable mirroring.
    static func isCaptured(_ window: UIWindow?) -> Bool {
        if let scene = window?.windowScene, scene.traitCollection.sceneCaptureState == .active { return true }
        return UIScreen.main.isCaptured
    }

    /// Split View / Slide Over / a Stage Manager window that doesn't fill the screen.
    static func isMultitasking(_ window: UIWindow?) -> Bool {
        guard let window, let screen = window.windowScene?.screen else { return false }
        let w = window.bounds.size, s = screen.bounds.size
        let a = abs(w.width - s.width) < 2 && abs(w.height - s.height) < 2
        let b = abs(w.width - s.height) < 2 && abs(w.height - s.width) < 2
        return !(a || b)
    }

    // MARK: - Jailbreak

    private static let jailbreakPaths = [
        "/Applications/Cydia.app", "/Applications/Sileo.app", "/Applications/Zebra.app", "/Applications/Filza.app",
        "/Applications/TrollStore.app", "/var/jb", "/var/binpack", "/.bootstrapped", "/.installed_dopamine",
        "/Library/MobileSubstrate/MobileSubstrate.dylib", "/usr/lib/libsubstitute.dylib", "/usr/lib/TweakInject",
        "/usr/sbin/sshd", "/usr/bin/ssh", "/bin/bash", "/etc/apt", "/private/var/lib/apt", "/private/var/lib/cydia",
    ]
    private static let hookLibraries = ["substrate", "substitute", "libhooker", "ellekit", "frida", "cycript",
                                        "sslkillswitch", "tweakinject", "libblackjack", "shadow", "flex"]

    /// Signs of a modified iPadOS (never meaningful in the simulator, which runs on macOS).
    static func jailbreakSigns() -> [String] {
        #if targetEnvironment(simulator)
        return []
        #else
        var out: [String] = []
        let fm = FileManager.default
        for p in jailbreakPaths where fm.fileExists(atPath: p) { out.append(p) }
        // The sandbox forbids writing outside the app's container.
        let probe = "/private/fairplay-\(UUID().uuidString)"
        if (try? "x".write(toFile: probe, atomically: false, encoding: .utf8)) != nil {
            out.append("sandbox writable")
            try? fm.removeItem(atPath: probe)
        }
        if let env = getenv("DYLD_INSERT_LIBRARIES"), !String(cString: env).isEmpty { out.append("DYLD_INSERT_LIBRARIES") }
        for i in 0..<_dyld_image_count() {
            guard let c = _dyld_get_image_name(i) else { continue }
            let name = String(cString: c).lowercased()
            if let hit = hookLibraries.first(where: { name.contains($0) }) { out.append("loaded \(hit)") }
        }
        for scheme in ["cydia", "sileo", "zbra", "filza"] {
            if let u = URL(string: "\(scheme)://"), UIApplication.shared.canOpenURL(u) { out.append("\(scheme) app") }
        }
        return Array(Set(out)).sorted()
        #endif
    }

    // MARK: - Debugger

    static func debuggerAttached() -> Bool {
        var info = kinfo_proc()
        var mib: [Int32] = [CTL_KERN, KERN_PROC, KERN_PROC_PID, getpid()]
        var size = MemoryLayout<kinfo_proc>.stride
        guard sysctl(&mib, UInt32(mib.count), &info, &size, nil, 0) == 0 else { return false }
        return (info.kp_proc.p_flag & P_TRACED) != 0
    }

    // MARK: - Network

    /// VPN tunnels (reported, allowed: campus networks need them) and HTTP proxies
    /// (reported; our own requests never use one and TLS pins catch interception).
    static func networkPaths() -> (vpn: Bool, proxy: Bool) {
        guard let dict = CFNetworkCopySystemProxySettings()?.takeRetainedValue() as? [String: Any] else { return (false, false) }
        let scoped = (dict["__SCOPED__"] as? [String: Any])?.keys ?? [String: Any]().keys
        let vpn = scoped.contains { k in ["tap", "tun", "ppp", "ipsec", "utun"].contains { k.lowercased().hasPrefix($0) } }
        let proxy = (dict["HTTPEnable"] as? Int) == 1 || (dict["HTTPSEnable"] as? Int) == 1
            || (dict["ProxyAutoConfigEnable"] as? Int) == 1
        return (vpn, proxy)
    }
}
