import Foundation
import CryptoKit
import Security

/// Per-device P-256 key (docs/device-key.md). The private half stays in the Secure Enclave
/// when there is one, otherwise in a non-extractable Keychain key. The Mac app compiles the
/// same file (mac/Sources/Proctora/DeviceKey.swift); keep the two identical.
public protocol DeviceKeyBacking {
    var publicKeyDER: Data { get }
    var hardwareBacked: Bool { get }
    func signDER(_ message: Data) throws -> Data
}

public protocol DeviceKeyStore {
    func load() throws -> DeviceKeyBacking?
    func create() throws -> DeviceKeyBacking
    func readMode() throws -> String?
    func writeMode(_ mode: String) throws
}

public struct DeviceKeyError: Error, CustomStringConvertible {
    public let description: String
    public init(_ description: String) { self.description = description }
}

public final class DeviceKey {
    public static let domain = "fp-device-v1|"

    private let backing: DeviceKeyBacking
    public var publicKeyDER: Data { backing.publicKeyDER }
    public var hardwareBacked: Bool { backing.hardwareBacked }

    public init(_ backing: DeviceKeyBacking) { self.backing = backing }

    public var derivedDeviceId: String { DeviceKey.deviceId(publicKeyDER: publicKeyDER) }
    public var publicKeyBase64URL: String { DeviceKey.base64URL(publicKeyDER) }

    /// First 20 bytes of SHA-256(DER SubjectPublicKeyInfo), lowercase hex (40 chars).
    public static func deviceId(publicKeyDER: Data) -> String {
        SHA256.hash(data: publicKeyDER).prefix(20).map { String(format: "%02x", $0) }.joined()
    }

    /// ECDSA-P256-SHA256 over "fp-device-v1|" + message, ASN.1 DER.
    public func sign(_ message: String) throws -> Data {
        try backing.signDER(Data((DeviceKey.domain + message).utf8))
    }

    public func signatureBase64URL(_ message: String) throws -> String {
        DeviceKey.base64URL(try sign(message))
    }

    public static func base64URL(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
}

/// What an earlier FairPlay install left on this device (decides the id, see DeviceBinding).
public enum LegacyEvidence { case present, absent, unknown }

public struct DeviceProof {
    public let deviceId: String
    public let publicKey: String?
    public let signature: String?
}

/// The device id to use and the key that goes with it. An install that already had a
/// legacy id keeps sending it together with the key (the server upgrades the bind once);
/// only a brand-new install uses the derived id. The choice is stored before the key is
/// created and never changes. Any failure leaves `key` nil: the app then behaves as before
/// (legacy id, no key fields) and `failure` is reported as a client event.
public struct DeviceBinding {
    public static let legacyMode = "legacy"
    public static let derivedMode = "derived"

    public let deviceId: String
    public let key: DeviceKey?
    public let failure: String?
    let legacyId: String

    public static func resolve(legacyId: String, evidence: () -> LegacyEvidence,
                               store: DeviceKeyStore) -> DeviceBinding {
        func fallback(_ why: String) -> DeviceBinding {
            DeviceBinding(deviceId: legacyId, key: nil, failure: why, legacyId: legacyId)
        }
        do {
            var mode = try store.readMode()
            if mode == nil {
                switch evidence() {
                case .unknown: return fallback("earlier install state unreadable")
                case .present: mode = legacyMode
                case .absent: mode = derivedMode
                }
                try store.writeMode(mode!)
            }
            let backing = try store.load() ?? store.create()
            let key = DeviceKey(backing)
            let id = mode == legacyMode ? legacyId : key.derivedDeviceId
            return DeviceBinding(deviceId: id, key: key, failure: nil, legacyId: legacyId)
        } catch {
            return fallback("\(error)")
        }
    }

    /// Fields for the start / trial / code-login proof. `message` builds the signed start
    /// message for a device id. If the key cannot sign, the proof is the legacy one.
    public func proof(message: (String) -> String) -> DeviceProof {
        guard let key else { return DeviceProof(deviceId: legacyId, publicKey: nil, signature: nil) }
        do {
            return DeviceProof(deviceId: deviceId, publicKey: key.publicKeyBase64URL,
                               signature: try key.signatureBase64URL(message(deviceId)))
        } catch {
            return DeviceProof(deviceId: legacyId, publicKey: nil, signature: nil)
        }
    }
}

/// Keychain-backed store. `namespace` separates the key tag and the mode item.
public final class KeychainDeviceKeyStore: DeviceKeyStore {
    private let tag: Data
    private let modeService: String

