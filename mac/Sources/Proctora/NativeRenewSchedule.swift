import Foundation

/// When to ask the OJ again. Pure: no timers, the clock and the random source are injected.
/// Only timing lives here; what is sent and how an answer is verified is unchanged.
struct NativeRenewSchedule {
    static let renewInterval: TimeInterval = 20
    static let holdPollInterval: TimeInterval = 10
    static let jitterFraction = 0.2
    static let backoffSteps: [TimeInterval] = [2, 4, 8, 16]
    static let backoffCap: TimeInterval = 30
    static let retryAfterCap: TimeInterval = 30
    static let completionFollowUps: [TimeInterval] = [2, 5]
    /// How long a verified answer keeps the exam's approved sites; matches the OJ lease, not the 30 s receipt.
    static let policyWindow: TimeInterval = 90
    static let completionTitle = "Exam submitted"

    enum Outcome: Equatable {
        case success
        /// Timeout (nil status), 429 or 5xx; `retryAfter` is the parsed Retry-After seconds.
        case overloaded(retryAfter: TimeInterval?)
        /// Any other refusal: normal pace, the backoff is neither advanced nor reset.
        case other
        /// An unsigned, display-only refusal (instructor, not open, not FairPlay): a fixed slow pace.
        case refused(after: TimeInterval)
    }

    enum Pace { case renew, hold }

    /// A late renew must not flicker the allowed sites; an OJ silent for the whole window still removes them.
    static func policyExpiry(signedExpires: TimeInterval, receivedAt: Date) -> Date {
        max(Date(timeIntervalSince1970: signedExpires), receivedAt.addingTimeInterval(policyWindow))
    }

    var random: () -> Double
    private(set) var failures = 0

    init(random: @escaping () -> Double = { Double.random(in: 0..<1) }) { self.random = random }

    static func isOverloaded(status: Int?, transportFailed: Bool) -> Bool {
        guard !transportFailed, let status else { return true }
        return status == 429 || (500...599).contains(status)
    }

    /// Seconds form only; a missing, negative or unreadable value is nil.
    static func retryAfter(_ header: String?) -> TimeInterval? {
        guard let header, let seconds = Double(header.trimmingCharacters(in: .whitespaces)), seconds.isFinite, seconds >= 0 else { return nil }
        return seconds
    }

    /// Steps 2, 4, 8, 16 s, then the cap for every later failure.
    static func step(forFailure count: Int) -> TimeInterval {
        count <= backoffSteps.count ? min(backoffSteps[max(count, 1) - 1], backoffCap) : backoffCap
    }

    /// Half the step is always waited, so a fleet-wide outage cannot bring retries back near zero.
    static func equalJitter(step: TimeInterval, random: Double) -> TimeInterval {
        step / 2 + min(max(random, 0), 1) * step / 2
    }

    func jittered(_ base: TimeInterval) -> TimeInterval {
        base * (1 + Self.jitterFraction * (2 * min(max(random(), 0), 1) - 1))
    }

    func interval(_ pace: Pace) -> TimeInterval {
        jittered(pace == .renew ? Self.renewInterval : Self.holdPollInterval)
    }

    /// Records the answer and returns the wait before the next request.
    mutating func next(after outcome: Outcome, pace: Pace) -> TimeInterval {
        switch outcome {
        case .success:
            failures = 0
            return interval(pace)
        case .other:
            return interval(pace)
        case .refused(let after):
            return jittered(after)
        case .overloaded(let retryAfter):
            failures += 1
            let wait = Self.equalJitter(step: Self.step(forFailure: failures), random: random())
            guard let retryAfter else { return wait }
            return max(wait, min(retryAfter, Self.retryAfterCap))
        }
    }

    mutating func reset() { failures = 0 }

    /// The OJ hand-in answer is the page for the contest's leave POST; the title is a second signal.
    /// Either only asks the OJ sooner. It never unlocks anything.
    static func looksLikeHandIn(url: URL?, title: String?, contest: String?) -> Bool {
        if let title, title.hasPrefix(completionTitle) { return true }
        guard let url, let contest, !contest.isEmpty else { return false }
        let path = url.path.hasSuffix("/") ? String(url.path.dropLast()) : url.path
        return path == "/contest/" + contest + "/leave"
    }
}

/// Keeps a fleet-wide outage from filling the log: the first failure, then one counted line per window,
/// then one line when requests work again. Only status numbers are ever passed in.
struct RepeatedFailureLog {
    static let window: TimeInterval = 60
    private var current: String?
    private var count = 0
    private var lastLine = Date.distantPast

    mutating func failure(_ summary: String, now: Date) -> String? {
        if current != summary {
            current = summary
            count = 1
            lastLine = now
            return summary
        }
        count += 1
        guard now.timeIntervalSince(lastLine) >= Self.window else { return nil }
        lastLine = now
        return "\(summary) (\(count) times so far)"
    }

    mutating func success() -> String? {
        guard current != nil else { return nil }
        defer { current = nil; count = 0 }
        return "native-request-recovered after \(count) failures"
    }
}
