import Foundation
import Security
import CryptoKit

/// The enrollment endpoint authenticates the app proof; the bundled CA and
/// locally held private key authenticate the returned client certificate.
enum NativeBrowserProvisioning {
    static func prepare(profile: NativeBrowserProfile) async throws -> SecIdentity {
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
        if let der = try? Data(contentsOf: certificateFile),
           let identity = key.identity(certificateDER: der, trustedAuthorityDER: profile.authorityDER) { return identity }
        let csr = try NativeEnrollmentCSR.make(using: key)
        let digest = SHA256.hash(data: csr).map { String(format: "%02x", $0) }.joined()
        let transport = URLSession(configuration: .ephemeral, delegate: NoRedirect(), delegateQueue: nil)
        defer { transport.invalidateAndCancel() }
        func send(_ action: String, _ body: [String: String]) async throws -> [String: String] {
            var request = URLRequest(url: profile.enrollmentURL.appendingPathComponent(action))
            request.httpMethod = "POST"; request.timeoutInterval = 15
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
