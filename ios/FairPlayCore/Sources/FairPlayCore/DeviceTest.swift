import Foundation

public enum DeviceTestEnd: String, Equatable {
    case leave, timeout, refused, error
}

public struct DeviceTestContext: Equatable {
    public var fromServerPage: Bool
    public var examActive: Bool
    public var websiteMode: Bool

    public init(fromServerPage: Bool, examActive: Bool, websiteMode: Bool) {
        self.fromServerPage = fromServerPage
        self.examActive = examActive
        self.websiteMode = websiteMode
    }
}

/// The exam-feel preview: a hard time limit kept by the app's own clock. Only `begin` and
/// `restore` touch the screen; no lock, no network, no attempt.
public final class DeviceTest {
    public static let minSeconds = 10
    public static let maxSeconds = 90
    public static let defaultSeconds = 60
    public static let backgroundGrace: TimeInterval = 4

    public enum Message: Equatable {
        case started(remaining: Int)
        case ended(DeviceTestEnd, remaining: Int)
    }

    private let clock: () -> Date
    private let begin: () -> Void
    private let restore: () -> Void
    private let emit: (Message) -> Void
    private var deadline: Date?
    private var backgroundedAt: Date?

    public init(clock: @escaping () -> Date = Date.init, begin: @escaping () -> Void,
                restore: @escaping () -> Void, emit: @escaping (Message) -> Void) {
        self.clock = clock
        self.begin = begin
        self.restore = restore
        self.emit = emit
    }

    public var isRunning: Bool { deadline != nil }

    public static func clamp(_ raw: Any?) -> Int {
        let value: Double
        switch raw {
        case let n as NSNumber where CFGetTypeID(n) != CFBooleanGetTypeID(): value = n.doubleValue
        case let d as Double: value = d
        case let i as Int: value = Double(i)
        default: return defaultSeconds
        }
        guard value.isFinite else { return defaultSeconds }
        return Int(min(max(value.rounded(), Double(minSeconds)), Double(maxSeconds)))
    }

    public var remaining: Int {
        guard let deadline else { return 0 }
        return max(0, Int(deadline.timeIntervalSince(clock()).rounded(.up)))
    }

    public func start(seconds raw: Any?, context: DeviceTestContext) {
        guard context.fromServerPage, !context.examActive, !context.websiteMode, deadline == nil else {
            emit(.ended(.refused, remaining: 0))
            return
        }
        let seconds = DeviceTest.clamp(raw)
        deadline = clock().addingTimeInterval(TimeInterval(seconds))
        backgroundedAt = nil
        begin()
        emit(.started(remaining: seconds))
    }

    public func end(_ reason: DeviceTestEnd) {
        guard deadline != nil else { return }
        let left = remaining
        deadline = nil
        backgroundedAt = nil
        restore()
        emit(.ended(reason, remaining: left))
    }

    public func tick() {
        guard let deadline else { return }
        let now = clock()
        if now >= deadline {
            end(.timeout)
        } else if let b = backgroundedAt, now.timeIntervalSince(b) > DeviceTest.backgroundGrace {
            end(.leave)
        }
    }

    public func appBackgrounded() {
        if deadline != nil, backgroundedAt == nil { backgroundedAt = clock() }
    }

    public func appForegrounded() {
        guard deadline != nil, let b = backgroundedAt else { return }
        backgroundedAt = nil
        if clock().timeIntervalSince(b) > DeviceTest.backgroundGrace { end(.leave) }
    }
}
