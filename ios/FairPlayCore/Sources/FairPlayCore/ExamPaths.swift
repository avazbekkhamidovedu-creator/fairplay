import Foundation

/// Which pages the student app may show (same rules as the Mac's MainWindowController).
public enum ExamPaths {

    /// Same scheme, host (any case) and port as the exam server (default ports filled in).
    public static func isServerURL(_ url: URL?, server: URL) -> Bool {
        guard let url, let host = url.host else { return false }
        func port(_ u: URL) -> Int { u.port ?? (u.scheme?.lowercased() == "https" ? 443 : 80) }
        return host.lowercased() == (server.host ?? "").lowercased() && port(url) == port(server)
            && url.scheme?.lowercased() == server.scheme?.lowercased()
    }

    /// The student app never opens teacher pages or the API docs (audit M-15).
    public static func isTeacherPath(_ path: String) -> Bool {
        let p = path.lowercased()
        return ["/teacher", "/docs", "/redoc", "/openapi.json"].contains { p == $0 || p.hasPrefix($0 + "/") }
    }

    /// Pages a locked iPad may show: the locked attempt's exam / website-exam / done pages
    /// (any attempt's while the attempt is not known yet, i.e. an offline start).
    /// 2026-10-23 (review): the pre-exam lock (no attempt) shows exactly the pages
    /// PreLockState.allowedWhilePreLocked allows (`examId`: the exam it was taken for).
    public static func allowedWhileLocked(path: String, attemptId: String?, preLock: Bool = false,
                                          examId: String? = nil) -> Bool {
        if preLock && attemptId == nil { return PreLockState.allowedWhilePreLocked(path: path, examId: examId) }
        let p = path.lowercased()
        let roots = ["/student/exam/", "/student/external/", "/student/external-done/", "/student/done/"]
        guard let aid = attemptId?.lowercased() else { return roots.contains { p.hasPrefix($0) } }
        let offlineOK = ["/student/exam/offline:", "/student/exam/offline%3a",
                         "/student/done/offline:", "/student/done/offline%3a"].contains { p.hasPrefix($0) }
        return offlineOK || roots.contains { p == $0 + aid || p.hasPrefix($0 + aid + "/") }
    }

    /// `window.__fairplayNonce` for documents whose origin is the server's (audit W-10).
    public static func nonceScript(nonce: String, server: URL) -> String {
        let scheme = (server.scheme ?? "https").lowercased()
        let host = (server.host ?? "").lowercased()
        let defaultPort = scheme == "https" ? 443 : 80
        let port = server.port.map { $0 == defaultPort ? "" : ":\($0)" } ?? ""
        let origin = "\(scheme)://\(host)\(port)".filter { $0.isLetter || $0.isNumber || ":/.-[]".contains($0) }
        let safeNonce = nonce.filter { $0.isLetter || $0.isNumber || $0 == "-" }
        return "if (location.origin === '\(origin)') { window.__fairplayNonce = '\(safeNonce)'; }"
    }

    /// Teacher-allowed app ids usable on iPad as Assessment Mode participants: well-formed
    /// bundle ids, never a browser, at most 40. (Windows process names have no dot.)
    public static func allowedParticipants(_ raw: Any?) -> [String] {
        let never = ["com.apple.mobilesafari", "com.apple.safari", "com.google.chrome", "org.mozilla",
                     "com.microsoft.msedge", "com.brave", "com.opera", "com.duckduckgo", "com.apple.shortcuts",
                     "com.apple.mobileslideshow", "com.apple.mobilenotes", "com.apple.mobilesms",
                     // review L7: offline places to keep notes or files
                     "com.apple.documentsapp", "com.apple.ibooks", "com.apple.freeform", "com.apple.voicememos",
                     "com.apple.pages", "com.apple.keynote", "com.apple.numbers", "com.apple.mobilemail",
                     "com.apple.reminders", "com.apple.camera", "com.apple.facetime", "com.apple.translate"]
        var out: [String] = []
        for id in ((raw as? [Any]) ?? []).compactMap({ $0 as? String }).prefix(40) {
            let lower = id.lowercased()
            guard (3...200).contains(lower.count), lower.contains("."), !lower.hasPrefix("."), !lower.hasSuffix("."),
                  lower.unicodeScalars.allSatisfy({ ($0.value >= 48 && $0.value <= 57) || ($0.value >= 97 && $0.value <= 122)
                      || $0 == "." || $0 == "-" || $0 == "_" }),
                  !never.contains(where: { lower == $0 || lower.hasPrefix($0 + ".") }),
                  !out.contains(id) else { continue }
            out.append(id)
        }
        return out
    }
}
