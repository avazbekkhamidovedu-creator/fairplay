import Testing
import Foundation
@testable import Proctora

@Suite struct DownloadPolicyTests {
    let trusted: Set<String> = ["https://10.200.31.64:8443", "https://assets.example.edu"]

    @Test func onlyTheOJAndSignedAssetOriginsMayDownload() {
        #expect(DownloadPolicy.originAllowed(URL(string: "https://10.200.31.64:8443/files/a.pdf"), trusted: trusted))
        #expect(DownloadPolicy.originAllowed(URL(string: "https://assets.example.edu/a.zip"), trusted: trusted))
        #expect(!DownloadPolicy.originAllowed(URL(string: "https://10.200.31.64/a.pdf"), trusted: trusted))
        #expect(!DownloadPolicy.originAllowed(URL(string: "http://10.200.31.64:8443/a.pdf"), trusted: trusted))
        #expect(!DownloadPolicy.originAllowed(URL(string: "https://evil.example/a.pdf"), trusted: trusted))
        #expect(!DownloadPolicy.originAllowed(URL(string: "https://10.200.31.64.evil.example:8443/a.pdf"), trusted: trusted))
        #expect(!DownloadPolicy.originAllowed(nil, trusted: trusted))
    }

    @Test func policyTrustsStartOriginAndAssetsOnly() {
        let p = ProtectedBrowserPolicy(start: "https://10.200.31.64:8443", additionalOrigins: "https://login.example.edu",
                                       forbiddenOrigin: URL(string: "https://fairplay.invalid")!, assetOrigins: ["https://cdn.example.edu"])
        #expect(p?.downloadOrigins == ["https://10.200.31.64:8443", "https://cdn.example.edu"])
    }

    @Test func courseFilesOfAnyTypeAreSaved() {
        for n in ["a.js", "run.sh", "tool.jar", "a.app", "a.exe", "a.zip", "report.pdf.sh", "notes.pdf", "data.csv"] {
            #expect(DownloadPolicy.decide(suggestedName: n, expectedBytes: 10) == .save(name: n), "\(n)")
        }
        #expect(DownloadPolicy.decide(suggestedName: "data.csv", expectedBytes: -1) == .save(name: "data.csv"))
    }

    @Test func namesAreSanitised() {
        #expect(DownloadPolicy.sanitize("../../etc/passwd") == "passwd")
        #expect(DownloadPolicy.sanitize("..\\..\\x.txt") == "x.txt")
        #expect(DownloadPolicy.sanitize(".hidden") == "hidden")
        #expect(DownloadPolicy.sanitize("...") == "download")
        #expect(DownloadPolicy.sanitize(nil) == "download")
        #expect(DownloadPolicy.sanitize("a\u{0}b:c.txt") == "abc.txt")
        let long = String(repeating: "a", count: 400) + ".pdf"
        let out = DownloadPolicy.sanitize(long)
        #expect(out.count <= DownloadPolicy.maxNameLength && out.hasSuffix(".pdf"))
    }

    @Test func namesNeverOverwrite() {
        #expect(DownloadPolicy.uniqueName("a.pdf") { _ in false } == "a.pdf")
        #expect(DownloadPolicy.uniqueName("a.pdf") { ["a.pdf"].contains($0) } == "a (2).pdf")
        #expect(DownloadPolicy.uniqueName("a.pdf") { ["a.pdf", "a (2).pdf"].contains($0) } == "a (3).pdf")
        #expect(DownloadPolicy.uniqueName("notes") { ["notes"].contains($0) } == "notes (2)")
    }

    @Test func sizeCapIsOneGigabyte() {
        #expect(DownloadPolicy.maxBytes == 1024 * 1024 * 1024)
        #expect(DownloadPolicy.decide(suggestedName: "a.zip", expectedBytes: DownloadPolicy.maxBytes) == .save(name: "a.zip"))
        guard case .refuse = DownloadPolicy.decide(suggestedName: "a.zip", expectedBytes: DownloadPolicy.maxBytes + 1) else {
            Issue.record("oversized saved"); return
        }
    }

    @Test func statusLine() {
        #expect(DownloadPolicy.statusLine(saved: "a.pdf") == "Saved a.pdf to FairPlay Exam Files")
    }
}

@Suite struct DownloadFolderSafetyTests {
    private func home() throws -> URL {
        let dir = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent("fp-dl-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.resolvingSymlinksInPath()
    }

    @Test func createsAPlainFolderUnderTheHome() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        let f = try DownloadPolicy.safeFolder(home: h, relative: ["Downloads", DownloadPolicy.folderName])
        #expect(f.path == h.path + "/Downloads/" + DownloadPolicy.folderName)
        #expect(try DownloadPolicy.safeFolder(home: h, relative: ["Downloads", DownloadPolicy.folderName]) == f)
    }

    @Test func aSymlinkedFolderIsRefused() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        let target = h.appendingPathComponent("LaunchAgents")
        try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: h.appendingPathComponent("Downloads"), withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: h.appendingPathComponent("Downloads/" + DownloadPolicy.folderName), withDestinationURL: target)
        #expect(throws: DownloadPolicy.FolderError.symlink) { try DownloadPolicy.safeFolder(home: h, relative: ["Downloads", DownloadPolicy.folderName]) }
    }

    @Test func aSymlinkedParentIsRefused() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        let target = h.appendingPathComponent("elsewhere")
        try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: h.appendingPathComponent("Downloads"), withDestinationURL: target)
        #expect(throws: DownloadPolicy.FolderError.symlink) { try DownloadPolicy.safeFolder(home: h, relative: ["Downloads", DownloadPolicy.folderName]) }
        #expect(!FileManager.default.fileExists(atPath: target.appendingPathComponent(DownloadPolicy.folderName).path))
    }

    @Test func aFileInPlaceOfTheFolderIsRefused() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        try FileManager.default.createDirectory(at: h.appendingPathComponent("Downloads"), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: h.appendingPathComponent("Downloads/" + DownloadPolicy.folderName).path, contents: Data())
        #expect(throws: DownloadPolicy.FolderError.notDirectory) { try DownloadPolicy.safeFolder(home: h, relative: ["Downloads", DownloadPolicy.folderName]) }
    }

    @Test func reserveNeverFollowsOrReplaces() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        let outside = h.appendingPathComponent("victim")
        FileManager.default.createFile(atPath: outside.path, contents: Data("keep".utf8))
        try FileManager.default.createSymbolicLink(at: h.appendingPathComponent("a.txt"), withDestinationURL: outside)
        #expect(!DownloadPolicy.reserve("a.txt", in: h))
        #expect(try String(contentsOf: outside, encoding: .utf8) == "keep")
        FileManager.default.createFile(atPath: h.appendingPathComponent("b.txt").path, contents: Data())
        #expect(!DownloadPolicy.reserve("b.txt", in: h))
        #expect(DownloadPolicy.reserve("c.txt", in: h))
        #expect(!FileManager.default.fileExists(atPath: h.appendingPathComponent("c.txt").path))
    }

    @Test func quarantineMarksRegularFilesAndSkipsLinks() throws {
        let h = try home(); defer { try? FileManager.default.removeItem(at: h) }
        let file = h.appendingPathComponent("f.bin")
        FileManager.default.createFile(atPath: file.path, contents: Data("x".utf8))
        #expect(DownloadPolicy.quarantine(file))
        #expect(getxattr(file.path, "com.apple.quarantine", nil, 0, 0, 0) > 0)
        let link = h.appendingPathComponent("l.bin")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: file)
        #expect(!DownloadPolicy.quarantine(link))
    }
}
