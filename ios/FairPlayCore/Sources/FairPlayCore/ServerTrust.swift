import Foundation
import CryptoKit
import Security

public enum ServerTrust {
    /// True when the connection may be used. Hosts other than the exam server only get
    /// normal validation. A private CA for the exam server (`extraRoot`) is an extra anchor
    /// for that host only; the pin check still has to find a pinned certificate in the chain.
    public static func evaluate(_ trust: SecTrust, host: String, serverHost: String?, pins: [String],
                                extraRoot: SecCertificate?) -> Bool {
        let isServer = host.lowercased() == serverHost?.lowercased()
        if isServer, let extraRoot {
            SecTrustSetAnchorCertificates(trust, [extraRoot] as CFArray)
            SecTrustSetAnchorCertificatesOnly(trust, false)
        }
        var error: CFError?
        guard SecTrustEvaluateWithError(trust, &error) else { return false }
        guard !pins.isEmpty, isServer else { return true }
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate] else { return false }
        for cert in chain {
            let der = SecCertificateCopyData(cert) as Data
            if pins.contains(Data(SHA256.hash(data: der)).base64EncodedString()) { return true }
        }
        return false
    }

    public static func certificate(fromPEM pem: String) -> SecCertificate? {
        let body = pem.split(whereSeparator: \.isNewline)
            .filter { !$0.hasPrefix("-----") }.joined()
        guard let der = Data(base64Encoded: body) else { return nil }
        return SecCertificateCreateWithData(nil, der as CFData)
    }
}
