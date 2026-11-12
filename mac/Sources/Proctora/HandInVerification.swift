import Foundation

/// The app's own view of "did my answers reach DKU OJ?" after a hand-in. Pure: the caller supplies the clock.
/// The Mac only WATCHES (signed state polls); the OJ page's own Leave POST (and its auto re-post) does the hand-in.
/// One rule, shared with the Windows app: progress until a signed completed/released state or one timeout of 60 s;
/// the OJ's FINAL "Hand-in not complete" page shows the failure at once (with Try again); the OJ's
/// "Checking your answers" page is progress, never a failure. Nothing here unlocks anything.
struct HandInVerification {
    enum Phase: Equatable { case idle, checking, failed }
    enum Action: Equatable { case none, failed }

    static let window: TimeInterval = 60
    /// Page signals are ignored for this long after "Back to the exam" (the OJ page re-posts by itself every few seconds).
    static let backQuiet: TimeInterval = 5
    static let completionTitle = "Exam submitted"
    /// The OJ's final failure page. Only this title is a failure.
    static let incompleteTitle = "Hand-in not complete"
    /// The OJ's auto-retrying page: progress, never a failure.
    static let checkingPageTitle = "Checking your answers"

    static let checkingText = "Checking that your answers reached DKU OJ…"
    static let checkingDetail = "This Mac unlocks by itself when DKU OJ confirms."
    static let receivingText = "DKU OJ is receiving your answers…"
    static let receivingDetail = "This Mac unlocks by itself when it is done."
    static let timeoutText = "DKU OJ has not confirmed your hand-in yet."
    static let timeoutDetail = "Your Mac stays locked. Choose Try again, or go back to the exam and press Leave there again."
    static let refusedText = "DKU OJ says the hand-in did not finish."
    static let refusedDetail = "Go back to the exam and press Leave again, or choose Try again. Your Mac stays locked."
    static let tryAgainTitle = "Try again"
    static let backTitle = "Back to the exam"

    private(set) var phase: Phase = .idle
    private(set) var startedAt: TimeInterval = 0
    /// The OJ itself said the hand-in did not finish (as opposed to the app's own timeout).
    private(set) var refused = false
    /// The OJ's auto-retrying page is on screen.
    var onCheckingPage = false
    /// True when the check came from a signed "finishing" state with no page signal.
    var receiving = false

    var text: String {
        switch phase {
        case .idle: return ""
        case .checking: return receiving ? Self.receivingText : Self.checkingText
        case .failed: return refused ? Self.refusedText : Self.timeoutText
        }
    }
    var detail: String {
        switch phase {
        case .idle: return ""
        case .checking: return receiving ? Self.receivingDetail : Self.checkingDetail
        case .failed: return refused ? Self.refusedDetail : Self.timeoutDetail
        }
    }

    /// Starts a check from nothing; a running check keeps its clock.
    @discardableResult
    mutating func begin(at now: TimeInterval) -> Bool {
        guard phase == .idle else { return false }
        restart(at: now)
        return true
    }

    /// Try again, or the OJ's checking page after a failure: a fresh window.
    mutating func restart(at now: TimeInterval) {
        phase = .checking
        startedAt = now
        refused = false
    }

    /// The OJ's final "not complete" page.
    mutating func pageRefused() {
        phase = .failed
        refused = true
    }

    mutating func tick(at now: TimeInterval) -> Action {
        guard phase == .checking, now - startedAt >= Self.window else { return .none }
        phase = .failed
        refused = false
        return .failed
    }

    mutating func reset() { phase = .idle; refused = false; onCheckingPage = false; receiving = false }

    /// Only the OJ's own /contest/<bound contest>/leave answer on the OJ origin; the title is a second signal on that URL.
    static func isHandInPage(url: URL?, origin: URL?, contest: String?) -> Bool {
        guard let url, let contest, !contest.isEmpty else { return false }
        if let origin, ProtectedBrowserPolicy.origin(url) != ProtectedBrowserPolicy.origin(origin) { return false }
        let path = url.path.hasSuffix("/") ? String(url.path.dropLast()) : url.path
        return path == "/contest/" + contest + "/leave"
    }

    static func isIncompletePage(title: String?) -> Bool { title?.hasPrefix(incompleteTitle) == true }
    static func isCheckingPage(title: String?) -> Bool { title?.hasPrefix(checkingPageTitle) == true }

    /// An active answer is a reopen only when the student's deadline moved later; a failed hand-in also rolls back to active.
    static func isReopen(state: String, deadline: Int?, startDeadline: Int?) -> Bool {
        guard state == "active", let deadline, let startDeadline else { return false }
        return deadline > startDeadline
    }

    /// A poll starts a progress check by itself only on ENTRY into finishing, and only from no check at all.
    static func startsOnState(previous: String?, current: String, phase: Phase) -> Bool {
        current == "finishing" && previous != "finishing" && phase == .idle
    }
}
