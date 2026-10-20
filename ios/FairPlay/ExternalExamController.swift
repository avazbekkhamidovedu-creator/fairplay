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
        let rules = SiteRule.rules(from: (body["allowed_domains"] as? [Any]) ?? [], startURL: url)
        let finish = (body["finish_url_contains"] as? String).flatMap { $0.isEmpty ? nil : $0 }
        let serverTime = ExternalExamController.parseDate(body["server_time"] as? String) ?? Date()
        let deadline = ExternalExamController.parseDate(body["deadline_at"] as? String) ?? Date().addingTimeInterval(3600)
        let title = (body["title"] as? String) ?? "Exam"
        stopTimers()
        finishing = false
        session = Session(attemptId: aid, startURL: url, rules: rules, finishContains: finish, deadline: deadline,
                          clockOffset: serverTime.timeIntervalSince(Date()), title: title, token: token,
                          appSession: (body["session"] as? String) ?? "")
        DebugLog.log("external", "website exam start: \(url.host ?? "?") rules=\(rules.count) finish=\(finish ?? "-")")
        bar?.setExternal(title: title)
        startTimers()
        observeURL()
        webView?.load(URLRequest(url: url))
        return nil
    }

    func allows(_ url: URL) -> Bool {
        guard let session, let host = url.host?.lowercased(), ExternalExamController.schemeOK(url) else { return false }
        return session.rules.contains { $0.matches(host: host, path: url.path) }
    }

    func checkFinishURL(_ url: URL?) {
        guard let session, !finishing, let needle = session.finishContains, let url else { return }
        if url.absoluteString.contains(needle) {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.finish(reason: "finish_url") }
        }
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
        guard isActive, !finishing else { return }
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
        teardown()
        var comps = URLComponents(url: server.appendingPathComponent("student/external-done/\(aid)"), resolvingAgainstBaseURL: false)
        comps?.queryItems = [URLQueryItem(name: "reason", value: reason)]
        if let url = comps?.url { webView?.load(URLRequest(url: url)) }
    }

    /// Leave website mode without submitting (invigilator exit).
    func abort() {
        guard isActive else { return }
        DebugLog.log("external", "aborted (invigilator exit)")
        teardown()
        webView?.load(URLRequest(url: server.appendingPathComponent("student")))
    }

    private func teardown() {
        stopTimers()
        urlObservation?.invalidate(); urlObservation = nil
        bar?.setExternal(title: nil)
        session = nil
        pendingEvents.removeAll()
    }

    private func observeURL() {
        urlObservation?.invalidate()
        urlObservation = webView?.observe(\.url, options: [.new]) { [weak self] wv, _ in
            DispatchQueue.main.async { self?.checkFinishURL(wv.url) }
        }
    }
}
