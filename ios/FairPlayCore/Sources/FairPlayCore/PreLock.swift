import Foundation

/// 2026-10-23: the pre-exam lock (same rules as the Mac's PreLock.swift). "Before you
/// start" -> "Lock and continue" locks the iPad exactly like an exam (Assessment Mode the
/// same way) while the page shows the start code, but no exam is attached yet: no attempt,
/// no session, no beats, no events to the server. The student may still leave
/// ({action: "preunlock"}). The exam's own "lock" (or a session for the attempt) turns it
/// into the exam lock without unlocking.
public enum PreLockState {

    /// {action: "prelock"}
    public enum OnPreLock: Equatable {
        /// Take the lock with no exam; reply {type: "locked", prelock: true}.
        case lock
        /// Already the pre-exam lock: reply {type: "locked", prelock: true}.
        case alreadyPreLocked
        /// Already locked for an exam (or an offline start): leave the lock alone and
        /// reply {type: "locked", prelock: false}.
        case examLocked
    }

    public static func onPreLock(locked: Bool, preLocked: Bool, hasExam: Bool) -> OnPreLock {
        guard locked else { return .lock }
        return preLocked && !hasExam ? .alreadyPreLocked : .examLocked
    }

    /// {action: "preunlock"}
    public enum OnPreUnlock: Equatable {
        /// Unlock with no server check: there is no attempt.
        case unlock
        /// Nothing is locked and no lock is coming up: reply {type: "unlocked"}.
        case notLocked
        /// 2026-10-23 (review): a pre-exam lock is still coming up (the iPad's Assessment Mode
        /// prompt): cancel it (fail its waiters), and only then reply {type: "unlocked"}.
        case cancelPending
        /// An exam lock (or an exam attached to the lock, or an exam lock still coming up):
        /// reply {type: "preunlockRefused"}. Never "unlocked" while a lock is in flight.
        case refuse
    }

    /// `lockInFlight`: a lock was asked for and has not answered yet; `preLockInFlight`: that
    /// lock is a pre-exam lock with no exam (the Mac locks at once: both always false there).
    public static func onPreUnlock(locked: Bool, preLocked: Bool, hasExam: Bool,
                                   lockInFlight: Bool = false, preLockInFlight: Bool = false) -> OnPreUnlock {
        guard locked else {
            guard lockInFlight else { return .notLocked }
            return preLockInFlight && !hasExam ? .cancelPending : .refuse
        }
        return preLocked && !hasExam ? .unlock : .refuse
    }

    /// The exam lock ({action: "lock"}, online or offline) or an exam session arriving.
    public enum OnExamLock: Equatable {
        /// The normal lock path.
        case lock
        /// Keep the screen locked and make the pre-exam lock the exam lock.
        case promote
    }

    public static func onExamLock(locked: Bool, preLocked: Bool) -> OnExamLock {
        locked && preLocked ? .promote : .lock
    }

    /// The invigilator exit during the pre-exam lock simply unlocks (no code, no server):
    /// there is no attempt to protect.
    public static func emergencyExitUnlocksAtOnce(locked: Bool, preLocked: Bool, hasExam: Bool) -> Bool {
        locked && preLocked && !hasExam
    }

    /// Pages the pre-exam lock may show (no attempt is known yet). 2026-10-23 (review): one
    /// rule on the Mac, the iPad and Windows (NavigationPolicy.AllowedWhilePreLocked): the
    /// sign-in page (the token may run out meanwhile); the "Before you start" page of the exam
    /// the lock was taken for (/student/exams/<id>/start; any exam's when that is not known);
    /// the exam page the start code creates (/student/exam/<anything>) and its done page
    /// (/student/done/<anything>). Never the student home or the check-in page: the page asks
    /// "preunlock" before it goes back there.
    public static func allowedWhilePreLocked(path: String, examId: String? = nil) -> Bool {
        let p = path.lowercased()
        if p == "/student/login" || p == "/student/login/" { return true }
        for root in ["/student/exam/", "/student/done/"] where p.hasPrefix(root) && p.count > root.count {
            return true
        }
        guard let id = startPageExamId(path: path) else { return false }
        guard let examId, !examId.isEmpty else { return true }
        return id.lowercased() == examId.lowercased()
    }

    /// The exam id of a "Before you start" page, /student/exams/<id>/start (trailing slash
    /// allowed), or nil for any other path or an id that is not a plain token (1-64 of
    /// A-Z a-z 0-9 _ -).
    public static func startPageExamId(path: String) -> String? {
        let parts = path.split(separator: "/", omittingEmptySubsequences: true)
        guard parts.count == 4, parts[0].lowercased() == "student", parts[1].lowercased() == "exams",
              parts[3].lowercased() == "start", !path.contains("//") else { return nil }
        let id = String(parts[2])
        guard (1...64).contains(id.count), id.unicodeScalars.allSatisfy({ idChars.contains($0) }) else { return nil }
        return id
    }

    private static let idChars = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyz"
                                                + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

    /// Where an in-page route change the pre-exam lock does not allow goes back to: the start
    /// page of the exam it was taken for. nil when that is not known (2026-10-23, review: the
    /// student home is not allowed while pre-locked any more; the caller goes back to the last
    /// allowed page it showed instead).
    public static func homePath(examId: String?) -> String? {
        guard let examId, startPageExamId(path: "/student/exams/\(examId)/start") == examId else { return nil }
        return "student/exams/\(examId)/start"
    }

    /// What the relaunch marker holds during the pre-exam lock. Not a safe token (the "+"),
    /// so it can never be read back as an attempt id.
    public static let marker = "+prelock"

    /// A marker left by a run that ended during the pre-exam lock: nothing to report, and
    /// the next launch starts normally (unlocked).
    public static func markerIsPreLock(_ text: String) -> Bool {
        text.trimmingCharacters(in: .whitespacesAndNewlines) == marker
    }
}
