// 2026-10-23: the pre-exam lock ("Before you start" -> "Lock and continue" -> start code).
import Testing
import Foundation
@testable import Proctora

@Suite struct PreLockTests {

    @Test func preLockRequest() {
        #expect(PreLockState.onPreLock(locked: false, preLocked: false, hasExam: false) == .lock)
        // A session left from an unlocked page doesn't stop it (the bridge drops that session).
        #expect(PreLockState.onPreLock(locked: false, preLocked: false, hasExam: true) == .lock)
        #expect(PreLockState.onPreLock(locked: true, preLocked: true, hasExam: false) == .alreadyPreLocked)
        // Locked for an exam (or an offline start with no session yet): the lock is left alone.
        #expect(PreLockState.onPreLock(locked: true, preLocked: false, hasExam: true) == .examLocked)
        #expect(PreLockState.onPreLock(locked: true, preLocked: false, hasExam: false) == .examLocked)
        #expect(PreLockState.onPreLock(locked: true, preLocked: true, hasExam: true) == .examLocked)
    }

    @Test func preUnlockOnlyWithoutAnExam() {
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: false) == .unlock)
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: true) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: false, hasExam: true) == .refuse)
        // An offline-start exam lock has no session either: still refused.
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: false, hasExam: false) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: false) == .notLocked)
    }

    /// Review 2026-10-23: never "unlocked" while a lock is still coming up (the iPad's
    /// Assessment Mode prompt; the Mac locks at once, so it never passes these).
    @Test func preUnlockWhileALockIsComingUp() {
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: false,
                                         lockInFlight: true, preLockInFlight: true) == .cancelPending)
        // An exam's lock coming up (or one an exam lock joined) is refused.
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: false,
                                         lockInFlight: true, preLockInFlight: false) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: true,
                                         lockInFlight: true, preLockInFlight: true) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: false,
                                         lockInFlight: false, preLockInFlight: false) == .notLocked)
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: false,
                                         lockInFlight: false, preLockInFlight: false) == .unlock)
    }

    @Test func examLockPromotesThePreLock() {
        #expect(PreLockState.onExamLock(locked: true, preLocked: true) == .promote)
        #expect(PreLockState.onExamLock(locked: false, preLocked: false) == .lock)
        #expect(PreLockState.onExamLock(locked: true, preLocked: false) == .lock)   // lock() keeps the exam lock
    }

    @Test func emergencyShortcut() {
        #expect(PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: true, hasExam: false))
        #expect(!PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: true, hasExam: true))
        #expect(!PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: false, hasExam: false))
        #expect(!PreLockState.emergencyExitUnlocksAtOnce(locked: false, preLocked: false, hasExam: false))
    }

    @Test func preflightLeavesOutWhatTheLockItselfDoes() {
        let all: [(id: String, message: String)] = [("accessibility", "a"), ("displays", "d"),
                                                    ("input_source", "i"), ("remote", "r")]
        #expect(PreLockState.preflightProblems(all, preLocked: false).map(\.id)
                == ["accessibility", "displays", "input_source", "remote"])
        #expect(PreLockState.preflightProblems(all, preLocked: true).map(\.id) == ["displays", "remote"])
    }

    /// Review 2026-10-23: one rule on the Mac, the iPad and Windows.
    @Test func startCodePagesWhilePreLocked() {
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/login"))
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/login/", examId: "e_1"))
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/exams/e_1/start", examId: "e_1"))
        #expect(PreLockState.allowedWhilePreLocked(path: "/Student/Exams/E_1/Start", examId: "e_1"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams/e_2/start", examId: "e_1"))
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/exams/e_2/start"))     // exam not known: any
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/exam/a_Xy12", examId: "e_1"))
        #expect(PreLockState.allowedWhilePreLocked(path: "/student/done/a_Xy12", examId: "e_1"))
        // Not the student home, not check-in, not the bare roots.
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams/e_1/checkin", examId: "e_1"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams/e_1/checkin"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exam/"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/done/"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams/e_1/started"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/exams/e_1"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/external/a_1"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/student/grades"))
        #expect(!PreLockState.allowedWhilePreLocked(path: "/teacher/exams/e_1/start"))

        #expect(MainWindowController.allowedWhileLocked(path: "/student/exams/e_1/start", attemptId: nil,
                                                        preLock: true, examId: "e_1"))
        #expect(!MainWindowController.allowedWhileLocked(path: "/student/exams/e_2/start", attemptId: nil,
                                                         preLock: true, examId: "e_1"))
        #expect(MainWindowController.allowedWhileLocked(path: "/student/exam/a_9", attemptId: nil, preLock: true))
        #expect(!MainWindowController.allowedWhileLocked(path: "/student", attemptId: nil, preLock: true))
        #expect(!MainWindowController.allowedWhileLocked(path: "/student/exams/e_1/checkin", attemptId: nil, preLock: true))
        #expect(!MainWindowController.allowedWhileLocked(path: "/student/grades", attemptId: nil, preLock: true))
        // Not for an exam lock, offline start included.
        #expect(!MainWindowController.allowedWhileLocked(path: "/student/exams/e_1/start", attemptId: nil))
        #expect(!MainWindowController.allowedWhileLocked(path: "/student", attemptId: "a_1", preLock: true))
    }

    @Test func startPageAndWayBack() {
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1/start") == "e_1")
        #expect(PreLockState.startPageExamId(path: "/student/exams/E-9/start/") == "E-9")
        #expect(PreLockState.startPageExamId(path: "/Student/Exams/e1/START") == "e1")
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1/checkin") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams//start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student//exams/e_1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/e%2F1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/teacher/exams/e_1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/" + String(repeating: "a", count: 65) + "/start") == nil)
        #expect(PreLockState.homePath(examId: "e_1") == "student/exams/e_1/start")
        // Review 2026-10-23: no student home to fall back to (not allowed while pre-locked).
        #expect(PreLockState.homePath(examId: nil) == nil)
        #expect(PreLockState.homePath(examId: "../teacher") == nil)
        // The way back is a page the pre-exam lock shows.
        #expect(PreLockState.allowedWhilePreLocked(path: "/" + (PreLockState.homePath(examId: "e_1") ?? ""), examId: "e_1"))
    }

    @Test func watchdogMarker() {
        let exam = AppSession.Exam(attemptId: "a_1", token: "tok", session: "sess-12345678")
        #expect(Watchdog.markerText(exam: nil, preLock: true) == "\n\n\nprelock")
        #expect(Watchdog.markerText(exam: exam, preLock: false) == "a_1\ntok\nsess-12345678")
        #expect(Watchdog.markerIsPreLock(Watchdog.markerText(exam: nil, preLock: true)))
        #expect(!Watchdog.markerIsPreLock(Watchdog.markerText(exam: nil, preLock: false)))   // offline start
        #expect(!Watchdog.markerIsPreLock(Watchdog.markerText(exam: exam, preLock: false)))
        // An attempt named in the marker always counts as an exam lock.
        #expect(!Watchdog.markerIsPreLock(Watchdog.markerText(exam: exam, preLock: true)))
    }

    /// Review 2026-10-23: the watchdog's mode comes from its launch argument, never from the
    /// student-writable marker.
    @Test func watchdogModeFromItsArgument() {
        #expect(Watchdog.arguments(parent: 42, preLock: true) == ["--watchdog", "42", "--prelock"])
        #expect(Watchdog.arguments(parent: 42, preLock: false) == ["--watchdog", "42"])
        let pre = Watchdog.parseArguments(["FairPlayGuard"] + Watchdog.arguments(parent: 42, preLock: true))
        #expect(pre?.parent == 42 && pre?.preLock == true)
        let exam = Watchdog.parseArguments(["FairPlayGuard"] + Watchdog.arguments(parent: 42, preLock: false))
        #expect(exam?.parent == 42 && exam?.preLock == false)
        #expect(Watchdog.parseArguments(["FairPlay"]) == nil)
        #expect(Watchdog.parseArguments(["FairPlay", "--watchdog", "x"]) == nil)
        // An exam watchdog reports and reopens even when the marker was rewritten to look
        // like the pre-exam lock's.
        let rewritten = Watchdog.markerText(exam: nil, preLock: true)
        #expect(Watchdog.onParentGone(preLockMode: false, markerText: rewritten) == .reportAndReopen)
        #expect(Watchdog.onParentGone(preLockMode: false, markerText: nil) == .reportAndReopen)
        // A pre-exam one stays silent; it removes only the pre-exam lock's own marker.
        #expect(Watchdog.onParentGone(preLockMode: true, markerText: rewritten) == .silent(removeMarker: true))
        let examMarker = Watchdog.markerText(exam: AppSession.Exam(attemptId: "a_1", token: "tok", session: "sess-12345678"),
                                             preLock: false)
        #expect(Watchdog.onParentGone(preLockMode: true, markerText: examMarker) == .silent(removeMarker: false))
    }
}