    public init(namespace: String) {
        tag = Data("\(namespace).devicekey.v1".utf8)
        modeService = "\(namespace).devicekey.mode"
    }

    private static let spkiP256Prefix = Data([
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01,
        0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ])

    private final class Backing: DeviceKeyBacking {
        let key: SecKey
        let publicKeyDER: Data
        let hardwareBacked: Bool

        init(key: SecKey, hardwareBacked: Bool) throws {
            guard let pub = SecKeyCopyPublicKey(key),
                  let raw = SecKeyCopyExternalRepresentation(pub, nil) as Data?,
                  raw.count == 65, raw.first == 0x04 else { throw DeviceKeyError("public key unreadable") }
            self.key = key
            self.hardwareBacked = hardwareBacked
            publicKeyDER = KeychainDeviceKeyStore.spkiP256Prefix + raw
        }

        func signDER(_ message: Data) throws -> Data {
            var err: Unmanaged<CFError>?
            guard let sig = SecKeyCreateSignature(key, .ecdsaSignatureMessageX962SHA256, message as CFData, &err) as Data? else {
                throw DeviceKeyError("sign failed: \(err?.takeRetainedValue().localizedDescription ?? "unknown")")
            }
            return sig
        }
    }

    private func keyQuery(dataProtection: Bool) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassKey,
                                kSecAttrApplicationTag as String: tag,
                                kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                kSecReturnRef as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        #if os(macOS)
        if dataProtection { q[kSecUseDataProtectionKeychain as String] = true }
        #endif
        return q
    }

    public func load() throws -> DeviceKeyBacking? {
        #if os(macOS)
        let scopes = [true, false]
        #else
        let scopes = [false]
        #endif
        for scope in scopes {
            var out: CFTypeRef?
            let status = SecItemCopyMatching(keyQuery(dataProtection: scope) as CFDictionary, &out)
            if status == errSecSuccess, let out {
                let key = out as! SecKey
                let attrs = SecKeyCopyAttributes(key) as? [String: Any]
                let hw = (attrs?[kSecAttrTokenID as String] as? String) == (kSecAttrTokenIDSecureEnclave as String)
                return try Backing(key: key, hardwareBacked: hw)
            }
            if status != errSecItemNotFound && status != errSecMissingEntitlement {
                throw DeviceKeyError("keychain read failed (\(status))")
            }
        }
        return nil
    }

    public func create() throws -> DeviceKeyBacking {
        if let key = createSecureEnclave() { return try Backing(key: key, hardwareBacked: true) }
        return try Backing(key: try createSoftware(), hardwareBacked: false)
    }

    private func createSecureEnclave() -> SecKey? {
        guard let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly, .privateKeyUsage, nil) else { return nil }
        var attrs: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecPrivateKeyAttrs as String: [kSecAttrIsPermanent as String: true,
                                            kSecAttrApplicationTag as String: tag,
                                            kSecAttrAccessControl as String: access] as [String: Any],
        ]
        #if os(macOS)
        attrs[kSecUseDataProtectionKeychain as String] = true
        #endif
        return SecKeyCreateRandomKey(attrs as CFDictionary, nil)
    }

    private func createSoftware() throws -> SecKey {
        var priv: [String: Any] = [kSecAttrIsPermanent as String: true,
                                   kSecAttrApplicationTag as String: tag,
                                   kSecAttrIsExtractable as String: false]
        #if os(iOS)
        priv[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        #endif
        let attrs: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                    kSecAttrKeySizeInBits as String: 256,
                                    kSecPrivateKeyAttrs as String: priv]
        var err: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attrs as CFDictionary, &err) else {
            throw DeviceKeyError("key creation failed: \(err?.takeRetainedValue().localizedDescription ?? "unknown")")
        }
        return key
    }

    private var modeQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: modeService,
         kSecAttrAccount as String: "mode"]
    }

    public func readMode() throws -> String? {
        var q = modeQuery
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = out as? Data, let s = String(data: data, encoding: .utf8),
              s == DeviceBinding.legacyMode || s == DeviceBinding.derivedMode else {
            throw DeviceKeyError("device key mode unreadable (\(status))")
        }
        return s
    }

    public func writeMode(_ mode: String) throws {
        var add = modeQuery
        add[kSecValueData as String] = Data(mode.utf8)
        #if os(iOS)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        #endif
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess else { throw DeviceKeyError("device key mode not saved (\(status))") }
    }
}
