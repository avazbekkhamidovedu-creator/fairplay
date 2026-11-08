import AppKit

// `--watchdog <pid> [--prelock|--native]`: the helper copy started while locked (Watchdog.swift).
if let watchdog = Watchdog.parseArguments(CommandLine.arguments) {
    Watchdog.run(parent: watchdog.parent, preLock: watchdog.preLock, native: watchdog.native)
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var main: MainWindowController?
    private var pendingExam: String?
    private var recoveringExam = false
    private var relockingNative = false

    func application(_ application: NSApplication, open urls: [URL]) {
        _ = receiveLaunch(urls)
    }

    func application(_ sender: NSApplication, openFile filename: String) -> Bool {
        receiveLaunch([URL(fileURLWithPath: filename)])
    }

    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        sender.reply(toOpenOrPrint: receiveLaunch(filenames.map { URL(fileURLWithPath: $0) }) ? .success : .failure)
    }

    private func receiveLaunch(_ urls: [URL]) -> Bool {
        guard !recoveringExam, !relockingNative, urls.count == 1, let url = urls.first else { return false }
        let exam: String?
        if url.isFileURL {
            guard (url.host == nil || url.host == "" || url.host == "localhost"),
                  url.pathExtension.lowercased() == "fairplay",
                  (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true,
                  let file = try? FileHandle(forReadingFrom: url) else { return false }
            defer { try? file.close() }
            guard let data = try? file.read(upToCount: 4097) else { return false }
            exam = ExamLaunch.parseFile(data)
        } else { exam = ExamLaunch.parse(url) }
        guard let exam else { return false }
        if let main { main.openUniversityExam(exam) }
        else { pendingExam = exam }
        return true
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        DebugLog.installHooks()
        NSApp.setActivationPolicy(.regular)
        buildMenu()

        let wc = MainWindowController()
        main = wc
        let lock = LockdownController.shared
        lock.surface = wc
        lock.onUnlocked = { [weak self, weak wc] in
            self?.recoveringExam = false
            self?.relockingNative = false
            wc?.cancelNativeRecovery()
            wc?.endExternalIfActive()
            wc?.send(["type": "unlocked"])
        }
        EventLog.shared.onEvent = { [weak wc] type, detail, time in
            wc?.sendEvent(type: type, detail: detail, time: time)
        }
        AppSession.shared.onStatus = { [weak wc] status in
            wc?.handleServerStatus(status)
        }
        NotificationCenter.default.addObserver(forName: .proctoraEmergencyExit, object: nil, queue: .main) { [weak wc] _ in
            guard EventTap.postsEmergencyExit(isDev: Config.isDevBuild, nativeLock: EventTap.nativeLockHeld) else { return }
            wc?.promptEmergencyExit()
        }
        recoveringExam = Watchdog.cleanUpAtLaunch()
        if recoveringExam {
            // The last run ended while locked (killed / crashed). The page forwards this
            // once the student is back in the exam; the server also sees the beat gap.
            EventLog.shared.record(.previousRunKilled)
            DebugLog.log("app", "last run ended while LOCKED (killed, crashed or Mac restarted)")
        }

        Net.onUpgradeRequired = { [weak wc] minimum in wc?.showUpdateRequired(minimum: minimum) }
        if case .relock(let saved) = NativeRelaunch.decide(store: NativeLockSetup.live.store,
                                          relockRequested: CommandLine.arguments.contains(Watchdog.relockArgument)) {
            relockingNative = true
            DebugLog.log("app", "saved native lock found: locking again before anything is shown")
            wc.startRecovering(saved)
        } else {
            wc.start()
            if !recoveringExam, let pendingExam { wc.openUniversityExam(pendingExam) }
        }
        pendingExam = nil
        NSApp.activate(ignoringOtherApps: true)
        // 2026-10-22: release builds look for a newer FairPlay now and every 6 hours.
        Updater.shared.onAvailable = { [weak wc] in wc?.sendState() }
        Updater.shared.start()
        // Plan M4: answers sealed offline last time go to the server before anything else.
        if Sealed.hasPending {
            DebugLog.log("sealed", "\(Sealed.pending().count) sealed answer file(s) waiting: uploading")
            Sealed.uploadAll()
        }

        print("""
        ------------------------------------------------------------
        FairPlay \(Config.version) (\(Config.isDevBuild ? "dev" : "release") build)  server: \(Config.serverURL.absoluteString)
        Safety auto-unlock: \(Config.safetyUnlockMinutes > 0 ? "\(Int(Config.safetyUnlockMinutes)) min" : "off")
        Exit while locked: ask your instructor for an exit code
        TLS pins: \(Pinning.enabled ? "\(Config.pins.count)" : "none")   device: \(AppSession.shared.deviceId)
        Log: \(EventLog.shared.logPath)
        ------------------------------------------------------------
        """)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        main?.endDeviceTest(.leave)
        let locked = LockdownController.shared.isLocked || LockdownController.shared.nativeLockActive
        if !locked { main?.closeBox() }
        DebugLog.log("app", locked ? "quit requested while locked: refused" : "quitting")
        return locked ? .terminateCancel : .terminateNow
    }

    func applicationWillTerminate(_ notification: Notification) {
        main?.endDeviceTest(.leave)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        main?.takeFocus(); return true
    }

    @objc func hardReloadPage(_ sender: Any?) {
        guard main?.reloadProtectedBrowser(hard: true) == true else { return }
        DebugLog.log("app", "View > Hard Reload (protected browser)")
    }

    @objc func reloadPage(_ sender: Any?) {
        if main?.reloadProtectedBrowser(hard: false) == true {
            DebugLog.log("app", "View > Reload (protected browser)")
            return
        }
        // Audit M-18: a reload during the exam drops page state; not while locked.
        guard !LockdownController.shared.isLocked else {
            DebugLog.log("app", "View > Reload ignored while locked")
            return
        }
        DebugLog.log("app", "View > Reload")
        main?.reload()
    }

    @objc func openProtectedBrowser(_ sender: Any?) {
        main?.showProtectedBrowser()
    }

    private func buildMenu() {
        let bar = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About FairPlay",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit FairPlay", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        bar.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        bar.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadPage(_:)), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)
        let hardReload = NSMenuItem(title: "Hard Reload", action: #selector(hardReloadPage(_:)), keyEquivalent: "R")
        hardReload.target = self
        view.addItem(hardReload)
        let browser = NSMenuItem(title: "Protected Browser…", action: #selector(openProtectedBrowser(_:)), keyEquivalent: "")
        browser.target = self
        view.addItem(browser)
        viewItem.submenu = view
        bar.addItem(viewItem)

        NSApp.mainMenu = bar
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()

