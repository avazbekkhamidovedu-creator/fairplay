import AppKit
import Carbon.HIToolbox

/// What the lock needs from the window that shows the exam.
protocol LockSurface: AnyObject {
    func enterLockedMode()
    func exitLockedMode()
    func takeFocus()
    func coverAllScreens()
    /// `quitAction`: shows a "Quit these apps now" button when non-nil.
    func showBlocking(reason: String, quitAction: (() -> Void)?)
    func hideBlocking()
}

/// Owns the locked state. Defences, strongest first (same as lockdown 0.2):
///  1. Session-wide CGEventTap swallows every escape shortcut. Lock refuses to start without it.
///  2. Kiosk presentation options: Dock/menu bar hidden, Cmd-Tab, force-quit, hide, logout disabled.
///  3. Focus guard re-asserts our window every 300 ms (never fights system dialogs).
///  4. Space-change, display-change and clipboard watchers.
///  5. (audit 2026-10-12) At lock and every 3 s: running apps/processes, VM, mirrored or
///     extra displays, remote session, tap health, keyboard input source. Problems put a
///     blocking screen over the exam until fixed and are reported to the teacher.
///  6. (audit) Watchdog + ignored SIGTERM so killing the app is loud; the app's own signed
///     beats tell the server whether it is really locked.
final class LockdownController {

    static let shared = LockdownController()

    private(set) var isLocked = false
    /// 2026-10-23: locked before the start code, no exam attached yet (PreLock.swift).
    private(set) var isPreLock = false
    private var observers: [NSObjectProtocol] = []
    private var focusTimer: Timer?
    private var pasteboardTimer: Timer?
    private var checkTimer: Timer?
    private var safetyWork: DispatchWorkItem?
    private var lastChangeCount = 0

    /// Current blocking problems by check name -> message shown to the student.
    private var problems: [String: String] = [:]
    /// Check names already reported to the server during this lock (one event per kind of problem).
    private var reported: Set<String> = []
    private var lastSummary = ""
    /// Apps asked to quit: pid -> time asked (force-quit after a 2 s grace period).
    private var quitting: [pid_t: Date] = [:]
    /// Windowed apps open behind the exam this lock (teacher-allowed ones; names, for the beat).
    private var backgroundApps: Set<String> = []
    /// M-13: the process list could not be read this round ("proc=?" in the beat).
    private var procListFailed = false
    /// M-12: when this lock started; scripts started well before it are only reported.
    private var lockedAt: Date?

    weak var surface: LockSurface?
    /// True while our own exit dialog's code field is open: macOS then reports "secure
    /// input" because of US, which must not be flagged as another app (audit M-02).
    var ownSecureField = false
    /// Called after the lock ends for any reason (submit, emergency exit, safety timer).
    var onUnlocked: (() -> Void)?

    init() {
        AppSession.shared.checksProvider = { [weak self] in self?.lastSummary ?? "" }
    }

