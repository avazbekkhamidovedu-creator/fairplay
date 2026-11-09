import Foundation

enum DownloadPolicy {
    static let maxBytes: Int64 = 100 * 1024 * 1024
    static let maxNameLength = 120
    static let folderName = "FairPlay Exam Files"
    static let riskyExtensions: Set<String> = [
        "app", "dmg", "pkg", "command", "sh", "scpt", "workflow", "jar", "exe", "bat", "ps1", "js", "terminal", "webloc", "iso"
    ]

    enum Verdict: Equatable {
        case save(name: String)
        case refuse(reason: String)
    }

    static func originAllowed(_ url: URL?, trusted: Set<String>) -> Bool {
        guard let url, let origin = ProtectedBrowserPolicy.origin(url) else { return false }
        return trusted.contains(origin)
    }

    static func sanitize(_ suggested: String?) -> String {
        var name = (suggested ?? "").replacingOccurrences(of: "\\", with: "/")
        name = name.split(separator: "/", omittingEmptySubsequences: true).last.map(String.init) ?? ""
        name = String(name.unicodeScalars.filter { $0.value >= 32 && $0.value != 127 && $0 != ":" })
        name = name.trimmingCharacters(in: .whitespaces)
        while name.hasPrefix(".") { name.removeFirst() }
        name = name.trimmingCharacters(in: .whitespaces)
        if name.isEmpty { name = "download" }
        if name.count > maxNameLength {
            let ext = (name as NSString).pathExtension
            let keep = ext.isEmpty || ext.count > 16 ? "" : "." + ext
            name = String(name.dropLast(name.count - maxNameLength + keep.count).prefix(maxNameLength - keep.count)) + keep
            if name.count > maxNameLength { name = String(name.prefix(maxNameLength)) }
        }
        return name
    }

    static func isRisky(_ name: String) -> Bool {
        name.lowercased().split(separator: ".").dropFirst().contains { riskyExtensions.contains(String($0)) }
    }

    static func uniqueName(_ name: String, exists: (String) -> Bool) -> String {
        if !exists(name) { return name }
        let ext = (name as NSString).pathExtension
        let stem = ext.isEmpty ? name : String(name.dropLast(ext.count + 1))
        var n = 2
        while true {
            let candidate = ext.isEmpty ? "\(stem) (\(n))" : "\(stem) (\(n)).\(ext)"
            if !exists(candidate) { return candidate }
            n += 1
        }
    }

    static func decide(suggestedName: String?, expectedBytes: Int64) -> Verdict {
        let name = sanitize(suggestedName)
        if isRisky(name) { return .refuse(reason: "\(name) was not saved: this file type is not allowed.") }
        if expectedBytes > maxBytes { return .refuse(reason: "\(name) was not saved: it is larger than \(maxBytes / 1_048_576) MB.") }
        return .save(name: name)
    }

    static func statusLine(saved name: String) -> String { "Saved \(name) to \(folderName)" }
}
