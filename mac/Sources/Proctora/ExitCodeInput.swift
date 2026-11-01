import Foundation

/// What the invigilator types into the visible "Invigilator exit" control: the per-exam
/// 6-digit code. Only the shape is checked here; the server decides if it is right.
enum ExitCodeInput {

    static let length = 6

    /// The code without surrounding blanks, or nil unless it is exactly 6 ASCII digits.
    static func normalized(_ typed: String) -> String? {
        let t = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.utf8.count == length, t.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) else { return nil }
        return t
    }

    /// The 429 body carries the seconds left as "retry_after", either at the top or under
    /// "detail" (FastAPI); the header is not exposed by the app's network layer.
    static func retryAfterSeconds(json: [String: Any]?) -> Int? {
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
    static func waitText(seconds: Int) -> String {
        let s = max(1, seconds)
        if s < 60 { return "\(s) s" }
        let m = s / 60, r = s % 60
        return r == 0 ? "\(m) min" : "\(m) min \(r) s"
    }
}
