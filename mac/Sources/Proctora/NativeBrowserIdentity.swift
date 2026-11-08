import Foundation
import Security
import CryptoKit

/// A provisioned native TLS identity, never selected by website JavaScript.
/// Key possession is not remote attestation of the operating system or lock.
final class NativeBrowserIdentity: NSObject, URLSessionDelegate, URLSessionTaskDelegate {
    let origin: URL
    private(set) var identity: SecIdentity?
    private(set) var fingerprint: String?
    private(set) var certificateExpiry: Date?
    private let ownsLock: () -> Bool
    private var testAnchor: SecCertificate?

    init(origin: URL, ownsLock: @escaping () -> Bool) {
        self.origin = origin
        self.ownsLock = ownsLock
        var item: CFTypeRef?
        let query: [String: Any] = [kSecClass as String: kSecClassIdentity,
            kSecAttrLabel as String: "FairPlay DKU native identity",
            kSecReturnRef as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var selected: SecIdentity?
        if SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
           let item, CFGetTypeID(item) == SecIdentityGetTypeID() { selected = (item as! SecIdentity) }
        #if FAIRPLAY_ISOLATED_TEST
        if origin.host == "localhost", origin.port == 18443,
           let path = Bundle.main.object(forInfoDictionaryKey: "LocalDKUIdentityPath") as? String,
           let data = try? Data(contentsOf: URL(fileURLWithPath: path)) {
            var imported: CFArray?
            let options = [kSecImportExportPassphrase as String: "local-native-fixture"]
            if SecPKCS12Import(data as CFData, options as CFDictionary, &imported) == errSecSuccess,
               let rows = imported as? [[String: Any]], let first = rows.first,
               let value = first[kSecImportItemIdentity as String] {
                selected = (value as! SecIdentity)
            }
        }
        if origin.host == "localhost", origin.port == 18443,
           let path = Bundle.main.object(forInfoDictionaryKey: "LocalDKUAnchorPath") as? String,
           let data = try? Data(contentsOf: URL(fileURLWithPath: path)) {
            testAnchor = SecCertificateCreateWithData(nil, data as CFData)
        }
        #endif
        identity = selected
        if let profile = NativeBrowserProfile.bundled, origin == profile.gatewayURL {
            testAnchor = SecCertificateCreateWithData(nil, profile.authorityDER as CFData)
        }
        var certificate: SecCertificate?
        if let selected, SecIdentityCopyCertificate(selected, &certificate) == errSecSuccess, let certificate {
            fingerprint = Self.fingerprint(certificate)
            certificateExpiry = CertificateRenewal.notAfter(certificate)
        } else { fingerprint = nil }
        super.init()
    }

    func prepare() async throws {
        guard let profile = NativeBrowserProfile.bundled, origin == profile.gatewayURL else { return }
        let selected = try await NativeBrowserProvisioning.prepare(profile: profile)
        var cert: SecCertificate?
        guard SecIdentityCopyCertificate(selected, &cert) == errSecSuccess, let cert,
              let digest = Self.fingerprint(cert) else { throw DeviceKeyError("Native identity unavailable") }
        identity = selected; fingerprint = digest
        certificateExpiry = CertificateRenewal.notAfter(cert)
    }

    /// Issues the certificate again with the same key when less than `threshold` of it is left; the
    /// fingerprint must not change (it identifies the device to the OJ).
    func renewIfNeeded(threshold: TimeInterval, now: Date = Date()) async throws {
        guard let profile = NativeBrowserProfile.bundled, origin == profile.gatewayURL, let before = fingerprint else { return }
        let selected = try await NativeBrowserProvisioning.prepare(profile: profile, renewBefore: threshold, now: now)
        var cert: SecCertificate?
        guard SecIdentityCopyCertificate(selected, &cert) == errSecSuccess, let cert,
              Self.fingerprint(cert) == before else { throw DeviceKeyError("Renewed identity does not match") }
        identity = selected
        certificateExpiry = CertificateRenewal.notAfter(cert)
    }
    static func fingerprint(_ cert: SecCertificate) -> String? {
        guard let key = SecCertificateCopyKey(cert),
              let raw = SecKeyCopyExternalRepresentation(key, nil) as Data?,
              let attributes = SecKeyCopyAttributes(key) as? [String: Any] else { return nil }
        func wrap(_ tag: UInt8, _ bytes: Data) -> Data {
            let n = bytes.count
            let length: [UInt8] = n < 128 ? [UInt8(n)] : n < 256 ? [0x81, UInt8(n)] : [0x82, UInt8(n >> 8), UInt8(n & 255)]
            return Data([tag] + length) + bytes
        }
        let spki: Data
        if attributes[kSecAttrKeyType as String] as? String == kSecAttrKeyTypeECSECPrimeRandom as String, raw.count == 65 {
            spki = Data([0x30,0x59,0x30,0x13,0x06,0x07,0x2a,0x86,0x48,0xce,0x3d,0x02,0x01,0x06,0x08,0x2a,0x86,0x48,0xce,0x3d,0x03,0x01,0x07,0x03,0x42,0x00]) + raw
        } else if attributes[kSecAttrKeyType as String] as? String == kSecAttrKeyTypeRSA as String, raw.count < 8192 {
            spki = wrap(0x30, Data([0x30,0x0d,0x06,0x09,0x2a,0x86,0x48,0x86,0xf7,0x0d,0x01,0x01,0x01,0x05,0x00]) + wrap(0x03, Data([0]) + raw))
        } else { return nil }
        return SHA256.hash(data: spki).map { String(format: "%02x", $0) }.joined()
    }
    func respond(_ challenge: URLAuthenticationChallenge,
                 completion: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        let space = challenge.protectionSpace
        let sameOrigin = !space.isProxy() && space.host.lowercased() == origin.host?.lowercased()
            && space.port == (origin.port ?? 443) && space.protocol?.lowercased() == "https"
        if space.authenticationMethod == NSURLAuthenticationMethodClientCertificate {
            guard sameOrigin, ownsLock(), let identity else { completion(.rejectProtectionSpace, nil); return }
            completion(.useCredential, URLCredential(identity: identity, certificates: nil, persistence: .forSession))
            return
        }
        if sameOrigin, let anchor = testAnchor, space.authenticationMethod == NSURLAuthenticationMethodServerTrust,
           let trust = space.serverTrust {
            SecTrustSetAnchorCertificates(trust, [anchor] as CFArray)
            SecTrustSetAnchorCertificatesOnly(trust, true)
            guard SecTrustEvaluateWithError(trust, nil) else { completion(.cancelAuthenticationChallenge, nil); return }
            completion(.useCredential, URLCredential(trust: trust)); return
        }
        completion(.performDefaultHandling, nil)
    }
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        respond(challenge, completion: completionHandler)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
    func makeSession() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.timeoutIntervalForRequest = LoadTimeouts.request
        return URLSession(configuration: config, delegate: self, delegateQueue: .main)
    }
}
