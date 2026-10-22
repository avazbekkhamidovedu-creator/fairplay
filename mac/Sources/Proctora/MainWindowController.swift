import AppKit
import WebKit

final class ExamWindow: NSWindow {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

/// No context menu (it offers "Look Up", "Search with Google", Services, Share...)
/// and no force-click Look Up.
final class ExamWebView: WKWebView {
    override func willOpenMenu(_ menu: NSMenu, with event: NSEvent) {
        menu.removeAllItems()
        super.willOpenMenu(menu, with: event)
    }
    override func quickLook(with event: NSEvent) {}
}

/// WKUserContentController retains its handlers; this breaks the cycle.
final class WeakScriptHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        target?.userContentController(c, didReceive: m)
    }
}

/// The one window: student web UI in a WKWebView, plus the native bridge
/// (CONTRACT.md "Native bridge"). Normal window until the exam asks to lock;
/// then borderless, covering every screen, above everything.
final class MainWindowController: NSWindowController, LockSurface,
                                  WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {

    private var webView: ExamWebView!
    private var permissionView: NSView?
    private var permissionStatus: NSTextField?
    private var blockingView: NSView?
    private var blockingLabel: NSTextField?
    private var trustPoll: Timer?
    private var permissionShownAt = Date()
    private var unlockRetry: Timer?
    private var urlWatch: NSKeyValueObservation?
    /// Audit M-18: set only while WE load an about:/data: page (loadOwnHTML); a link or
    /// script on a page can never set it. (WKNavigationAction.sourceFrame is declared
    /// non-optional, so a nil check on it is compiled away and can't be used.)
    private var ownPageLoadPending = false
    /// Audit W-10: per-launch nonce stamped on every message to the page.
    private let bridgeNonce = UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private var normalFrame = NSRect(x: 0, y: 0, width: 1280, height: 820)
    private let server = Config.serverURL
    private let external = ExternalExamController(server: Config.serverURL)
    /// 2026-10-23: the exam's teacher-allowed websites (AllowedSites.swift), shown in the
    /// exam page's side pane. Used only while locked; cleared when the lock ends.
    private var allowedSites: [String] = []
    private var batteryTimer: Timer?

    private static let normalStyle: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]

    convenience init() {
        let win = ExamWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                             styleMask: MainWindowController.normalStyle, backing: .buffered, defer: false)
        win.title = "FairPlay"
        win.minSize = NSSize(width: 900, height: 600)
        win.sharingType = .none                 // excluded from screenshots / screen recording (verify on macOS 15+)
        win.isReleasedWhenClosed = false
        win.collectionBehavior = [.fullScreenNone]
        win.center()
        self.init(window: win)
        buildWebView()
    }

    // MARK: - Setup

    private func buildWebView() {
        guard let content = window?.contentView else { return }
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()    // keeps localStorage: offline answers survive a crash
        config.applicationNameForUserAgent = "FairPlay/\(Config.version)"
        config.preferences.javaScriptCanOpenWindowsAutomatically = false
        // No Apple Intelligence rewrite/proofread in answers. Set through KVC because some
        // Xcode SDKs don't expose WKWebViewConfiguration.writingToolsBehavior to Swift even
        // though the runtime has it (build failed on a second Mac with "no member").
        if #available(macOS 15.0, *) {
            let sel = NSSelectorFromString("setWritingToolsBehavior:")
            if config.responds(to: sel) {
                config.setValue(NSWritingToolsBehavior.none.rawValue, forKey: "writingToolsBehavior")
            }
        }

        let ucc = WKUserContentController()
        ucc.add(WeakScriptHandler(self), name: "proctora")
        let guardJS = """
        (function () {
          document.addEventListener('contextmenu', function (e) { e.preventDefault(); }, true);
          document.addEventListener('dragstart', function (e) { e.preventDefault(); }, true);
          window.open = function () { return null; };
        })();
        """
        ucc.addUserScript(WKUserScript(source: guardJS, injectionTime: .atDocumentStart, forMainFrameOnly: false))
        // Audit W-10: the page drops 'proctora-native' events that lack this nonce, so page
        // JS can't fake "locked"/"unlocked" to its own UI.
        // Audit 2026-10-13: only the FairPlay server's own pages get it, never a website
        // exam (send() only talks to server pages anyway).
        ucc.addUserScript(WKUserScript(source: MainWindowController.nonceScript(nonce: bridgeNonce, server: server),
                                       injectionTime: .atDocumentStart, forMainFrameOnly: true))
        if DebugLog.isOn {   // dev builds: page clicks + JS errors into logs/events.log
            ucc.addUserScript(WKUserScript(source: DebugLog.pageScript, injectionTime: .atDocumentStart,
                                           forMainFrameOnly: false))
        }
        config.userContentController = ucc

        let wv = ExamWebView(frame: content.bounds, configuration: config)
        wv.autoresizingMask = [.width, .height]
        wv.navigationDelegate = self
        wv.uiDelegate = self
        wv.allowsBackForwardNavigationGestures = false
        wv.allowsMagnification = false
        wv.allowsLinkPreview = false
        content.addSubview(wv)
        webView = wv
        // In-page (pushState) route changes never reach decidePolicyFor, so also watch the
        // URL itself: a teacher page inside the student app goes back to the student login.
        urlWatch = wv.observe(\.url, options: [.new]) { [weak self] view, _ in
            DispatchQueue.main.async {
                guard let self, let u = view.url, self.isServerURL(u) else { return }
                if MainWindowController.isTeacherPath(u.path) {
                    EventLog.shared.record(.blockedNavigation("teacher page \(u.path)"))
                    DebugLog.log("web", "teacher page opened in-page, sent back: \(u.path)")
                    view.load(URLRequest(url: Config.startURL))
                    return
                }
                // Review 2026-10-14: in-page route changes away from the locked attempt
                // (e.g. a "Back to home" link) go back to it.
                let aid = AppSession.shared.exam?.attemptId
                if LockdownController.shared.isLocked,
                   !MainWindowController.allowedWhileLocked(path: u.path, attemptId: aid,
                                                            preLock: LockdownController.shared.isPreLock), let aid {
                    EventLog.shared.record(.blockedNavigation("left the exam: \(u.path)"))
                    view.load(URLRequest(url: Config.serverURL.appendingPathComponent("student/exam/\(aid)")))
                }
            }
        }
        external.attach(webView: wv, container: content, window: window)
        startBatteryUpdates()
    }

    // MARK: - Battery for the exam top bar (2026-10-23)

    /// On every page load (didFinish), every 60 s, and when the power source changes.
    /// A Mac without a battery sends nothing.
    private func startBatteryUpdates() {
        guard batteryTimer == nil else { return }
        let t = Timer(timeInterval: 60, repeats: true) { [weak self] _ in self?.pushBattery() }
        RunLoop.main.add(t, forMode: .common)
        batteryTimer = t
        Readiness.observePowerChanges { [weak self] in self?.pushBattery() }
    }

    /// Only into the exam server's own pages; the script holds number and boolean literals only.
    private func pushBattery() {
        guard let webView, isServerURL(webView.url), let b = Readiness.batteryLevel(),
              let js = BatteryScript.make(level: b.level, charging: b.charging) else { return }
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    func start() {
        showWindow(nil)
        takeFocus()
        guard Config.serverIsAcceptable else {
            // Release builds never talk to a plain-http server (audit #11).
            loadOwnHTML("""
            <!doctype html><meta charset="utf-8"><body style="font:16px -apple-system;padding:40px">
            \(BrandMark.svg(size: 44))
            <h1>This copy of FairPlay is not set up</h1>
            <p>It has no secure (https) exam server. Ask your school for the correct FairPlay app.</p>
            """)
            return
        }
        webView.load(URLRequest(url: MainWindowController.launchURL()))
        if !EventTap.isTrusted { showPermission() }
    }

    func reload() {
        guard Config.serverIsAcceptable else { return }
        if webView.url == nil || webView.url?.scheme == "about" {
            webView.load(URLRequest(url: Config.startURL))
        } else {
            webView.reload()
        }
    }

    // MARK: - Bridge (JS <-> native)

    private func defaultPort(_ scheme: String?) -> Int { scheme?.lowercased() == "https" ? 443 : 80 }

    /// `window.__fairplayNonce` for documents whose origin is the server's.
    static func nonceScript(nonce: String, server: URL) -> String {
        let scheme = (server.scheme ?? "https").lowercased()
        let host = (server.host ?? "").lowercased()
        let defaultPort = scheme == "https" ? 443 : 80
        let port = server.port.map { $0 == defaultPort ? "" : ":\($0)" } ?? ""
        let origin = "\(scheme)://\(host)\(port)".filter { $0.isLetter || $0.isNumber || ":/.-[]".contains($0) }
        let safeNonce = nonce.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return "if (location.origin === '\(origin)') { window.__fairplayNonce = '\(safeNonce)'; }"
    }

    private func isServerURL(_ url: URL?) -> Bool { MainWindowController.isServerURL(url, server: server) }

    /// Same scheme, host (any case) and port as the exam server (default ports filled in).
    static func isServerURL(_ url: URL?, server: URL) -> Bool {
        guard let url, let host = url.host else { return false }
        func port(_ u: URL) -> Int { u.port ?? (u.scheme?.lowercased() == "https" ? 443 : 80) }
        return host.lowercased() == (server.host ?? "").lowercased() && port(url) == port(server)
            && url.scheme?.lowercased() == server.scheme?.lowercased()
    }

    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        // Dev trace lines from any page (exam page or website exam). Never drive anything.
        if let body = message.body as? [String: Any], body["action"] as? String == "log" {
            let host: String = message.frameInfo.securityOrigin.host
            let kind: String = (body["kind"] as? String) ?? "?"
            let text: String = String(((body["msg"] as? String) ?? "").prefix(600))
            let from: String = host.isEmpty ? "local" : host
            DebugLog.log("js", kind + " [" + from + "] " + text)
            return
        }
        // Only the exam server's own pages (same scheme, host and port) may drive the lock.
        let origin = message.frameInfo.securityOrigin
        let originPort = origin.port == 0 ? defaultPort(origin.protocol) : origin.port
        let serverPort = server.port ?? defaultPort(server.scheme)
        guard message.frameInfo.isMainFrame,
              origin.protocol.lowercased() == server.scheme?.lowercased(),
              origin.host == server.host, originPort == serverPort else { return }
        guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        DebugLog.log("bridge", "page -> app: \(action)")

        switch action {
        case "hello":
            sendState()
        case "flushEvents":
            flushEventQueue()
        case "attest":
            send(AppSession.shared.attestation(nonce: (body["nonce"] as? String) ?? ""))
        case "session":
            let promote = examLockAction() == .promote
            if AppSession.shared.setExam(attemptId: body["attempt_id"] as? String, token: body["token"] as? String,
                                         session: body["session"] as? String) {
                allowedSites = AllowedSites.parse(body["allowed_sites"])
                if promote {
                    attachExamToPreLock(allowedApps: body["allowed_apps"])
                } else {
                    Allowlist.updateTeacherAllowed(body["allowed_apps"])
                }
            }
        case "prelock":
            // 2026-10-23: lock before the start code (PreLock.swift): the same lock as an
            // exam's, with no exam session.
            let lock = LockdownController.shared
            switch PreLockState.onPreLock(locked: lock.isLocked, preLocked: lock.isPreLock,
                                          hasExam: AppSession.shared.exam != nil) {
            case .examLocked:
                send(["type": "locked", "prelock": false])
            case .alreadyPreLocked:
                send(["type": "locked", "prelock": true])
            case .lock:
                // No exam: a session left from an earlier page (not locked) must not beat
                // for this lock or be taken for its attempt.
                if AppSession.shared.exam != nil { AppSession.shared.clearExam() }
                Allowlist.updateTeacherAllowed(body["allowed_apps"])
                allowedSites = AllowedSites.parse(body["allowed_sites"])
                if lock.preLock() {
                    hidePermission()
                    send(["type": "locked", "prelock": true])
                } else {
                    showPermission()
                    send(["type": "lockFailed", "reason": "accessibility"])
                }
            }
        case "preunlock":
            // 2026-10-23: "Not ready? Leave and unlock": only the pre-exam lock with no exam
            // attached, and then with no server check (there is no attempt).
            let lock = LockdownController.shared
            switch PreLockState.onPreUnlock(locked: lock.isLocked, preLocked: lock.isPreLock,
                                            hasExam: AppSession.shared.exam != nil) {
            case .unlock:
                lock.endPreLock()            // onUnlocked sends "unlocked"
                sendState()
            case .notLocked:
                send(["type": "unlocked"])
                sendState()
            case .refuse:
                DebugLog.log("lock", "preunlock refused: this is an exam lock")
                send(["type": "preunlockRefused"])
            }
        case "lock":
            // Audit M-06: lock only for a valid exam session -- or an explicit offline start
            // (no server session yet; then only the invigilator or a hand-in that reaches the
            // server ends the lock).
            let offlineStart = (body["offline"] as? Bool) == true
                && body["attempt_id"] == nil && body["session"] == nil
            let promote = examLockAction() == .promote
            if !offlineStart && !AppSession.shared.setExam(attemptId: body["attempt_id"] as? String,
                                                          token: body["token"] as? String,
                                                          session: body["session"] as? String) {
                send(["type": "lockFailed", "reason": "bad_session"])
                return
            }
            if offlineStart { DebugLog.log("lock", "offline start: locking without a server session") }
            allowedSites = AllowedSites.parse(body["allowed_sites"])
            if promote {
                // 2026-10-23: already locked before the start code: the screen stays locked
                // and this becomes the exam's lock.
                attachExamToPreLock(allowedApps: body["allowed_apps"])
                hidePermission()
                send(["type": "locked"])
                return
            }
            Allowlist.updateTeacherAllowed(body["allowed_apps"])
            if LockdownController.shared.lock() {
                hidePermission()
                send(["type": "locked"])
            } else {
                showPermission()
                send(["type": "lockFailed", "reason": "accessibility"])
            }
        case "openExternal":
            let promote = examLockAction() == .promote
            guard AppSession.shared.setExam(attemptId: body["attempt_id"] as? String, token: body["token"] as? String,
                                            session: body["session"] as? String) else {
                send(["type": "externalFailed", "reason": "bad_session"])
                return
            }
            allowedSites = AllowedSites.parse(body["allowed_sites"])
            if promote {
                attachExamToPreLock(allowedApps: body["allowed_apps"])
            } else {
                Allowlist.updateTeacherAllowed(body["allowed_apps"])
            }
            if !LockdownController.shared.isLocked {
                guard LockdownController.shared.lock() else {
                    showPermission()
                    send(["type": "lockFailed", "reason": "accessibility"])
                    return
                }
                hidePermission()
                send(["type": "locked"])
            }
            if let problem = external.start(body: body) {
                send(["type": "externalFailed", "reason": problem])
            }
        case "preflight":
            // Audit M-11: before locking, list what must be fixed outside the lock.
            // Plan M2: plus the full "Check this Mac" rows (server, clock, version, disk...).
            Allowlist.updateTeacherAllowed(body["allowed_apps"])   // only applies while unlocked
            runPreflight()
        case "quitApps":
            // 2026-10-22: close the listed apps before the lock (the student saved first).
            guard !LockdownController.shared.isLocked else { return }
            Allowlist.updateTeacherAllowed(body["allowed_apps"])
            LockdownController.shared.quitAppsBeforeLock { [weak self] still in
                self?.send(["type": "appsQuit", "still_running": still])
            }
        case "seal":
            let aid = (body["attempt_id"] as? String) ?? ""
            let seq = (body["client_seq"] as? NSNumber)?.int64Value ?? -1
            switch AppSession.shared.seal(attemptId: aid, answersJSON: (body["answers_json"] as? String) ?? "",
                                          clientSeq: seq) {
            case .success: send(["type": "sealed", "attempt_id": aid, "ok": true])
            case .failure(let e): send(["type": "sealed", "attempt_id": aid, "ok": false, "reason": e.rawValue])
            }
        case "sealedToken":
            // Plan M4: a fresh token from a signed-in page for sealed files from an earlier run.
            if let token = body["token"] as? String, !token.isEmpty, token.count < 4000, Sealed.hasPending {
                Sealed.uploadAll(token: token)
            }
        case "openSettings":
            // 2026-10-22: a fixed System Settings pane for a pre-exam problem.
            SettingsPane.open((body["pane"] as? String) ?? "")
        case "unlock":
            requestUnlock()
        case "installUpdate":
            // 2026-10-22: in-app update (Updater.swift); refused while locked or in an exam.
            Updater.shared.install { [weak self] message in self?.send(message) }
        case "openDownloadPage":
            // The fixed public download page, in the browser (the window shows only the server).
            guard !LockdownController.shared.isLocked else { return }
            DebugLog.log("update", "open the download page")
            NSWorkspace.shared.open(Updater.releasePage)
        default:
            break
        }
    }

    /// 2026-10-23: what an exam lock / session does now (PreLock.swift).
    private func examLockAction() -> PreLockState.OnExamLock {
        PreLockState.onExamLock(locked: LockdownController.shared.isLocked,
                                preLocked: LockdownController.shared.isPreLock)
    }

    /// The exam's lock or session arrived during the pre-exam lock: its teacher-allowed
    /// apps replace the pre-exam lock's (like a lock taken right now), then the lock
    /// becomes the exam's without unlocking.
    private func attachExamToPreLock(allowedApps: Any?) {
        Allowlist.setTeacherAllowed(allowedApps)
        LockdownController.shared.promoteToExamLock()
    }

    /// The page asks to unlock after handing in. Audit #11: the page alone can no longer
    /// unlock -- the server must confirm the attempt is submitted. Retries every 3 s.
    private func requestUnlock() {
        guard LockdownController.shared.isLocked else {
            send(["type": "unlocked"])
            return
        }
        AppSession.shared.confirmHandIn { [weak self] result in
            guard let self, LockdownController.shared.isLocked else { return }
            switch result {
            case .confirmed:
                self.unlockRetry?.invalidate(); self.unlockRetry = nil
                LockdownController.shared.unlock()       // onUnlocked sends "unlocked"
                AppSession.shared.clearExam()   // after unlock: its last beat (locked=0) still needs the session
            case .notYet, .unreachable:
                self.send(["type": "unlockPending",
                           "reason": result == .unreachable ? "offline" : "not_submitted"])
                if self.unlockRetry == nil {
                    let t = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
                        guard LockdownController.shared.isLocked else {
                            self?.unlockRetry?.invalidate(); self?.unlockRetry = nil
                            return
                        }
                        self?.requestUnlockOnce()
                    }
                    RunLoop.main.add(t, forMode: .common)
                    self.unlockRetry = t
                }
            }
        }
    }

    private func requestUnlockOnce() {
        AppSession.shared.confirmHandIn { [weak self] result in
            guard let self, result == .confirmed, LockdownController.shared.isLocked else { return }
            self.unlockRetry?.invalidate(); self.unlockRetry = nil
            LockdownController.shared.unlock()
            AppSession.shared.clearExam()   // after unlock: its last beat (locked=0) still needs the session
        }
    }

    /// Server status from the app's own beats: a teacher close also unlocks.
    func handleServerStatus(_ status: String) {
        if status == "submitted", LockdownController.shared.isLocked, !external.isActive {
            requestUnlockOnce()
        }
        // Plan M3: our session is gone (exam moved to another computer). The signed hand-in
        // check decides; at most once every 10 s.
        if status == "session_gone", LockdownController.shared.isLocked,
           Date().timeIntervalSince(lastGoneCheck) > 10 {
            lastGoneCheck = Date()
            requestUnlockOnce()
        }
    }
    private var lastGoneCheck = Date.distantPast

    func sendState() {
        var state: [String: Any] = ["type": "state",
                                    "locked": LockdownController.shared.isLocked,
                                    "trusted": EventTap.isTrusted,
                                    "version": Config.version,
                                    "app_version": Config.version,
                                    "platform": "mac",
                                    "sealed_pending": Sealed.hasPending,
                                    "prelock": LockdownController.shared.isPreLock]
        // 2026-10-22: a newer release the app can install (Updater.swift); never while locked.
        if let update = Updater.shared.available, !LockdownController.shared.isLocked {
            state["update"] = ["version": update.version]
        }
        send(state)
    }

    /// Review 2026-10-14: integrity events wait here until a page that forwards them to the
    /// server (the exam page) has asked for them ("flushEvents"), instead of being dropped
    /// while no such page is listening (between loads, the error page, the start page...).
    private var eventsReady = false
    private var eventQueue: [[String: Any]] = []
    private let eventQueueMax = 300

    func sendEvent(type: String, detail: String, time: String) {
        if external.isActive {
            // Our JS page is gone while the exam website is open: the controller sends events itself.
            external.queueEvent(type: type, detail: detail, time: time)
            return
        }
        let event: [String: Any] = ["type": type, "detail": detail, "client_time": time]
        if eventsReady, let webView, isServerURL(webView.url) {
            send(["type": "event", "event": event])
        } else {
            eventQueue.append(event)
            if eventQueue.count > eventQueueMax { eventQueue.removeFirst(eventQueue.count - eventQueueMax) }
        }
    }

    private func flushEventQueue() {
        eventsReady = true
        let queued = eventQueue
        eventQueue.removeAll()
        for e in queued { send(["type": "event", "event": e]) }
    }

    func send(_ detail: [String: Any]) {
        guard let webView, isServerURL(webView.url),
              let data = try? JSONSerialization.data(withJSONObject: detail.merging(["bridge_nonce": bridgeNonce]) { _, b in b }),
              let json = String(data: data, encoding: .utf8) else { return }
        if let t = detail["type"] as? String, t != "event" {
            var extra = ""
            if let r = detail["reason"] { extra = " reason=\(r)" }
            DebugLog.log("bridge", "app -> page: \(t)\(extra)")
        }
        let js = "window.dispatchEvent(new CustomEvent('proctora-native', { detail: \(json) }));"
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    // MARK: - Navigation: only the exam server

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        // Audit M-15: the student app never opens teacher pages or the API docs on the
        // exam server (a teacher login there shows start codes, exit codes, answer keys).
        if isServerURL(url) && MainWindowController.isTeacherPath(url.path) {
            EventLog.shared.record(.blockedNavigation("teacher page \(url.path)"))
            DebugLog.log("web", "navigation BLOCKED (teacher page): \(url.path)")
            decisionHandler(.cancel)
            return
        }
        // Audit M-18: about:/data: pages only when WE load them (loadOwnHTML sets the flag;
        // navigation type "other", main frame), never from a link or script on a page.
        let isLocal = url.scheme == "about" || url.scheme == "data"
        let ownLocalPage = isLocal && ownPageLoadPending && navigationAction.navigationType == .other
            && (navigationAction.targetFrame?.isMainFrame ?? true)
        if ownLocalPage { ownPageLoadPending = false }
        // Review 2026-10-14: while locked, only the locked attempt's own pages.
        if isServerURL(url), (navigationAction.targetFrame?.isMainFrame ?? true), LockdownController.shared.isLocked,
           !MainWindowController.allowedWhileLocked(path: url.path, attemptId: AppSession.shared.exam?.attemptId,
                                                    preLock: LockdownController.shared.isPreLock) {
            EventLog.shared.record(.blockedNavigation("left the exam: \(url.path)"))
            DebugLog.log("web", "navigation BLOCKED while locked: \(url.path)")
            decisionHandler(.cancel)
            return
        }
        // 2026-10-23: the exam page's side pane of teacher-allowed websites (AllowedSites.swift):
        // sub-frames only, only while locked, https on a listed host or its subdomains (or
        // about:blank / about:srcdoc). No downloads from there.
        if !(navigationAction.targetFrame?.isMainFrame ?? true), LockdownController.shared.isLocked,
           !isServerURL(url), !external.allows(url), AllowedSites.frameAllowed(url, allowed: allowedSites) {
            if navigationAction.shouldPerformDownload {
                EventLog.shared.record(.blockedNavigation("download from \(url.host ?? "an allowed site")"))
                DebugLog.log("web", "download BLOCKED in an allowed-site frame: \(url.host ?? "")\(url.path)")
                decisionHandler(.cancel)
            } else {
                decisionHandler(.allow)
            }
            return
        }
        if ownLocalPage || isServerURL(url) || external.allows(url) {
            if navigationAction.targetFrame?.isMainFrame ?? true {
                external.checkFinishURL(url)
                eventsReady = false            // the new page asks again when it listens
            }
            decisionHandler(.allow)
        } else {
            EventLog.shared.record(.blockedNavigation(url.host ?? url.absoluteString))
            DebugLog.log("web", "navigation BLOCKED: \(url.scheme ?? "")://\(url.host ?? "")\(url.path)")
            decisionHandler(.cancel)
        }
    }

    /// 2026-10-23: nothing downloads from a sub-frame while locked (the allowed-site pane):
    /// a response WebKit cannot show, or one sent as an attachment, is cancelled. Everything
    /// else as WebKit's own default (show what it can show, cancel the rest).
    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if !navigationResponse.isForMainFrame, LockdownController.shared.isLocked {
            let disposition = (navigationResponse.response as? HTTPURLResponse)?
                .value(forHTTPHeaderField: "Content-Disposition")
            if !navigationResponse.canShowMIMEType || AllowedSites.isAttachment(contentDisposition: disposition) {
                let host = navigationResponse.response.url?.host ?? "?"
                EventLog.shared.record(.blockedNavigation("download from \(host)"))
                DebugLog.log("web", "download BLOCKED in a frame: \(host)")
                decisionHandler(.cancel)
                return
            }
        }
        decisionHandler(navigationResponse.canShowMIMEType ? .allow : .cancel)
    }

    /// Audit M-17: relaunched by the watchdog after the app ended while locked -> straight
    /// back into that attempt (the page resumes it and asks to lock again).
    static func launchURL() -> URL {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--resume-attempt"), i + 1 < args.count,
           AppSession.isSafeToken(args[i + 1], min: 3, max: 32) {
            DebugLog.log("app", "relaunched after ending while locked: resuming \(args[i + 1])")
            return Config.serverURL.appendingPathComponent("student/exam/\(args[i + 1])")
        }
        return Config.startURL
    }

    /// Pages a locked computer may show: the locked attempt's exam / website-exam / done
    /// pages (and any attempt's while the attempt is not known yet, i.e. an offline start).
    /// 2026-10-23: the pre-exam lock (no attempt) also shows the start-code pages.
    static func allowedWhileLocked(path: String, attemptId: String?, preLock: Bool = false) -> Bool {
        if preLock && attemptId == nil && PreLockState.allowedWhilePreLocked(path: path) { return true }
        let p = path.lowercased()
        let roots = ["/student/exam/", "/student/external/", "/student/external-done/", "/student/done/"]
        guard let aid = attemptId?.lowercased() else { return roots.contains { p.hasPrefix($0) } }
        let offlineOK = ["/student/exam/offline:", "/student/exam/offline%3a",
                         "/student/done/offline:", "/student/done/offline%3a"].contains { p.hasPrefix($0) }
        return offlineOK || roots.contains { p == $0 + aid || p.hasPrefix($0 + aid + "/") }
    }

    static func isTeacherPath(_ path: String) -> Bool {
        let p = path.lowercased()
        return ["/teacher", "/docs", "/redoc", "/openapi.json"].contains { p == $0 || p.hasPrefix($0 + "/") }
    }

    /// TLS: normal validation plus the optional server pin (audit #11).
    func webView(_ webView: WKWebView, didReceive challenge: URLAuthenticationChallenge,
                 completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        Pinning.handle(challenge, completionHandler)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, isServerURL(url) || external.allows(url) {
            webView.load(URLRequest(url: url))   // same-server links open in place
        } else if let url = navigationAction.request.url, LockdownController.shared.isLocked,
                  AllowedSites.isAllowedPopup(url, allowed: allowedSites) {
            // 2026-10-23: a new window / target=_blank from the allowed-site pane: never a
            // window, and not the exam's main frame either. Ignored (not a cheating attempt).
            DebugLog.log("web", "new window from an allowed site ignored: \(url.host ?? "")")
        } else if let url = navigationAction.request.url {
            EventLog.shared.record(.blockedNavigation(url.host ?? url.absoluteString))
        }
        return nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let ns = error as NSError
        DebugLog.log("web", "page load failed: \(ns.domain) \(ns.code) \(ns.localizedDescription)")
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        let ns = error as NSError
        DebugLog.log("web", "page failed: \(ns.domain) \(ns.code) \(ns.localizedDescription)")
        showOffline(error)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let u = webView.url { DebugLog.log("web", "page shown: \(u.host ?? u.scheme ?? "")\(u.path)") }
        pushBattery()
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        DebugLog.log("crash", "exam page process crashed; reloading")
        reload()
    }

    private func showOffline(_ error: Error) {
        let ns = error as NSError
        if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return }
        if ns.domain == "WebKitErrorDomain" && ns.code == 102 { return }   // interrupted by policy
        var retry = (ns.userInfo[NSURLErrorFailingURLErrorKey] as? URL) ?? Config.startURL
        if !isServerURL(retry) && !external.allows(retry) { retry = Config.startURL }
        let target = retry.absoluteString
            .replacingOccurrences(of: "\\", with: "")
            .replacingOccurrences(of: "'", with: "")
            .replacingOccurrences(of: "<", with: "")
            .replacingOccurrences(of: "\"", with: "")
        let html = """
        <!doctype html><html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        font:16px -apple-system,system-ui,sans-serif;background:#f4f5f8;color:#111827}
        .card{background:#fff;border-radius:14px;padding:32px 36px;max-width:460px;
        box-shadow:0 2px 12px rgba(0,0,0,.08)}h1{font-size:22px;margin:0 0 10px}
        p{color:#4b5563;line-height:1.5}button{margin-top:12px;font:inherit;font-weight:600;
        padding:10px 18px;border:0;border-radius:10px;background:#2f4bd9;color:#fff}
        </style></head><body><div class="card">
        \(BrandMark.svg(size: 44))
        <h1>Cannot reach the exam server</h1>
        <p>FairPlay is trying again every 3 seconds. Answers you already typed are saved on this computer.</p>
        <p style="font-size:13px">\(server.absoluteString)</p>
        <button onclick="location.href='\(target)'">Try again now</button>
        </div><script>setTimeout(function(){ location.href='\(target)'; }, 3000);</script></body></html>
        """
        loadOwnHTML(html)
    }

    /// Plan M2: the lock problems plus server reachability, clock, minimum version, disk,
    /// battery and VPN. Answers within ~2.5 s (the page waits 3 s) even when offline.
    private func runPreflight() {
        // 2026-10-23: during the pre-exam lock, not what FairPlay's own lock causes or fixes.
        let preLocked = LockdownController.shared.isPreLock
        let problems = PreLockState.preflightProblems(LockdownController.shared.preflightProblems(), preLocked: preLocked)
        var answered = false
        let finish: (Bool, String?) -> Void = { [weak self] reached, minimum in
            guard let self, !answered else { return }
            answered = true
            var blocking = problems
            if let minimum, !AppVersion.meets(Config.version, minimum: minimum) {
                blocking.append(("app_version", "Update FairPlay: this is version \(Config.version), the exam needs \(minimum) or newer."))
            }
            if let off = ServerClock.shared.offset, !ServerClock.skewOK(off),
               !blocking.contains(where: { $0.id == "clock" }) {
                blocking.append(("clock", "Set your Mac's date and time to automatic (System Settings > General > Date & Time)."))
            }
            let rows = Readiness.rows(problems: blocking, serverReached: reached, minVersion: minimum,
                                      clockOffset: reached ? ServerClock.shared.offset : nil,
                                      freeBytes: Readiness.freeBytes(), battery: Readiness.battery(),
                                      vpn: Readiness.vpnInterfaces(), accessibility: EventTap.isTrusted)
            self.send(["type": "preflight", "problems": blocking.map { $0.message },
                       "apps_to_close": preLocked ? [String]() : LockdownController.names(LockdownController.shared.appsToClose()),
                       "checks": rows.map { $0.dict }, "platform": "mac",
                       "app_version": Config.version, "os_version": Readiness.macOSVersion])
        }
        Net.request("GET", path: "api/app/version", token: "", appSession: nil, body: nil) { status, json in
            if status == 200 {
                finish(true, json?["mac"] as? String)
            } else if status != nil {
                // An older server without /api/app/version: reachable, no minimum.
                finish(true, nil)
            } else {
                finish(false, nil)
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { finish(false, nil) }
    }

    /// Plan M5: the server refuses this build (426). Never while locked: the exam in
    /// progress continues and the next start shows this screen.
    func showUpdateRequired(minimum: String) {
        DebugLog.log("app", "server requires FairPlay \(minimum) or newer (this is \(Config.version))")
        guard !LockdownController.shared.isLocked else { return }
        let safe = minimum.filter { $0.isNumber || $0 == "." }
        let html = """
        <!doctype html><html><head><meta charset="utf-8"><style>
        body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
        font:16px -apple-system,system-ui,sans-serif;background:#f4f5f8;color:#111827}
        .card{background:#fff;border-radius:14px;padding:32px 36px;max-width:460px;
        box-shadow:0 2px 12px rgba(0,0,0,.08)}h1{font-size:22px;margin:0 0 10px}
        p{color:#4b5563;line-height:1.5}
        </style></head><body><div class="card">
        \(BrandMark.svg(size: 44))
        <h1>Update FairPlay</h1>
        <p>This is FairPlay \(Config.version). Your exams need version \(safe.isEmpty ? "a newer one" : safe) or newer.</p>
        <p>Download the new version from your course page or ask your teacher, then open FairPlay again.</p>
        </div></body></html>
        """
        loadOwnHTML(html)
    }

    private func loadOwnHTML(_ html: String) {
        ownPageLoadPending = true
        webView.loadHTMLString(html, baseURL: nil)
    }

    // MARK: - Accessibility permission panel (shown on top until granted)

    private func showPermission() {
        DebugLog.log("lock", "Accessibility permission screen shown")
        guard permissionView == nil, let content = window?.contentView else {
            startTrustPoll(); return
        }
        let v = NSView(frame: content.bounds)
        v.autoresizingMask = [.width, .height]
        v.wantsLayer = true
        v.layer?.backgroundColor = NSColor(calibratedRed: 0.08, green: 0.09, blue: 0.12, alpha: 1).cgColor

        let title = label("One permission before your exam", size: 28, weight: .bold)
        let body = label("""
        FairPlay locks this Mac during the exam so that no other app, website \
        or shortcut can be used. macOS needs your OK for that, once.

        1. Click "Fix permission". macOS asks: click "Open System Settings".
        2. In Privacy & Security > Accessibility turn ON "FairPlay"
           (newer macOS: "Device Control and Data Access").
        3. Come back here. This screen disappears by itself.
           Still here after turning it on? Click "Restart FairPlay".
        """, size: 15, weight: .regular, color: NSColor(white: 0.85, alpha: 1))
        body.alignment = .left
        body.preferredMaxLayoutWidth = 600

        // 2026-10-22: which copy is running, and other copies that confuse macOS.
        var where_ = "This copy: " + Permission.display(Permission.thisCopy)
        let others = Permission.otherCopies()
        if !others.isEmpty {
            where_ += "\nOther FairPlay copies on this Mac (move them to the Trash, then click Fix permission):\n"
                + others.prefix(4).map { "  " + Permission.display($0) }.joined(separator: "\n")
            DebugLog.log("lock", "other FairPlay copies: " + others.map(\.path).joined(separator: ", "))
        }
        let copies = label(where_, size: 12, weight: .regular, color: NSColor(white: 0.62, alpha: 1))
        copies.alignment = .left
        copies.preferredMaxLayoutWidth = 600

        let status = label("Waiting for permission...", size: 14, weight: .semibold,
                           color: NSColor(calibratedRed: 1, green: 0.78, blue: 0.4, alpha: 1))
        permissionStatus = status

        let fix = NSButton(title: "Fix permission", target: self, action: #selector(fixPermission))
        fix.bezelStyle = .rounded
        fix.keyEquivalent = "\r"
        let open = NSButton(title: "Open settings", target: self, action: #selector(openAccessibility))
        open.bezelStyle = .rounded
        let restart = NSButton(title: "Restart FairPlay", target: self, action: #selector(restartForPermission))
        restart.bezelStyle = .rounded
        let buttons = NSStackView(views: [fix, open, restart])
        buttons.orientation = .horizontal; buttons.spacing = 12

        let stack = NSStackView(views: [BrandMark.view(size: 56), title, body, copies, status, buttons])
        stack.orientation = .vertical; stack.spacing = 20; stack.alignment = .centerX
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: v.widthAnchor, constant: -40)
        ])
        permissionShownAt = Date()
        content.addSubview(v, positioned: .above, relativeTo: webView)
        permissionView = v
        startTrustPoll()
    }

    private func startTrustPoll() {
        guard trustPoll == nil else { return }
        let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            if EventTap.isTrusted {
                self.hidePermission()
                self.sendState()
            } else {
                let now = Date().formatted(date: .omitted, time: .standard)
                let waited = Date().timeIntervalSince(self.permissionShownAt)
                self.permissionStatus?.stringValue = waited > 20
                    ? "Still waiting (\(now)). Already turned on? Click \"Fix permission\", then turn FairPlay on again."
                    : "Waiting for permission... (checked \(now))"
            }
        }
        RunLoop.main.add(t, forMode: .common)
        trustPoll = t
    }

    private func hidePermission() {
        trustPoll?.invalidate(); trustPoll = nil
        permissionView?.removeFromSuperview()
        permissionView = nil; permissionStatus = nil
    }

    @objc private func openAccessibility() {
        DebugLog.log("click", "\"Open settings\" pressed")
        EventTap.requestTrust()
        Permission.openSettings()
    }

    @objc private func fixPermission() {
        DebugLog.log("click", "\"Fix permission\" pressed")
        let ok = Permission.resetAndAsk()
        permissionShownAt = Date()
        permissionStatus?.stringValue = ok
            ? "Old FairPlay entries removed. Turn FairPlay ON in the list that opens."
            : "Turn FairPlay ON in the list that opens (if it is on, turn it off and on)."
    }

    @objc private func restartForPermission() {
        DebugLog.log("click", "\"Restart FairPlay\" pressed")
        Permission.relaunch()
    }

    // MARK: - LockSurface

    private var lockedLevel: NSWindow.Level {
        if Config.floatingLockedWindow { return .floating }   // dev builds only
        return NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.maximumWindow)))
    }

    func enterLockedMode() {
        guard let win = window else { return }
        normalFrame = win.frame
        win.styleMask = [.borderless]
        win.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
        win.level = lockedLevel
        win.hasShadow = false
        coverAllScreens()
        takeFocus()
        win.makeFirstResponder(webView)
    }

    func exitLockedMode() {
        allowedSites = []                       // the next exam sends its own
        guard let win = window else { return }
        win.level = .normal
        win.styleMask = MainWindowController.normalStyle
        win.collectionBehavior = [.fullScreenNone]
        win.hasShadow = true
        win.title = "FairPlay"
        win.setFrame(normalFrame, display: true)
        win.makeFirstResponder(webView)
    }

    func takeFocus() {
        guard let win = window else { return }
        win.makeKeyAndOrderFront(nil)
        win.orderFrontRegardless()
    }

    func coverAllScreens() {
        guard let win = window, LockdownController.shared.isLocked else { return }
        let union = NSScreen.screens.reduce(NSRect.zero) { $0.union($1.frame) }
        let target = union.isEmpty ? (NSScreen.main?.frame ?? win.frame) : union
        // Called every 3 s: only touch the window when the screens really changed.
        if win.frame != target { win.setFrame(target, display: true) }
    }

    private var blockingAction: (() -> Void)?
    private var blockingButton: NSButton?

    func showBlocking(reason: String, quitAction: (() -> Void)?) {
        blockingAction = quitAction
        if blockingView == nil { DebugLog.log("lock", "red blocking screen shown") }
        if blockingView != nil {
            blockingLabel?.stringValue = reason
            blockingButton?.isHidden = quitAction == nil
            return
        }
        guard let content = window?.contentView else { return }
        let v = NSView(frame: content.bounds)
        v.autoresizingMask = [.width, .height]
        v.wantsLayer = true
        v.layer?.backgroundColor = NSColor(calibratedRed: 0.55, green: 0.15, blue: 0.05, alpha: 0.97).cgColor
        let l = label(reason, size: 22, weight: .semibold)
        l.preferredMaxLayoutWidth = 760
        let b = NSButton(title: "Quit these apps now", target: self, action: #selector(blockingButtonPressed))
        b.bezelStyle = .rounded
        b.controlSize = .large
        b.isHidden = quitAction == nil
        let stack = NSStackView(views: [l, b])
        stack.orientation = .vertical; stack.spacing = 24; stack.alignment = .centerX
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        content.addSubview(v)   // topmost
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: v.centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: v.centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualTo: v.widthAnchor, constant: -80)
        ])
        blockingView = v; blockingLabel = l; blockingButton = b
    }

    @objc private func blockingButtonPressed() {
        blockingAction?()
    }

    func hideBlocking() {
        if blockingView != nil { DebugLog.log("lock", "red blocking screen hidden") }
        blockingView?.removeFromSuperview()
        blockingView = nil; blockingLabel = nil; blockingButton = nil; blockingAction = nil
    }

    /// Emergency exit or safety unlock while a website exam is open: leave it without submitting.
    func endExternalIfActive() {
        external.abort()
    }

    // MARK: - Invigilator emergency exit (Ctrl-Opt-Cmd-E while locked)

    /// Audit #5: the normal code is per exam, lives only on the server (shown in the
    /// teacher's live room) and has an attempt limit there.
    /// 2026-10-12: when the server cannot answer (offline, server error, exam never reached
    /// it) the invigilator uses the offline code instead (OfflineExit.swift), so a stuck
    /// lock never needs a Mac restart.
    func promptEmergencyExit() {
        DebugLog.log("exit", "Ctrl-Opt-Cmd-E pressed (locked=\(LockdownController.shared.isLocked))")
        // 2026-10-23: the pre-exam lock has no attempt to protect: the shortcut just unlocks
        // (no code, no server, no exit event).
        if PreLockState.emergencyExitUnlocksAtOnce(locked: LockdownController.shared.isLocked,
                                                   preLocked: LockdownController.shared.isPreLock,
                                                   hasExam: AppSession.shared.exam != nil) {
            DebugLog.log("exit", "pre-exam lock: unlocked by the shortcut (no exam attached)")
            LockdownController.shared.endPreLock()   // onUnlocked sends "unlocked"
            sendState()
            return
        }
        guard LockdownController.shared.isLocked, let win = window, win.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Invigilator exit"
        alert.informativeText = "Enter the invigilator code from the teacher's live room to end locked mode. "
            + "The teacher will see this.\n\nNo connection or an error? Choose \"Offline code\"."
            + (Config.isDevBuild ? "\n\nDev build: 0000 also works." : "")
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        field.placeholderString = "Invigilator code"
        alert.accessoryView = field
        alert.addButton(withTitle: "End locked mode")
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Offline code…")
        alert.window.initialFirstResponder = field
        LockdownController.shared.ownSecureField = true
        alert.beginSheetModal(for: win) { [weak self] response in
            LockdownController.shared.ownSecureField = false
            let choice: String
            if response == .alertFirstButtonReturn { choice = "End locked mode" }
            else if response == .alertThirdButtonReturn { choice = "Offline code" }
            else { choice = "Cancel" }
            DebugLog.log("exit", "exit dialog: " + choice)
            if response == .alertThirdButtonReturn {
                DispatchQueue.main.async { self?.promptOfflineExit(reason: nil) }
                return
            }
            guard response == .alertFirstButtonReturn else { return }
            let typed = field.stringValue
            guard !typed.isEmpty else { return }
            // Dev builds: the fixed test code ("0000") ends locked mode right here, no server
            // and no second dialog. Release builds compile this out (always false).
            if OfflineExit.devCodeMatches(typed.filter { !$0.isWhitespace && $0 != "-" }) {
                DebugLog.log("exit", "dev test code accepted (dev build only)")
                EventLog.shared.record(.offlineExit)
                LockdownController.shared.unlock()
                AppSession.shared.clearExam()   // after unlock: its last beat (locked=0) still needs the session
                return
            }
            AppSession.shared.checkExitCode(typed) { result in
                DebugLog.log("exit", "server exit-code result: \(result)")
                guard LockdownController.shared.isLocked else { return }
                switch result {
                case .ok:
                    LockdownController.shared.unlock()
                    AppSession.shared.clearExam()   // after unlock: its last beat (locked=0) still needs the session
                case .wrong:
                    NSSound.beep()
                    self?.notice("Wrong invigilator code. Press Ctrl-Opt-Cmd-E to try again.")
                case .tooMany:
                    self?.notice("Too many wrong codes. Wait 5 minutes.")   // server: WRONG_EXIT_LOCK_S
                case .unreachable:
                    self?.promptOfflineExit(reason: "The exam server cannot be reached.")
                case .noSession:
                    self?.promptOfflineExit(reason: "This exam has not reached the server yet.")
                case .serverError(let status):
                    self?.promptOfflineExit(reason: "The exam server answered with an error (\(status)).")
                }
            }
        }
    }

    /// Offline exit: show the Mac code, check the invigilator's answer locally.
    func promptOfflineExit(reason: String?) {
        guard LockdownController.shared.isLocked, let win = window, win.attachedSheet == nil else { return }
        guard OfflineExit.isAvailable else {
            DebugLog.log("exit", "offline exit not available: no offline key for this exam")
            notice((reason.map { $0 + "\n\n" } ?? "")
                   + "Offline exit is not available: this Mac never reached the exam server for this exam. "
                   + "If you must end the exam here, restart the Mac. Answers are saved on it.")
            return
        }
        let alert = NSAlert()
        alert.messageText = "Offline invigilator exit"
        var info = ""
        if let reason { info += reason + "\n\n" }
        info += "Mac code:  \(OfflineExit.challengeForDisplay)\n\n"
            + "Invigilator: in the teacher's live room choose \"Mac offline or stuck?\", pick this "
            + "student, type the Mac code and enter the 8-digit code here. "
            + "Answers stay saved on this Mac. This is logged."
        alert.informativeText = info
        let field = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 220, height: 24))
        field.placeholderString = "8-digit offline code"
        alert.accessoryView = field
        alert.addButton(withTitle: "End locked mode")
        alert.addButton(withTitle: "Cancel")
        alert.window.initialFirstResponder = field
        LockdownController.shared.ownSecureField = true
        alert.beginSheetModal(for: win) { [weak self] response in
            LockdownController.shared.ownSecureField = false
            guard response == .alertFirstButtonReturn, LockdownController.shared.isLocked else {
                DebugLog.log("exit", "offline dialog cancelled")
                return
            }
            let result = OfflineExit.check(field.stringValue)
            DebugLog.log("exit", "offline code result: \(result) (Mac code \(OfflineExit.challengeForDisplay))")
            switch result {
            case .ok:
                EventLog.shared.record(.offlineExit)
                LockdownController.shared.unlock()
                AppSession.shared.clearExam()   // after unlock: its last beat (locked=0) still needs the session
            case .wrong:
                NSSound.beep()
                EventLog.shared.record(.offlineExitRejected)
                DispatchQueue.main.async { self?.promptOfflineExit(reason: "Wrong code, try again.") }
            case .tooMany:
                EventLog.shared.record(.offlineExitRejected)
                DispatchQueue.main.async { self?.notice("Too many wrong codes. Wait \(Int(OfflineExit.waitAfterWrong / 60)) minutes, then press Ctrl-Opt-Cmd-E.") }
            case .unavailable:
                DispatchQueue.main.async { self?.promptOfflineExit(reason: nil) }   // shows why
            }
        }
    }

    private func notice(_ text: String) {
        guard let win = window, win.attachedSheet == nil else { return }
        let a = NSAlert()
        a.messageText = "Invigilator exit"
        a.informativeText = text
        a.addButton(withTitle: "OK")
        a.beginSheetModal(for: win, completionHandler: nil)
    }

    // MARK: - Helpers

    private func label(_ text: String, size: CGFloat, weight: NSFont.Weight,
                       color: NSColor = .white) -> NSTextField {
        let l = NSTextField(labelWithString: text)
        l.font = .systemFont(ofSize: size, weight: weight)
        l.textColor = color
        l.alignment = .center
        l.maximumNumberOfLines = 0
        l.translatesAutoresizingMaskIntoConstraints = false
        return l
    }
}
