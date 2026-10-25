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
            if pathPrefix.hasSuffix("/") { return path.hasPrefix(pathPrefix) }
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
    private var canvasFills = CanvasFillBudget()
    private var canvasBusy = false
    private var canvasHelpShown = false
    private var finishWork: DispatchWorkItem?
    private var popupView: WKWebView?
    private var popupClose: NSButton?

    static let stripHeight: CGFloat = 52

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
        let finish = (body["finish_url_contains"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let serverTime = ExternalExamController.parseDate(body["server_time"] as? String) ?? Date()
        let offset = serverTime.timeIntervalSince(Date())
        let deadline = ExternalExamController.parseDate(body["deadline_at"] as? String)
            ?? Date().addingTimeInterval(3600)
        let title = (body["title"] as? String) ?? "Exam"
        let appSession = (body["session"] as? String) ?? ""
        if isCanvas && url.scheme?.lowercased() != "https" { return "bad_request" }
        let regex = isCanvas ? CanvasFinish.compile(body["finish_url_regex"] as? String) : nil

        stopTimers()
        finishWork?.cancel(); finishWork = nil
        closePopup()
        finishing = false
        canvasFills = CanvasFillBudget()
        canvasBusy = false
        canvasHelpShown = false
        session = Session(attemptId: aid, startURL: url, rules: rules, finishContains: finish,
                          deadline: deadline, clockOffset: offset, title: title, token: token,
                          appSession: appSession, canvas: isCanvas, finishRegex: regex)
        DebugLog.log("external", "website exam start: \(url.host ?? "?") rules=\(rules.count) finish=\(finish ?? "-") canvas=\(isCanvas) regex=\(regex != nil)")
        showStrip(title: title)
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
        if session.canvas { return CanvasPolicy.allows(url, rules: session.rules) }
        return session.rules.contains { $0.matches(host: host, path: url.path) }
    }

    /// Call for every allowed main-frame navigation and URL change.
    func checkFinishURL(_ url: URL?) {
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
        // Give the site a moment to show its own "submitted" confirmation, then check again
        // that the same exam is still on a finish page.
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.finishWork = nil
            guard let current = self.session, current.attemptId == aid, !self.finishing,
                  let live = self.webView?.url, self.finishHit(live, session: current) else { return }
            self.finish(reason: "finish_url")
        }
        finishWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 2, execute: work)
    }

    private func finishHit(_ url: URL, session: Session) -> Bool {
        if session.canvas {
            return CanvasFinish.matches(url, contains: session.finishContains, regex: session.finishRegex)
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
        Net.request("POST", path: path, token: session.token,
                    appSession: session.appSession.isEmpty ? nil : session.appSession,
                    body: body, completion: completion)
    }

    // MARK: - Finish / teardown

    @objc private func finishPressed() {
        DebugLog.log("external", "Finish exam button pressed")
        guard isActive, !finishing else { return }
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
        let canvasRules = session.rules
        teardown()
        var comps = URLComponents(url: server.appendingPathComponent("student/external-done/\(aid)"),
                                  resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "reason", value: reason)]
        if let url = comps?.url { webView?.load(URLRequest(url: url)) }
        if wasCanvas { clearCanvasData(rules: canvasRules) }
    }

    /// Leave external mode without submitting (emergency exit / safety unlock).
    func abort() {
        guard isActive else { return }
        DebugLog.log("external", "aborted (emergency exit / safety unlock)")
        let wasCanvas = session?.canvas == true
        let canvasRules = session?.rules ?? []
        teardown()
        if wasCanvas { clearCanvasData(rules: canvasRules) }
        webView?.load(URLRequest(url: server.appendingPathComponent("student")))
    }

    private func teardown() {
        stopTimers()
        finishWork?.cancel(); finishWork = nil
        closePopup()
        urlObservation?.invalidate(); urlObservation = nil
        hideStrip()
        session = nil
        pendingEvents.removeAll()
    }

    private func observeURL() {
        urlObservation?.invalidate()
        urlObservation = webView?.observe(\.url, options: [.new]) { [weak self] wv, _ in
            let url = wv.url
            DispatchQueue.main.async { self?.checkFinishURL(url) }
        }
    }

    // MARK: - Canvas mode

    var isCanvas: Bool { session?.canvas == true }

    func childAllows(_ url: URL) -> Bool {
        isCanvas && (CanvasPolicy.isBlank(url) || allows(url))
    }

    func popupPlan(url: URL?, openerHost: String?) -> CanvasPopup.Plan {
        guard let session, session.canvas else { return .block }
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
        return child
    }

    @objc private func closePopupPressed() { closePopup() }

    func closePopup(ifView view: WKWebView? = nil) {
        if let view, view !== popupView { return }
        popupView?.stopLoading()
        popupView?.navigationDelegate = nil
        popupView?.uiDelegate = nil
        popupView?.removeFromSuperview()
        popupClose?.removeFromSuperview()
        popupView = nil
        popupClose = nil
    }

    func isPopup(_ view: WKWebView) -> Bool { popupView != nil && view === popupView }

    private func canvasOrigin(_ view: WKWebView?, session: Session) -> String? {
        guard let url = view?.url else { return nil }
        return CanvasPolicy.fillOrigin(page: url, start: session.startURL)
    }

    func canvasPageLoaded(_ view: WKWebView) {
        guard let session, session.canvas, !finishing, !canvasBusy,
              canvasOrigin(view, session: session) != nil else { return }
        canvasBusy = true
        view.evaluateJavaScript(CanvasScripts.probe, in: nil, in: .defaultClient) { [weak self, weak view] (result: Result<Any, Error>) in
            guard let self else { return }
            var found = false
            if case .success(let value) = result { found = (value as? Bool) == true }
            self.canvasProbed(found: found, view: view)
        }
    }

    private func canvasProbed(found: Bool, view: WKWebView?) {
        guard found, let session, !finishing, let view, canvasOrigin(view, session: session) != nil else {
            canvasBusy = false
            return
        }
        guard !canvasFills.exhausted else {
            canvasBusy = false
            canvasFailed("fill limit reached")
            return
        }
        requestCanvasCode(attemptId: session.attemptId, view: view, retries: 0)
    }

    /// A 429 can be left over from an earlier run of this attempt (resume): up to three more tries,
    /// each after the server's Retry-After, before the student is told to ask for help.
    private func requestCanvasCode(attemptId: String, view: WKWebView, retries: Int) {
        guard let session, session.attemptId == attemptId, !finishing else { canvasBusy = false; return }
        fetchCanvasCode(session: session) { [weak self, weak view] code, status, retryAfter in
            guard let self else { return }
            guard let current = self.session, current.attemptId == attemptId, current.canvas, !self.finishing else {
                self.canvasBusy = false
                return
            }
            guard let code else {
                if status == 429, retries < CanvasRetry.maxRetries {
                    let wait = CanvasRetry.delay(retryAfter: retryAfter)
                    DispatchQueue.main.asyncAfter(deadline: .now() + wait) { [weak self, weak view] in
                        guard let self else { return }
                        guard let view, self.canvasOrigin(view, session: current) != nil else {
                            self.canvasBusy = false
                            return
                        }
                        self.requestCanvasCode(attemptId: attemptId, view: view, retries: retries + 1)
                    }
                    return
                }
                self.canvasBusy = false
                self.canvasFailed("code unavailable")
                return
            }
            guard let view, let origin = self.canvasOrigin(view, session: current),
                  self.canvasFills.take() else {
                self.canvasBusy = false
                return
            }
            DebugLog.log("external", "canvas access code fill \(self.canvasFills.used)")
            let script = CanvasScripts.fill(code: code, origin: origin)
            view.evaluateJavaScript(script, in: nil, in: .defaultClient) { [weak self] (result: Result<Any, Error>) in
                guard let self else { return }
                self.canvasBusy = false
                var outcome = "none"
                if case .success(let value) = result, let text = value as? String { outcome = text }
                if outcome != "clicked" { self.canvasFailed("fill \(outcome)") }
            }
        }
    }

    private func fetchCanvasCode(session: Session, completion: @escaping (String?, Int?, String?) -> Void) {
        let ts = Int64(Date().timeIntervalSince1970 * 1000)
        let body = CanvasProof.body(attemptId: session.attemptId, session: session.appSession,
                                    deviceId: AppSession.shared.deviceId, ts: ts, sign: AppSession.sign)
        Net.requestWithRetryAfter("POST", path: "api/student/attempts/\(session.attemptId)/canvas-code",
                                  token: session.token,
                                  appSession: session.appSession.isEmpty ? nil : session.appSession,
                                  body: body) { status, json, retryAfter in
            completion(CanvasProof.parseCode(status: status, json: json), status, retryAfter)
        }
    }

    private func canvasFailed(_ reason: String) {
        EventLog.shared.record(.canvasCodeFailed(reason))
        guard !canvasHelpShown else { return }
        canvasHelpShown = true
        guard let window, window.attachedSheet == nil else { return }
        let alert = NSAlert()
        alert.messageText = "Ask your teacher for help"
        alert.informativeText = "FairPlay could not enter the access code for this quiz."
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window, completionHandler: nil)
    }

    private func clearCanvasData(rules: [SiteRule]) {
        let store = WKWebsiteDataStore.default()
        let serverHost = (server.host ?? "").lowercased()
        let ruleHosts = rules.map { $0.host }
        let types = WKWebsiteDataStore.allWebsiteDataTypes()
        store.fetchDataRecords(ofTypes: types) { records in
            let others = records.filter {
                CanvasData.shouldRemove(domain: $0.displayName, serverHost: serverHost, ruleHosts: ruleHosts)
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

        let stack = NSStackView(views: [BrandMark.view(size: 28), lock, t, spacer, time, button])
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
        if let container, let webView {
            webView.frame = container.bounds
            popupView?.frame = container.bounds
        }
    }
}
