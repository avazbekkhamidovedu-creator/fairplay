import Foundation

/// The same event repeating is sent once per `repeatWindow`, and at most `maxPerType` of
/// one type per run, so the server's per-attempt cap can't be used up to bury real
/// evidence (Mac review 2026-10-14). Lock / exit events always go out.
public struct EventThrottle {
    public static let repeatWindow: TimeInterval = 10
    public static let maxPerType = 300
    public static let throttled: Set<String> = [
        "other_app_foreground", "blocked_shortcut", "blocked_navigation", "process_blocked",
        "app_backgrounded", "screenshot_taken", "screen_captured", "multitasking", "keyboard_connected",
        "focus_lost",
    ]

    private var lastSent: [String: Date] = [:]
    private var perType: [String: Int] = [:]
    private var capped: Set<String> = []

    public init() {}

    /// What to send for this event: nothing, the event, or a one-time "events_capped".
    public mutating func decide(type: String, detail: String, at now: Date) -> [(String, String)] {
        guard EventThrottle.throttled.contains(type) else { return [(type, detail)] }
        let key = type + "|" + detail
        if let t = lastSent[key], now.timeIntervalSince(t) < EventThrottle.repeatWindow { return [] }
        lastSent[key] = now
        if lastSent.count > 2000 { lastSent.removeAll() }
        let n = (perType[type] ?? 0) + 1
        perType[type] = n
        if n <= EventThrottle.maxPerType { return [(type, detail)] }
        guard !capped.contains(type) else { return [] }
        capped.insert(type)
        return [("events_capped", "more than \(EventThrottle.maxPerType) \(type) events; later ones only in the local log")]
    }
}
