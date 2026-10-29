import Foundation
import FairPlayCore

/// The app's own link to the server for the attempt it is locked for (CONTRACT.md "App
/// session"), the same protocol as the Mac app:
/// - signs start challenges so the server knows the attempt starts in FairPlay;
/// - sends a signed beat every 5 s with the REAL lock state and the check summary;
/// - unlocks only on a signed hand-in answer; checks invigilator codes with the server;
/// - fetches the per-attempt offline exit key (memory only).
@MainActor
final class AppSession {

    static let shared = AppSession()

    struct Exam {
        let attemptId: String
        let token: String
        let session: String
    }

    private(set) var exam: Exam?
    private(set) var offlineKey: Data?
    private var offlineKeyTries = 0
    let deviceId = DeviceIdentity.deviceId
    /// LockdownController sets these. `lockedProvider`: a lock is in force (guards the
    /// exam session and the relaunch marker, review M1). `heldProvider`: iPadOS really
    /// holds it right now (the beat's `locked` bit only).
    var lockedProvider: (() -> Bool)?
    var heldProvider: (() -> Bool)?
    var checksProvider: (() -> String)?
    var isLockedNow: Bool { lockedProvider?() ?? false }
    /// Attempt status from each beat ("submitted"...).
    var onStatus: ((String) -> Void)?

    private var beatTimer: Timer?
    private var lastTs: Int64 = 0

    static func sign(_ message: String) -> String { Signing.hmacHex(key: Config.appKey, message) }

    /// Bridge reply to {"action": "attest", "nonce": ...}.
    func attestation(nonce: String) -> [String: Any] {
        guard Signing.isSafeToken(nonce, min: 8, max: 128) else {
            return ["type": "attestationFailed", "nonce": nonce, "reason": "bad_nonce"]
        }
        return ["type": "attestation", "nonce": nonce, "device_id": deviceId,
                "signature": AppSession.sign(Signing.startMessage(nonce: nonce, deviceId: deviceId)),
                "version": Config.version, "build": Config.isDevBuild ? "dev" : "release"]
    }

    // MARK: - Exam session + beats

    /// Returns false (and changes nothing) when malformed or, while locked, when the page
    /// names another attempt or another student's token.
    @discardableResult
    func setExam(attemptId: String?, token: String?, session: String?) -> Bool {
        guard let attemptId, let token, let session,
              Signing.isSafeToken(attemptId, min: 3, max: 32),
              Signing.isSafeToken(session, min: 8, max: 64),
              !token.isEmpty, token.count < 4000, !token.contains("\n") else {
            DebugLog.log("exam", "exam session REFUSED (malformed attempt/token/session)")
            return false
        }
        if isLockedNow, let cur = exam,
           !Signing.mayReplace(currentAttempt: cur.attemptId, currentToken: cur.token, attemptId: attemptId, token: token) {
            DebugLog.log("exam", "exam session REFUSED: locked for \(cur.attemptId), page asked for \(attemptId)")
            EventLog.shared.record("blocked_navigation", "another exam or account while locked")
            return false
        }
        let moved = exam?.attemptId != attemptId || exam?.session != session
        let changed = moved || exam?.token != token
        exam = Exam(attemptId: attemptId, token: token, session: session)
        if changed {
            DebugLog.log("exam", "exam session set: attempt \(attemptId)\(moved ? "" : " (new token)")")
            if isLockedNow { LockMarker.update(attemptId: attemptId) }
            startBeats()
        }
        if moved {
            offlineKey = nil
            offlineKeyTries = 0
            fetchOfflineKey()
        }
        return true
    }

    func clearExam() {
        DebugLog.log("exam", "exam session cleared")
        beatTimer?.invalidate(); beatTimer = nil
        exam = nil
        offlineKey = nil
    }

    private func startBeats() {
        beatTimer?.invalidate()
        let t = Timer(timeInterval: 5, repeats: true) { _ in MainActor.assumeIsolated { AppSession.shared.beat() } }
        RunLoop.main.add(t, forMode: .common)
        beatTimer = t
        beat()
    }

    private func nextTs() -> Int64 {
        var ts = Int64(Date().timeIntervalSince1970 * 1000)
        if ts <= lastTs { ts = lastTs + 1 }
        lastTs = ts
        return ts
    }

