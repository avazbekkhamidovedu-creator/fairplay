import Foundation

/// A relaunch with a saved native lock while the keyboard tap cannot start shows a full-screen block and no
/// browser. This keeps asking the OJ for the signed state meanwhile, so an instructor release (or a completed
/// exam) and the deadline failsafe still unlock the Mac. Same verification and same rules as the browser's loop:
/// only a verified signed state that matches the saved binding releases, and the failsafe is DeadlineFailsafe.
final class NativeRecoveryRelease {
    struct Deps {
        /// Sends one signed-state request; false when nothing could be sent. `done` gets the verified context or nil.
        var request: (_ path: String, _ done: @escaping (NativeDeviceContext?) -> Void) -> Bool
        var schedule: (TimeInterval, @escaping () -> Void) -> () -> Void
        var monotonic: () -> NativeExit.Monotonic
        var release: () -> Void
        var random: () -> Double = { Double.random(in: 0..<1) }
    }

    private let deps: Deps
    private var exam: NativeExamSession
    private var failsafe = DeadlineFailsafe()
    private var pace: NativeRenewSchedule
    private var cancel: (() -> Void)?
    private var inFlight = false
    private var generation = 0
    private(set) var isRunning = false

    init(state: NativeLockState, deps: Deps) {
        self.deps = deps
        exam = NativeExamSession(restoring: state.contest, binding: state.binding)
        pace = NativeRenewSchedule(random: deps.random)
    }

    func start() {
        guard !isRunning else { return }
        isRunning = true
        step()
    }

    func stop() {
        isRunning = false
        generation += 1
        inFlight = false
        cancel?(); cancel = nil
    }

    private var path: String? {
        if let session = exam.binding?.session { return "session/" + session + "/renew" }
        return exam.contest.map { "recover/" + $0 }
    }

    private func step() {
        cancel = nil
        guard isRunning, !inFlight else { return }
        if failsafe.decide(at: deps.monotonic()) == .release { finish(); return }
        guard let path else { return }
        inFlight = true
        let mine = generation
        let sent = deps.request(path) { [weak self] context in
            guard let self, self.isRunning, mine == self.generation else { return }
            self.inFlight = false
            guard let context, self.exam.accept(context) else { self.next(.other); return }
            if self.exam.permitsUnlock { self.finish(); return }
            self.failsafe.accept(context, at: self.deps.monotonic())
            self.next(.success)
        }
        if !sent { inFlight = false; next(.other) }
    }

    private func next(_ outcome: NativeRenewSchedule.Outcome) {
        var delay = pace.next(after: outcome, pace: .renew)
        if let left = failsafe.secondsUntilDue(at: deps.monotonic()) { delay = min(delay, max(left, 0) + 1) }
        cancel?()
        cancel = deps.schedule(delay) { [weak self] in self?.step() }
    }

    private func finish() {
        stop()
        deps.release()
    }
}
