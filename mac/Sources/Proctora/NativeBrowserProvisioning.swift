import Foundation
import Security
import CryptoKit

/// The enrollment endpoint authenticates the app proof; the bundled CA and
/// locally held private key authenticate the returned client certificate.
/// When the device certificate (7 days) must be issued again. The key, and so the fingerprint, stays the same.
enum CertificateRenewal {
    static let beforeStart: TimeInterval = 24 * 3600
    static let duringExam: TimeInterval = 6 * 3600
    static let retryFirst: TimeInterval = 30
    static let retryCap: TimeInterval = 600

    static func needsRenewal(remaining: TimeInterval?, threshold: TimeInterval) -> Bool {
        guard let remaining else { return false }
        return remaining < threshold
    }

    /// Wait before the next background attempt after `failures` failures in a row: 30 s, doubling, capped.
    static func backoff(afterFailures failures: Int) -> TimeInterval {
        min(retryCap, retryFirst * pow(2, Double(max(failures, 1) - 1)))
    }

    static func shouldTryNow(remaining: TimeInterval?, inFlight: Bool, now: Date, notBefore: Date?) -> Bool {
        guard !inFlight, needsRenewal(remaining: remaining, threshold: duringExam) else { return false }
        return notBefore.map { now >= $0 } ?? true
    }

    /// The certificate's notAfter, from the Keychain's own parsing.
    static func notAfter(_ der: Data) -> Date? {
        guard let cert = SecCertificateCreateWithData(nil, der as CFData) else { return nil }
        return notAfter(cert)
    }

    static func notAfter(_ cert: SecCertificate) -> Date? {
        let oid = kSecOIDX509V1ValidityNotAfter as CFString
        guard let values = SecCertificateCopyValues(cert, [oid] as CFArray, nil) as? [CFString: Any],
              let entry = values[oid] as? [CFString: Any], let seconds = entry[kSecPropertyKeyValue] as? NSNumber else { return nil }
        return Date(timeIntervalSinceReferenceDate: seconds.doubleValue)
    }
}

enum NativeBrowserProvisioning {
    /// `renewBefore`: a stored certificate with less validity than this is replaced (same key). If the
    /// replacement fails and the stored one is still valid, that one is used.
    static func prepare(profile: NativeBrowserProfile, renewBefore: TimeInterval = CertificateRenewal.beforeStart,
                        now: Date = Date()) async throws -> SecIdentity {
        let name = Config.deviceKeyNamespace + ".native-browser-key-tag"
        let key: NativeEnrollmentKey
        if let tag = UserDefaults.standard.data(forKey: name),
           let stored = try NativeEnrollmentKey.loadNonExportable(tag: tag) { key = stored }
        else {
            let created = try NativeEnrollmentKey.createNonExportable()
            key = created.key
            UserDefaults.standard.set(created.tag, forKey: name)
        }
        let folder = Sealed.folder.deletingLastPathComponent().appendingPathComponent("native-browser")
        let certificateFile = folder.appendingPathComponent("device.der")
        var stored: SecIdentity?
        if let der = try? Data(contentsOf: certificateFile),
           let identity = key.identity(certificateDER: der, trustedAuthorityDER: profile.authorityDER) {
            let remaining = CertificateRenewal.notAfter(der).map { $0.timeIntervalSince(now) }
            if !CertificateRenewal.needsRenewal(remaining: remaining, threshold: renewBefore) { return identity }
            stored = identity
        }
        do { return try await enroll(key: key, profile: profile, folder: folder, certificateFile: certificateFile) }
        catch { if let stored { return stored }; throw error }
    }

    private static func enroll(key: NativeEnrollmentKey, profile: NativeBrowserProfile, folder: URL, certificateFile: URL) async throws -> SecIdentity {
        let csr = try NativeEnrollmentCSR.make(using: key)
        let digest = SHA256.hash(data: csr).map { String(format: "%02x", $0) }.joined()
        let transport = URLSession(configuration: .ephemeral, delegate: NoRedirect(), delegateQueue: nil)
        defer { transport.invalidateAndCancel() }
        func send(_ action: String, _ body: [String: String]) async throws -> [String: String] {
            var request = URLRequest(url: profile.enrollmentURL.appendingPathComponent(action))
            request.httpMethod = "POST"; request.timeoutInterval = LoadTimeouts.request
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
            let (data, response) = try await transport.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200, data.count <= 20000 else {
                throw DeviceKeyError("Native device enrollment is unavailable")
            }
            return try JSONDecoder().decode([String: String].self, from: data)
        }
        let challenge = try await send("challenge", ["csr_sha256": digest])
        guard let nonce = challenge["nonce"], nonce.count == 64,
              nonce.allSatisfy({ $0.isHexDigit && !$0.isUppercase }) else { throw DeviceKeyError("Invalid enrollment challenge") }
        let signature = AppSession.sign("native-browser-enroll-v1|" + nonce + "|" + digest)
        let issued = try await send("certificate", ["nonce": nonce, "csr": csr.base64EncodedString(), "signature": signature])
        guard let encoded = issued["certificate"], let der = Data(base64Encoded: encoded),
              let identity = key.identity(certificateDER: der, trustedAuthorityDER: profile.authorityDER) else {
            throw DeviceKeyError("The native certificate could not be verified")
        }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try der.write(to: certificateFile, options: .atomic)
        return identity
    }
    private final class NoRedirect: NSObject, URLSessionTaskDelegate {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
    }
}
