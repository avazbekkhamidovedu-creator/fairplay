import UIKit
import WebKit
import FairPlayCore

/// Exams held on another website (CONTRACT.md slice 3, `openExternal`), as on the Mac:
/// the lock bar shows title, countdown and "Finish exam"; navigation is limited to the
/// exam server plus the teacher's allowed sites; while our JS page is gone this class
/// sends heartbeats and integrity events itself. Finishing loads
/// <server>/student/external-done/<attempt>, whose page submits and unlocks.
@MainActor
final class ExternalExamController {

    struct Session {
        let attemptId: String
        let startURL: URL
        let rules: [SiteRule]
        let finishContains: String?
        let canvas: Bool
        let finishRegex: NSRegularExpression?
        var deadline: Date
        let clockOffset: TimeInterval
        let title: String
        let token: String
        let appSession: String
    }

    private(set) var session: Session?
    var isActive: Bool { session != nil }

    private let server: URL
    private weak var webView: WKWebView?
    private weak var bar: LockBar?
    /// Presents the "Finish the exam?" confirmation.
    var confirmFinish: ((_ done: @escaping () -> Void) -> Void)?

    private var countdownTimer: Timer?
    private var heartbeatTimer: Timer?
    private var urlObservation: NSKeyValueObservation?
    private var pendingEvents: [[String: Any]] = []
    private var sendingEvents = false
    private var finishing = false
    private var browser: CanvasBrowser?
    private var finishTimer: Timer?
    private var noticeTimer: Timer?
    private var recoveryPending = false
    private var uploadReported = false
    private var canvasScriptInstalled = false
    private var persistentNotice: String?
    private var doneAttempt: String?
    var resetScripts: (() -> Void)?
    var defaults = UserDefaults.standard
    var clearsWebData = true

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain = ISO8601DateFormatter()

    static func parseDate(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return isoFrac.date(from: s) ?? isoPlain.date(from: s)
    }

    init(server: URL) { self.server = server }

    func attach(webView: WKWebView, bar: LockBar) {
        self.webView = webView
        self.bar = bar
        bar.onFinish = { [weak self] in self?.finishPressed() }
        bar.onBack = { [weak self] in self?.backPressed() }
        bar.onQuiz = { [weak self] in self?.quizPressed() }
    }

    private static func schemeOK(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        return scheme == "https" || (Config.isDevBuild && scheme == "http")
    }

