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
        /// Nothing is locked: reply {type: "unlocked"}.
        case notLocked
        /// An exam lock (or an exam attached to the lock): reply {type: "preunlockRefused"}.
        case refuse
    }

    public static func onPreUnlock(locked: Bool, preLocked: Bool, hasExam: Bool) -> OnPreUnlock {
        guard locked else { return .notLocked }
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

    /// Pages the pre-exam lock may show besides the exam pages (no attempt is known): the
    /// student home, an exam's "Before you start" page (/start, where the start code is typed) and its check-in page, and the
    /// sign-in page if the token runs out meanwhile.
    public static func allowedWhilePreLocked(path: String) -> Bool {
        let p = path.lowercased()
        if ["/student", "/student/", "/student/login"].contains(p) { return true }
        // /student/exams/<exam id>/start or /checkin
        let parts = p.split(separator: "/", omittingEmptySubsequences: false)
        return parts.count == 5 && parts[0].isEmpty && parts[1] == "student" && parts[2] == "exams"
            && !parts[3].isEmpty && (parts[4] == "start" || parts[4] == "checkin")
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
    /// page of the exam it was taken for (when known), else the student home.
    public static func homePath(examId: String?) -> String {
        guard let examId, startPageExamId(path: "/student/exams/\(examId)/start") == examId else { return "student" }
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
