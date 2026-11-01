import Foundation
import Security
import FairPlayCore

/// This iPad's FairPlay device id (Mac audit M-07 on iPad).
///
/// iPadOS has no hardware UUID for apps, and identifierForVendor changes on reinstall. A
/// random secret lives in the Keychain with kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly:
/// it survives reinstalls, is not in backups and never moves to another iPad. The server
/// sees only "ipad-" + a hash of it with the app key.
///
/// Review L5: the secret is created only when the Keychain says it doesn't exist. Any
/// other read error (e.g. before the first unlock after a restart) uses an id for this
/// launch only and leaves the stored secret alone, so the device id never changes for good.
enum DeviceIdentity {
    private static let service = "ai.proctora.student.ipad.device"
    private static let account = "device-secret"

    /// Whether the Keychain already held a device secret when this launch first looked:
    /// that is an install that may have a legacy id bound at the server.
    private static let loaded: (secret: String, evidence: LegacyEvidence) = loadOrCreateSecret()

    static let legacyDeviceId: String = Signing.deviceId(secret: loaded.secret, key: Config.appKey)

    static let binding: DeviceBinding = DeviceBinding.resolve(
        legacyId: legacyDeviceId, evidence: { loaded.evidence },
        store: KeychainDeviceKeyStore(namespace: "ai.proctora.student.ipad"))

    static var deviceId: String { binding.deviceId }

    private static var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private static func loadOrCreateSecret() -> (secret: String, evidence: LegacyEvidence) {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        if status == errSecSuccess, let data = out as? Data, let s = String(data: data, encoding: .utf8), s.count >= 32 {
            return (s, .present)
        }
        let secret = randomSecret()
        if status == errSecSuccess {
            // Re-review N7: an item that exists but holds no usable secret is replaced once,
            // so the id settles instead of changing on every launch.
            let update = [kSecValueData as String: Data(secret.utf8)] as CFDictionary
            if SecItemUpdate(baseQuery as CFDictionary, update) != errSecSuccess {
                DebugLog.log("app", "Keychain refused to repair the device secret; device id for this launch only")
            }
            return (secret, .present)
        }
        guard status == errSecItemNotFound else {
            DebugLog.log("app", "Keychain read failed (\(status)); device id for this launch only")
            return (secret, .unknown)
        }
        var add = baseQuery
        add[kSecValueData as String] = Data(secret.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        if SecItemAdd(add as CFDictionary, nil) != errSecSuccess {
            DebugLog.log("app", "Keychain refused the device secret; device id for this launch only")
        }
        return (secret, .absent)
    }

    private static func randomSecret() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        if SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) != errSecSuccess {
            // Never an all-zero secret: fall back to two system UUIDs (122 random bits each).
            return (UUID().uuidString + UUID().uuidString).replacingOccurrences(of: "-", with: "").lowercased()
        }
        return Signing.hex(Data(bytes))
    }
}
