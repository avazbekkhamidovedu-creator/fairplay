import Foundation
import FairPlayCore

/// S5 (CONTRACT.md "Sealed offline submit"): answers sealed on this iPad when the hand-in
/// at time-up can't reach the server, uploaded later. Same file and rules as the Mac app.
///
/// File `Application Support/FairPlay/sealed/<attempt>.json` (not in backups, protected
/// until first unlock): `{payload, answers_json, proof, token}`. `proof` uses the attempt's
/// offline key (from the server, per session; never in the binary), `sealed_at` comes from
/// the server clock, so moving the iPad's clock back doesn't make late answers look on time.
@MainActor
enum Sealed {
    enum SealError: String, Error { case noExam = "no_exam", noKey = "no_key", badInput = "bad_input", writeFailed = "write_failed" }

    static var folder: URL {
        let fm = FileManager.default
        return (fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fm.temporaryDirectory)
            .appendingPathComponent("FairPlay/sealed", isDirectory: true)
    }

    @discardableResult
    static func seal(attemptId: String, answersJSON: String, clientSeq: Int64, exam: AppSession.Exam?,
                     offlineKey: Data?, deviceId: String) throws -> URL {
        guard let exam, exam.attemptId == attemptId else { throw SealError.noExam }
        guard let key = offlineKey, key.count == 32 else { throw SealError.noKey }
        guard Signing.isSafeToken(attemptId, min: 3, max: 32), clientSeq >= -1,
              SealedFormat.answersAreValid(answersJSON) else { throw SealError.badInput }
        let p = SealedFormat.payload(attemptId: attemptId, session: exam.session, deviceId: deviceId,
                                     clientSeq: clientSeq, sealedAt: ServerClock.iso(ServerClock.shared.now()),
                                     answersJSON: answersJSON)
        let file: [String: Any] = ["payload": p, "answers_json": answersJSON,
                                   "proof": SealedFormat.proof(payload: p, key: key), "token": exam.token]
        do {
            var dir = folder
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? dir.setResourceValues(values)
            let url = dir.appendingPathComponent("\(attemptId).json")
            try JSONSerialization.data(withJSONObject: file)
                .write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            return url
        } catch {
            throw SealError.writeFailed
        }
    }

    static func pending() -> [URL] {
        let items = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        return items.filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static var hasPending: Bool { !pending().isEmpty }

    // MARK: - Upload

    private static var uploading = false
    private static var retryTimer: Timer?

    /// Uploads every sealed file; `token` (from the page, bridge "sealedToken") replaces
    /// the stored one when given. At launch and every 60 s while files are left.
    static func uploadAll(token: String? = nil) {
        guard !uploading else { return }
        let files = pending()
        guard !files.isEmpty else { retryTimer?.invalidate(); retryTimer = nil; return }
        uploading = true
        var left = files.count
        for url in files {
            upload(url, token: token) {
                left -= 1
                if left == 0 { uploading = false; scheduleRetry() }
            }
        }
    }

    private static func scheduleRetry() {
        guard hasPending else { retryTimer?.invalidate(); retryTimer = nil; return }
        guard retryTimer == nil else { return }
        let t = Timer(timeInterval: 60, repeats: true) { _ in MainActor.assumeIsolated { Sealed.uploadAll() } }
        RunLoop.main.add(t, forMode: .common)
        retryTimer = t
    }

    private static func upload(_ url: URL, token: String?, done: @escaping @MainActor () -> Void) {
        guard let data = try? Data(contentsOf: url),
              let file = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let payload = file["payload"] as? String, let answers = file["answers_json"] as? String,
              let proof = file["proof"] as? String,
              let aid = payload.split(separator: "|").first.map(String.init),
              Signing.isSafeToken(aid, min: 3, max: 32) else {
            DebugLog.log("sealed", "unreadable sealed file \(url.lastPathComponent): set aside")
            moveAside(url); done(); return
        }
        let pick = SealedFormat.chooseToken(page: token, stored: file["token"] as? String)
        Net.request("POST", path: "api/student/attempts/\(aid)/sealed-submit", token: pick.token, appSession: nil,
                    body: ["payload": payload, "answers_json": answers, "proof": proof]) { status, _ in
            let o = SealedFormat.outcome(status: status, borrowed: pick.borrowed)
            DebugLog.log("sealed", "upload \(aid): HTTP \(status.map(String.init) ?? "none") -> \(o)")
            switch o {
            case .done:
                try? FileManager.default.removeItem(at: url)     // the server records sealed_submit
            case .reject:
                moveAside(url)
            case .keep:
                if pick.matches, pick.token != (file["token"] as? String) {
                    var f = file
                    f["token"] = pick.token
                    if let d = try? JSONSerialization.data(withJSONObject: f) {
                        try? d.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
                    }
                }
            }
            done()
        }
    }

    private static func moveAside(_ url: URL) {
        let rejected = url.deletingLastPathComponent().appendingPathComponent("rejected", isDirectory: true)
        try? FileManager.default.createDirectory(at: rejected, withIntermediateDirectories: true)
        try? FileManager.default.moveItem(at: url, to: rejected.appendingPathComponent(
            "\(Int(Date().timeIntervalSince1970))-\(url.lastPathComponent)"))
    }
}
