import Foundation

/// What the invigilator types into the visible "Invigilator exit" control: the per-exam
/// 6-digit code. Only the shape is checked here; the server decides if it is right.
public enum ExitCodeInput {

    public static let length = 6

    /// The code without surrounding blanks, or nil unless it is exactly 6 ASCII digits.
    public static func normalized(_ typed: String) -> String? {
        let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.utf8.count == length, t.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) else { return nil }
        return t
    }

    /// The 429 body carries the seconds left as "retry_after", either at the top or under
    /// "detail" (FastAPI); the header is not exposed by the app's network layer.
    public static func retryAfterSeconds(json: [String: Any]?) -> Int? {
        func seconds(_ v: Any?) -> Int? {
            if let n = v as? Int { return n }
            if let d = v as? Double, d.isFinite { return Int(d.rounded(.up)) }
            if let s = v as? String { return Int(s.trimmingCharacters(in: .whitespaces)) }
            return nil
        }
        let detail = json?["detail"] as? [String: Any]
        guard let s = seconds(json?["retry_after"]) ?? seconds(detail?["retry_after"]), s > 0 else { return nil }
        return min(s, 3600)
    }

    /// "5 min", "1 min 30 s", "45 s".
    public static func waitText(seconds: Int) -> String {
        let s = max(1, seconds)
        if s < 60 { return "\(s) s" }
        let m = s / 60, r = s % 60
        return r == 0 ? "\(m) min" : "\(m) min \(r) s"
    }
}

/// What the exit sheet shows and what the page is told after a verified invigilator exit.
public enum ExitSheetLogic {

    public static let homePath = "/student"

    /// Only ASCII digits, at most 6 (typing, pasting, hardware keyboard).
    public static func digitsOnly(_ typed: String) -> String {
        String(String.UnicodeScalarView(typed.unicodeScalars.filter { $0.value >= 48 && $0.value <= 57 }.prefix(ExitCodeInput.length)))
    }

    public static func canSubmit(_ typed: String, busy: Bool, waitSeconds: Int) -> Bool {
        !busy && waitSeconds <= 0 && typed.utf8.count == ExitCodeInput.length && ExitCodeInput.normalized(typed) != nil
    }

    public struct Message: Equatable {
        public let text: String
        public let offersOffline: Bool
        public init(text: String, offersOffline: Bool) { self.text = text; self.offersOffline = offersOffline }
    }

    /// The red line under the field; nil for `.ok`.
    public static func message(for result: Signing.ExitResult, waitSeconds: Int) -> Message? {
        switch result {
        case .ok: return nil
        case .wrong: return Message(text: "Wrong code. Check it and try again.", offersOffline: false)
        case .tooMany: return Message(text: "Too many wrong codes. Wait \(ExitCodeInput.waitText(seconds: waitSeconds)).", offersOffline: false)
        case .unreachable: return Message(text: "The exam server cannot be reached.", offersOffline: true)
        case .noSession: return Message(text: "This exam has not reached the server yet.", offersOffline: true)
        case .serverError(let s): return Message(text: "The exam server answered with an error (\(s)).", offersOffline: true)
        }
    }

    /// Bridge message to the page after a verified exit.
    public static func exitedMessage(attemptId: String?) -> [String: Any] {
        var m: [String: Any] = ["type": "exited"]
        if let attemptId { m["attempt_id"] = attemptId }
        return m
    }

    /// One second after the page was told: go to the student home unless the page already
    /// moved on (a different document) or is not the exam server's page at all.
    public static func needsHomeFallback(urlAtExit: String?, urlNow: String?, nowIsServerPage: Bool) -> Bool {
        if !nowIsServerPage { return true }
        guard let urlNow else { return true }
        return urlNow == urlAtExit
    }
}
