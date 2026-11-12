import Foundation

/// When to ask the OJ again. Pure: no timers, the clock and the random source are injected.
/// Only timing lives here; what is sent and how an answer is verified is unchanged.
struct NativeRenewSchedule {
    static let renewInterval: TimeInterval = 20
    static let holdPollInterval: TimeInterval = 10
    /// The light signed-state read (GET) sent between renews, so an instructor release shows up in seconds.
    /// 300 students: about 60 requests/s in all (15 renews + 45 light reads), against 15 with renews alone.
    static let watchInterval: TimeInterval = 5
    /// While a hand-in is being verified.
    static let handInPollInterval: TimeInterval = 2
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

    enum Pace { case renew, hold, watch, handIn }

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

    /// Delta-seconds or an HTTP-date (IMF-fixdate, RFC 850, asctime) turned into seconds from `now`;
    /// a missing or unreadable value is nil, a date already past is 0.
    static func retryAfter(_ header: String?, now: Date = Date()) -> TimeInterval? {
        guard let header else { return nil }
        let text = header.trimmingCharacters(in: .whitespaces)
        if !text.isEmpty, text.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 || $0 == 46 }), let seconds = Double(text), seconds.isFinite {
            return seconds
        }
        for format in httpDateFormats {
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.timeZone = TimeZone(identifier: "GMT")
            f.dateFormat = format
            if let date = f.date(from: text) { return max(0, date.timeIntervalSince(now)) }
        }
        return nil
    }

    private static let httpDateFormats = ["EEE, dd MMM yyyy HH:mm:ss 'GMT'", "EEEE, dd-MMM-yy HH:mm:ss 'GMT'", "EEE MMM d HH:mm:ss yyyy"]

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
        switch pace {
        case .renew: return jittered(Self.renewInterval)
        case .hold: return jittered(Self.holdPollInterval)
        case .watch: return jittered(Self.watchInterval)
        case .handIn: return jittered(Self.handInPollInterval)
        }
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

    /// The OJ hand-in answer is the page for the contest's leave POST; the title is only a second signal on that URL.
    /// Either only asks the OJ sooner. It never unlocks anything.
    static func looksLikeHandIn(url: URL?, title: String?, contest: String?) -> Bool {
        HandInVerification.isHandInPage(url: url, origin: nil, contest: contest)
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
