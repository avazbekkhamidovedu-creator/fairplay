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

    @Test func dangerousTypesAreRefused() {
        for n in ["a.app", "a.DMG", "a.pkg", "a.command", "a.sh", "a.scpt", "a.workflow", "a.jar", "a.exe", "a.bat",
                  "a.ps1", "a.js", "a.terminal", "a.webloc", "a.iso", "report.pdf.sh"] {
            guard case .refuse = DownloadPolicy.decide(suggestedName: n, expectedBytes: 10) else { Issue.record("\(n) saved"); continue }
        }
        #expect(DownloadPolicy.decide(suggestedName: "notes.pdf", expectedBytes: 10) == .save(name: "notes.pdf"))
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

    @Test func sizeCap() {
        #expect(DownloadPolicy.decide(suggestedName: "a.zip", expectedBytes: DownloadPolicy.maxBytes) == .save(name: "a.zip"))
        guard case .refuse = DownloadPolicy.decide(suggestedName: "a.zip", expectedBytes: DownloadPolicy.maxBytes + 1) else {
            Issue.record("oversized saved"); return
        }
    }

    @Test func statusLine() {
        #expect(DownloadPolicy.statusLine(saved: "a.pdf") == "Saved a.pdf to FairPlay Exam Files")
    }
}
