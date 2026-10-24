import Foundation
import UIKit
import GameController
import FairPlayCore

/// What the lock needs from the screen that shows the exam (ExamViewController).
@MainActor
protocol LockSurface: AnyObject {
    var hostWindow: UIWindow? { get }
    func enterLockedMode()
    func exitLockedMode()
    /// Red screen over the exam until fixed; `actionTitle` adds a button.
    func showBlocking(reason: String, actionTitle: String?, action: (() -> Void)?)
    func hideBlocking()
    /// Covers the exam while the app is not in front (system overlays, app switcher).
    func setPrivacyCover(_ on: Bool)
    func lockStateChanged()
    /// The exam is drawn in the capture-proof layer (review M4).
    var captureShieldActive: Bool { get }
}

/// Owns the locked state on iPad. Defences, strongest first:
///  1. Apple Assessment Mode (AssessmentMode.swift): iPadOS pins FairPlay, blocks
///     screenshots / recording / AirPlay / notifications / dictation / shortcuts.
///  2. Single App Mode on supervised school iPads (or Guided Access).
///     Release builds refuse to lock without 1 or 2; if iPadOS drops them mid-exam the
///     exam is covered until they are back, and the server is told at once.
///  3. Capture shield: the exam is invisible to any capture even without 1.
///  4. Checks every 3 s and on every system notification: displays, capture, Split View /
///     Slide Over / Stage Manager, leaving the app, screenshots, jailbreak, debugger,
///     hardware keyboard, VPN / proxy. Problems block the exam and reach the teacher.
///  5. Third-party keyboards are refused for the whole app (AppDelegate), the clipboard is
///     emptied, the edit menu has no Copy / Look Up / Translate / Share / Writing Tools.
///  6. Signed beats every 5 s carry the real state (AppSession); a killed app is a gap
///     the server flags, and the next launch reports it and goes straight back in.
@MainActor
final class LockdownController {

    static let shared = LockdownController()

    private(set) var isLocked = false
    /// 2026-10-23: locked before the start code, no exam attached yet (FairPlayCore PreLock.swift).
    private(set) var isPreLock = false
    /// A pre-exam lock is coming up (iPadOS may be asking the student first).
    private var wantPreLock = false
    private(set) var state = DeviceState()
    weak var surface: LockSurface?
    var onUnlocked: (() -> Void)?
    /// While waiting for iPadOS (consent prompt): the page keeps waiting on these.
    var onLockPending: (() -> Void)?

    private let assessment = AssessmentMode()
    private var locking = false
    /// 2026-10-23 (review): which lock attempt is current. A cancelled one's late answers
    /// (Assessment Mode, Single App Mode, a retry) see another number and do nothing.
    private var lockAttempt = 0
    private var lockWaiters: [(LockResult) -> Void] = []
    private var singleAppRequested = false
    private var participants: [String] = []
    private var problems: [String: String] = [:]
    private var reported: Set<String> = []
    private var observers: [NSObjectProtocol] = []
    private var checkTimer: Timer?
    private var leaves = 0
    private var screenshots = 0
    private var lastSummary = ""
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    private var pendingTimer: Timer?

    /// How this lock is held. Set once when the lock starts.
    enum Mode: String { case assessment = "assessment mode", singleApp = "single app mode", soft = "watched lock" }
    private(set) var mode: Mode = .soft
    private var lockedAt: Date?

    enum LockResult: Equatable { case locked, failed(String) }

    init() {
        AppSession.shared.checksProvider = { [weak self] in self?.lastSummary ?? "" }
        AppSession.shared.lockedProvider = { [weak self] in self?.isLocked ?? false }
        AppSession.shared.heldProvider = { [weak self] in self?.reallyLocked ?? false }
        assessment.onInterrupted = { [weak self] why in self?.assessmentInterrupted(why) }
        assessment.onLateBegin = { [weak self] in self?.assessmentCameUpLate() ?? false }
    }

    /// Locked AND held by iPadOS (or a dev soft lock): what the signed beat says.
    var reallyLocked: Bool { isLocked && (state.assessment || state.singleApp || state.softLock) }

    /// A lock was asked for and iPadOS has not answered yet (e.g. the Assessment Mode prompt).
    var lockInFlight: Bool { locking }
    /// ... and it is a pre-exam lock with no exam (an exam's lock asked meanwhile makes it the exam's).
    var preLockInFlight: Bool { locking && wantPreLock }