    /// Also called right after lock/unlock and on every change the teacher should see.
    func beat() {
        guard let exam else { return }
        let ts = nextTs()
        let locked = heldProvider?() ?? false
        let checks = String((checksProvider?() ?? "").prefix(200))
        let msg = Signing.beatMessage(aid: exam.attemptId, session: exam.session, deviceId: deviceId,
                                      ts: ts, locked: locked, checks: checks)
        let body: [String: Any] = ["session": exam.session, "device_id": deviceId, "ts": NSNumber(value: ts),
                                   "locked": locked, "checks": checks, "signature": AppSession.sign(msg)]
        Net.request("POST", path: "api/student/attempts/\(exam.attemptId)/app-beat", token: exam.token,
                    appSession: exam.session, body: body) { status, json in
            if status == 409 {
                AppSession.shared.onStatus?(GoneCheck.sessionGone)
                return
            }
            guard status == 200, let st = json?["status"] as? String else { return }
            AppSession.shared.onStatus?(st)
        }
    }

    // MARK: - Sealed answers (S5)

    /// Bridge "seal": the hand-in at time-up failed; freeze the answers on this iPad.
    func seal(attemptId: String, answersJSON: String, clientSeq: Int64) -> Sealed.SealError? {
        do {
            try Sealed.seal(attemptId: attemptId, answersJSON: answersJSON, clientSeq: clientSeq,
                            exam: exam, offlineKey: offlineKey, deviceId: deviceId)
            DebugLog.log("sealed", "answers sealed for \(attemptId)")
            beat()
            return nil
        } catch let e as Sealed.SealError {
            DebugLog.log("sealed", "seal FAILED for \(attemptId): \(e.rawValue)")
            return e
        } catch {
            return .writeFailed
        }
    }

    // MARK: - Offline exit key

    private func fetchOfflineKey() {
        guard let exam, offlineKey == nil else { return }
        offlineKeyTries += 1
        let ts = nextTs()
        let msg = Signing.offkeyMessage(aid: exam.attemptId, session: exam.session, deviceId: deviceId, ts: ts)
        let body: [String: Any] = ["session": exam.session, "device_id": deviceId, "ts": NSNumber(value: ts),
                                   "signature": AppSession.sign(msg)]
        Net.request("POST", path: "api/student/attempts/\(exam.attemptId)/offline-key", token: exam.token,
                    appSession: exam.session, body: body) { status, json in
            let me = AppSession.shared
            guard let now = me.exam, now.attemptId == exam.attemptId, now.session == exam.session else { return }
            if status == 200, let hex = json?["key"] as? String, let key = Signing.data(hex: hex), key.count == 32 {
                // Review M2: the reply must be signed; a man in the middle can't plant a key.
                guard Signing.offkeyReplyIsGenuine(key: Config.appKey, json: json, attemptId: exam.attemptId,
                                                   session: exam.session, keyHex: hex, ts: ts) else {
                    EventLog.shared.record("server_reply_unsigned", "offline key answer")
                    return
                }
                me.offlineKey = key
                DebugLog.log("exam", "offline exit key received")
            } else if Signing.offlineKeyRetryable(status) && me.offlineKeyTries < 30 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 10) { AppSession.shared.fetchOfflineKey() }
            } else {
                DebugLog.log("exam", "offline exit key NOT available (HTTP \(status.map(String.init) ?? "none"))")
            }
        }
    }

    // MARK: - Unlock checks

    enum HandIn { case confirmed, notYet, unreachable }

    /// May the lock end? Only on a SIGNED answer from the server (Mac audit M-04).
    func confirmHandIn(_ done: @escaping @MainActor (HandIn) -> Void) {
        guard let exam else { done(.notYet); return }
        let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        let path = "api/student/attempts/\(exam.attemptId)/handin?nonce=\(nonce)&device=\(deviceId)"
        Net.request("GET", path: path, token: exam.token, appSession: exam.session, body: nil) { status, json in
            guard status == 200, let st = json?["status"] as? String, let proofs = json?["proofs"] as? [String] else {
                DebugLog.log("exam", "hand-in check: HTTP \(status.map(String.init) ?? "none")")
                done(status == nil ? .unreachable : .notYet)
                return
            }
            let genuine = Signing.handInReplyIsGenuine(key: Config.appKey, proofs: proofs, attemptId: exam.attemptId,
                                                       session: exam.session, status: st, nonce: nonce)
            DebugLog.log("exam", "hand-in check: \(st), signature \(genuine ? "ok" : "WRONG")")
            guard genuine else {
                EventLog.shared.record("server_reply_unsigned", "hand-in answer")
                done(.notYet)
                return
            }
            done(Signing.unlockAllowed(status: st) ? .confirmed : .notYet)
        }
    }

    /// Invigilator exit code, checked (and rate-limited) by the server; "ok" only counts
    /// with the server's signature.
    func checkExitCode(_ code: String, _ done: @escaping @MainActor (Signing.ExitResult) -> Void) {
        guard let exam else { done(.noSession); return }
        let nonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        Net.request("POST", path: "api/student/attempts/\(exam.attemptId)/exit-code", token: exam.token,
                    appSession: exam.session, body: ["code": String(code.prefix(16)), "nonce": nonce]) { status, json in
            let r = Signing.exitResult(key: Config.appKey, status: status, json: json, attemptId: exam.attemptId,
                                       session: exam.session, nonce: nonce)
            if r == .serverError(200) { EventLog.shared.record("server_reply_unsigned", "exit-code answer") }
            done(r)
        }
    }
}

