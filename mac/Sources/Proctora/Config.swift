import Foundation

/// Settings (audit 2026-10-12 #4).
///
/// Release builds read NOTHING a student can change: the server, app key and pins come
/// from Contents/Resources/FairPlayBuild.plist, which build.sh writes into the bundle before
/// it is signed (BuildConfig.swift only holds dev defaults); there is no safety auto-unlock,
/// no window-level switch and no local invigilator code (the server checks it).
///
/// Dev builds (build.sh with FAIRPLAY_BUILD=dev, the default) keep the old overrides:
///   defaults write ai.proctora.student ServerURL http://192.168.1.20:8000
///   defaults write ai.proctora.student SafetyUnlockMinutes -int 0
enum Config {
    static let version = "0.8.5"

    /// Same wording as the web UI's appVersionLabel; version only.
    static func versionLabelText(version: String = Config.version, dev: Bool = Config.isDevBuild) -> String {
        "FairPlay \(version) · Mac" + (dev ? " · dev" : "")
    }

    #if FAIRPLAY_ISOLATED_TEST && !FAIRPLAY_DEV
    #error("FAIRPLAY_ISOLATED_TEST is permitted only in development builds")
    #endif
    #if FAIRPLAY_ISOLATED_TEST && FAIRPLAY_DEV
    static let isIsolatedTestProfile = true
    #else
    static let isIsolatedTestProfile = false
    #endif
    static let supportFolderName = isIsolatedTestProfile ? "FairPlay-PR4-Isolated" : "FairPlay"
    static let deviceKeyNamespace = isIsolatedTestProfile ? "ai.proctora.student.pr4-isolated.mac" : "ai.proctora.student.mac"
    static let rememberKeyService = isIsolatedTestProfile ? "FairPlay.pr4-isolated.homeworkRemember" : "FairPlay.homeworkRemember"


    #if FAIRPLAY_DEV
    static let isDevBuild = true
    #else
    static let isDevBuild = false
    #endif

    /// Audit M-14: what build.sh wrote into the signed bundle (falls back to dev defaults).
    private static let built: [String: Any] = {
        guard let url = Bundle.main.url(forResource: "FairPlayBuild", withExtension: "plist"),
              let data = try? Data(contentsOf: url),
              let dict = (try? PropertyListSerialization.propertyList(from: data, format: nil)) as? [String: Any]
        else { return [:] }
        return dict
    }()

    /// Public verification key; the private signing key stays on the university server.
    static var universityPolicyPublicKey: Data? {
        (built["UniversityPolicyPublicKey"] as? String).flatMap { Data(base64Encoded: $0) }
            ?? NativeBrowserProfile.bundled.flatMap { Data(base64Encoded: $0.policyKey) }
    }

    /// Off unless build.sh was run with FAIRPLAY_REQUIRE_DP_KEYCHAIN=1.
    static var requiresDataProtectionKeychain: Bool {
        (Bundle.main.object(forInfoDictionaryKey: "RequireDataProtectionKeychain") as? Bool) == true
    }

    /// X25519 public key (32 bytes) the per-lock exit key is sealed to; set by build.sh.
    static var nativeExitPublicKey: Data? {
        (built["NativeExitPublicKey"] as? String).flatMap { Data(base64Encoded: $0) }.flatMap { $0.count == 32 ? $0 : nil }
    }

    static var appKey: String { (built["AppKey"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? BuildConfig.appKey }
    static var pins: [String] { (built["Pins"] as? [String]) ?? BuildConfig.pins }
    static var extraRootPEM: String? { (built["ExtraRootPEM"] as? String).flatMap { $0.isEmpty ? nil : $0 } }
    static var debugLogPath: String { (built["DebugLogPath"] as? String) ?? BuildConfig.debugLogPath }

    /// Read once at launch; never changes while the app runs.
    static let serverURL: URL = {
        #if FAIRPLAY_ISOLATED_TEST && FAIRPLAY_DEV
        return URL(string: "http://127.0.0.1:18080")!
        #else
        var raw = (built["ServerURL"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? BuildConfig.serverURL
        #if FAIRPLAY_DEV
        if let env = ProcessInfo.processInfo.environment["PROCTORA_SERVER"], !env.isEmpty {
            raw = env
        } else if let d = UserDefaults.standard.string(forKey: "ServerURL"), !d.isEmpty {
            raw = d
        }
        #endif
        let trimmed = raw.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        return URL(string: trimmed) ?? URL(string: "https://server-not-configured.invalid")!
        #endif
    }()

    static var startURL: URL { serverURL.appendingPathComponent("student/login") }

    /// Release builds only talk to an https server with at least one pin (audit #11): without pins
    /// the app would fall back to the system trust store, which a user-installed root defeats.
    static var serverIsAcceptable: Bool {
        acceptable(isDev: isDevBuild, scheme: serverURL.scheme, pins: pins)
    }

    static func acceptable(isDev: Bool, scheme: String?, pins: [String]) -> Bool {
        isDev || (scheme?.lowercased() == "https" && !pins.isEmpty)
    }

    /// Dev-build safety net: unlock by itself after this long. Always 0 (never) in release.
    static var safetyUnlockMinutes: Double {
        #if FAIRPLAY_DEV
        let v = UserDefaults.standard.object(forKey: "SafetyUnlockMinutes") as? Double
        return v ?? 180
        #else
        return 0
        #endif
    }

    /// Dev only: keep the locked window at .floating so a debugger stays usable.
    static var floatingLockedWindow: Bool {
        #if FAIRPLAY_DEV
        return UserDefaults.standard.string(forKey: "WindowLevel") == "floating"
        #else
        return false
        #endif
    }
}
