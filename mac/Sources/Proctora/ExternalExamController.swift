import AppKit
import WebKit

/// Exams held on another website (CONTRACT.md slice 3, `openExternal`).
///
/// While active: a light native strip sits above the web view (title, countdown,
/// "Finish exam"); navigation is limited to the exam server plus the teacher's
/// allowed sites; because our own JS page is gone, this class sends heartbeats and
/// integrity events to the server itself. Finishing loads
/// <server>/student/external-done/<attempt>, whose page submits and unlocks.
///
/// Audit 2026-10-12 #10: allowed sites are matched EXACTLY ("canvas.dku.edu" does not
/// open "x.canvas.dku.edu"; "*.dku.edu" does), with an optional path prefix
/// ("docs.google.com/forms/d/e/1FAIpQL/"). Only https in release builds.
final class ExternalExamController {

    /// One allowed-site entry: [*.]host[/path-prefix].
    struct SiteRule: Equatable {
        let wildcard: Bool
        let host: String
        let pathPrefix: String

        init?(_ raw: String) {
            var s = raw.trimmingCharacters(in: .whitespaces)
            if let r = s.range(of: "://") { s = String(s[r.upperBound...]) }
            if let q = s.firstIndex(where: { $0 == "?" || $0 == "#" }) { s = String(s[..<q]) }
            var host = s
            var path = ""
            if let slash = s.firstIndex(of: "/") {
                host = String(s[..<slash])
                path = String(s[slash...])
            }
            var wild = false
            if host.hasPrefix("*.") { wild = true; host = String(host.dropFirst(2)) }
            if let colon = host.firstIndex(of: ":") { host = String(host[..<colon]) }
            host = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
            guard !host.isEmpty, !host.contains("*") else { return nil }
            wildcard = wild
            self.host = host
            pathPrefix = path == "/" ? "" : path
        }

        func matches(host h: String, path p: String) -> Bool {
            let hostOK = h == host || (wildcard && h.hasSuffix("." + host))
            guard hostOK else { return false }
            if pathPrefix.isEmpty { return true }
            // Audit 2026-10-13: whole path segments only: "/forms/abc" allows "/forms/abc"
            // and "/forms/abc/..." but not "/forms/abcdef".
            let path = p.isEmpty ? "/" : p
            // URL.path drops a trailing slash, so "/3/" itself arrives as "/3".
            if pathPrefix.hasSuffix("/") { return path.hasPrefix(pathPrefix) || path == String(pathPrefix.dropLast()) }
            return path == pathPrefix || path.hasPrefix(pathPrefix + "/")
        }
    }

    struct Session {
        let attemptId: String
        let startURL: URL
        let rules: [SiteRule]
        let finishContains: String?
        var deadline: Date
        let clockOffset: TimeInterval      // server time minus local time
        let title: String
        let token: String
        let appSession: String
        let canvas: Bool
        let finishRegex: NSRegularExpression?
        let scope: CanvasScope?
    }

    private(set) var session: Session?
    var isActive: Bool { session != nil }

    private let server: URL
    private weak var webView: WKWebView?
    private weak var container: NSView?
    private weak var window: NSWindow?

    private var strip: NSView?
    private var titleLabel: NSTextField?
    private var timeLabel: NSTextField?
    private var countdownTimer: Timer?
    private var heartbeatTimer: Timer?
    private var urlObservation: NSKeyValueObservation?
    private var pendingEvents: [[String: Any]] = []
    private var sendingEvents = false
    private var finishing = false
    private var finishWork: DispatchWorkItem?
    private var lastMainCommit: URL?
    private var finishFromPopup = false
    private var popupObservation: NSKeyValueObservation?
    private var popupView: WKWebView?
    private var popupClose: NSButton?
    private(set) var phase: CanvasPhase = .lobby
    private var autoOpened = false
    private var recovery = CanvasRecoveryGate()
    private var recoveryPending = false
    private var recoveryTries = 0
    private var finalLoadDone = false
    private var finishPatternRejected = false
    private(set) var armed = false
    private var keepArmedOnFirstCommit = false
    private var markerToClear: String?
    private var persistentNotice: String?
    private var blockLog = CanvasRecoveryGate()
    private var blockedReports = 0
    static let maxBlockReports = 100
    private var signInSeen = false
    private var firstCommitSeen = false
    private var noticeLabel: NSTextField?
    private var noticeTimer: Timer?
    private var quizButton: NSButton?
    private var backButton: NSButton?
    private var finishButton: NSButton?

