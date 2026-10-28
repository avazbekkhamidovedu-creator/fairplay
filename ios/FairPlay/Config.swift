import Foundation

/// Settings (same rules as the Mac app's Config.swift).
///
/// Release builds read NOTHING a student can change: server, app key and pins come from
/// FairPlayBuild.plist in the signed bundle (ios/configure-release.sh writes it); no fixed
/// exit code, no http. The only lock without Assessment Mode is the "watched" lock of a
/// test build whose plist says WatchedLock (the sideloaded .ipa on the GitHub release).
///
/// Dev builds (Debug configuration, FAIRPLAY_DEV) take the server from a launch argument
/// (scheme > Run > Arguments: -ServerURL http://192.168.1.20:8000). It is saved for
/// home-screen launches and shown on the connecting / offline screens, where
/// "Change server" edits or clears it.
enum Config {
    static let version = "0.7.7"

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

    /// Base64 body of the private CA certificate trusted for the exam server only.
    static var extraRootPEM: String? {
        #if FAIRPLAY_DEV
        return nil
        #else
        return (built["ExtraRootPEM"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        #endif
    }

    /// Read once at launch; never changes while the app runs.
    static let serverURL: URL = {
        var raw = (built["ServerURL"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? BuildConfig.serverURL
        #if FAIRPLAY_DEV
        if let env = ProcessInfo.processInfo.environment["PROCTORA_SERVER"], !env.isEmpty {
            raw = env
        } else if let d = DevServer.resolve() {
            raw = d
        }
        #endif
        return parseServer(raw) ?? URL(string: "https://server-not-configured.invalid")!
    }()

    /// "http(s)://host[:port][/path]" without trailing slashes, or nil.
    static func parseServer(_ raw: String) -> URL? {
        let trimmed = raw.trimmingCharacters(in: CharacterSet(charactersIn: "/ \n\t"))
        guard let u = URL(string: trimmed), let scheme = u.scheme?.lowercased(),
              scheme == "http" || scheme == "https", let host = u.host, !host.isEmpty else { return nil }
        return u
    }

    /// Short form for screens: "10.28.165.19:8000", "exam.speakupai.uz".
    static var serverLabel: String {
        (serverURL.host ?? serverURL.absoluteString) + (serverURL.port.map { ":\($0)" } ?? "")
    }

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
        return isWatchedBuild
        #endif
    }

    /// Test build for sideloading (FairPlayBuild.plist WatchedLock = true): without Apple's
    /// Assessment Mode permission it locks in "watched" mode. Its beats say aac=0, sam=0,
    /// soft=1, so the server and the teacher's live room count it as NOT locked; the
    /// integrity checks, events and hand-in rules are those of a release build.
    static var isWatchedBuild: Bool {
        #if FAIRPLAY_DEV
        return false
        #else
        return (built["WatchedLock"] as? Bool) == true
        #endif
    }

    /// Try Apple Assessment Mode. The simulator shows its consent prompt but can never
    /// finish turning it on (its assessmentagent can't write its policy store), so dev
    /// builds skip it there unless -Assessment YES. Elsewhere: unless the provisioning
    /// profile shows it lacks the entitlement.
    static var tryAssessmentMode: Bool {
        #if FAIRPLAY_DEV && targetEnvironment(simulator)
        return devFlag("Assessment", default: false)
        #else
        // A build signed without the Assessment Mode entitlement (free Apple ID, a
        // sideloaded test build) can never start it: don't make the student wait for it.
        return ProvisioningProfile.allows("com.apple.developer.automatic-assessment-configuration") != false
        #endif
    }

    /// Dev builds only: the screen may be mirrored / recorded while locked (QuickTime over
    /// the cable, AirPlay) so a test can be watched: -AllowCapture YES. Still reported.
    static var allowsCapture: Bool {
        #if FAIRPLAY_DEV
        return devFlag("AllowCapture", default: false)
        #else
        return false
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

/// Dev builds: the server address from the -ServerURL launch argument, remembered for
/// launches from the home screen. Only an argument changes it (an old value can't come
/// back on its own), and the connecting / offline screens show it with "Change server".
enum DevServer {
    static let savedKey = "FairPlayDevServerURL"
    private static let argumentKey = "ServerURL"

    static func resolve() -> String? {
        let d = UserDefaults.standard
        // Before 0.5.1 the argument itself was persisted under "ServerURL", so an old
        // address came back on every launch even after the argument was gone.
        let fromArgument = d.volatileDomain(forName: UserDefaults.argumentDomain)[argumentKey] as? String
        if d.persistentDomain(forName: Bundle.main.bundleIdentifier ?? "")?[argumentKey] != nil {
            d.removeObject(forKey: argumentKey)
        }
        if let a = fromArgument, Config.parseServer(a) != nil {
            d.set(a, forKey: savedKey)
            return a
        }
        if let s = d.string(forKey: savedKey), Config.parseServer(s) != nil { return s }
        return nil
    }

    /// nil = forget it (back to the built-in default). Takes effect on the next launch.
    static func save(_ value: String?) {
        if let value { UserDefaults.standard.set(value, forKey: savedKey) }
        else { UserDefaults.standard.removeObject(forKey: savedKey) }
    }
}

/// Entitlements of the provisioning profile this copy was signed with. nil = no profile in
/// the bundle (App Store / TestFlight, simulator) or unreadable: unknown.
enum ProvisioningProfile {
    private static let entitlements: [String: Any]? = {
        guard let url = Bundle.main.url(forResource: "embedded", withExtension: "mobileprovision"),
              let data = try? Data(contentsOf: url) else { return nil }
        // A CMS envelope around an XML plist: take the plist part.
        guard let start = data.range(of: Data("<?xml".utf8)),
              let end = data.range(of: Data("</plist>".utf8), in: start.lowerBound..<data.endIndex) else { return nil }
        let plist = data.subdata(in: start.lowerBound..<end.upperBound)
        let dict = (try? PropertyListSerialization.propertyList(from: plist, format: nil)) as? [String: Any]
        return dict?["Entitlements"] as? [String: Any]
    }()

    /// true / false when the profile says, nil when unknown.
    static func allows(_ key: String) -> Bool? {
        guard let e = entitlements else { return nil }
        if let b = e[key] as? Bool { return b }
        return e[key] != nil
    }
}
