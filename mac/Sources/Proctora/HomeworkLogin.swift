import Foundation

/// 2026-10-30: the separate DKU OJ homework sign-in window. Not a lock: nothing is closed, no
/// event tap, no beats; the student can close the window at any time (that is a cancel). The
/// page sends {action:"openHomeworkLogin", state, start_url, allowed_hosts, finish_url,
/// resume_paths}; the window answers {type:"homeworkLoginResult", ...} (`message(for:)`).
///
/// Pure decisions only (HomeworkLoginWindowController acts on them), for the tests. The same
/// file, rule for rule, is the iPad's FairPlayCore HomeworkLogin.swift.
struct HomeworkLogin {

    static let notDuringExam = "Not available during an exam"
    static let notPartOfSignIn = "This page isn't part of sign-in"
    static let startFailed = "Sign-in could not be started"
    static let pageFailed = "The sign-in page stopped working"
    static let maxResumes = 3
    static let minTokenLength = 16
    static let maxTokenLength = 128

    enum Outcome: Equatable {
        case ticket(String)
        case cancelled
        case failed(String)
    }

    enum Navigation: Equatable {
        case allow
        case block
        /// A finish URL without a matching state: the navigation is cancelled, the window stays.
        case ignore
        case finish(ticket: String)
    }

    let state: String
    let startURL: URL
    let allowedHosts: [String]
    let finishURL: URL
    let resumePaths: [String]
    private(set) var resumes = 0

    /// The refusal text when sign-in may not open now, else nil.
    static func refusal(examActive: Bool, deviceTestRunning: Bool) -> String? {
        examActive || deviceTestRunning ? notDuringExam : nil
    }

    /// A state or ticket: only A-Z a-z 0-9 _ -, 16 to 128 characters.
    static func isToken(_ s: String) -> Bool {
        (minTokenLength...maxTokenLength).contains(s.utf8.count)
            && s.utf8.allSatisfy { ($0 >= 0x30 && $0 <= 0x39) || ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || $0 == 0x5F || $0 == 0x2D }
    }

    /// nil when the message is not usable: a state that is not a token, a start page that is not
    /// https on an allowed host, no allowed hosts, or a finish URL that is not https.
    init?(body: [String: Any]) {
        guard let state = body["state"] as? String, Self.isToken(state),
              let startRaw = body["start_url"] as? String, let start = URL(string: startRaw),
              let finishRaw = body["finish_url"] as? String, let finish = URL(string: finishRaw),
              finish.scheme?.lowercased() == "https", finish.host != nil else { return nil }
        let hosts = AllowedSites.parse(body["allowed_hosts"])
        guard !hosts.isEmpty, start.scheme?.lowercased() == "https",
              AllowedSites.frameAllowed(start, allowed: hosts) else { return nil }
        let paths = (body["resume_paths"] as? [Any] ?? []).compactMap { $0 as? String }.filter { $0.hasPrefix("/") }
        self.state = state
        self.startURL = start
        self.allowedHosts = hosts
        self.finishURL = finish
        self.resumePaths = paths.isEmpty ? ["/"] : paths
    }

    /// What to do with a navigation that is about to start. The finish URL is checked first and
    /// only for a top-level navigation; every other URL, in any frame, must be https on an
    /// allowed host (AllowedSites.frameAllowed: default port, no user name or password).
    func decide(_ url: URL, mainFrame: Bool) -> Navigation {
        if mainFrame, isFinishPrefix(url) {
            guard let ticket = ticket(in: url) else { return .ignore }
            return .finish(ticket: ticket)
        }
        return AllowedSites.frameAllowed(url, allowed: allowedHosts) ? .allow : .block
    }

    /// After a top-level navigation FINISHED on `url`: the start page again when DKU's NetID
    /// login lands on one of the resume paths on the OJ origin, at most `maxResumes` times.
    mutating func resume(afterFinishing url: URL?) -> URL? {
        guard resumes < Self.maxResumes, let url, url.scheme?.lowercased() == "https",
              let host = url.host?.lowercased(), host == startURL.host?.lowercased(),
              (url.port ?? 443) == (startURL.port ?? 443) else { return nil }
        let path = url.path.isEmpty ? "/" : url.path
        guard resumePaths.contains(path) else { return nil }
        resumes += 1
        return startURL
    }

    /// The host as the address strip shows it, punycode as WebKit reports it, so a look-alike shows.
    static func displayHost(_ url: URL?) -> String {
        url?.host?.lowercased() ?? ""
    }

    /// The message for the page ({type:"homeworkLoginResult"}). `state` is the one the page sent.
    func message(for outcome: Outcome) -> [String: Any] {
        Self.message(state: state, outcome: outcome)
    }

    static func message(state: String?, outcome: Outcome) -> [String: Any] {
        var m: [String: Any] = ["type": "homeworkLoginResult"]
        switch outcome {
        case .ticket(let t):
            m["state"] = state ?? ""
            m["ticket"] = t
        case .cancelled:
            m["cancelled"] = true
        case .failed(let text):
            if let state { m["state"] = state }
            m["error"] = text
        }
        return m
    }

    private func isFinishPrefix(_ url: URL) -> Bool {
        guard let a = URLComponents(url: url, resolvingAgainstBaseURL: false),
              let f = URLComponents(url: finishURL, resolvingAgainstBaseURL: false),
              a.scheme?.lowercased() == f.scheme?.lowercased(),
              a.host?.lowercased() == f.host?.lowercased(), a.port == f.port,
              a.user == nil, a.password == nil else { return false }
        let path = a.percentEncodedPath, base = f.percentEncodedPath
        guard path.hasPrefix(base) else { return false }
        return base.hasSuffix("/") || path.count == base.count || path.dropFirst(base.count).first == "/"
    }

    private func ticket(in url: URL) -> String? {
        guard let raw = URLComponents(url: url, resolvingAgainstBaseURL: false)?.percentEncodedFragment else { return nil }
        var found: [String: String] = [:]
        for pair in raw.split(separator: "&", omittingEmptySubsequences: true) {
            let kv = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            guard kv.count == 2, let key = kv[0].removingPercentEncoding,
                  let value = kv[1].removingPercentEncoding else { continue }
            if found[key] != nil, key == "ticket" || key == "state" { return nil }
            found[key] = value
        }
        guard let s = found["state"], s == state, let t = found["ticket"], Self.isToken(t) else { return nil }
        return t
    }
}