    // MARK: - Lock / unlock

    func lock(participants ids: [String], completion: @escaping (LockResult) -> Void) {
        if isLocked {
            // 2026-10-23: the exam's lock during the pre-exam lock: stays locked, becomes the exam's.
            if isPreLock { promoteToExamLock(participants: ids) }
            completion(.locked)
            return
        }
        if locking && wantPreLock {
            // The exam's lock arrived while the pre-exam lock was still coming up: this lock
            // is the exam's from the start.
            wantPreLock = false
            EventLog.shared.forwarding = true
        }
        startLock(participants: ids, completion: completion)
    }

    /// 2026-10-23: the pre-exam lock. Exactly the exam's lock (Assessment Mode the same way,
    /// "lockPending" while iPadOS asks), with no exam: nothing goes to the server until an
    /// exam is attached.
    func preLock(participants ids: [String], completion: @escaping (LockResult) -> Void) {
        if isLocked { completion(.locked); return }
        if !locking {
            // No exam: a session left from an earlier page (not locked) must not beat for
            // this lock or be taken for its attempt.
            if AppSession.shared.exam != nil { AppSession.shared.clearExam() }
            wantPreLock = true
            EventLog.shared.forwarding = false
        }
        startLock(participants: ids, completion: completion)
    }

    private func startLock(participants ids: [String], completion: @escaping (LockResult) -> Void) {
        lockWaiters.append(completion)
        guard !locking else { return }
        locking = true
        lockAttempt += 1
        participants = ids
        DebugLog.log("lock", "locking")
        onLockPending?()
        let pt = Timer(timeInterval: 4, repeats: true) { _ in
            MainActor.assumeIsolated { LockdownController.shared.onLockPending?() }
        }
        RunLoop.main.add(pt, forMode: .common)
        pendingTimer = pt
        beginAssessment(ids, triesLeft: 2, attempt: lockAttempt)
    }

    /// 2026-10-23 (review): "preunlock" while the pre-exam lock is still coming up (the
    /// Assessment Mode prompt is up). Cancels it: the pending Assessment Mode session is ended
    /// (AssessmentMode ends it too if it comes up later), its waiters get .failed("cancelled")
    /// and nothing locks afterwards. False, and nothing changes, when no pre-exam lock is
    /// coming up (nothing pending, or an exam's lock asked meanwhile).
    @discardableResult
    func cancelPendingPreLock() -> Bool {
        guard locking, wantPreLock, !isLocked else { return false }
        DebugLog.log("lock", "pre-exam lock cancelled before it came up (preunlock)")
        lockAttempt += 1                    // late answers of this attempt are ignored
        assessment.end()
        finishLock(.failed("cancelled"))    // also turns event forwarding back on
        return true
    }

