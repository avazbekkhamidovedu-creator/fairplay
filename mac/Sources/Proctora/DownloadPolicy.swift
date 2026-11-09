import Foundation
import Darwin

enum DownloadPolicy {
    static let maxBytes: Int64 = 1024 * 1024 * 1024
    static let maxNameLength = 120
    static let folderName = "FairPlay Exam Files"
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
        if expectedBytes > maxBytes { return .refuse(reason: "\(name) was not saved: it is larger than \(maxBytes / 1_048_576) MB.") }
        return .save(name: name)
    }

    static func statusLine(saved name: String) -> String { "Saved \(name) to \(folderName)" }

    enum FolderError: Error, Equatable { case notUnderHome, symlink, notDirectory, notOwned, createFailed }

    /// The exam-files folder below `home`: every component under the home directory must be a real
    /// directory owned by this user. A symlink anywhere on the path refuses the download.
    static func safeFolder(home: URL, relative: [String]) throws -> URL {
        var current = home.standardizedFileURL.path
        var st = stat()
        guard lstat(current, &st) == 0, st.st_mode & S_IFMT == S_IFDIR else { throw FolderError.notDirectory }
        for part in relative {
            guard !part.isEmpty, part != ".", part != "..", !part.contains("/") else { throw FolderError.notUnderHome }
            current += "/" + part
            if lstat(current, &st) != 0 {
                guard errno == ENOENT, mkdir(current, 0o700) == 0 else { throw FolderError.createFailed }
                guard lstat(current, &st) == 0 else { throw FolderError.createFailed }
            }
            guard st.st_mode & S_IFMT != S_IFLNK else { throw FolderError.symlink }
            guard st.st_mode & S_IFMT == S_IFDIR else { throw FolderError.notDirectory }
            guard st.st_uid == getuid() else { throw FolderError.notOwned }
        }
        return URL(fileURLWithPath: current, isDirectory: true)
    }

    /// Reserves a new name without following links. WebKit needs the path free, so the placeholder is
    /// removed straight after the exclusive create proved nothing was there.
    static func reserve(_ name: String, in folder: URL) -> Bool {
        let path = folder.appendingPathComponent(name).path
        let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { return false }
        close(fd)
        return unlink(path) == 0
    }

    /// Marks a saved file as downloaded so Gatekeeper treats it as untrusted. Never follows a link.
    @discardableResult
    static func quarantine(_ file: URL) -> Bool {
        var st = stat()
        guard lstat(file.path, &st) == 0, st.st_mode & S_IFMT == S_IFREG else { return false }
        let value = "0083;" + String(Int(Date().timeIntervalSince1970), radix: 16) + ";FairPlay;"
        return value.withCString { setxattr(file.path, "com.apple.quarantine", $0, strlen($0), 0, XATTR_NOFOLLOW) } == 0
    }
}