/// Offline invigilator exit for the current lock (FairPlayCore.OfflineExitCode holds the rule).
@MainActor
enum OfflineExit {
    private static var state = OfflineExitCode()

    static var challengeForDisplay: String { state.challengeForDisplay }

    static func reset() { state = OfflineExitCode() }

    /// The attempt's offline key; dev builds fall back to the app key
    /// (server/tools/offline_exit_code.py). Release builds without a key: no offline exit.
    private static var keyData: Data? {
        if let k = AppSession.shared.offlineKey { return k }
        #if FAIRPLAY_DEV
        return Data(Config.appKey.utf8)
        #else
        return nil
        #endif
    }

    static var isAvailable: Bool { keyData != nil }

    static func check(_ typed: String) -> OfflineExitCode.Result {
        state.check(typed, key: keyData)
    }

    /// Dev builds only: the fixed test code (default "0000", -DevExitCode 1234).
    static func devCodeMatches(_ typed: String) -> Bool {
        #if FAIRPLAY_DEV
        let dev = UserDefaults.standard.string(forKey: "DevExitCode") ?? "0000"
        let digits = typed.filter { !$0.isWhitespace && $0 != "-" }
        return !dev.isEmpty && Signing.constantTimeEqual(digits, dev)
        #else
        return false
        #endif
    }
}

/// Mac audit M-17 on iPad: iPadOS can't relaunch an app, so the app remembers the attempt
/// it was locked for. If it ends while locked (crash, forced restart, battery), the next
/// launch reports it and opens that attempt directly; the page then asks to lock again.
enum LockMarker {
    private static var url: URL {
        let fm = FileManager.default
        return (fm.urls(for: .applicationSupportDirectory, in: .userDomainMask).first ?? fm.temporaryDirectory)
            .appendingPathComponent("FairPlay/locked-attempt")
    }

    /// Locked: the attempt id ("-" for an offline start, PreLockState.marker for the
    /// pre-exam lock). nil: not locked.
    static func update(attemptId: String?) {
        if let attemptId {
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? attemptId.write(to: url, atomically: true, encoding: .utf8)
        } else {
            try? FileManager.default.removeItem(at: url)
        }
    }

    /// A lock that never ended (and forgets it): its attempt id when known.
    static func takeLeftover() -> (wasLocked: Bool, attemptId: String?) {
        guard let s = try? String(contentsOf: url, encoding: .utf8) else { return (false, nil) }
        try? FileManager.default.removeItem(at: url)
        // 2026-10-23: only the pre-exam lock (no attempt): nothing to report or resume.
        if PreLockState.markerIsPreLock(s) {
            DebugLog.log("app", "last run ended during the pre-exam lock (no exam): starting normally")
            return (false, nil)
        }
        let id = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return (true, Signing.isSafeToken(id, min: 3, max: 32) ? id : nil)
    }
}
