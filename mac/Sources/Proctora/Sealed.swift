import Foundation
import CryptoKit

/// Plan M4 (2026-10-20): answers sealed on this Mac when the hand-in at time-up can't
/// reach the server, uploaded later ("submit later from this Mac", like Bluebook).
///
/// File `Application Support/FairPlay/sealed/<attempt>.json` (0600, folder 0700):
/// `{payload, answers_json, proof, token}`.
/// - `payload` is `attempt|session|device|client_seq|sealed_at|sha256(answers_json)`.
/// - `proof` is HMAC-SHA256 with the attempt's offline key (from the server, per session).
///   The server recomputes it.
/// - `sealed_at` comes from ServerClock (last server time + monotonic time), so moving
///   the Mac's clock back doesn't make late answers look on time.
/// - The token is the student's own and lets the app upload at its next launch. If it has
///   expired by then, the page hands over a fresh one (bridge "sealedToken").
enum Sealed {
    static let maxAnswersBytes = 5 * 1024 * 1024

    static var folder: URL {
        let fm = FileManager.default
        let base = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fm.temporaryDirectory
        return base.appendingPathComponent(Config.supportFolderName, isDirectory: true).appendingPathComponent("sealed", isDirectory: true)
    }

    static func hex(_ bytes: some Sequence<UInt8>) -> String { bytes.map { String(format: "%02x", $0) }.joined() }

    static func sha256Hex(_ text: String) -> String { hex(SHA256.hash(data: Data(text.utf8))) }

    static func payload(attemptId: String, session: String, deviceId: String, clientSeq: Int64,
                        sealedAt: String, answersJSON: String) -> String {
        "\(attemptId)|\(session)|\(deviceId)|\(clientSeq)|\(sealedAt)|\(sha256Hex(answersJSON))"
    }

    static func proof(payload: String, key: Data) -> String {
        hex(HMAC<SHA256>.authenticationCode(for: Data(payload.utf8), using: SymmetricKey(data: key)))
    }

    enum SealError: String, Error { case noExam = "no_exam", noKey = "no_key", badInput = "bad_input", writeFailed = "write_failed" }

    /// Builds and writes the sealed file. Returns the file URL.
    @discardableResult
    static func seal(attemptId: String, answersJSON: String, clientSeq: Int64, exam: AppSession.Exam?,
                     offlineKey: Data?, deviceId: String, now: Date = ServerClock.shared.now(),
                     folder: URL = Sealed.folder) throws -> URL {
        guard let exam, exam.attemptId == attemptId else { throw SealError.noExam }
        guard let key = offlineKey, key.count == 32 else { throw SealError.noKey }
        guard AppSession.isSafeToken(attemptId, min: 3, max: 32), clientSeq >= -1,
              answersJSON.utf8.count <= maxAnswersBytes,
              (try? JSONSerialization.jsonObject(with: Data(answersJSON.utf8))) is [String: Any]
        else { throw SealError.badInput }
        let p = payload(attemptId: attemptId, session: exam.session, deviceId: deviceId, clientSeq: clientSeq,
                        sealedAt: ServerClock.iso(now), answersJSON: answersJSON)
        let file: [String: Any] = ["payload": p, "answers_json": answersJSON, "proof": proof(payload: p, key: key),
                                   "token": exam.token]
        let fm = FileManager.default
        do {
            try fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let url = folder.appendingPathComponent("\(attemptId).json")
            let data = try JSONSerialization.data(withJSONObject: file)
            try data.write(to: url, options: .atomic)
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            return url
        } catch {
            throw SealError.writeFailed
        }
    }