    /// After FairPlay ended while locked, iPadOS still holds the old session for a moment
    /// and refuses with an unknown error while it cleans up: try again shortly.
    private func beginAssessment(_ ids: [String], triesLeft: Int, attempt: Int) {
        guard attempt == lockAttempt, locking else { return }   // cancelled meanwhile
        guard Config.tryAssessmentMode else {
            // Simulator (dev), or signed without the entitlement (free Apple ID, sideloaded
            // test build). fallBack still decides: Single App Mode, else a watched lock
            // only where Config.allowsSoftLock says so, else the lock fails.
            DebugLog.log("lock", "Assessment Mode skipped (simulator, or not in this build's provisioning profile)")
            fallBack(why: "not available in this copy of FairPlay", attempt: attempt)
            return
        }
        assessment.begin(participants: ids) { [weak self] error in
            guard let self, !self.isLocked, attempt == self.lockAttempt, self.locking else { return }
            if error == nil { self.didLock(.assessment); return }
            let ns = error as NSError?
            if triesLeft > 0, ns?.domain == "AEAssessmentErrorDomain", ns?.code == 1 {
                DebugLog.log("lock", "Assessment Mode refused (stale session?), retrying")
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    self.beginAssessment(ids, triesLeft: triesLeft - 1, attempt: attempt)
                }
                return
            }
            self.fallBack(why: ns?.localizedDescription ?? "unknown", attempt: attempt)
        }
    }

    /// No Assessment Mode. Review H1: Single App Mode counts only when FairPlay turns it on
    /// itself (supervised iPad with an Autonomous Single App Mode payload). Guided Access
    /// that was already on was started by someone with its passcode and doesn't count.
    private func fallBack(why: String, attempt: Int) {
        let wasOn = SingleAppMode.anyOn
        SingleAppMode.request(true) { [weak self] ok in
            guard let self, !self.isLocked else { return }
            guard attempt == self.lockAttempt, self.locking else {
                // Cancelled meanwhile (review 2026-10-23): give back what we just turned on.
                if ok && !wasOn { SingleAppMode.request(false) { _ in } }
                return
            }
            if ok && !wasOn {
                self.singleAppRequested = true
                EventLog.shared.record("assessment_unavailable", "using Single App Mode: \(why)")
                self.didLock(.singleApp)
            } else if Config.allowsSoftLock {
                EventLog.shared.record("assessment_unavailable", "watched lock: \(why)")
                self.didLock(.soft)
            } else {
                EventLog.shared.record("lock_failed", wasOn
                    ? "Assessment Mode: \(why); Guided Access was already on (not counted)"
                    : "Assessment Mode: \(why)")
                self.finishLock(.failed("assessment"))
            }
        }
    }

    private func finishLock(_ r: LockResult) {
        locking = false
        pendingTimer?.invalidate(); pendingTimer = nil
        if r != .locked && wantPreLock {
            wantPreLock = false
            EventLog.shared.forwarding = true
        }
        let waiters = lockWaiters
        lockWaiters.removeAll()
        waiters.forEach { $0(r) }
    }

    private func didLock(_ mode: Mode) {
        isLocked = true
        isPreLock = wantPreLock && AppSession.shared.exam == nil
        wantPreLock = false
        if !isPreLock { EventLog.shared.forwarding = true }
        self.mode = mode
        lockedAt = Date()
        state = DeviceState()
        state.assessment = mode == .assessment
        state.singleApp = mode == .singleApp
        state.softLock = mode == .soft
        problems.removeAll(); reported.removeAll()
        leaves = 0; screenshots = 0
        OfflineExit.reset()
        LockMarker.update(attemptId: isPreLock ? PreLockState.marker : (AppSession.shared.exam?.attemptId ?? "-"))
        EventLog.shared.record("lock_started", mode.rawValue)
        clearPasteboard()
        surface?.enterLockedMode()
        installWatchers()
        // 2026-10-24 (the Mac fix "the lock takes the front back"): the student may have
        // left FairPlay between "Lock and continue" and the lock coming up (a watched or
        // Single App Mode lock can come up while FairPlay is in the background, before the
        // watchers above existed). iPadOS lets no app bring itself back: count it as leaving
        // and cover the exam until the student returns (didBecomeActive removes the cover).
        let appState = UIApplication.shared.applicationState
        if appState == .background {
            leaves += 1
            EventLog.shared.record("app_backgrounded", "not in front when the lock started")
            surface?.setPrivacyCover(true)
        } else if appState == .inactive {
            // Review 2026-10-24: Control Center / Notification Center (or iPadOS's own lock
            // prompt) over FairPlay when the lock came up: willResignActive already fired
            // before the watchers existed, so cover now too. Not a leave and not reported
            // (the Assessment Mode prompt also makes the app inactive); didBecomeActive
            // removes the cover as soon as FairPlay is in front again.
            DebugLog.log("lock", "inactive when the lock started (system overlay): exam covered")
            surface?.setPrivacyCover(true)
        }
        runChecks(atLock: true)
        let t = Timer(timeInterval: 3, repeats: true) { _ in
            MainActor.assumeIsolated { LockdownController.shared.runChecks(atLock: false) }
        }
        RunLoop.main.add(t, forMode: .common)
        checkTimer = t
        AppSession.shared.beat()
        finishLock(.locked)
    }

    func unlock() {
        guard isLocked else { return }
        DebugLog.log("lock", "unlocking")
        isLocked = false
        assessment.end()
        if singleAppRequested { SingleAppMode.request(false) { _ in } }
        singleAppRequested = false
        observers.forEach { NotificationCenter.default.removeObserver($0) }
        observers.removeAll()
        checkTimer?.invalidate(); checkTimer = nil
        endBackgroundTask()
        problems.removeAll()
        OfflineExit.reset()
        LockMarker.update(attemptId: nil)
        surface?.hideBlocking()
        surface?.setPrivacyCover(false)
        surface?.exitLockedMode()
        EventLog.shared.record("lock_ended")
        AppSession.shared.beat()
        if isPreLock {
            // After "lock_ended" above: that one stays local too.
            isPreLock = false
            EventLog.shared.forwarding = true
        }
        state = DeviceState()
        onUnlocked?()
    }

    // MARK: - Pre-exam lock (2026-10-23)

    /// The exam was attached (its lock / session / website exam): the pre-exam lock becomes
    /// the exam lock without unlocking. From here on everything is as after lock(): events
    /// reach the teacher (current problems reported again for this exam), beats run, the
    /// relaunch marker names the attempt and only the exam's ways out unlock.
    /// Assessment Mode keeps the participants it began with (none for a pre-exam lock):
    /// changing them would mean ending it, i.e. unlocking.
    func promoteToExamLock(participants ids: [String] = []) {
        guard isLocked, isPreLock else { return }
        DebugLog.log("lock", "pre-exam lock -> exam lock (stays locked)")
        if mode == .assessment && Set(ids) != Set(participants) {
            DebugLog.log("lock", "teacher-allowed apps not added: Assessment Mode began without them")
        }
        isPreLock = false
        EventLog.shared.forwarding = true
        LockMarker.update(attemptId: AppSession.shared.exam?.attemptId ?? "-")
        reported.removeAll()
        leaves = 0; screenshots = 0
        OfflineExit.reset()
        EventLog.shared.record("lock_started", mode.rawValue)
        runChecks(atLock: true)
        AppSession.shared.beat()
    }

    /// "Not ready? Leave and unlock" (or the invigilator exit) during the pre-exam lock:
    /// the normal unlock with no server check, since there is no attempt. False, and
    /// nothing changes, when an exam is attached or this is not the pre-exam lock.
    @discardableResult
    func endPreLock() -> Bool {
        guard PreLockState.onPreUnlock(locked: isLocked, preLocked: isPreLock,
                                       hasExam: AppSession.shared.exam != nil) == .unlock else { return false }
        DebugLog.log("lock", "pre-exam lock ended (no exam was attached)")
        unlock()
        return true
    }

    // MARK: - Assessment Mode lost mid-exam

    /// A begin we had given up on came up after all: keep it only to upgrade a dev soft
    /// lock. Otherwise (not locked, or locked another way) AssessmentMode ends it.
    private func assessmentCameUpLate() -> Bool {
        guard isLocked, mode == .soft else { return false }
        mode = .assessment
        state.assessment = true
        state.softLock = false
        EventLog.shared.record("assessment_resumed", "Assessment Mode came up late")
        runChecks(atLock: false)
        AppSession.shared.beat()
        return true
    }

    private func assessmentInterrupted(_ why: String) {
        guard isLocked, mode == .assessment else { return }
        state.assessment = false
        EventLog.shared.record("assessment_interrupted", why)
        runChecks(atLock: false)          // red screen + "Resume locked mode"
        AppSession.shared.beat()
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.resumeAssessment() }
    }

    /// After a real interruption only (review M3): never in Single App or soft mode, and
    /// never a second begin while one is in flight (AssessmentMode joins it). Re-review N1:
    /// waits for the interrupted session to wind down and retries iPadOS's "stale session"
    /// refusal like the first begin does.
    func resumeAssessment(triesLeft: Int = 3) {
        guard isLocked, mode == .assessment, !assessment.isActive else { return }
        guard assessment.isIdle || assessment.isPending else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.resumeAssessment(triesLeft: triesLeft) }
            return
        }
        assessment.begin(participants: participants) { [weak self] error in
            guard let self, self.isLocked, self.mode == .assessment else { return }
            if let ns = error as NSError? {
                if triesLeft > 0, ns.domain == "AEAssessmentErrorDomain", ns.code == 1 {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { self.resumeAssessment(triesLeft: triesLeft - 1) }
                }
                return
            }
            self.state.assessment = true
            EventLog.shared.record("assessment_resumed")
            self.runChecks(atLock: false)
            AppSession.shared.beat()
        }
    }

    /// The "Resume locked mode" button (re-review N5): Assessment Mode, or Single App Mode
    /// again when that is how this lock is held.
    func resumeLock() {
        switch mode {
        case .assessment: resumeAssessment()
        case .singleApp:
            SingleAppMode.request(true) { [weak self] ok in
                guard let self, self.isLocked, self.mode == .singleApp else { return }
                if ok { EventLog.shared.record("single_app_on", "Single App Mode turned on again") }
                self.runChecks(atLock: false)
            }
        case .soft: break
        }
    }

    private func endBackgroundTask() {
        if backgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(backgroundTask)
            backgroundTask = .invalid
        }
    }

    // MARK: - Watchers

    private func installWatchers() {
        let nc = NotificationCenter.default
        func on(_ name: Notification.Name, _ block: @escaping @MainActor (Notification) -> Void) {
            observers.append(nc.addObserver(forName: name, object: nil, queue: .main) { note in
                MainActor.assumeIsolated { block(note) }
            })
        }
        on(UIApplication.willResignActiveNotification) { [weak self] _ in
            guard let self, self.isLocked else { return }
            // Control Center / Notification Center / Siri / an incoming call sheet.
            EventLog.shared.record("focus_lost", "the exam was not in front")
            self.surface?.setPrivacyCover(true)
        }
        on(UIApplication.didEnterBackgroundNotification) { [weak self] _ in
            guard let self, self.isLocked else { return }
            if self.assessment.isActive && !self.participants.isEmpty {
                // Review M5: iPadOS only lets the student into a teacher-allowed app here.
                EventLog.shared.record("app_backgrounded", "switched to an allowed app (Assessment Mode on)")
            } else {
                self.leaves += 1
                EventLog.shared.record("app_backgrounded", "left FairPlay (\(self.leaves)x this exam)")
            }
            self.runChecks(atLock: false)
            // Tell the server now, while iPadOS still lets us run (review L4: one task at a
            // time; the expiry handler ends its own task, not whatever is current).
            self.endBackgroundTask()
            var task: UIBackgroundTaskIdentifier = .invalid
            task = UIApplication.shared.beginBackgroundTask(withName: "fairplay-beat") {
                MainActor.assumeIsolated {
                    UIApplication.shared.endBackgroundTask(task)
                    let me = LockdownController.shared
                    if me.backgroundTask == task { me.backgroundTask = .invalid }
                }
            }
            self.backgroundTask = task
            AppSession.shared.beat()
        }
        on(UIApplication.didBecomeActiveNotification) { [weak self] _ in
            guard let self, self.isLocked else { return }
            self.endBackgroundTask()
            self.surface?.setPrivacyCover(false)
            self.runChecks(atLock: false)
        }
        on(UIApplication.userDidTakeScreenshotNotification) { [weak self] _ in
            guard let self, self.isLocked else { return }
            self.screenshots += 1
            EventLog.shared.record("screenshot_taken", "screenshot \(self.screenshots) (the exam is hidden from it)")
            self.runChecks(atLock: false)
        }
        on(UIScreen.capturedDidChangeNotification) { [weak self] _ in self?.runChecks(atLock: false) }
        on(UIScene.willConnectNotification) { [weak self] _ in self?.runChecks(atLock: false) }
        on(UIScene.didDisconnectNotification) { [weak self] _ in self?.runChecks(atLock: false) }
        on(.GCKeyboardDidConnect) { [weak self] _ in
            guard let self, self.isLocked else { return }
            EventLog.shared.record("keyboard_connected", "hardware keyboard")
            self.runChecks(atLock: false)
        }
        on(UIApplication.protectedDataWillBecomeUnavailableNotification) { _ in
            EventLog.shared.record("screen_locked", "the iPad was locked during the exam")
        }
        on(UIPasteboard.changedNotification) { [weak self] _ in self?.clearPasteboard() }
        on(UIAccessibility.guidedAccessStatusDidChangeNotification) { [weak self] _ in
            guard let self, self.isLocked else { return }
            EventLog.shared.record(SingleAppMode.anyOn ? "single_app_on" : "single_app_off")
            self.runChecks(atLock: false)
        }
    }

    private func clearPasteboard() {
        guard isLocked else { return }
        if UIPasteboard.general.numberOfItems > 0 { UIPasteboard.general.items = [] }
    }

    // MARK: - Checks

    func runChecks(atLock: Bool) {
        guard isLocked else { return }
        var s = IntegrityChecks.collect(window: surface?.hostWindow)
        s.assessment = mode == .assessment && assessment.isActive
        s.softLock = mode == .soft
        // Re-review N5: iPadOS may report Guided Access a moment after the request said yes.
        let justLocked = lockedAt.map { Date().timeIntervalSince($0) < 3 } ?? false
        s.singleApp = mode == .singleApp && singleAppRequested && (SingleAppMode.anyOn || justLocked)
        s.shield = surface?.captureShieldActive ?? false
        s.participants = s.assessment ? participants.count : 0      // "apps=" in the beat
        s.leaves = leaves
        s.screenshots = screenshots
        s.appAttest = AppAttest.shared.status
        s.sealedPending = Sealed.hasPending
        state = s

        let found = Integrity.problems(s, release: !Config.isDevBuild)
        for key in found.keys.sorted() where !reported.contains(key) {
            reported.insert(key)
            if let (t, d) = Integrity.event(forProblem: key, state: s) { EventLog.shared.record(t, d) }
        }
        if s.debugger && !reported.contains("dbg") { reported.insert("dbg"); EventLog.shared.record("debugger_attached") }
        if !s.jailbreak.isEmpty && !reported.contains("jb") {
            reported.insert("jb"); EventLog.shared.record("jailbreak_detected", s.jailbreak.prefix(4).joined(separator: ", "))
        }
        if s.vpn && !reported.contains("vpn") { reported.insert("vpn"); EventLog.shared.record("vpn_on", "VPN tunnel active") }
        if s.proxy && !reported.contains("proxy") { reported.insert("proxy"); EventLog.shared.record("proxy_on", "HTTP proxy set") }

        let summary = s.summary
        if found != problems || atLock {
            DebugLog.log("check", found.isEmpty ? "all checks OK (\(summary))" : "BLOCKED by \(found.keys.sorted().joined(separator: ", "))")
        }
        let changed = summary != lastSummary || found != problems
        problems = found
        lastSummary = summary
        if problems.isEmpty {
            surface?.hideBlocking()
        } else {
            let text = problems.keys.sorted().compactMap { problems[$0] }.joined(separator: "\n\n")
                + "\n\nStuck? Invigilator: press and hold the lock bar at the top for 3 seconds."
            if problems["assessment"] != nil {
                surface?.showBlocking(reason: text, actionTitle: "Resume locked mode") { [weak self] in self?.resumeLock() }
            } else {
                surface?.showBlocking(reason: text, actionTitle: nil, action: nil)
            }
        }
        surface?.lockStateChanged()
        if changed && !atLock { AppSession.shared.beat() }
    }

    /// Before locking (bridge "preflight"): what must be fixed first, plus the S1 device-check
    /// rows (server, clock, minimum version...). Changes nothing. Answers within ~2.5 s.
    func preflight(_ done: @escaping @MainActor (_ problems: [String], _ rows: [CheckRow]) -> Void) {
        let s = IntegrityChecks.collect(window: surface?.hostWindow)
        let major = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        let problems = Integrity.preflightProblems(s, release: !Config.isDevBuild, osMajor: major)
        var answered = false
        let finish: @MainActor (Bool, String?) -> Void = { reached, minimum in
            guard !answered else { return }
            answered = true
            var blocking = problems
            if let minimum, !AppVersion.meets(Config.version, minimum: minimum) {
                blocking.append(("app_version", "Update FairPlay: this is version \(Config.version), the exam needs \(minimum) or newer."))
            }
            let rows = CheckRow.ipadRows(problems: blocking.filter { $0.id != "app_version" }, appVersion: Config.version,
                                         minVersion: minimum, osVersion: UIDevice.current.systemVersion,
                                         serverReached: reached, clockOffset: reached ? ServerClock.shared.offset : nil,
                                         batteryPercent: s.batteryPercent, charging: s.charging, vpn: s.vpn)
            let texts = blocking.map(\.message)
            DebugLog.log("check", texts.isEmpty ? "preflight OK" : "preflight problems: " + texts.joined(separator: " || "))
            done(texts, rows)
        }
        Net.request("GET", path: "api/app/version", token: "", appSession: nil, body: nil) { status, json in
            finish(status != nil, status == 200 ? json?["ipad"] as? String : nil)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { finish(false, nil) }
    }
}