    /// Returns an error text when the bridge message is unusable.
    func start(body: [String: Any]) -> String? {
        guard let aid = body["attempt_id"] as? String, !aid.isEmpty,
              let urlText = body["url"] as? String, let url = URL(string: urlText),
              ExternalExamController.schemeOK(url), url.host != nil,
              let token = body["token"] as? String, !token.isEmpty else {
            return "bad_request"
        }
        let isCanvas = (body["kind"] as? String) == "canvas"
        let allowed = (body["allowed_domains"] as? [Any]) ?? []
        let rules: [SiteRule]
        var canvasSetup: CanvasSetup?
        if isCanvas {
            switch CanvasSetup.make(startURL: url, allowedDomains: allowed, rawScope: body["canvas_scope"],
                                    rawRegex: body["finish_url_regex"] as? String) {
            case .failure:
                return "bad_request"
            case .success(let setup):
                rules = setup.rules
                canvasSetup = setup
            }
        } else {
            rules = SiteRule.rules(from: allowed, startURL: url)
        }
        let finish = isCanvas ? nil : (body["finish_url_contains"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let serverTime = ExternalExamController.parseDate(body["server_time"] as? String) ?? Date()
        let deadline = ExternalExamController.parseDate(body["deadline_at"] as? String) ?? Date().addingTimeInterval(3600)
        let title = (body["title"] as? String) ?? "Exam"
        stopTimers()
        finishTimer?.invalidate(); finishTimer = nil
        finishing = false
        recoveryPending = false
        uploadReported = false
        session = Session(attemptId: aid, startURL: url, rules: rules, finishContains: finish, canvas: isCanvas,
                          finishRegex: canvasSetup?.finishRegex, deadline: deadline,
                          clockOffset: serverTime.timeIntervalSince(Date()), title: title, token: token,
                          appSession: (body["session"] as? String) ?? "")
        let finishRejected = canvasSetup?.finishRejected == true
        doneAttempt = nil
        if let canvasSetup {
            let restored = CanvasQuizMarker.isSet(aid, in: defaults)
            browser = CanvasBrowser(startURL: url, rules: rules, scope: canvasSetup.scope, quizStarted: restored,
                                    armed: restored && CanvasQuizMarker.isArmed(aid, in: defaults),
                                    finishUsable: !finishRejected)
            installCanvasScript()
        } else {
            browser = nil
        }
        DebugLog.log("external", "website exam start: \(url.host ?? "?") rules=\(rules.count) finish=\(finish ?? "-") canvas=\(isCanvas)")
        bar?.setExternal(title: title)
        bar?.setCanvasButtons(isCanvas)
        updateBar()
        persistentNotice = nil
        if finishRejected {
            EventLog.shared.record("blocked_navigation", "finish pattern rejected")
            showNotice("The finish page could not be set up. Ask your teacher.", persistent: true)
        }
        startTimers()
        observeURL()
        webView?.load(URLRequest(url: url))
        return nil
    }

    func allows(_ url: URL) -> Bool {
        guard let session, let host = url.host?.lowercased(), ExternalExamController.schemeOK(url) else { return false }
        if session.canvas { return browser?.allows(url) ?? false }
        return session.rules.contains { $0.matches(host: host, path: url.path) }
    }

    func checkFinishURL(_ url: URL?) {
        guard let session, !finishing, let url, finishHit(url, session: session) else { return }
        guard session.canvas else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.finish(reason: "finish_url") }
            return
        }
        guard finishTimer == nil else { return }
        let aid = session.attemptId
        let timer = Timer(timeInterval: 2, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.finishTimer = nil
                guard let current = self.session, current.attemptId == aid, !self.finishing,
                      let live = self.webView?.url, self.finishHit(live, session: current) else { return }
                self.finish(reason: "finish_url")
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        finishTimer = timer
    }

    func checkFrameFinish(_ url: URL) {
        guard let session, session.canvas, !finishing, finishTimer == nil, allows(url),
              finishHit(url, session: session) else { return }
        let aid = session.attemptId
        let timer = Timer(timeInterval: 2, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.finishTimer = nil
                guard let current = self.session, current.attemptId == aid, !self.finishing,
                      self.browser?.finishArmedNow == true else { return }
                self.finish(reason: "finish_url")
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        finishTimer = timer
    }

    private func finishHit(_ url: URL, session: Session) -> Bool {
        if session.canvas { return browser?.isFinish(url, regex: session.finishRegex) ?? false }
        guard let needle = session.finishContains else { return false }
        return url.absoluteString.contains(needle)
    }

    // MARK: - Events

    func queueEvent(type: String, detail: String, time: String) {
        guard isActive else { return }
        pendingEvents.append(["type": type, "detail": detail, "client_time": time])
        flushEvents()
    }

    private func flushEvents() {
        guard let session, !sendingEvents, !pendingEvents.isEmpty else { return }
        let batch = Array(pendingEvents.prefix(100))
        sendingEvents = true
        post(path: "api/student/attempts/\(session.attemptId)/events", session: session, body: ["events": batch]) { [weak self] status, _ in
            guard let self else { return }
            self.sendingEvents = false
            if let status, (200..<300).contains(status) || ((400..<500).contains(status) && status != 429) {
                self.pendingEvents.removeFirst(min(batch.count, self.pendingEvents.count))
                if !self.pendingEvents.isEmpty { self.flushEvents() }
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.flushEvents() }
            }
        }
    }

    // MARK: - Heartbeat + countdown

    private func startTimers() {
        let cd = Timer(timeInterval: 1, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.tick() } }
        RunLoop.main.add(cd, forMode: .common)
        countdownTimer = cd
        let hb = Timer(timeInterval: 5, repeats: true) { [weak self] _ in MainActor.assumeIsolated { self?.heartbeat() } }
        RunLoop.main.add(hb, forMode: .common)
        heartbeatTimer = hb
        tick()
        heartbeat()
    }

    private func stopTimers() {
        countdownTimer?.invalidate(); countdownTimer = nil
        heartbeatTimer?.invalidate(); heartbeatTimer = nil
    }

