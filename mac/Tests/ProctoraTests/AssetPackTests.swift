// 2026-10-23: the bundled asset pack (fairplay-asset://bundle/...): URL -> file, path
// traversal, file types, headers, manifest version and the page script.
import Testing
import Foundation
@testable import Proctora

@Suite struct AssetPackTests {

    private func url(_ s: String) -> URL { URL(string: s)! }

    // MARK: URL -> relative path

    @Test func relativePathOfOurURLs() {
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle/pyodide/pyodide.mjs")) == "pyodide/pyodide.mjs")
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle/pdfjs/cmaps/78-EUC-H.bcmap"))
                == "pdfjs/cmaps/78-EUC-H.bcmap")
        // Query and fragment are not part of the file.
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle/pyodide/pyodide.asm.wasm?v=1#x"))
                == "pyodide/pyodide.asm.wasm")
        // Scheme and host in any case.
        #expect(AssetPack.relativePath(for: url("FAIRPLAY-ASSET://Bundle/a.js")) == "a.js")
        // Percent-encoded names are decoded.
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle/pdfjs/a%20b.txt")) == "pdfjs/a b.txt")
        #expect(AssetPack.baseURL == "\(AssetPack.scheme)://\(AssetPack.host)/")
    }

    @Test func otherURLsAreNotOurs() {
        #expect(AssetPack.relativePath(for: url("https://bundle/a.js")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-app://bundle/a.js")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-asset://other/a.js")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle:8080/a.js")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-asset://u:p@bundle/a.js")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle/")) == nil)
        #expect(AssetPack.relativePath(for: url("fairplay-asset://bundle")) == nil)
    }

    @Test func traversalIsRefused() {
        for s in ["fairplay-asset://bundle/../etc/passwd",
                  "fairplay-asset://bundle/pyodide/../../x",
                  "fairplay-asset://bundle/%2e%2e/x",
                  "fairplay-asset://bundle/%2E%2E%2Fx",
                  "fairplay-asset://bundle/pyodide/%2e%2e%2f%2e%2e%2fx",
                  "fairplay-asset://bundle/./a.js",
                  "fairplay-asset://bundle/.hidden",
                  "fairplay-asset://bundle/pdfjs/.DS_Store",
                  "fairplay-asset://bundle/a//b.js",
                  "fairplay-asset://bundle//etc/passwd",
                  "fairplay-asset://bundle/pyodide/",
                  "fairplay-asset://bundle/a%5Cb.js",
                  "fairplay-asset://bundle/a%00.js",
                  "fairplay-asset://bundle/a%0a.js",
                  "fairplay-asset://bundle/~/x",
                  "fairplay-asset://bundle/%ZZ"] {
            if let u = URL(string: s) {
                #expect(AssetPack.relativePath(for: u) == nil, "\(s)")
            }
        }
    }

    @Test func safeRelativePaths() {
        #expect(AssetPack.safeRelativePath("a/b.js") == "a/b.js")
        #expect(AssetPack.safeRelativePath("/a/b.js") == "a/b.js")
        #expect(AssetPack.safeRelativePath("pyodide.asm.wasm") == "pyodide.asm.wasm")
        #expect(AssetPack.safeRelativePath("LICENSE_FOXIT") == "LICENSE_FOXIT")
        #expect(AssetPack.safeRelativePath("") == nil)
        #expect(AssetPack.safeRelativePath("/") == nil)
        #expect(AssetPack.safeRelativePath("//a") == nil)
        #expect(AssetPack.safeRelativePath("..") == nil)
        #expect(AssetPack.safeRelativePath("a/..") == nil)
        #expect(AssetPack.safeRelativePath("a/../b") == nil)
        #expect(AssetPack.safeRelativePath("a/./b") == nil)
        #expect(AssetPack.safeRelativePath("a\\b") == nil)
        #expect(AssetPack.safeRelativePath("a\u{0}b") == nil)
        #expect(AssetPack.safeRelativePath("a\u{7f}b") == nil)
        #expect(AssetPack.safeRelativePath("a/") == nil)
        #expect(AssetPack.safeRelativePath(String(repeating: "a", count: 513)) == nil)
        #expect(AssetPack.safeRelativePath(String(repeating: "a", count: 512)) != nil)
    }

    // MARK: relative path -> file

    /// root/{a.js, sub/b.wasm, dir/}, outside/secret.txt, and symlinks out of root.
    private func makePack() throws -> (root: URL, base: URL) {
        let fm = FileManager.default
        let base = fm.temporaryDirectory.appendingPathComponent("assetpack-\(UUID().uuidString)", isDirectory: true)
        let root = base.appendingPathComponent("app-assets", isDirectory: true)
        try fm.createDirectory(at: root.appendingPathComponent("sub"), withIntermediateDirectories: true)
        try fm.createDirectory(at: root.appendingPathComponent("dir"), withIntermediateDirectories: true)
        try fm.createDirectory(at: base.appendingPathComponent("outside"), withIntermediateDirectories: true)
        try Data("js".utf8).write(to: root.appendingPathComponent("a.js"))
        try Data([0, 97, 115, 109]).write(to: root.appendingPathComponent("sub/b.wasm"))
        try Data("secret".utf8).write(to: base.appendingPathComponent("outside/secret.txt"))
        try fm.createSymbolicLink(at: root.appendingPathComponent("escape.txt"),
                                  withDestinationURL: base.appendingPathComponent("outside/secret.txt"))
        try fm.createSymbolicLink(at: root.appendingPathComponent("escapedir"),
                                  withDestinationURL: base.appendingPathComponent("outside"))
        try fm.createSymbolicLink(at: root.appendingPathComponent("inside.js"),
                                  withDestinationURL: root.appendingPathComponent("a.js"))
        return (root, base)
    }

    @Test func filesInsideThePack() throws {
        let (root, base) = try makePack()
        defer { try? FileManager.default.removeItem(at: base) }
        let a = try #require(AssetPack.fileURL(forRelativePath: "a.js", root: root))
        #expect(try Data(contentsOf: a) == Data("js".utf8))
        let b = try #require(AssetPack.fileURL(forRelativePath: "sub/b.wasm", root: root))
        #expect(b.lastPathComponent == "b.wasm")
        // A symlink that stays inside the pack is fine.
        let inside = try #require(AssetPack.fileURL(forRelativePath: "inside.js", root: root))
        #expect(try Data(contentsOf: inside) == Data("js".utf8))
    }

    @Test func nothingOutsideThePack() throws {
        let (root, base) = try makePack()
        defer { try? FileManager.default.removeItem(at: base) }
        #expect(AssetPack.fileURL(forRelativePath: "missing.js", root: root) == nil)
        #expect(AssetPack.fileURL(forRelativePath: "dir", root: root) == nil)          // a folder
        #expect(AssetPack.fileURL(forRelativePath: "sub", root: root) == nil)
        #expect(AssetPack.fileURL(forRelativePath: "escape.txt", root: root) == nil)   // symlink out
        #expect(AssetPack.fileURL(forRelativePath: "escapedir/secret.txt", root: root) == nil)
        #expect(AssetPack.fileURL(forRelativePath: "../outside/secret.txt", root: root) == nil)
        #expect(AssetPack.fileURL(forRelativePath: "/../outside/secret.txt", root: root) == nil)
        // An absolute path is taken as relative to the pack, never as a path on disk.
        #expect(AssetPack.fileURL(forRelativePath: base.appendingPathComponent("outside/secret.txt").path,
                                  root: root) == nil)
    }

    @Test func replies() throws {
        let (root, base) = try makePack()
        defer { try? FileManager.default.removeItem(at: base) }
        let a = url("fairplay-asset://bundle/a.js")
        if case .file(let f, let mime, let bodyless) = AssetPack.reply(method: "GET", url: a, root: root) {
            #expect(f.lastPathComponent == "a.js")
            #expect(mime == "text/javascript")
            #expect(!bodyless)
        } else {
            Issue.record("GET a.js should serve the file")
        }
        if case .file(_, let mime, let bodyless) = AssetPack.reply(method: nil, url: url("fairplay-asset://bundle/sub/b.wasm"), root: root) {
            #expect(mime == "application/wasm")
            #expect(!bodyless)
        } else {
            Issue.record("a missing method is GET")
        }
        if case .file(_, _, let bodyless) = AssetPack.reply(method: "head", url: a, root: root) {
            #expect(bodyless)
        } else {
            Issue.record("HEAD a.js should answer")
        }
        #expect(AssetPack.reply(method: "OPTIONS", url: a, root: root) == .preflight)
        #expect(AssetPack.reply(method: "POST", url: a, root: root) == .methodNotAllowed)
        #expect(AssetPack.reply(method: "PUT", url: a, root: root) == .methodNotAllowed)
        #expect(AssetPack.reply(method: "GET", url: a, root: nil) == .notFound)            // no pack
        #expect(AssetPack.reply(method: "GET", url: nil, root: root) == .notFound)
        #expect(AssetPack.reply(method: "GET", url: url("fairplay-asset://bundle/nope.js"), root: root) == .notFound)
        #expect(AssetPack.reply(method: "GET", url: url("fairplay-asset://bundle/escape.txt"), root: root) == .notFound)
        #expect(AssetPack.reply(method: "GET", url: url("fairplay-asset://bundle/%2e%2e/outside/secret.txt"), root: root) == .notFound)
        #expect(AssetPack.reply(method: "GET", url: url("fairplay-asset://other/a.js"), root: root) == .notFound)
    }

    // MARK: types and headers

    @Test func mimeTypes() {
        #expect(AssetPack.mimeType(forPath: "pyodide/pyodide.js") == "text/javascript")
        #expect(AssetPack.mimeType(forPath: "pyodide/pyodide.mjs") == "text/javascript")
        #expect(AssetPack.mimeType(forPath: "pdfjs/pdf.worker.min.mjs") == "text/javascript")
        #expect(AssetPack.mimeType(forPath: "pyodide/pyodide.asm.wasm") == "application/wasm")
        #expect(AssetPack.mimeType(forPath: "pyodide/pyodide-lock.json") == "application/json")
        #expect(AssetPack.mimeType(forPath: "pyodide/python_stdlib.zip") == "application/zip")
        #expect(AssetPack.mimeType(forPath: "pdfjs/cmaps/78-H.bcmap") == "application/octet-stream")
        #expect(AssetPack.mimeType(forPath: "pdfjs/standard_fonts/FoxitSans.pfb") == "application/octet-stream")
        #expect(AssetPack.mimeType(forPath: "pdfjs/standard_fonts/LiberationSans-Bold.ttf") == "font/ttf")
        #expect(AssetPack.mimeType(forPath: "x/font.OTF") == "font/otf")
        #expect(AssetPack.mimeType(forPath: "a/LICENSE.txt") == "text/plain; charset=utf-8")
        #expect(AssetPack.mimeType(forPath: "pdfjs/standard_fonts/LICENSE_FOXIT") == "application/octet-stream")
        #expect(AssetPack.mimeType(forPath: "a.JS") == "text/javascript")
        #expect(AssetPack.mimeType(forPath: "dir.js/file") == "application/octet-stream")
        #expect(AssetPack.mimeType(forPath: "a.html") == "application/octet-stream")   // never a page
    }

    @Test func headers() {
        let h = AssetPack.headers(mime: "application/wasm", length: 12_345)
        #expect(h["Content-Type"] == "application/wasm")
        #expect(h["Content-Length"] == "12345")
        #expect(h["Access-Control-Allow-Origin"] == "*")
        #expect(h["Cache-Control"] == "max-age=31536000, immutable")
        #expect(h["X-Content-Type-Options"] == "nosniff")
        #expect(AssetPack.headers(mime: "x", length: -3)["Content-Length"] == "0")
        #expect(AssetPack.notFoundHeaders["Access-Control-Allow-Origin"] == "*")
        #expect(AssetPack.notFoundHeaders["Cache-Control"] == "no-store")
        #expect(AssetPack.preflightHeaders["Access-Control-Allow-Origin"] == "*")
        #expect(AssetPack.preflightHeaders["Access-Control-Allow-Methods"]?.contains("GET") == true)
    }

    // MARK: manifest

    @Test func manifestVersions() {
        func v(_ s: String) -> String? { AssetPack.manifestVersion(Data(s.utf8)) }
        #expect(v(#"{"version": "3f2a9c01d4e5b6a7", "files": {"pyodide/pyodide.mjs": "ab"}}"#) == "3f2a9c01d4e5b6a7")
        #expect(v(#"{"version": "  0.6.2  "}"#) == "0.6.2")
        #expect(v(#"{"files": {}}"#) == nil)
        #expect(v(#"{"version": ""}"#) == nil)
        #expect(v(#"{"version": "   "}"#) == nil)
        #expect(v(#"{"version": 12}"#) == nil)
        #expect(v(#"["version"]"#) == nil)
        #expect(v("not json") == nil)
        #expect(v("") == nil)
        #expect(v(#"{"version": "\#(String(repeating: "a", count: 129))"}"#) == nil)
        #expect(v(#"{"version": "\#(String(repeating: "a", count: 128))"}"#)?.count == 128)
    }

    // MARK: page script

    @Test func injectionOnlyForTheServerOrigin() throws {
        let js = try #require(AssetPack.injectionScript(version: "3f2a9c01d4e5b6a7", server: url("https://Exam.Example.edu")))
        #expect(js == #"if (location.origin === "https://exam.example.edu") { window.fairplayAssets = Object.freeze({ base: "fairplay-asset://bundle/", version: "3f2a9c01d4e5b6a7" }); }"#)
        // Non-default port kept, default port left out (like location.origin).
        let dev = try #require(AssetPack.injectionScript(version: "v", server: url("http://127.0.0.1:8000/")))
        #expect(dev.hasPrefix(#"if (location.origin === "http://127.0.0.1:8000")"#))
        let p443 = try #require(AssetPack.injectionScript(version: "v", server: url("https://exam.example.edu:443")))
        #expect(p443.hasPrefix(#"if (location.origin === "https://exam.example.edu")"#))
        // No pack (no version): no script.
        #expect(AssetPack.injectionScript(version: nil, server: url("https://exam.example.edu")) == nil)
        #expect(AssetPack.injectionScript(version: "", server: url("https://exam.example.edu")) == nil)
    }

    @Test func versionIsEscaped() throws {
        let evil = "\"); alert(1); (\"</script>\u{2028}'\\\n"
        let js = try #require(AssetPack.injectionScript(version: evil, server: url("https://exam.example.edu")))
        let literal = AssetPack.jsString(evil)
        #expect(js.contains("version: \(literal) }"))
        #expect(!literal.dropFirst().dropLast().contains("\""))
        #expect(!literal.contains("<"))
        #expect(!literal.contains("'"))
        #expect(!literal.contains("\n"))
        #expect(!literal.contains("\u{2028}"))
        #expect(!literal.dropFirst().dropLast().contains("\\\\"))   // the backslash is \u005c
        #expect(literal.contains("\\u2028"))
    }

    @Test func jsStrings() {
        #expect(AssetPack.jsString("abc-1.2_3+x:/ y") == #""abc-1.2_3+x:/ y""#)
        #expect(AssetPack.jsString("\"") == #""\u0022""#)
        #expect(AssetPack.jsString("\\") == #""\u005c""#)
        #expect(AssetPack.jsString("<") == #""\u003c""#)
        #expect(AssetPack.jsString("é") == #""\u00e9""#)
        #expect(AssetPack.jsString("😀") == #""\ud83d\ude00""#)
        #expect(AssetPack.jsString("") == #""""#)
    }
}
