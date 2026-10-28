import Foundation

/// Device test: a short preview of the exam-style block screen. It is not a lock: no apps
/// are closed or hidden, no Watchdog marker is written, no attempt exists. The app's own
/// clock ends it, whatever the page does.
protocol DeviceTestEffects: AnyObject {
    func deviceTestBegin()
    func deviceTestRestore()
    func deviceTestNotify(_ message: [String: Any])
}

final class DeviceTestSession {

    static let defaultSeconds = 60
    static let allowedSeconds = 10...90

    enum Reason: String {
        case leave, timeout, refused, error
    }

    struct Context {
        var examActive: Bool
        var fromExamPage: Bool
        var websiteExam: Bool
    }

    static func clamp(_ raw: Any?) -> Int {
        guard let n = raw as? NSNumber, !(raw is Bool) else { return defaultSeconds }
        let d = n.doubleValue
        guard d.isFinite else { return defaultSeconds }
        return min(allowedSeconds.upperBound, max(allowedSeconds.lowerBound, Int(d.rounded(.down))))
    }

    private let clock: () -> TimeInterval
    private weak var effects: DeviceTestEffects?
    private var deadline: TimeInterval = 0
    private(set) var isRunning = false

    init(clock: @escaping () -> TimeInterval, effects: DeviceTestEffects) {
        self.clock = clock
        self.effects = effects
    }

    var remaining: Int {
        isRunning ? max(0, Int((deadline - clock()).rounded(.up))) : 0
    }

    @discardableResult
    func start(seconds raw: Any?, context: Context) -> Bool {
        guard !isRunning, !context.examActive, context.fromExamPage, !context.websiteExam else {
            effects?.deviceTestNotify(Self.message(state: "ended", reason: .refused, remaining: 0))
            return false
        }
        let seconds = Self.clamp(raw)
        deadline = clock() + Double(seconds)
        isRunning = true
        effects?.deviceTestBegin()
        effects?.deviceTestNotify(Self.message(state: "started", reason: nil, remaining: seconds))
        return true
    }

    func end(_ reason: Reason) {
        guard isRunning else { return }
        isRunning = false
        effects?.deviceTestRestore()
        effects?.deviceTestNotify(Self.message(state: "ended", reason: reason, remaining: 0))
    }

    func tick() {
        guard isRunning, clock() >= deadline else { return }
        end(.timeout)
    }

    static func message(state: String, reason: Reason?, remaining: Int) -> [String: Any] {
        var m: [String: Any] = ["type": "deviceTest", "state": state, "remaining": remaining]
        if let reason { m["reason"] = reason.rawValue }
        return m
    }
}
