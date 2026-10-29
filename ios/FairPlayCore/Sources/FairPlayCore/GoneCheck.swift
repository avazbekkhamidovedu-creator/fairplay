import Foundation

/// A beat answered 409: the session was rotated away. The signed hand-in check decides, at most once
/// every `minInterval` seconds.
public struct GoneCheck {
    public static let sessionGone = "session_gone"
    public let minInterval: TimeInterval
    private var last = Date.distantPast

    public init(minInterval: TimeInterval = 10) { self.minInterval = minInterval }

    public mutating func shouldCheck(now: Date = Date()) -> Bool {
        guard now.timeIntervalSince(last) > minInterval else { return false }
        last = now
        return true
    }
}
