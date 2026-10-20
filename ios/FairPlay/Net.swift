import Foundation
import CryptoKit
import Security
import FairPlayCore

/// TLS for the exam server: normal validation plus, when the build has pins, a pin check
/// (base64 SHA-256 of one DER certificate in the chain; mac/pin.sh computes them).
enum Pinning {
    static var enabled: Bool { !Config.pins.isEmpty }

    static func evaluate(_ trust: SecTrust, host: String) -> Bool {
        var error: CFError?
        guard SecTrustEvaluateWithError(trust, &error) else { return false }
        guard enabled, host.lowercased() == Config.serverURL.host?.lowercased() else { return true }
        guard let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate] else { return false }
        for cert in chain {
            let der = SecCertificateCopyData(cert) as Data
            if Config.pins.contains(Data(SHA256.hash(data: der)).base64EncodedString()) { return true }
        }
        return false
    }

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
            DispatchQueue.main.async { EventLog.shared.record("tls_pin_mismatch", host) }
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
    static let session: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 15
        cfg.connectionProxyDictionary = [:]     // never through a configured HTTP proxy
        return URLSession(configuration: cfg, delegate: PinnedSessionDelegate(), delegateQueue: nil)
    }()

    /// JSON request with the student's token (and app session when given). Completion runs
    /// on the main thread with (HTTP status or nil when offline, decoded JSON object).
    static func request(_ method: String, path: String, token: String, appSession: String?,
                        body: [String: Any]?, completion: @escaping @MainActor (Int?, [String: Any]?) -> Void) {
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        var url = Config.serverURL.appendingPathComponent(parts.first ?? path)
        if parts.count == 2, var c = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            c.percentEncodedQuery = parts[1]
            url = c.url ?? url
        }
        var req = URLRequest(url: url, timeoutInterval: 15)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let appSession, !appSession.isEmpty { req.setValue(appSession, forHTTPHeaderField: "X-FairPlay-Session") }
        // S2: the server gates start / app-beat by platform minimum and answers 426.
        req.setValue(Config.version, forHTTPHeaderField: "X-FairPlay-Version")
        req.setValue("ipad", forHTTPHeaderField: "X-FairPlay-Platform")
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
            if !(path.hasSuffix("/app-beat") && status == 200) {
                let what = status.map { "HTTP \($0)" } ?? "NO RESPONSE (\(error?.localizedDescription ?? "?"))"
                DebugLog.log("net", "\(method) /\(parts.first ?? path) -> \(what) in \(ms) ms")
            }
            var json: [String: Any]?
            if let data { json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] }
            if status == 426 {
                // The page shows "Update FairPlay" for its own requests; this is for the trace.
                DebugLog.log("net", "server needs FairPlay \((json?["min_app_version"] as? String) ?? "?") or newer")
            }
            DispatchQueue.main.async { completion(status, json) }
        }.resume()
    }
}
