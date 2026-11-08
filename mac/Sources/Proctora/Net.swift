import Foundation
import CryptoKit
import Security

/// TLS for the exam server (audit 2026-10-12 #11): normal certificate validation plus,
/// when build.sh got FAIRPLAY_PINS, a pin check. A pin is the base64 SHA-256 of one
/// certificate (DER) in the server's chain; pin your CA's intermediate and root (and a
/// backup) so routine renewals keep working. Compute one with mac/pin.sh.
enum Pinning {
    static var enabled: Bool { !Config.pins.isEmpty }

    /// True when the connection may be used. Hosts other than the exam server (exam
    /// websites in website mode) only get normal validation.
    static func evaluate(_ trust: SecTrust, host: String) -> Bool {
        evaluate(trust, host: host, serverHost: Config.serverURL.host, pins: Config.pins,
                 extraRoot: Config.extraRootPEM.flatMap(certificate(fromPEM:)))
    }

    /// A private CA for the exam server (FAIRPLAY_EXTRA_ROOT_PEM) is an extra anchor for that
    /// host only; the pin check below still has to find a pinned certificate in the chain.
    static func evaluate(_ trust: SecTrust, host: String, serverHost: String?, pins: [String],
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
            let hash = Data(SHA256.hash(data: der)).base64EncodedString()
            if pins.contains(hash) { return true }
        }
        return false
    }

    static func certificate(fromPEM pem: String) -> SecCertificate? {
        let body = pem.split(whereSeparator: \.isNewline)
            .filter { !$0.hasPrefix("-----") }.joined()
        guard let der = Data(base64Encoded: body) else { return nil }
        return SecCertificateCreateWithData(nil, der as CFData)
    }

    /// Shared handler for URLSession and WKWebView server-trust challenges.
    static func handle(_ challenge: URLAuthenticationChallenge,
                       _ completion: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust else {
            completion(.performDefaultHandling, nil)
            return
        }
        let host = challenge.protectionSpace.host
        if evaluate(trust, host: host) {
            completion(.useCredential, URLCredential(trust: trust))
        } else {
            EventLog.shared.record(.pinMismatch(host))
            completion(.cancelAuthenticationChallenge, nil)
        }
    }
}

final class PinnedSessionDelegate: NSObject, URLSessionDelegate {
    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pinning.handle(challenge, completionHandler)
    }
}

/// All native requests to the exam server go through here.
enum Net {
    /// 426 Upgrade Required from the server (plan M5): this build is older than the
    /// server's minimum. Set by the window controller; runs on the main thread.
    static var onUpgradeRequired: ((String) -> Void)?

    static let session: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = LoadTimeouts.request
        return URLSession(configuration: cfg, delegate: PinnedSessionDelegate(), delegateQueue: nil)
    }()

    /// JSON request with the student's token (and app session when given). Completion
    /// runs on the main thread with (HTTP status or nil when offline, decoded JSON object).
    static func request(_ method: String, path: String, token: String, appSession: String?,
                        body: [String: Any]?, completion: @escaping (Int?, [String: Any]?) -> Void) {
        // "path?query": appendingPathComponent would escape the "?" (audit M-04 needs ?nonce=).
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        var url = Config.serverURL.appendingPathComponent(parts.first ?? path)
        if parts.count == 2, var c = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            c.percentEncodedQuery = parts[1]
            url = c.url ?? url
        }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        if !token.isEmpty { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        // Plan S2/M5 (2026-10-20): the server gates old app versions and sends its clock.
        req.setValue(Config.version, forHTTPHeaderField: "X-FairPlay-Version")
        req.setValue("mac", forHTTPHeaderField: "X-FairPlay-Platform")
        if let appSession, !appSession.isEmpty {
            req.setValue(appSession, forHTTPHeaderField: "X-FairPlay-Session")
        }
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        let started = Date()
        session.dataTask(with: req) { data, response, error in
            let http = response as? HTTPURLResponse
            let status = http?.statusCode
            if let t = http?.value(forHTTPHeaderField: "X-Server-Time") { ServerClock.shared.update(serverTime: t) }
            let ms = Int(Date().timeIntervalSince(started) * 1000)
            // Beats every 5 s: only log them when they fail.
            if !(path.hasSuffix("/app-beat") && status == 200) {
                let what = status.map { "HTTP \($0)" } ?? "NO RESPONSE (\(error?.localizedDescription ?? "?"))"
                DebugLog.log("net", "\(method) /\(path) -> \(what) in \(ms) ms")
            }
            var json: [String: Any]?
            if let data { json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] }
            DispatchQueue.main.async {
                if status == 426 { Net.onUpgradeRequired?((json?["min_app_version"] as? String) ?? "") }
                completion(status, json)
            }
        }.resume()
    }
}
