import Foundation

/// 2026-10-23: the heavy web files of the exam page (the Python engine, pdf.js worker,
/// character maps and fonts) ship inside the app as the folder `app-assets` (made by the
/// web build into server/static/app-assets, copied into the bundle at build time). The exam
/// web view serves them as fairplay-asset://bundle/<path>, so starting an exam never waits
/// for a 12 MB download. This is the pure part: URL -> file, file types, headers, the
/// manifest version and the script that tells the exam page where the files are.
public enum AssetPack {
    public static let scheme = "fairplay-asset"
    public static let host = "bundle"
    public static let folderName = "app-assets"
    public static let manifestName = "manifest.json"
    public static let baseURL = "fairplay-asset://bundle/"

    /// Longest relative path served (the pack's longest is well under this).
    static let maxPathLength = 512

    // MARK: - URL -> relative path

    /// "pyodide/pyodide.mjs" for fairplay-asset://bundle/pyodide/pyodide.mjs (query and
    /// fragment ignored). nil when the URL is not ours or the path is not a plain relative
    /// path inside the pack.
    public static func relativePath(for url: URL) -> String? {
        guard url.scheme?.lowercased() == scheme,
              let comps = URLComponents(url: url, resolvingAgainstBaseURL: false),
              comps.host?.lowercased() == host, comps.port == nil,
              comps.user == nil, comps.password == nil,
              let decoded = comps.percentEncodedPath.removingPercentEncoding else { return nil }
        return safeRelativePath(decoded)
    }

    /// The path when every part is a plain name: no "..", ".", hidden names (".x"), empty
    /// parts ("a//b"), backslashes, control characters or NUL. One leading "/" is allowed
    /// (URL paths have it).
    public static func safeRelativePath(_ raw: String) -> String? {
        var path = Substring(raw)
        if path.hasPrefix("/") { path = path.dropFirst() }
        guard !path.isEmpty, path.utf8.count <= maxPathLength, !path.hasPrefix("/"),
              !path.contains("\\"), !path.contains("\0"),
              // a "%" left after decoding is a broken escape ("%ZZ"); no pack file has one
              !path.contains("%"),
              !path.unicodeScalars.contains(where: { $0.value < 0x20 || $0.value == 0x7f }) else { return nil }
        let parts = path.split(separator: "/", omittingEmptySubsequences: false)
        for part in parts {
            guard !part.isEmpty, !part.hasPrefix("."), part != "~" else { return nil }
        }
        return String(path)
    }

    // MARK: - relative path -> file