    private func tick() {
        guard let session else { return }
        let now = Date().addingTimeInterval(session.clockOffset)
        let left = max(0, Int(session.deadline.timeIntervalSince(now).rounded(.down)))
        let h = left / 3600, m = (left % 3600) / 60, s = left % 60
        bar?.setCountdown(h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%02d:%02d", m, s), warn: left < 300)
        if left == 0 { finish(reason: "deadline") }
    }

    private func heartbeat() {
        guard let session, !finishing else { return }
        post(path: "api/student/attempts/\(session.attemptId)/heartbeat", session: session,
             body: ["current_question": 0, "locked": LockdownController.shared.reallyLocked]) { [weak self] status, json in
            guard let self, status == 200, let json else { return }
            if let d = ExternalExamController.parseDate(json["deadline_at"] as? String) { self.session?.deadline = d }
            if (json["status"] as? String) == "submitted" { self.finish(reason: "closed_by_teacher") }
        }
    }

    private func post(path: String, session: Session, body: [String: Any],
                      completion: @escaping @MainActor (Int?, [String: Any]?) -> Void) {
        Net.request("POST", path: path, token: session.token,
                    appSession: session.appSession.isEmpty ? nil : session.appSession, body: body, completion: completion)
    }

    // MARK: - Finish / teardown

    private func finishPressed() {
        DebugLog.log("external", "Finish exam button pressed")
        guard isActive, !finishing, !isLockedIn else { return }
        confirmFinish? { [weak self] in self?.finish(reason: "student_finished") }
    }

    func finish(reason: String) {
        guard let session, !finishing else { return }
        DebugLog.log("external", "finish: \(reason)")
        finishing = true
        var batch = pendingEvents
        batch.append(["type": "external_finished", "detail": reason,
                      "client_time": ExternalExamController.isoFrac.string(from: Date())])
        post(path: "api/student/attempts/\(session.attemptId)/events", session: session, body: ["events": batch]) { _, _ in }
        let aid = session.attemptId
        let wasCanvas = session.canvas
        doneAttempt = wasCanvas ? aid : nil
        teardown()
        var comps = URLComponents(url: server.appendingPathComponent("student/external-done/\(aid)"), resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "reason", value: reason)]
        if let url = comps?.url { webView?.load(URLRequest(url: url)) }
        if wasCanvas && clearsWebData { clearCanvasData() }
    }

    /// Leave website mode without submitting (invigilator exit).
    func abort() {
        guard isActive else { return }
        DebugLog.log("external", "aborted (invigilator exit)")
        let wasCanvas = session?.canvas == true
        if let aid = session?.attemptId { CanvasQuizMarker.clear(aid, in: defaults) }
        teardown()
        if wasCanvas && clearsWebData { clearCanvasData() }
        webView?.load(URLRequest(url: server.appendingPathComponent("student")))
    }

    private func teardown() {
        stopTimers()
        finishTimer?.invalidate(); finishTimer = nil
        noticeTimer?.invalidate(); noticeTimer = nil
        persistentNotice = nil
        urlObservation?.invalidate(); urlObservation = nil
        removeCanvasScript()
        bar?.setExternal(title: nil)
        session = nil
        browser = nil
        pendingEvents.removeAll()
    }

    private func observeURL() {
        urlObservation?.invalidate()
        urlObservation = webView?.observe(\.url, options: [.new]) { [weak self] wv, _ in
            DispatchQueue.main.async {
                let url = wv.url
                self?.checkFinishURL(url)
                self?.updateBar()
                self?.recoverIfBlocked(url)
            }
        }
    }

    // MARK: - Canvas mode

    var isCanvas: Bool { session?.canvas == true }

    var isLockedIn: Bool { browser?.isLockedIn == true }

    var startURL: URL? { session?.startURL }

    static let blockedNotice = CanvasBrowser.blockedNotice

    func childAllows(_ url: URL, mainFrame: Bool = true) -> Bool {
        guard isCanvas, let browser else { return false }
        return browser.childAllows(url, mainFrame: mainFrame)
    }

    func blocksMainFrame(_ url: URL) -> Bool { browser?.blocksMainFrame(url) ?? false }

    func reportBlocked(_ url: URL) {
        if let detail = browser?.recordBlocked(url) { EventLog.shared.record("blocked_navigation", detail) }
        showNotice()
    }

    func reportDownloadBlocked(host: String?) {
        EventLog.shared.record("blocked_navigation", "download from \(host ?? "?")")
        showNotice()
    }

    func popupPlan(url: URL?) -> CanvasPopup.Plan {
        guard isCanvas, let browser else { return .block }
        return browser.popupPlan(url: url)
    }

