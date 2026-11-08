import Foundation

/// Last-resort unlock after the exam is over. The deadline is converted to "seconds left at
/// acceptance" with the signed server time (`expires`), then counted on the continuous clock
/// (it runs through sleep), so neither the Mac's wall clock nor sleep can start or stop it.
/// It lives in memory only: a stored copy a student could edit would be an early unlock.
/// Only a verified signed context ever reaches `accept`; an offline or unverifiable reply cannot
/// release early, but a permanently unreachable OJ still releases after `hardBound`.
struct DeadlineFailsafe: Equatable {
    static let grace: TimeInterval = 20 * 60
    /// Continuous failure to hear a verified answer after the deadline plus grace: release anyway.
    static let hardBound: TimeInterval = 2 * 3600
    static let probeRetry: TimeInterval = 60
    /// A new deadline within this of the stored one is the same deadline (latency), not an extension.
    static let sameDeadlineTolerance: TimeInterval = 30

    enum Action: Equatable { case wait, probe, release }

    private(set) var boot: String?
    private(set) var fireAt: TimeInterval?
    private var probing = false
    private var verifiedWhileProbing = false

    var isArmed: Bool { fireAt != nil }

    /// Only an active, not-held context with a deadline arms it; every accepted context replaces the last one.
    mutating func accept(_ context: NativeDeviceContext, at mono: NativeExit.Monotonic) {
        // "finishing" (answers still being received) stays armed: a student who handed in and then lost the OJ must
        // still be released. Without a deadline it keeps the last one; anywhere else a missing deadline means off.
        guard context.state == "active" || context.state == "finishing", context.hold != true else { clear(); return }
        guard let deadline = context.deadline else {
            if context.state != "finishing" { clear() }
            return
        }
        let next = mono.seconds + Double(deadline - context.expires) + Self.grace
        if boot == mono.boot, let current = fireAt, abs(next - current) <= Self.sameDeadlineTolerance {
            if probing { verifiedWhileProbing = true }
            return
        }
        boot = mono.boot
        fireAt = next
        probing = false; verifiedWhileProbing = false
    }

    mutating func clear() { boot = nil; fireAt = nil; probing = false; verifiedWhileProbing = false }

    func secondsUntilDue(at mono: NativeExit.Monotonic) -> TimeInterval? {
        guard let fireAt, boot == mono.boot else { return nil }
        return fireAt - mono.seconds
    }

    func isDue(at mono: NativeExit.Monotonic) -> Bool {
        secondsUntilDue(at: mono).map { $0 <= 0 } ?? false
    }

    /// Not due: wait. Due: ask the OJ (again every probeRetry); release on a verified answer that did not
    /// move the deadline, or when no verified answer has come for hardBound.
    mutating func decide(at mono: NativeExit.Monotonic) -> Action {
        guard let left = secondsUntilDue(at: mono), left <= 0 else { return .wait }
        if probing && verifiedWhileProbing { return .release }
        if -left >= Self.hardBound { return .release }
        probing = true
        return .probe
    }
}