    /// The regular file `relative` names inside `root`, after resolving symlinks: nil when
    /// it is missing, a folder, or (through a symlink) outside `root`.
    public static func fileURL(forRelativePath relative: String, root: URL) -> URL? {
        guard let safe = safeRelativePath(relative) else { return nil }
        let realRoot = root.standardizedFileURL.resolvingSymlinksInPath()
        var candidate = realRoot
        for part in safe.split(separator: "/") { candidate.appendPathComponent(String(part)) }
        let real = candidate.resolvingSymlinksInPath()
        let rootPath = realRoot.path.hasSuffix("/") ? realRoot.path : realRoot.path + "/"
        guard real.path.hasPrefix(rootPath) else { return nil }
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: real.path, isDirectory: &isDir), !isDir.boolValue else { return nil }
        return real
    }

    // MARK: - What to answer

    public enum Reply: Equatable {
        /// 200 with the file (HEAD: headers only).
        case file(URL, mime: String, bodyless: Bool)
        /// 204 to a CORS preflight.
        case preflight
        case notFound
        case methodNotAllowed
    }

    /// The answer to `method url` with the pack at `root` (nil: no pack in this build).
    public static func reply(method: String?, url: URL?, root: URL?) -> Reply {
        let m = (method ?? "GET").uppercased()
        if m == "OPTIONS" { return .preflight }
        guard m == "GET" || m == "HEAD" else { return .methodNotAllowed }
        guard let url, let root, let rel = relativePath(for: url),
              let file = fileURL(forRelativePath: rel, root: root) else { return .notFound }
        return .file(file, mime: mimeType(forPath: rel), bodyless: m == "HEAD")
    }

    // MARK: - Types and headers

    public static func mimeType(forPath path: String) -> String {
        let name = path.split(separator: "/").last.map(String.init) ?? path
        guard let dot = name.lastIndex(of: "."), dot != name.startIndex else { return "application/octet-stream" }
        switch name[name.index(after: dot)...].lowercased() {
        case "js", "mjs": return "text/javascript"
        case "wasm": return "application/wasm"
        case "json": return "application/json"
        case "zip": return "application/zip"
        case "bcmap", "pfb": return "application/octet-stream"
        case "ttf": return "font/ttf"
        case "otf": return "font/otf"
        case "txt": return "text/plain; charset=utf-8"
        case "css": return "text/css"
        default: return "application/octet-stream"
        }
    }

    /// Headers of a 200: the files are versioned with the app, so cached for good; any
    /// origin may read them (the exam page's origin is the server's, not this scheme).
    public static func headers(mime: String, length: Int) -> [String: String] {
        ["Content-Type": mime,
         "Content-Length": String(max(0, length)),
         "Access-Control-Allow-Origin": "*",
         "Cache-Control": "max-age=31536000, immutable",
         "X-Content-Type-Options": "nosniff"]
    }

    public static let notFoundHeaders: [String: String] = [
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Length": "0",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-store"]

    public static let preflightHeaders: [String: String] = [
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
        "Content-Length": "0"]

    // MARK: - Manifest

    /// manifest.json {"version": "...", "files": {...}}: the version, when it is a
    /// non-empty string of at most 128 characters.
    public static func manifestVersion(_ data: Data) -> String? {
        guard let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any], let v = dict["version"] as? String else { return nil }
        let trimmed = v.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 128 else { return nil }
        return trimmed
    }

    // MARK: - Page script

    /// `window.fairplayAssets = {base, version}` for documents whose origin is the exam
    /// server's (the same check as the bridge nonce). nil without a version (no pack).
    public static func injectionScript(version: String?, server: URL) -> String? {
        guard let version, !version.isEmpty else { return nil }
        let origin = serverOrigin(server)
        return "if (location.origin === \(jsString(origin))) { window.fairplayAssets = Object.freeze({ base: "
            + "\(jsString(baseURL)), version: \(jsString(version)) }); }"
    }

    /// "https://host[:port]" (default port left out), lower-case, like location.origin.
    static func serverOrigin(_ server: URL) -> String {
        let scheme = (server.scheme ?? "https").lowercased()
        let host = (server.host ?? "").lowercased()
        let defaultPort = scheme == "https" ? 443 : 80
        let port = server.port.map { $0 == defaultPort ? "" : ":\($0)" } ?? ""
        return "\(scheme)://\(host)\(port)"
    }

    /// A double-quoted JavaScript string literal: letters, digits and " ._+-:/" as they are,
    /// every other UTF-16 unit as \uXXXX (so no quote, backslash, "<", line break or
    /// U+2028 can end the string or the script).
    public static func jsString(_ s: String) -> String {
        var out = "\""
        for unit in s.utf16 {
            let plain = (unit >= 0x30 && unit <= 0x39) || (unit >= 0x41 && unit <= 0x5a) || (unit >= 0x61 && unit <= 0x7a)
                || [0x20, 0x2e, 0x5f, 0x2b, 0x2d, 0x3a, 0x2f].contains(unit)
            if plain, let scalar = Unicode.Scalar(unit) {
                out.unicodeScalars.append(scalar)
            } else {
                let hex = String(unit, radix: 16)
                out += "\\u" + String(repeating: "0", count: 4 - hex.count) + hex
            }
        }
        return out + "\""
    }
}