    /// Returns false (and stays unlocked) when Accessibility is not granted.
    @discardableResult
    func lock() -> Bool {
        guard !isLocked else { return true }
        guard EventTap.shared.start() else {
            EventLog.shared.record(.tapUnavailable)
            DebugLog.log("lock", "LOCK FAILED: keyboard tap not available (Accessibility off?)")
            return false
        }
        DebugLog.log("lock", "locking")
        let devKept = LockdownController.names(LockdownController.devKeptApps())
        if !devKept.isEmpty {
            DebugLog.log("lock", "dev build: left running (Allowlist.devKeep, hidden): " + devKept.joined(separator: ", "))
        }
        isLocked = true
        problems.removeAll(); reported.removeAll(); quitting.removeAll()
        // Audit M-09 (review 2026-10-14: the flag was never set, so VoiceOver chords were
        // always swallowed). Refreshed by every check round too.
        EventTap.voiceOverOn = NSWorkspace.shared.isVoiceOverEnabled
        backgroundApps.removeAll(); procListFailed = false; lockedAt = Date()
        OfflineExit.reset()
        EventLog.shared.record(.lockStarted)
        if IsSecureEventInputEnabled() { EventLog.shared.record(.secureInputOn) }

        NSApp.presentationOptions = [
            .hideDock, .hideMenuBar, .disableAppleMenu,
            .disableProcessSwitching, .disableForceQuit,
            .disableSessionTermination, .disableHideApplication
        ]

        surface?.enterLockedMode()
        // The student may have switched to another app during "Close them and start"
        // (2026-10-24): hide it so FairPlay really comes to the front (reclaimFront).
        hideRefusedLogged.removeAll()
        reclaimFront(from: NSWorkspace.shared.frontmostApplication)
        installWorkspaceWatchers()
        installFocusGuard()
        clearAndGuardPasteboard()
        Watchdog.lockStarted(preLock: isPreLock)
        runChecks(atLock: true)
        installCheckTimer()
        AppSession.shared.beat()

        let minutes = Config.safetyUnlockMinutes   // always 0 in release builds
        if minutes > 0 {
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.isLocked else { return }
                EventLog.shared.record(.safetyUnlock)
                self.unlock()
            }
            safetyWork = work
            DispatchQueue.main.asyncAfter(deadline: .now() + minutes * 60, execute: work)
        }
        return true
    }

    func unlock() {
        guard isLocked else { return }
        DebugLog.log("lock", "unlocking")
        isLocked = false
        Allowlist.setTeacherAllowed(nil)      // the next exam brings its own list
        safetyWork?.cancel(); safetyWork = nil
        EventTap.shared.stop()
        NSApp.presentationOptions = []
        observers.forEach {
            NSWorkspace.shared.notificationCenter.removeObserver($0)
            NotificationCenter.default.removeObserver($0)
        }
        observers.removeAll()
        focusTimer?.invalidate(); focusTimer = nil
        pasteboardTimer?.invalidate(); pasteboardTimer = nil
        checkTimer?.invalidate(); checkTimer = nil
        problems.removeAll()
        OfflineExit.reset()
        Watchdog.lockEnded()
        surface?.hideBlocking()
        surface?.exitLockedMode()
        EventLog.shared.record(.lockEnded)
        AppSession.shared.beat()
        if isPreLock {
            // After .lockEnded above: that one stays local too (setForwarding is in order).
            isPreLock = false
            EventLog.shared.setForwarding(true)
        }
        onUnlocked?()
    }

    // MARK: - Pre-exam lock (2026-10-23, PreLock.swift)

    /// The same lock as an exam's (tap, kiosk options, watchers, checks, watchdog), with no
    /// exam: nothing goes to the server until an exam is attached. False (and unlocked)
    /// when Accessibility is not granted, like lock().
    @discardableResult
    func preLock() -> Bool {
        guard !isLocked else { return true }
        EventLog.shared.setForwarding(false)
        isPreLock = true
        if lock() {
            DebugLog.log("lock", "pre-exam lock on (no exam attached)")
            return true
        }
        isPreLock = false
        EventLog.shared.setForwarding(true)
        return false
    }

    /// The exam was attached (its lock / session / website exam): the pre-exam lock becomes
    /// the exam lock without unlocking. From here on everything is exactly as after lock():
    /// events reach the teacher (the current problems are reported again for this exam),
    /// beats run, the watchdog reports and reopens, and only the exam's ways out unlock.
    func promoteToExamLock() {
        guard isLocked, isPreLock else { return }
        DebugLog.log("lock", "pre-exam lock -> exam lock (stays locked)")
        isPreLock = false
        EventLog.shared.setForwarding(true)
        Watchdog.preLockEnded()
        reported.removeAll(); backgroundApps.removeAll()
        OfflineExit.reset()
        EventLog.shared.record(.lockStarted)
        if IsSecureEventInputEnabled() { EventLog.shared.record(.secureInputOn) }
        runChecks(atLock: true)
        AppSession.shared.beat()
    }

    /// "Not ready? Leave and unlock" (or the invigilator shortcut) during the pre-exam
    /// lock: the normal unlock with no server check, since there is no attempt. False, and
    /// nothing changes, when an exam is attached or this is not the pre-exam lock.
    @discardableResult
    func endPreLock() -> Bool {
        guard PreLockState.onPreUnlock(locked: isLocked, preLocked: isPreLock,
                                       hasExam: AppSession.shared.exam != nil) == .unlock else { return false }
        DebugLog.log("lock", "pre-exam lock ended (no exam was attached)")
        unlock()
        return true
    }

    private func bringToFront() {
        NSApp.activate(ignoringOtherApps: true)
        surface?.takeFocus()
    }

    /// 2026-10-24: another app is in front while locked (pre-exam or exam lock). Since
    /// macOS 14 activation is cooperative: a background app asking to activate (the call in
    /// bringToFront) is usually IGNORED while the student uses the app in front, so the
    /// lock stayed behind Chrome (and the kiosk options, which apply only while FairPlay is
    /// active, were off) until Chrome was quit by hand. Hiding the app in front always
    /// works and hands activation back to us; then we ask for it as before. System
    /// security dialogs are never touched. Apps that must go are still closed by the checks.
    private func reclaimFront(from front: NSRunningApplication?) {
        if let front, front.processIdentifier != ProcessInfo.processInfo.processIdentifier,
           !Allowlist.isSystemDialog(front), !front.isHidden, !front.isTerminated {
            let ok = front.hide()
            if ok || !hideRefusedLogged.contains(front.processIdentifier) {
                if !ok { hideRefusedLogged.insert(front.processIdentifier) }
                DebugLog.log("lock", "other app in front while locked -> hidden (\(ok ? "ok" : "REFUSED")): \(DebugLog.describe(front))")
            }
        }
        bringToFront()
    }
    private var hideRefusedLogged: Set<pid_t> = []

    private func installFocusGuard() {
        let t = Timer(timeInterval: 0.3, repeats: true) { [weak self] _ in
            guard let self, self.isLocked else { return }
            guard let front = NSWorkspace.shared.frontmostApplication else { return }
            if front.processIdentifier == ProcessInfo.processInfo.processIdentifier { return }
            if Allowlist.isSystemDialog(front) { return }
            self.reclaimFront(from: front)
        }
        RunLoop.main.add(t, forMode: .common)
        focusTimer = t
    }

    private func installCheckTimer() {
        let t = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            guard let self, self.isLocked else { return }
            self.runChecks(atLock: false)
        }
        RunLoop.main.add(t, forMode: .common)
        checkTimer = t
    }

    private func installWorkspaceWatchers() {
        let ws = NSWorkspace.shared.notificationCenter
        let nc = NotificationCenter.default
        let me = ProcessInfo.processInfo.processIdentifier

        observers.append(ws.addObserver(forName: NSWorkspace.didActivateApplicationNotification,
                                        object: nil, queue: .main) { [weak self] note in
            guard let self, self.isLocked,
                  let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                  app.processIdentifier != me,
                  !Allowlist.isSystemDialog(app) else { return }
            EventLog.shared.record(.otherAppForeground(app.localizedName ?? app.bundleIdentifier ?? "unknown"))
            // Hide it and take the front back (any build; activation alone is ignored on
            // macOS 14+). A dev-build kept terminal/browser is hidden like any other.
            self.reclaimFront(from: app)
        })

        observers.append(ws.addObserver(forName: NSWorkspace.didLaunchApplicationNotification,
                                        object: nil, queue: .main) { [weak self] note in
            guard let self, self.isLocked,
                  let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
            else { return }
            let denied = !Allowlist.keeps(app) && SystemChecks.deniedBundlePrefixes.contains {
                (app.bundleIdentifier ?? "").lowercased().hasPrefix($0)
            }
            guard denied || (app.activationPolicy == .regular && !Allowlist.permits(app)) else { return }
            EventLog.shared.record(.otherAppLaunched(app.localizedName ?? app.bundleIdentifier ?? "unknown"))
            // 2026-10-14 (owner decision): only macOS, VPN clients and the apps the teacher
            // allowed may run; anything else opened during the exam is closed.
            let finder = app.bundleIdentifier == Allowlist.finder
            DebugLog.log("quit", "app opened during lock -> \(denied ? "force quit" : finder ? "hidden" : "quit"): \(DebugLog.describe(app))")
            if denied { LockdownController.forceQuit(app) } else if finder { app.hide() } else { app.terminate() }
            self.bringToFront()
        })

        // 2026-10-22: an app we were closing is gone -> check again now, so the "These apps
        // must be closed" screen disappears at once instead of on the next 3 s round.
        observers.append(ws.addObserver(forName: NSWorkspace.didTerminateApplicationNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            guard let self, self.isLocked, self.problems["proc"] != nil else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
                guard let self, self.isLocked else { return }
                self.runChecks(atLock: false)
            }
        })

        observers.append(ws.addObserver(forName: NSWorkspace.activeSpaceDidChangeNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            guard let self, self.isLocked else { return }
            EventLog.shared.record(.spaceChanged)
            self.surface?.coverAllScreens()
            // A Space switch usually means another app's (full-screen) Space: hide that app.
            self.reclaimFront(from: NSWorkspace.shared.frontmostApplication)
        })

        observers.append(nc.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                        object: nil, queue: .main) { [weak self] _ in
            guard let self, self.isLocked else { return }
            self.runChecks(atLock: false)
        })
    }

    // MARK: - Checks

    /// Runs every check, updates the blocking screen and the beat summary.
    private func runChecks(atLock: Bool) {
        var found: [String: String] = [:]
        var summary: [String] = []

        // Virtual machine (#8)
        let vm = SystemChecks.isVirtualMachineOnce
        summary.append("vm=\(vm ? 1 : 0)")
        if vm {
            found["vm"] = "FairPlay cannot be used in a virtual machine.\nUse the Mac itself."
            report("vm", .vmDetected(SystemChecks.sysctlString("hw.model")))
        }

        // Displays incl. mirroring, AirPlay, Sidecar (#9)
        let d = SystemChecks.displays()
        summary.append("disp=\(d.online)")
        summary.append("mir=\(d.mirrored ? 1 : 0)")
        if d.online > 1 || NSScreen.screens.count > 1 || d.mirrored {
            found["display"] = "Only one screen is allowed.\nUnplug other displays and turn off mirroring, AirPlay and Sidecar."
                + (d.names.isEmpty ? "" : "\nFound: " + d.names.joined(separator: ", "))
            if d.mirrored { report("mirror", .mirroredDisplay(d.online)) }
            else { report("display", .extraDisplay(max(d.online, NSScreen.screens.count))) }
        }

        // Audit M-02: the signed beat carries these, the server uses them.
        let onConsole = SystemChecks.sessionIsOnConsole()
        summary.append("console=\(onConsole ? 1 : 0)")
        summary.append("secinput=\(IsSecureEventInputEnabled() && !ownSecureField ? 1 : 0)")

        // Remote login session
        if !onConsole {
            found["remote"] = "This Mac is being used remotely.\nThe exam only works at the Mac itself."
            report("remote", .remoteSession)
        }

        // Keyboard tap still alive (Accessibility not revoked, tap not disabled)
        let tapOK = EventTap.shared.ensureHealthy()
        summary.append("tap=\(tapOK ? 1 : 0)")
        EventTap.voiceOverOn = NSWorkspace.shared.isVoiceOverEnabled
        if !tapOK {
            found["tap"] = "The keyboard lock stopped working.\nAsk the invigilator. (Accessibility permission for FairPlay must stay on.)"
            report("tap", .tapLost)
        }

        // Input source: Apple keyboards / input methods only
        var inputOK = true
        if let id = SystemChecks.currentInputSourceID(), !SystemChecks.currentInputSourceIsApple() {
            report("input:\(id)", .inputSourceBlocked(id))
            if !SystemChecks.selectAppleKeyboard() {
                inputOK = false
                found["input"] = "Third-party keyboard input (\(id)) is not allowed.\nPress Ctrl-Space to switch to an Apple keyboard (e.g. ABC or Pinyin - Simplified)."
            }
        }
        summary.append("inp=\(inputOK ? 1 : 0)")

        // Processes (#7)
        let procProblems = checkProcesses(atLock: atLock)
        summary.append(procListFailed ? "proc=?" : "proc=\(procProblems.count)")
        summary.append("bg=\(backgroundApps.count)")
        if !procProblems.isEmpty {
            found["proc"] = "These apps must be closed for the exam:\n" + procProblems.prefix(6).joined(separator: ", ")
                + "\nFairPlay closes them in a few seconds, or press the button. Unsaved work in them is lost."
                + "\nOnly the apps your teacher allowed for this exam (and VPN apps) can stay open."
                + "\nRemote Login / Screen Sharing / Remote Management: turn them off in"
                + " System Settings > General > Sharing (FairPlay is not allowed to)."
        }

        if found != problems || atLock {
            let keys: [String] = found.keys.sorted()
            let texts: [String] = keys.compactMap { found[$0] }
            let line: String = found.isEmpty
                ? "all checks OK (" + summary.joined(separator: ",") + ")"
                : "BLOCKED by " + keys.joined(separator: ", ") + ": " + texts.joined(separator: " || ")
            DebugLog.log("check", line)
        }
        problems = found
        lastSummary = summary.joined(separator: ",")
        if problems.isEmpty {
            surface?.hideBlocking()
        } else {
            let quit: (() -> Void)? = problems["proc"] == nil ? nil : { [weak self] in self?.quitBlockedNow() }
            let text = problems.keys.sorted().compactMap { problems[$0] }.joined(separator: "\n\n")
                + "\n\nStuck? Invigilator: Ctrl-Opt-Cmd-E"
            surface?.showBlocking(reason: text, quitAction: quit)
        }
        surface?.coverAllScreens()
    }

    /// Ends what we may end; returns names of what still runs and must go.
    private func checkProcesses(atLock: Bool) -> [String] {
        var still: [String] = []
        let me = ProcessInfo.processInfo.processIdentifier

        // 2026-10-14 (owner decision, replaces the 2026-10-13 "leave them in the background"):
        // every windowed app that is not macOS, a VPN client or allowed by the teacher for
        // this exam is closed: asked politely, then force-quit after 2 s (a hidden "Save
        // changes?" sheet must not keep it alive). Finder is only hidden (macOS relaunches it).
        let now = Date()
        var askedNow = false
        defer {
            // 2026-10-22: force-quit what ignored the polite request right after its 2 s,
            // not on the next 3 s round (the blocking screen used to stay up to ~6 s).
            if askedNow {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { [weak self] in
                    guard let self, self.isLocked else { return }
                    self.runChecks(atLock: false)
                }
            }
        }
        for app in NSWorkspace.shared.runningApplications
        where app.processIdentifier != me && app.activationPolicy == .regular
            && !Allowlist.permits(app) && !app.isTerminated {
            let name = app.localizedName ?? app.bundleIdentifier ?? "app"
            if app.bundleIdentifier == Allowlist.finder {
                if !app.isHidden { _ = app.hide() }
                continue
            }
            if let asked = quitting[app.processIdentifier] {
                if now.timeIntervalSince(asked) > 2, LockdownController.forceQuit(app) { continue }
            } else {
                quitting[app.processIdentifier] = now
                askedNow = true
                report("app:\(app.bundleIdentifier ?? name)", .processBlocked("closed at lock: \(name)"))
                let asked = app.terminate()
                DebugLog.log("quit", "asked to quit (\(asked ? "sent" : "REFUSED")): \(DebugLog.describe(app))")
                if !asked, LockdownController.forceQuit(app) { continue }
            }
            still.append(name)
        }
        // Dev builds only: terminals/browsers kept for local testing stay open but hidden.
        for app in NSWorkspace.shared.runningApplications
        where app.processIdentifier != me && !app.isTerminated && !app.isHidden && Allowlist.isDevKept(app) {
            hideDevKept(app)
        }
        // Teacher-allowed apps that are open: noted once so the teacher sees them.
        for app in NSWorkspace.shared.runningApplications where Allowlist.isTeacherAllowed(app) {
            let name = app.localizedName ?? app.bundleIdentifier ?? "app"
            if !reported.contains("allowed:\(name)") {
                report("allowed:\(name)", .appInBackground("\(name) (allowed for this exam)"))
                backgroundApps.insert(name)
            }
        }
        // Review 2026-10-14: a window of another program floating over the exam (a
        // non-activating panel never becomes the "front app", so the focus guard misses it).
        for w in overlayWindows() {
            report("overlay:\(w.owner)", .processBlocked("window over the exam: \(w.owner)"))
            if let app = NSRunningApplication(processIdentifier: w.pid),
               !Allowlist.isSystemDialog(app), LockdownController.forceQuit(app) { continue }
            still.append(w.owner)
        }
        // Review 2026-10-14: the watchdog must stay up while locked.
        if !Watchdog.ensureRunning() { report("watchdog", .watchdogStopped) }

        let f = SystemChecks.scanProcesses(lockedAt: lockedAt)
        procListFailed = f.listFailed
        for app in f.deniedApps where !app.isTerminated {
            let name = app.localizedName ?? app.bundleIdentifier ?? "app"
            report("denied:\(app.bundleIdentifier ?? name)", .processBlocked(name))
            if !LockdownController.forceQuit(app) { still.append(name) }
        }
        for p in f.deniedProcs {
            report("proc:\(p.name)", .processBlocked(p.name))
            // Our own user's processes can be ended; root daemons (remote-control services)
            // cannot: the student has to turn them off in System Settings.
            if p.uid != getuid() || kill(p.pid, SIGKILL) != 0 {
                still.append(LockdownController.friendlyName(p.name))
            }
        }
        for p in f.orphanScripts {
            report("orphan:\(p.pid)", .orphanScriptEnded("\(p.name) pid \(p.pid)"))
            _ = kill(p.pid, SIGKILL)
        }
        for p in f.orphanReported {
            report("orphan-r:\(p.pid)", .processBlocked("background \(p.name) pid \(p.pid)"))
        }
        if IsSecureEventInputEnabled() && !ownSecureField { report("secure-input", .secureInputOn) }
        return Array(Set(still)).sorted()
    }

    /// Audit M-11: the checks that can't be fixed from INSIDE the lock, run before it (the
    /// student can still open System Settings). Changes nothing; returns problems to show.
    func preflight() -> [String] { preflightProblems().map { $0.message } }

    /// Plan M2 (2026-10-20): the same problems with a stable id for "Check this Mac".
    func preflightProblems() -> [(id: String, message: String)] {
        var out: [(id: String, message: String)] = []
        if !EventTap.isTrusted {
            out.append(("accessibility", "Turn on FairPlay in System Settings > Privacy & Security > Accessibility."))
        }
        if SystemChecks.isVirtualMachine() {
            out.append(("vm", "FairPlay cannot be used in a virtual machine. Use the Mac itself."))
        }
        let d = SystemChecks.displays()
        if d.online > 1 || NSScreen.screens.count > 1 || d.mirrored {
            out.append(("displays", "Only one screen is allowed. Unplug other displays and turn off mirroring, AirPlay and Sidecar."))
        }
        if !SystemChecks.sessionIsOnConsole() {
            out.append(("remote", "This Mac is being used remotely. The exam only works at the Mac itself."))
        }
        let f = SystemChecks.scanProcesses(lockedAt: nil)
        var services = Set(f.deniedProcs.filter { $0.uid != getuid() }.map { LockdownController.friendlyName($0.name) })
        // Review 2026-10-14: these services start only when someone connects, so a running
        // process isn't there to find. Their port listening means the setting is on.
        for (port, name) in SystemChecks.sharingPorts where SystemChecks.localPortOpen(port) {
            services.insert(name)
        }
        for name in services.sorted() {
            out.append(("blocked_apps", "Turn off \(name) in System Settings > General > Sharing."))
        }
        if let id = SystemChecks.currentInputSourceID(), !SystemChecks.currentInputSourceIsApple() {
            out.append(("input_source", "Switch to an Apple keyboard (e.g. ABC or Pinyin - Simplified); \(id) is not allowed."))
        }
        // Plan M5: a clock far from the server's breaks deadlines and sealed answers.
        if let off = ServerClock.shared.offset, !ServerClock.skewOK(off) {
            out.append(("clock", "Set your Mac's date and time to automatic (System Settings > General > Date & Time)."))
        }
        DebugLog.log("check", out.isEmpty ? "preflight OK" : "preflight problems: " + out.map { $0.message }.joined(separator: " || "))
        return out
    }

    // MARK: - Before the lock (2026-10-22)

    /// Windowed apps the lock would close (with the current teacher-allowed list): shown
    /// to the student before the exam starts so they can save their work first.
    func appsToClose() -> [NSRunningApplication] {
        let me = ProcessInfo.processInfo.processIdentifier
        return NSWorkspace.shared.runningApplications.filter {
            $0.processIdentifier != me && $0.activationPolicy == .regular && !$0.isTerminated
                && $0.bundleIdentifier != Allowlist.finder && !Allowlist.permits($0)
        }
    }

    static func names(_ apps: [NSRunningApplication]) -> [String] {
        Array(Set(apps.map { $0.localizedName ?? $0.bundleIdentifier ?? "app" })).sorted()
    }

    /// "Close them and start": asks each app to quit (it may show "Save changes?"), force-quits
    /// what is left after 3 s, and reports what still runs about a second later.
    func quitAppsBeforeLock(_ done: @escaping ([String]) -> Void) {
        let apps = appsToClose()
        DebugLog.log("quit", "before lock: closing " + LockdownController.names(apps).joined(separator: ", "))
        let kept = LockdownController.names(LockdownController.devKeptApps())
        if !kept.isEmpty {
            DebugLog.log("quit", "before lock: dev build keeps (not closed, hidden while locked): " + kept.joined(separator: ", "))
        }
        for app in apps { app.terminate() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in
            for app in apps where !app.isTerminated { LockdownController.forceQuit(app) }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                let still = LockdownController.names(self?.appsToClose() ?? [])
                DebugLog.log("quit", still.isEmpty ? "before lock: all closed" : "before lock: still running " + still.joined(separator: ", "))
                done(still)
            }
        }
    }

    /// The student pressed "Quit these apps now": force-quit everything that must go
    /// (unsaved work in those apps is lost -- the screen says so), then check again.
    func quitBlockedNow() {
        DebugLog.log("click", "\"Quit these apps now\" pressed")
        guard isLocked else { return }
        let me = ProcessInfo.processInfo.processIdentifier
        for app in NSWorkspace.shared.runningApplications
        where app.processIdentifier != me && app.activationPolicy == .regular && !Allowlist.permits(app)
            && app.bundleIdentifier != Allowlist.finder {
            LockdownController.forceQuit(app)
        }
        let f = SystemChecks.scanProcesses(lockedAt: lockedAt)
        for app in f.deniedApps { LockdownController.forceQuit(app) }
        let uid = getuid()
        for p in f.deniedProcs + f.orphanScripts where p.uid == uid {
            let r = kill(p.pid, SIGKILL)
            DebugLog.log("quit", "kill \(p.name) pid \(p.pid): \(r == 0 ? "ok" : "FAILED errno \(errno)")")
        }
        for p in f.deniedProcs where p.uid != uid {
            DebugLog.log("quit", "cannot end \(p.name) pid \(p.pid): owned by uid \(p.uid) (system service)")
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            guard let self, self.isLocked else { return }
            self.runChecks(atLock: false)
        }
    }

    /// Dev builds only (Allowlist.devKeep): hide a kept terminal/browser behind the exam.
    private func hideDevKept(_ app: NSRunningApplication) {
        guard !app.isHidden else { return }
        let ok = app.hide()
        DebugLog.log("quit", "dev build: kept but hidden (\(ok ? "ok" : "REFUSED")): \(DebugLog.describe(app))")
    }

    /// Dev builds only: the apps kept running for local testing (empty in release builds).
    static func devKeptApps() -> [NSRunningApplication] {
        NSWorkspace.shared.runningApplications.filter { !$0.isTerminated && Allowlist.isDevKept($0) }
    }

    /// forceTerminate() can return false (or be ignored) for some apps; SIGKILL on our own
    /// user's process always works. True when the app is gone or was killed.
    @discardableResult
    static func forceQuit(_ app: NSRunningApplication) -> Bool {
        if app.isTerminated { return true }
        let who = DebugLog.describe(app)
        if app.forceTerminate() { DebugLog.log("quit", "force quit ok: \(who)"); return true }
        let pid = app.processIdentifier
        let ok = pid > 0 && kill(pid, SIGKILL) == 0
        DebugLog.log("quit", "force quit refused; SIGKILL \(ok ? "ok" : "FAILED errno \(errno)"): \(who)")
        return ok
    }

    static func friendlyName(_ process: String) -> String {
        switch process.lowercased() {
        case "sshd", "sshd-session", "sshd-keygen-wrapper": return "Remote Login (SSH)"
        case let n where n.hasPrefix("screensharing"): return "Screen Sharing"
        default: return process
        }
    }

    /// Review 2026-10-14: other programs' windows at (or above) the exam window's level.
    /// Apple's own windows are left alone.
    private func overlayWindows() -> [SystemChecks.Overlay] {
        let myLevel = NSApp.windows.filter { $0.isVisible }.map { $0.level.rawValue }.max() ?? 0
        guard myLevel > 0,
              let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]]
        else { return [] }
        return SystemChecks.overlays(in: list, myPid: getpid(), myLevel: myLevel)
            .filter { !CodeSign.isApple(pid: $0.pid) }
    }

    /// Records an event once per lock for each problem key.
    private func report(_ key: String, _ event: EventLog.Event) {
        guard !reported.contains(key) else { return }
        reported.insert(key)
        EventLog.shared.record(event)
    }

    private func clearAndGuardPasteboard() {
        let pb = NSPasteboard.general
        pb.clearContents()
        lastChangeCount = pb.changeCount
        // 0.2 s (was 0.5 s): shorter window for Universal Clipboard / pasted notes.
        let t = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in
            guard let self, self.isLocked else { return }
            if pb.changeCount != self.lastChangeCount {
                pb.clearContents()
                self.lastChangeCount = pb.changeCount
            }
        }
        RunLoop.main.add(t, forMode: .common)
        pasteboardTimer = t
    }
}