    static func pending(folder: URL = Sealed.folder) -> [URL] {
        let items = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        return items.filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static var hasPending: Bool { !pending().isEmpty }

    /// What to do with a file after an upload answer.
    enum Outcome: Equatable { case done, keep, reject }

    static func outcome(status: Int?, borrowed: Bool = false) -> Outcome {
        if borrowed, let status, status != 200, status < 500 { return .keep }
        switch status {
        case 200?: return .done
        case nil, 401?, 403?, 408?, 429?: return .keep      // offline, token expired, busy
        case let s? where s >= 500: return .keep
        default: return .reject                             // the server said no: keep a copy aside
        }
    }

    static func tokenUserId(_ token: String?) -> String? {
        guard let token, let head = token.split(separator: ".", omittingEmptySubsequences: false).first else { return nil }
        var b64 = head.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        b64 += String(repeating: "=", count: (4 - b64.count % 4) % 4)
        guard let data = Data(base64Encoded: b64), let text = String(data: data, encoding: .utf8),
              let uid = text.split(separator: ":", omittingEmptySubsequences: false).first,
              (1...18).contains(uid.count), uid.allSatisfy({ $0.isASCII && $0.isNumber }) else { return nil }
        return String(uid)
    }

    /// The page token is used only for the user the file was sealed by. `borrowed` means a
    /// token the file does not vouch for, so no answer from it may set the file aside.
    static func chooseToken(page: String?, stored: String?) -> (token: String, matches: Bool, borrowed: Bool) {
        let own = stored ?? ""
        guard let page, !page.isEmpty else { return (own, false, false) }
        if own.isEmpty { return (page, false, true) }
        if let a = tokenUserId(page), a == tokenUserId(own) { return (page, true, false) }
        return (own, false, false)
    }

    // MARK: - Upload

    private static var uploading = false
    private static var retryTimer: Timer?

    /// Uploads every sealed file. `token` (from the page) replaces the stored one when given.
    /// Main thread only.
    static func uploadAll(token: String? = nil) {
        guard !uploading else { return }
        let files = pending()
        guard !files.isEmpty else { retryTimer?.invalidate(); retryTimer = nil; return }
        uploading = true
        var left = files.count
        for url in files {
            upload(url, token: token) { _ in
                left -= 1
                if left == 0 {
                    uploading = false
                    scheduleRetry()
                }
            }
        }
    }

    /// While files are left, try again every 60 s (the Wi-Fi may come back).
    private static func scheduleRetry() {
        guard hasPending else { retryTimer?.invalidate(); retryTimer = nil; return }
        guard retryTimer == nil else { return }
        let t = Timer(timeInterval: 60, repeats: true) { _ in uploadAll() }
        RunLoop.main.add(t, forMode: .common)
        retryTimer = t
    }

    private static func upload(_ url: URL, token: String?, done: @escaping (Outcome) -> Void) {
        guard let data = try? Data(contentsOf: url),
              let file = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let payload = file["payload"] as? String, let answers = file["answers_json"] as? String,
              let proof = file["proof"] as? String,
              let aid = payload.split(separator: "|").first.map(String.init),
              AppSession.isSafeToken(aid, min: 3, max: 32) else {
            DebugLog.log("sealed", "unreadable sealed file \(url.lastPathComponent): set aside")
            moveAside(url); done(.reject); return
        }
        let pick = chooseToken(page: token, stored: file["token"] as? String)
        Net.request("POST", path: "api/student/attempts/\(aid)/sealed-submit", token: pick.token, appSession: nil,
                    body: ["payload": payload, "answers_json": answers, "proof": proof]) { status, json in
            let o = outcome(status: status, borrowed: pick.borrowed)
            DebugLog.log("sealed", "upload \(aid): HTTP \(status.map(String.init) ?? "none") -> \(o)")
            switch o {
            case .done:
                try? FileManager.default.removeItem(at: url)   // the server records sealed_submit
            case .reject:
                moveAside(url)
                DebugLog.log("sealed", "server refused \(aid): \(json ?? [:])")
            case .keep:
                // A fresh token from the page next time: remember it in the file.
                if pick.matches, pick.token != (file["token"] as? String) {
                    var f = file
                    f["token"] = pick.token
                    if let d = try? JSONSerialization.data(withJSONObject: f) { try? d.write(to: url, options: .atomic) }
                }
            }
            done(o)
        }
    }

    private static func moveAside(_ url: URL) {
        let fm = FileManager.default
        let rejected = url.deletingLastPathComponent().appendingPathComponent("rejected", isDirectory: true)
        try? fm.createDirectory(at: rejected, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let dest = rejected.appendingPathComponent("\(Int(Date().timeIntervalSince1970))-\(url.lastPathComponent)")
        try? fm.moveItem(at: url, to: dest)
    }
}
