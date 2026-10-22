import Foundation

/// Settings (same rules as the Mac app's Config.swift).
///
/// Release builds read NOTHING a student can change: server, app key and pins come from
/// FairPlayBuild.plist in the signed bundle (ios/configure-release.sh writes it); no dev
/// soft lock, no fixed exit code, no http.
///
/// Dev builds (Debug configuration, FAIRPLAY_DEV) take overrides from launch arguments or
/// defaults, e.g. in the scheme: -ServerURL http://192.168.1.20:8000
enum Config {
    static let version = "0.5"

    #if FAIRPLAY_DEV
    static let isDevBuild = true
    #else
    static let isDevBuild = false
    #endif

    private static let built: [String: Any] = {
        guard let url = Bundle.main.url(forResource: "FairPlayBuild", withExtension: "plist"),
              let data = try? Data(contentsOf: url),
              let dict = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
        else { return [:] }
        return dict
    }()

    /// Review M6: a dev build (soft lock, 0000 exit, Web Inspector, http) never carries the
    /// release key or pins, even when configure-release.sh left FairPlayBuild.plist in the
    /// folder: it can only talk to a FAIRPLAY_DEV=1 server.
    static var appKey: String {
        #if FAIRPLAY_DEV
        return BuildConfig.appKey
        #else
        return (built["AppKey"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? BuildConfig.appKey
        #endif
    }

    static var pins: [String] {
        #if FAIRPLAY_DEV
        return BuildConfig.pins
        #else
        return (built["Pins"] as? [String]) ?? BuildConfig.pins
        #endif
    }

    /// Read once at launch; never changes while the app runs.
    static let serverURL: URL = {
        var raw = (built["ServerURL"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? BuildConfig.serverURL
        #if FAIRPLAY_DEV
        if let env = ProcessInfo.processInfo.environment["PROCTORA_SERVER"], !env.isEmpty {
            raw = env
        } else if let d = UserDefaults.standard.string(forKey: "ServerURL"), !d.isEmpty {
            raw = d
            // Remember a -ServerURL launch argument, so opening the app from the iPad's home
            // screen (no arguments) still finds the Mac's server.
            UserDefaults.standard.set(d, forKey: "ServerURL")
        }
        #endif
        let trimmed = raw.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        return URL(string: trimmed) ?? URL(string: "https://server-not-configured.invalid")!
    }()

    static var startURL: URL { serverURL.appendingPathComponent("student/login") }

    /// Dev launch arguments arrive as strings ("-CaptureShield NO"); bool(forKey:)
    /// understands YES/NO/true/false/1/0 where `as? Bool` silently gave the default.
    private static func devFlag(_ key: String, default value: Bool) -> Bool {
        UserDefaults.standard.object(forKey: key) == nil ? value : UserDefaults.standard.bool(forKey: key)
    }

    /// Release builds only talk to an https server, with TLS pins and a real app key
    /// (review L6: a plist without pins used to switch pinning off silently).
    static var serverIsAcceptable: Bool {
        isDevBuild || (serverURL.scheme?.lowercased() == "https" && !pins.isEmpty && appKey != BuildConfig.appKey)
    }

    /// Dev builds only: lock without Apple Assessment Mode when it can't start (simulator,
    /// no entitlement in the provisioning profile). Watched and reported ("soft=1"), but
    /// not a real lock. Always false in release builds.
    static var allowsSoftLock: Bool {
        #if FAIRPLAY_DEV
        return devFlag("SoftLock", default: true)
        #else
        return false
        #endif
    }

    /// Try Apple Assessment Mode. The simulator shows its consent prompt but can never
    /// finish turning it on (its assessmentagent can't write its policy store), so dev
    /// builds skip it there unless -Assessment YES. Always true in release builds.
    static var tryAssessmentMode: Bool {
        #if FAIRPLAY_DEV && targetEnvironment(simulator)
        return devFlag("Assessment", default: false)
        #else
        return true
        #endif
    }

    /// Hide the exam from screenshots, screen recording and mirroring (CaptureShield).
    /// Dev builds can turn it off for simulator screenshots: -CaptureShield NO
    static var captureShield: Bool {
        #if FAIRPLAY_DEV
        return devFlag("CaptureShield", default: true)
        #else
        return true
        #endif
    }
}

// Dev defaults only. Never put real keys here: release builds get theirs from
// FairPlayBuild.plist (ios/configure-release.sh).
enum BuildConfig {
    /// The simulator shares the Mac's network, so 127.0.0.1 is the Mac's local server.
    static let serverURL = "http://127.0.0.1:8000"
    /// Public dev key, accepted by servers only with FAIRPLAY_DEV=1.
    static let appKey = "fairplay-dev-app-key-not-secret"
    static let pins: [String] = []
}