    func noteUploadRefused() {
        guard isCanvas, !uploadReported else { return }
        uploadReported = true
        EventLog.shared.record("canvas_upload_unsupported", "file questions need a file panel")
    }

    func showNotice(_ text: String = ExternalExamController.blockedNotice, persistent: Bool = false) {
        if persistent { persistentNotice = text }
        bar?.setNotice(text)
        noticeTimer?.invalidate()
        let timer = Timer(timeInterval: 4, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self else { return }
                self.bar?.setNotice(self.persistentNotice)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        noticeTimer = timer
    }

    func canvasCommitted(_ url: URL?) {
        guard let session, session.canvas else { return }
        if browser?.committed(url) == true {
            CanvasQuizMarker.set(session.attemptId, in: defaults)
            EventLog.shared.record("canvas_quiz_started")
            DebugLog.log("external", "canvas phase: quiz")
        }
        persistArmed()
        updateBar()
    }

    func doneLoaded(_ url: URL?) {
        guard let aid = doneAttempt, session == nil, let url,
              url.path.lowercased().hasSuffix("/student/external-done/" + aid.lowercased()) else { return }
        doneAttempt = nil
        CanvasQuizMarker.clear(aid, in: defaults)
    }

    func canvasPageLoaded() {
        guard let session, session.canvas, !finishing else { return }
        updateBar()
        if let target = browser?.autoOpenTarget(webView?.url) {
            DebugLog.log("external", "canvas exam link opened after sign-in")
            perform(.load(target))
        }
    }

    private func recoverIfBlocked(_ url: URL?) {
        guard isCanvas, !finishing else { return }
        guard let step = browser?.recover(url: url, now: Date(), previous: webView?.backForwardList.backItem?.url) else {
            return
        }
        if let report = step.report { EventLog.shared.record("blocked_navigation", report) }
        if step.notice { showNotice() }
        if let action = step.action { perform(action) }
        if step.recheck { scheduleRecheck() }
    }

    private func scheduleRecheck() {
        guard !recoveryPending else { return }
        recoveryPending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + CanvasRecoveryGate.minInterval + 0.1) { [weak self] in
            guard let self else { return }
            self.recoveryPending = false
            self.recoverIfBlocked(self.webView?.url)
        }
    }

    private func perform(_ action: CanvasViewAction) {
        switch action {
        case .goBack:
            webView?.goBack()
        case .load(let url):
            webView?.load(URLRequest(url: url))
        }
    }

    private func quizPressed() {
        guard isCanvas, !finishing, finishTimer == nil, let webView, !webView.isLoading,
              let start = session?.startURL else { return }
        if browser?.isOnItem(webView.url) == true { return }
        browser?.disarm(keepingOn: webView.url, regex: session?.finishRegex)
        persistArmed()
        webView.load(URLRequest(url: start))
    }

    private func backPressed() {
        guard isCanvas, !finishing, let webView,
              let previous = webView.backForwardList.backItem?.url, browser?.backAllowed(previous) == true else { return }
        webView.goBack()
    }

    private func persistArmed() {
        guard let aid = session?.attemptId, let browser, browser.phase == .quiz else { return }
        CanvasQuizMarker.setArmed(browser.armed, aid, in: defaults)
    }

    private func updateBar() {
        guard isCanvas else { return }
        bar?.setFinishAllowed(!isLockedIn)
        if let previous = webView?.backForwardList.backItem?.url {
            bar?.setBackEnabled(browser?.backAllowed(previous) ?? false)
        } else {
            bar?.setBackEnabled(false)
        }
    }

    private func removeCanvasScript() {
        guard canvasScriptInstalled else { return }
        canvasScriptInstalled = false
        resetScripts?()
    }

    private func installCanvasScript() {
        guard !canvasScriptInstalled, let controller = webView?.configuration.userContentController else { return }
        canvasScriptInstalled = true
        let js = CanvasScripts.pageGuard(serverOrigin: FilePicker.origin(server))
        controller.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: false))
    }

    private func clearCanvasData() {
        let store = WKWebsiteDataStore.default()
        let serverHost = (server.host ?? "").lowercased()
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        store.fetchDataRecords(ofTypes: types) { records in
            let others = records.filter { CanvasData.shouldRemove(domain: $0.displayName, serverHost: serverHost) }
            guard !others.isEmpty else { return }
            store.removeData(ofTypes: types, for: others) {
                DebugLog.log("external", "canvas web data cleared: \(others.count) site(s)")
            }
        }
    }
}