    static let stripHeight: CGFloat = 52

    var clearsWebData = true
    var defaults = UserDefaults.standard
    var networkEnabled = true
    var now: () -> Date = { Date() }
    var actionObserver: ((CanvasViewAction) -> Void)?

    private static let isoFrac: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let isoPlain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parseDate(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return isoFrac.date(from: s) ?? isoPlain.date(from: s)
    }

    init(server: URL) {
        self.server = server
    }

    func attach(webView: WKWebView, container: NSView, window: NSWindow?) {
        self.webView = webView
        self.container = container
        self.window = window
    }

    private static func schemeOK(_ url: URL) -> Bool {
        guard let scheme = url.scheme?.lowercased() else { return false }
        if scheme == "https" { return true }
        // Plain http only in dev builds (local test sites).
        return Config.isDevBuild && scheme == "http"
    }

    // MARK: - Start

    /// Parses the bridge message body. Returns an error text when it is unusable.
    func start(body: [String: Any]) -> String? {
        guard let aid = body["attempt_id"] as? String, !aid.isEmpty,
              let urlText = body["url"] as? String, let url = URL(string: urlText),
              ExternalExamController.schemeOK(url), let startHost = url.host?.lowercased(),
              let token = body["token"] as? String, !token.isEmpty else {
            return "bad_request"
        }
        var rules = ((body["allowed_domains"] as? [Any]) ?? []).compactMap { ($0 as? String).flatMap(SiteRule.init) }
        let isCanvas = (body["kind"] as? String) == "canvas"
        if isCanvas {
            // Login redirects on the exam host leave the quiz folder: allow the plain host, exact.
            if !rules.contains(where: { !$0.wildcard && $0.host == startHost && $0.pathPrefix.isEmpty }),
               let r = SiteRule(startHost) {
                rules.append(r)
            }
        } else if !rules.contains(where: { $0.matches(host: startHost, path: url.path) }) {
            // The server always sends an entry for the exam address; if a (stale) page did not,
            // allow exactly the start page's host + folder, never the whole domain tree.
            let folder = url.path.contains("/") ? String(url.path[...url.path.lastIndex(of: "/")!]) : ""
            if let r = SiteRule(startHost + folder) { rules.append(r) }
        }
        let finish = isCanvas ? nil : (body["finish_url_contains"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let serverTime = ExternalExamController.parseDate(body["server_time"] as? String) ?? Date()
        let offset = serverTime.timeIntervalSince(Date())
        let deadline = ExternalExamController.parseDate(body["deadline_at"] as? String)
            ?? Date().addingTimeInterval(3600)
        let title = (body["title"] as? String) ?? "Exam"
        let appSession = (body["session"] as? String) ?? ""
        if isCanvas && url.scheme?.lowercased() != "https" { return "bad_request" }
        let rawRegex = isCanvas ? (body["finish_url_regex"] as? String) : nil
        let regex = CanvasFinish.compile(rawRegex)
        let finishUnusable = isCanvas && regex == nil
        var scope: CanvasScope?
        if isCanvas {
            guard let raw = body["canvas_scope"], !(raw is NSNull), let parsed = CanvasScope(json: raw),
                  CanvasNavigation.decide(url: url, examHost: startHost, scope: parsed, phase: .lobby) == .allow else {
                return "bad_request"
            }
            scope = parsed
        }

        stopTimers()
        finishWork?.cancel(); finishWork = nil
        lastMainCommit = nil
        closePopup()
        finishing = false
        phase = .lobby
        autoOpened = false
        signInSeen = false
        firstCommitSeen = false
        recovery = CanvasRecoveryGate()
        recoveryPending = false
        recoveryTries = 0
        finalLoadDone = false
        blockLog = CanvasRecoveryGate()
        blockedReports = 0
        session = Session(attemptId: aid, startURL: url, rules: rules, finishContains: finish,
                          deadline: deadline, clockOffset: offset, title: title, token: token,
                          appSession: appSession, canvas: isCanvas, finishRegex: regex, scope: scope)
        armed = false
        finishPatternRejected = finishUnusable
        persistentNotice = nil
        markerToClear = nil
        keepArmedOnFirstCommit = false
        if scope != nil, CanvasQuizMarker.isSet(aid, in: defaults) {
            phase = .quiz
            armed = CanvasQuizMarker.isArmed(aid, in: defaults)
            keepArmedOnFirstCommit = armed
            DebugLog.log("external", "canvas phase restored: quiz")
        }
        DebugLog.log("external", "website exam start: \(url.host ?? "?") rules=\(rules.count) finish=\(finish ?? "-") canvas=\(isCanvas) regex=\(regex != nil) scope=\(scope != nil)")
        showStrip(title: title)
        if finishUnusable {
            EventLog.shared.record(.blockedNavigation("finish pattern rejected"))
            showNotice("The finish pattern of this exam is not valid. Your teacher must fix it.", persistent: true)
        }
        startTimers()
        observeURL()
        webView?.load(URLRequest(url: url))
        return nil
    }

    // MARK: - Navigation policy

    func allows(_ url: URL) -> Bool {
        guard let session, let host = url.host?.lowercased(), ExternalExamController.schemeOK(url) else {
            return false
        }
        if session.canvas {
            if MainWindowController.isServerURL(url, server: server) { return false }
            return CanvasPolicy.allows(url, rules: session.rules)
        }
        return session.rules.contains { $0.matches(host: host, path: url.path) }
    }

    /// Call for every allowed main-frame navigation and URL change.
    func checkFinishURL(_ url: URL?, popup: WKWebView? = nil) {
        guard let session, !finishing, let url else { return }
        guard finishHit(url, session: session) else { return }
        if !session.canvas {
            // Give the site a moment to show its own "submitted" confirmation.
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                self?.finish(reason: "finish_url")
            }
            return
        }
        guard finishWork == nil else { return }
        let aid = session.attemptId
        let source: WKWebView? = popup ?? webView
        finishFromPopup = popup != nil
        // Give the site a moment to show its own "submitted" confirmation, then check again
        // that the same exam is still on a finish page.
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.finishWork = nil
            self.finishFromPopup = false
            guard let current = self.session, current.attemptId == aid, !self.finishing,
                  let live = source?.url, self.finishHit(live, session: current) else { return }
            self.finish(reason: "finish_url")
        }
        finishWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    var finishScheduled: Bool { finishWork != nil }

    private func setArmed(_ value: Bool) {
        guard armed != value else { return }
        armed = value
        if let aid = session?.attemptId { CanvasQuizMarker.setArmed(aid, value, in: defaults) }
    }

    /// A frame inside the exam page (New Quizzes show the submission screen in a frame) went to a page
    /// that matches the finish pattern. Frames cannot be read again later, so after the debounce the
    /// exam finishes if the same attempt is still running.
    func checkFrameFinish(_ url: URL) {
        guard let session, session.canvas, !finishing, finishWork == nil, allows(url),
              mainFrameOnExamItem(session), finishHit(url, session: session) else { return }
        let aid = session.attemptId
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.finishWork = nil
            guard let current = self.session, current.attemptId == aid, !self.finishing, self.armed,
                  self.mainFrameOnExamItem(current) else { return }
            self.finish(reason: "finish_url")
        }
        finishWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    private func mainFrameOnExamItem(_ session: Session) -> Bool {
        guard let scope = session.scope, let url = webView?.url ?? lastMainCommit else { return false }
        return CanvasNavigation.isUnderItem(url, scope: scope)
    }

    func isFinishPage(_ url: URL) -> Bool {
        guard let session else { return false }
        return finishHit(url, session: session)
    }

    private func finishHit(_ url: URL, session: Session) -> Bool {
        if session.canvas {
            return armed && CanvasFinish.matches(url, regex: session.finishRegex, scope: session.scope)
        }
        if let needle = session.finishContains { return url.absoluteString.contains(needle) }
        return false
    }

    // MARK: - Events (the JS page is gone while external)

    func queueEvent(type: String, detail: String, time: String) {
        guard isActive else { return }
        pendingEvents.append(["type": type, "detail": detail, "client_time": time])
        flushEvents()
    }

    private func flushEvents() {
        guard let session, !sendingEvents, !pendingEvents.isEmpty else { return }
        let batch = Array(pendingEvents.prefix(100))
        sendingEvents = true
        post(path: "api/student/attempts/\(session.attemptId)/events", session: session,
             body: ["events": batch]) { [weak self] status, _ in
            guard let self else { return }
            self.sendingEvents = false
            if let status, (200..<300).contains(status) || (400..<500).contains(status) && status != 429 {
                // stored, or rejected for good: drop this batch
                self.pendingEvents.removeFirst(min(batch.count, self.pendingEvents.count))
                if !self.pendingEvents.isEmpty { self.flushEvents() }
            } else {
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { [weak self] in self?.flushEvents() }
            }
        }
    }

    // MARK: - Heartbeat + countdown

    private func startTimers() {
        let cd = Timer(timeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
        RunLoop.main.add(cd, forMode: .common)
        countdownTimer = cd
        let hb = Timer(timeInterval: 5, repeats: true) { [weak self] _ in self?.heartbeat() }
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
        timeLabel?.stringValue = h > 0 ? String(format: "%d:%02d:%02d", h, m, s) : String(format: "%02d:%02d", m, s)
        timeLabel?.textColor = left < 300
            ? NSColor(calibratedRed: 0.71, green: 0.34, blue: 0.04, alpha: 1)
            : NSColor(calibratedRed: 0.11, green: 0.15, blue: 0.20, alpha: 1)
        if left == 0 { finish(reason: "deadline") }
    }

    /// Page-alive heartbeat (the page is gone). The lock state itself goes in the signed
    /// app beats (AppSession), which the server trusts; "locked" here is informational.
    private func heartbeat() {
        guard let session, !finishing else { return }
        let locked = LockdownController.shared.isLocked
        post(path: "api/student/attempts/\(session.attemptId)/heartbeat", session: session,
             body: ["current_question": 0, "locked": locked]) { [weak self] status, json in
            guard let self, status == 200, let json else { return }
            if let d = ExternalExamController.parseDate(json["deadline_at"] as? String) {
                self.session?.deadline = d          // teacher may have added time
            }
            if (json["status"] as? String) == "submitted" {
                self.finish(reason: "closed_by_teacher")
            }
        }
    }

    private func post(path: String, session: Session, body: [String: Any],
                      completion: @escaping (Int?, [String: Any]?) -> Void) {
        guard networkEnabled else { completion(nil, nil); return }
        Net.request("POST", path: path, token: session.token,
                    appSession: session.appSession.isEmpty ? nil : session.appSession,
                    body: body, completion: completion)
    }

    // MARK: - Finish / teardown

    @objc private func finishPressed() {
        DebugLog.log("external", "Finish exam button pressed")
        guard isActive, !finishing, !isLockedIn else { return }
        guard let window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Finish the exam?"
        alert.informativeText = "Make sure you submitted your answers on the website first. "
            + "After this, the exam closes and your computer unlocks."
        alert.addButton(withTitle: "Finish exam")
        alert.addButton(withTitle: "Keep working")
        alert.beginSheetModal(for: window) { [weak self] response in
            if response == .alertFirstButtonReturn { self?.finish(reason: "student_finished") }
        }
    }

    /// Submit via the web page and leave external mode. The lock stays until JS sends
    /// "unlock" and the server confirms the hand-in.
    func finish(reason: String) {
        guard let session, !finishing else { return }
        DebugLog.log("external", "finish: \(reason)")
        finishing = true
        // Send what is still queued plus the finish event straight away, with this
        // session's token: after teardown the exam page takes over event forwarding.
        var batch = pendingEvents
        batch.append(["type": "external_finished", "detail": reason,
                      "client_time": ExternalExamController.isoFrac.string(from: Date())])
        post(path: "api/student/attempts/\(session.attemptId)/events", session: session,
             body: ["events": batch]) { _, _ in }
        let aid = session.attemptId
        let wasCanvas = session.canvas
        markerToClear = aid
        teardown()
        var comps = URLComponents(url: server.appendingPathComponent("student/external-done/\(aid)"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "reason", value: reason)]
        if let url = comps?.url { webView?.load(URLRequest(url: url)) }
        if wasCanvas && clearsWebData { clearCanvasData() }
    }

    /// The hand-in page of a finished Canvas exam has loaded: the quiz marker of that attempt can go.
    func externalDonePageLoaded(_ url: URL?) {
        guard let aid = markerToClear, let url, MainWindowController.isServerURL(url, server: server),
              url.path.lowercased().hasPrefix("/student/external-done/" + aid.lowercased()) else { return }
        markerToClear = nil
        CanvasQuizMarker.clear(aid, in: defaults)
    }

    /// Leave external mode without submitting (emergency exit / safety unlock).
    func abort() {
        guard isActive else { return }
        DebugLog.log("external", "aborted (emergency exit / safety unlock)")
        let wasCanvas = session?.canvas == true
        if let aid = session?.attemptId { CanvasQuizMarker.clear(aid, in: defaults) }
        teardown()
        if wasCanvas && clearsWebData { clearCanvasData() }
        webView?.load(URLRequest(url: server.appendingPathComponent("student")))
    }

    private func teardown() {
        stopTimers()
        finishWork?.cancel(); finishWork = nil
        closePopup()
        urlObservation?.invalidate(); urlObservation = nil
        noticeTimer?.invalidate(); noticeTimer = nil
        hideStrip()
        session = nil
        pendingEvents.removeAll()
    }

    private func observeURL() {
        urlObservation?.invalidate()
        urlObservation = webView?.observe(\.url, options: [.new]) { [weak self] wv, _ in
            let url = wv.url
            DispatchQueue.main.async {
                self?.checkFinishURL(url)
                self?.updateNavButtons()
                self?.recoverIfBlocked(url)
            }
        }
    }

    // MARK: - Canvas mode

    var isCanvas: Bool { session?.canvas == true }

    /// `mainFrame` false for frames inside a page: the restricted Canvas browser applies to
    /// main-frame navigations only.
    func childAllows(_ url: URL, mainFrame: Bool = true) -> Bool {
        isCanvas && (CanvasPolicy.isBlank(url) || (allows(url) && !(mainFrame ? blocksMainFrame(url) : blocksExamHostFrame(url))))
    }

    /// During a Canvas exam no page may open any page of the exam server: it could end the exam or
    /// leave the lock. The app's own load of the hand-in page happens after the session is gone.
    func blocksExamServer(_ url: URL) -> Bool {
        isCanvas && MainWindowController.isServerURL(url, server: server)
    }

    var canvasStartURL: URL? { session?.canvas == true ? session?.startURL : nil }

    private var examHost: String { (session?.startURL.host ?? "").lowercased() }

    /// True when the restricted Canvas browser refuses this main-frame page of the exam's Canvas host.
    /// Always false for non-Canvas exams and Canvas exams without a scope.
    func blocksMainFrame(_ url: URL) -> Bool {
        guard let session, session.canvas, let scope = session.scope else { return false }
        return CanvasNavigation.decide(url: url, examHost: examHost, scope: scope, phase: phase) == .block
    }

    func blocksExamHostFrame(_ url: URL) -> Bool {
        guard let scope = session?.scope, url.host?.lowercased() == examHost else { return false }
        return !CanvasNavigation.isHelperFrame(url, scope: scope) && blocksMainFrame(url)
    }

    func allowsMainFrame(_ url: URL) -> Bool {
        allows(url) && !blocksMainFrame(url)
    }

    func blockDetail(_ url: URL) -> String {
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedPath ?? ""
        return "canvas page \(String(path.prefix(120)))"
    }

    /// Records a cancelled main-frame navigation once per page (and at most maxBlockReports per exam)
    /// and shows the notice every time.
    func reportBlocked(_ url: URL) {
        let detail = blockDetail(url)
        if blockedReports < ExternalExamController.maxBlockReports, blockLog.firstTime(detail) {
            blockedReports += 1
            EventLog.shared.record(.blockedNavigation(detail))
        }
        showNotice()
    }

    /// Whether Back (or a recovery) may return to this page: allowed by the policy, and in the quiz
    /// phase also on the exam's Canvas host and not a sign-in page.
    func backAllowed(_ previous: URL) -> Bool {
        guard allowsMainFrame(previous) else { return false }
        if phase == .quiz {
            guard let scope = session?.scope else { return false }
            return previous.host?.lowercased() == examHost && !CanvasNavigation.isSignIn(url: previous, examHost: examHost)
                && CanvasNavigation.isUnderItem(previous, scope: scope)
        }
        return true
    }

    static let blockedNotice = "This page is not available during the exam."

    /// A short message in the strip that fades after a few seconds.
    func showNotice(_ text: String = ExternalExamController.blockedNotice, persistent: Bool = false) {
        if persistent { persistentNotice = text }
        guard let label = noticeLabel else { return }
        label.stringValue = text
        label.isHidden = false
        noticeTimer?.invalidate()
        let timer = Timer(timeInterval: 4, repeats: false) { [weak self] _ in
            guard let self, let label = self.noticeLabel else { return }
            if let kept = self.persistentNotice {
                label.stringValue = kept
            } else {
                label.isHidden = true
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        noticeTimer = timer
    }

    /// A main-frame page of the exam view was committed: moves the phase on and refreshes the buttons.
    func canvasCommitted(_ url: URL?) {
        guard let session, session.canvas, let url, url.scheme?.lowercased() == "https" else { return }
        guard let scope = session.scope else { return }
        lastMainCommit = url
        if CanvasNavigation.isSignIn(url: url, examHost: examHost) { signInSeen = true }
        if !firstCommitSeen {
            firstCommitSeen = true
            if url != session.startURL, CanvasNavigation.isLanding(url: url, examHost: examHost, scope: scope) {
                signInSeen = true
            }
        }
        if phase == .lobby, CanvasNavigation.entersQuiz(url: url, examHost: examHost, scope: scope) {
            phase = .quiz
            CanvasQuizMarker.set(session.attemptId, in: defaults)
            EventLog.shared.record(.canvasQuizStarted)
            DebugLog.log("external", "canvas phase: quiz")
        }
        let keepArmed = keepArmedOnFirstCommit
        keepArmedOnFirstCommit = false
        if CanvasNavigation.entersQuiz(url: url, examHost: examHost, scope: scope) {
            setArmed(true)
        } else if scope.itemKind == "quizzes", !keepArmed, finishWork == nil,
                  CanvasNavigation.isItemLanding(url: url, examHost: examHost, scope: scope) {
            setArmed(false)
        }
        updateNavButtons()
        updateFinishButton()
    }

    /// Route changes made by the page itself (pushState) never reach the navigation policy. When the
    /// committed page is one the restricted browser refuses, go back to an allowed page. At most one
    /// recovery per second, and only to a page the policy allows.
    func recoverIfBlocked(_ url: URL?) {
        guard let session, session.canvas, session.scope != nil, !finishing, let url else { return }
        guard allows(url), blocksMainFrame(url) else {
            recoveryTries = 0
            finalLoadDone = false
            return
        }
        if recovery.firstTime(blockDetail(url)) && blockedReports < ExternalExamController.maxBlockReports {
            blockedReports += 1
            EventLog.shared.record(.blockedNavigation(blockDetail(url) + " (in page)"))
        }
        showNotice()
        guard recovery.shouldRecover(now: now()) else {
            scheduleRecheck()
            return
        }
        guard recoveryTries < ExternalExamController.maxRecoveries else {
            if !finalLoadDone {
                finalLoadDone = true
                perform(.load(session.startURL))
            }
            return
        }
        recoveryTries += 1
        let startOK = allowsMainFrame(session.startURL)
        if phase == .quiz && startOK {
            perform(.load(session.startURL))
        } else if let previous = webView?.backForwardList.backItem?.url, previous != url, backAllowed(previous) {
            perform(.goBack)
        } else if startOK {
            perform(.load(session.startURL))
        }
        scheduleRecheck()
    }

    static let maxRecoveries = 5

    private func scheduleRecheck() {
        guard !recoveryPending else { return }
        recoveryPending = true
        DispatchQueue.main.asyncAfter(deadline: .now() + CanvasRecoveryGate.minInterval + 0.1) { [weak self] in
            guard let self else { return }
            self.recoveryPending = false
            self.recoverIfBlocked(self.webView?.url)
        }
    }

    /// A popup child view that reaches a page the restricted browser refuses is closed.
    func popupCommitted(_ url: URL?) {
        guard let session, session.canvas, let url else { return }
        canvasCommitted(url)
        guard session.scope != nil, allows(url), blocksMainFrame(url) else { return }
        reportBlocked(url)
        closePopup()
    }

    func autoOpenIfNeeded(url: URL?) {
        guard let session, session.canvas, !finishing, let scope = session.scope, !autoOpened, signInSeen,
              phase == .lobby, let url,
              CanvasNavigation.isLanding(url: url, examHost: examHost, scope: scope) else { return }
        autoOpened = true
        DebugLog.log("external", "canvas exam link opened after sign-in")
        perform(.load(session.startURL))
    }

    private func perform(_ action: CanvasViewAction) {
        if let observer = actionObserver {
            observer(action)
            return
        }
        switch action {
        case .goBack:
            webView?.goBack()
        case .load(let url):
            webView?.load(URLRequest(url: url))
        }
    }

    @objc private func quizPressed() { openExamLink() }

    func openExamLink() {
        guard let session, session.canvas, !finishing, finishWork == nil else { return }
        if let webView {
            if webView.isLoading { return }
            if let scope = session.scope, let current = webView.url,
               current.host?.lowercased() == examHost, CanvasNavigation.isUnderItem(current, scope: scope) { return }
        }
        setArmed(false)
        perform(.load(session.startURL))
    }

    @objc private func backPressed() {
        guard let webView, session?.canvas == true, !finishing,
              let previous = webView.backForwardList.backItem?.url, backAllowed(previous) else { return }
        webView.goBack()
    }

    /// True once the quiz of a scoped Canvas exam has started: no control of the app ends the exam
    /// then (only the finish page, the server, the deadline or the emergency exit).
    var isLockedIn: Bool {
        session?.canvas == true && session?.scope != nil && phase == .quiz && !finishPatternRejected
    }

    private func updateFinishButton() {
        finishButton?.isHidden = isLockedIn
    }

    private func updateNavButtons() {
        guard let backButton else { return }
        if let previous = webView?.backForwardList.backItem?.url {
            backButton.isEnabled = backAllowed(previous)
        } else {
            backButton.isEnabled = false
        }
    }

    func popupPlan(url: URL?, openerHost: String?) -> CanvasPopup.Plan {
        guard let session, session.canvas else { return .block }
        if let url, !CanvasPolicy.isBlank(url), blocksExamServer(url) || blocksMainFrame(url) { return .block }
        if session.scope != nil, let url, !CanvasPolicy.isBlank(url), url.host?.lowercased() == examHost {
            return CanvasPolicy.allows(url, rules: session.rules) ? .sameView : .block
        }
        var survives = false
        if #unavailable(macOS 14.4) { survives = true }
        return CanvasPopup.plan(url: url, openerHost: openerHost, rules: session.rules,
                                openerSurvivesCrossOrigin: survives)
    }

    func makePopup(configuration: WKWebViewConfiguration) -> WKWebView? {
        guard isCanvas, let container, let webView else { return nil }
        if popupView != nil {
            EventLog.shared.record(.blockedNavigation("second popup"))
            return nil
        }
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: CanvasScripts.popupGuard, injectionTime: .atDocumentStart,
                                       forMainFrameOnly: false))
        configuration.userContentController = ucc
        let child = ExamWebView(frame: webView.frame, configuration: configuration)
        child.autoresizingMask = [.width, .height]
        child.navigationDelegate = webView.navigationDelegate
        child.uiDelegate = webView.uiDelegate
        child.allowsBackForwardNavigationGestures = false
        child.allowsMagnification = false
        child.allowsLinkPreview = false
        container.addSubview(child, positioned: .above, relativeTo: webView)
        let button = NSButton(title: "Close sign-in window", target: self, action: #selector(closePopupPressed))
        button.bezelStyle = .rounded
        button.sizeToFit()
        button.frame.origin = NSPoint(x: child.frame.maxX - button.frame.width - 12,
                                      y: child.frame.maxY - button.frame.height - 12)
        button.autoresizingMask = [.minXMargin, .minYMargin]
        container.addSubview(button, positioned: .above, relativeTo: child)
        popupView = child
        popupClose = button
        popupObservation = child.observe(\.url, options: [.new]) { [weak self, weak child] wv, _ in
            let url = wv.url
            DispatchQueue.main.async {
                guard let self, let child, self.popupView === child else { return }
                self.popupCommitted(url)
                self.checkFinishURL(url, popup: child)
            }
        }
        return child
    }

    @objc private func closePopupPressed() { closePopup() }

    func closePopup(ifView view: WKWebView? = nil) {
        if let view, view !== popupView { return }
        popupObservation?.invalidate(); popupObservation = nil
        if finishFromPopup {
            finishWork?.cancel(); finishWork = nil
            finishFromPopup = false
        }
        popupView?.stopLoading()
        popupView?.navigationDelegate = nil
        popupView?.uiDelegate = nil
        popupView?.removeFromSuperview()
        popupClose?.removeFromSuperview()
        popupView = nil
        popupClose = nil
    }

    func isPopup(_ view: WKWebView) -> Bool { popupView != nil && view === popupView }

    func canvasPageLoaded(_ view: WKWebView) {
        guard let session, session.canvas, !finishing else { return }
        updateNavButtons()
        autoOpenIfNeeded(url: view.url)
    }

    private func clearCanvasData() {
        let store = WKWebsiteDataStore.default()
        let serverHost = (server.host ?? "").lowercased()
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        store.fetchDataRecords(ofTypes: types) { records in
            let others = records.filter {
                CanvasData.shouldRemove(domain: $0.displayName, serverHost: serverHost)
            }
            guard !others.isEmpty else { return }
            store.removeData(ofTypes: types, for: others) {
                DebugLog.log("external", "canvas web data cleared: \(others.count) site(s)")
            }
        }
    }

    // MARK: - Strip UI (light: white, hairline bottom border)

    private func showStrip(title: String) {
        guard let container, let webView else { return }
        hideStrip()
        let h = ExternalExamController.stripHeight
        let b = container.bounds
        let v = NSView(frame: NSRect(x: 0, y: b.height - h, width: b.width, height: h))
        v.autoresizingMask = [.width, .minYMargin]
        v.wantsLayer = true
        v.layer?.backgroundColor = NSColor.white.cgColor

        let line = NSView(frame: NSRect(x: 0, y: 0, width: b.width, height: 1))
        line.autoresizingMask = [.width, .maxYMargin]
        line.wantsLayer = true
        line.layer?.backgroundColor = NSColor(calibratedRed: 0.886, green: 0.902, blue: 0.925, alpha: 1).cgColor
        v.addSubview(line)

        let lock = NSTextField(labelWithString: "Locked")
        lock.font = .systemFont(ofSize: 12, weight: .semibold)
        lock.textColor = NSColor(calibratedRed: 0.086, green: 0.475, blue: 0.298, alpha: 1)
        lock.wantsLayer = true

        let t = NSTextField(labelWithString: title)
        t.font = .systemFont(ofSize: 14, weight: .semibold)
        t.textColor = NSColor(calibratedRed: 0.11, green: 0.15, blue: 0.20, alpha: 1)
        t.lineBreakMode = .byTruncatingTail
        t.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        titleLabel = t

        let time = NSTextField(labelWithString: "--:--")
        time.font = .monospacedDigitSystemFont(ofSize: 16, weight: .semibold)
        timeLabel = time

        let button = NSButton(title: "Finish exam", target: self, action: #selector(finishPressed))
        button.bezelStyle = .rounded
        button.keyEquivalent = ""

        let spacer = NSView()
        spacer.setContentHuggingPriority(.init(1), for: .horizontal)

        let notice = NSTextField(labelWithString: "")
        notice.font = .systemFont(ofSize: 12, weight: .medium)
        notice.textColor = NSColor(calibratedRed: 0.71, green: 0.34, blue: 0.04, alpha: 1)
        notice.lineBreakMode = .byTruncatingTail
        notice.isHidden = true
        noticeLabel = notice

        var views: [NSView] = [BrandMark.view(size: 28), lock, t, notice, spacer, time]
        quizButton = nil
        backButton = nil
        if session?.canvas == true, session?.scope != nil {
            let back = NSButton(title: "Back", target: self, action: #selector(backPressed))
            back.bezelStyle = .rounded
            back.isEnabled = false
            let quiz = NSButton(title: "Go to my quiz", target: self, action: #selector(quizPressed))
            quiz.bezelStyle = .rounded
            backButton = back
            quizButton = quiz
            views.append(contentsOf: [back, quiz])
        }
        views.append(button)
        finishButton = button
        button.isHidden = isLockedIn

        let stack = NSStackView(views: views)
        stack.orientation = .horizontal
        stack.alignment = .centerY
        stack.spacing = 14
        stack.edgeInsets = NSEdgeInsets(top: 0, left: 20, bottom: 0, right: 16)
        stack.translatesAutoresizingMaskIntoConstraints = false
        v.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: v.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: v.trailingAnchor),
            stack.topAnchor.constraint(equalTo: v.topAnchor),
            stack.bottomAnchor.constraint(equalTo: v.bottomAnchor, constant: -1)
        ])

        container.addSubview(v, positioned: .above, relativeTo: webView)
        strip = v
        var f = b
        f.size.height = max(0, b.height - h)
        webView.frame = f
        popupView?.frame = f
    }

    private func hideStrip() {
        strip?.removeFromSuperview()
        strip = nil; titleLabel = nil; timeLabel = nil
        noticeLabel = nil; quizButton = nil; backButton = nil; finishButton = nil
        if let container, let webView {
            webView.frame = container.bounds
            popupView?.frame = container.bounds
        }
    }
}
