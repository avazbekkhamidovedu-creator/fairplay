import Foundation
import Security

/// Native enrollment key adapter. The trusted provisioning layer is responsible
/// for creating and persisting a non-exportable key and authenticating the CA
/// response. This wrapper does not enroll a user, validate a CA, or attest lock.
struct NativeEnrollmentKey: DeviceKeyBacking {
    private let privateKey: SecKey
    let publicKeyDER: Data
    let hardwareBacked: Bool

    /// The trusted native provisioning layer must persist the returned random
    /// tag with the authenticated profile. No key is created until this is called.
    /// Prefer Secure Enclave. Software fallback is non-exportable, matching the released app.
    static func createNonExportable() throws -> (key: NativeEnrollmentKey, tag: Data) {
        var tag = Data(count: 32)
        let randomStatus = tag.withUnsafeMutableBytes { bytes in
            SecRandomCopyBytes(kSecRandomDefault, bytes.count, bytes.baseAddress!)
        }
        guard randomStatus == errSecSuccess else { throw DeviceKeyError("enrollment key randomness unavailable") }
        guard let access = SecAccessControlCreateWithFlags(nil,
                kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly, .privateKeyUsage, nil) else {
            throw DeviceKeyError("enrollment key access control unavailable")
        }
        let attrs: [String: Any] = [
            kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecAttrTokenID as String: kSecAttrTokenIDSecureEnclave,
            kSecUseDataProtectionKeychain as String: true,
            kSecPrivateKeyAttrs as String: [kSecAttrIsPermanent as String: true,
                                            kSecAttrApplicationTag as String: tag,
                                            kSecAttrAccessControl as String: access],
        ]
        var error: Unmanaged<CFError>?
        if let key = SecKeyCreateRandomKey(attrs as CFDictionary, &error),
           let adapter = NativeEnrollmentKey(privateKey: key) { return (adapter, tag) }
        // Same compatibility policy as the released device key: hardware-backed
        // when available, otherwise a non-exportable key in the native Keychain.
        let software: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits as String: 256,
            kSecPrivateKeyAttrs as String: [kSecAttrIsPermanent as String: true,
                kSecAttrApplicationTag as String: tag, kSecAttrIsExtractable as String: false]]
        guard let key = SecKeyCreateRandomKey(software as CFDictionary, &error),
              let adapter = NativeEnrollmentKey(privateKey: key) else {
            throw DeviceKeyError("Native enrollment key unavailable")
        }
        return (adapter, tag)
    }

    static func loadNonExportable(tag: Data) throws -> NativeEnrollmentKey? {
        guard (16...64).contains(tag.count) else { throw DeviceKeyError("invalid enrollment key tag") }
        let query: [String: Any] = [kSecClass as String: kSecClassKey,
                                    kSecAttrApplicationTag as String: tag,
                                    kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                    kSecUseDataProtectionKeychain as String: true,
                                    kSecReturnRef as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        for protectedStore in [true, false] {
            var lookup = query
            if !protectedStore { lookup.removeValue(forKey: kSecUseDataProtectionKeychain as String) }
            var result: CFTypeRef?
            let status = SecItemCopyMatching(lookup as CFDictionary, &result)
            if status == errSecItemNotFound || status == errSecMissingEntitlement { continue }
            guard status == errSecSuccess, let result, CFGetTypeID(result) == SecKeyGetTypeID(),
                  let adapter = NativeEnrollmentKey(privateKey: result as! SecKey) else {
                throw DeviceKeyError("Native enrollment key unavailable")
            }
            return adapter
        }
        return nil
    }

    init?(privateKey: SecKey) {
        guard let attributes = SecKeyCopyAttributes(privateKey) as? [String: Any],
              attributes[kSecAttrKeyType as String] as? String == (kSecAttrKeyTypeECSECPrimeRandom as String),
              attributes[kSecAttrKeySizeInBits as String] as? Int == 256,
              attributes[kSecAttrKeyClass as String] as? String == (kSecAttrKeyClassPrivate as String),
              SecKeyIsAlgorithmSupported(privateKey, .sign, .ecdsaSignatureMessageX962SHA256),
              let publicKey = SecKeyCopyPublicKey(privateKey),
              let raw = SecKeyCopyExternalRepresentation(publicKey, nil) as Data?,
              raw.count == 65, raw.first == 0x04 else { return nil }
        self.privateKey = privateKey
        publicKeyDER = Data([0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01,
                             0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00]) + raw
        hardwareBacked = (attributes[kSecAttrTokenID as String] as? String) == (kSecAttrTokenIDSecureEnclave as String)
    }

    func signDER(_ message: Data) throws -> Data {
        guard let result = SecKeyCreateSignature(privateKey, .ecdsaSignatureMessageX962SHA256, message as CFData, nil) as Data? else {
            throw DeviceKeyError("enrollment signing failed")
        }
        return result
    }

    /// The CA must come from an authenticated, administrator-approved native
    /// profile, never from the enrollment response or university page. Validate
    /// the client certificate against that anchor and match its private key.
    func identity(certificateDER: Data, trustedAuthorityDER: Data) -> SecIdentity? {
        guard certificateDER.count <= 8192, trustedAuthorityDER.count <= 8192,
              let certificate = SecCertificateCreateWithData(nil, certificateDER as CFData),
              let authority = SecCertificateCreateWithData(nil, trustedAuthorityDER as CFData) else { return nil }
        var trust: SecTrust?
        let policy = SecPolicyCreateSSL(false, nil)
        guard SecTrustCreateWithCertificates(certificate, policy, &trust) == errSecSuccess,
              let trust, SecTrustSetAnchorCertificates(trust, [authority] as CFArray) == errSecSuccess,
              SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
              SecTrustSetNetworkFetchAllowed(trust, false) == errSecSuccess,
              SecTrustEvaluateWithError(trust, nil) else { return nil }
        guard let certificateKey = SecCertificateCopyKey(certificate),
              let raw = SecKeyCopyExternalRepresentation(certificateKey, nil) as Data?,
              raw.count == 65, publicKeyDER.suffix(65) == raw else { return nil }
        return Self.makeIdentity(certificate: certificate, privateKey: privateKey)
    }

    /// SecIdentityCreate is only declared in the newest SDK headers, but the symbol
    /// has shipped since macOS 10.12, so resolve it at runtime. Without it, fall
    /// back to storing the certificate beside the key and letting the Keychain pair them.
    private static func makeIdentity(certificate: SecCertificate, privateKey: SecKey) -> SecIdentity? {
        typealias Create = @convention(c) (CFAllocator?, SecCertificate, SecKey) -> Unmanaged<SecIdentity>?
        if let symbol = dlsym(UnsafeMutableRawPointer(bitPattern: -2), "SecIdentityCreate") {
            return unsafeBitCast(symbol, to: Create.self)(nil, certificate, privateKey)?.takeRetainedValue()
        }
        let add: [String: Any] = [kSecClass as String: kSecClassCertificate,
                                  kSecValueRef as String: certificate,
                                  kSecUseDataProtectionKeychain as String: true]
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem else { return nil }
        let query: [String: Any] = [kSecClass as String: kSecClassIdentity,
                                    kSecMatchItemList as String: [certificate],
                                    kSecUseDataProtectionKeychain as String: true,
                                    kSecReturnRef as String: true,
                                    kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let result,
              CFGetTypeID(result) == SecIdentityGetTypeID() else { return nil }
        let identity = result as! SecIdentity
        var key: SecKey?
        guard SecIdentityCopyPrivateKey(identity, &key) == errSecSuccess, let key,
              let a = SecKeyCopyPublicKey(key).flatMap({ SecKeyCopyExternalRepresentation($0, nil) as Data? }),
              a == SecKeyCopyPublicKey(privateKey).flatMap({ SecKeyCopyExternalRepresentation($0, nil) as Data? })
        else { return nil }
        return identity
    }
}
