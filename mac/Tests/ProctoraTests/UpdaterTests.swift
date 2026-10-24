// 2026-10-22: in-app updates (Updater.swift): what counts as an installable release, the
// digest and file checks, and the Info.plist check of the unpacked copy.
import Testing
import Foundation
@testable import Proctora

private let goodDigest = "sha256:" + String(repeating: "ab12", count: 16)

/// Shaped like GET /repos/{owner}/{repo}/releases/latest.
private func releaseJSON(tag: String = "v0.5.4",
                         assetName: String = "FairPlay-Mac.zip",
                         url: String? = nil,
                         digest: String? = goodDigest,
                         draft: Bool = false,
                         prerelease: Bool = false) -> Data {
    let dl = url ?? "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/\(tag)/\(assetName)"
    var mac: [String: Any] = [
        "url": "https://api.github.com/repos/avazbekkhamidovedu-creator/fairplay-download/releases/assets/301",
        "id": 301, "name": assetName, "label": "", "content_type": "application/zip",
        "state": "uploaded", "size": 9_876_543, "download_count": 12,
        "created_at": "2026-10-22T08:00:00Z", "updated_at": "2026-10-22T08:00:05Z",
        "browser_download_url": dl,
    ]
    if let digest { mac["digest"] = digest }
    let obj: [String: Any] = [
        "url": "https://api.github.com/repos/avazbekkhamidovedu-creator/fairplay-download/releases/200",
        "html_url": "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/tag/\(tag)",
        "id": 200, "tag_name": tag, "target_commitish": "main", "name": "FairPlay \(tag)",
        "draft": draft, "prerelease": prerelease,
        "created_at": "2026-10-22T07:59:00Z", "published_at": "2026-10-22T08:01:00Z",
        "assets": [
            mac,
            ["id": 302, "name": "FairPlay-Mac.dmg", "state": "uploaded", "digest": goodDigest,
             "browser_download_url": "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/\(tag)/FairPlay-Mac.dmg"],
            ["id": 303, "name": "FairPlay-Windows-Setup.exe", "state": "uploaded", "digest": goodDigest,
             "browser_download_url": "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/\(tag)/FairPlay-Windows-Setup.exe"],
        ],
        "body": "Fixes.",
    ]
    return try! JSONSerialization.data(withJSONObject: obj)
}

@Suite struct UpdaterParseTests {
    @Test func validReleaseParses() {
        let r = Updater.parseRelease(json: releaseJSON())
        #expect(r?.version == "0.5.4")
        #expect(r?.zipURL.absoluteString == "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip")
        #expect(r?.sha256 == String(repeating: "ab12", count: 16))
    }

    @Test func tagWithoutVIsAccepted() {
        #expect(Updater.parseRelease(json: releaseJSON(tag: "0.6.0"))?.version == "0.6.0")
    }

    @Test func missingOrBadDigestMeansNoUpdate() {
        #expect(Updater.parseRelease(json: releaseJSON(digest: nil)) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(digest: "sha256:abc")) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(digest: "sha512:" + String(repeating: "a", count: 64))) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(digest: "sha256:" + String(repeating: "g", count: 64))) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(digest: String(repeating: "a", count: 64))) == nil)
    }

    @Test func badTagsAreRefused() {
        for tag in ["latest", "v1.2", "v1.2.3.4", "v1.2.3-beta", "v01.2.x", "vv1.2.3", "v1..3", "v١.٢.٣", ""] {
            #expect(Updater.parseRelease(json: releaseJSON(tag: tag)) == nil, "tag \(tag)")
        }
    }

    @Test func wrongAssetNameIsRefused() {
        #expect(Updater.parseRelease(json: releaseJSON(assetName: "FairPlay-Mac-v2.zip")) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(assetName: "fairplay-mac.zip")) == nil)
    }

    @Test func foreignURLsAreRefused() {
        let bad = [
            "https://evil.example/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://github.com/khamidov17/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",  // old download repo (before 0.7.1)
            "http://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://github.com/someone/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.3/FairPlay-Mac.zip",
            "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/../../x/FairPlay-Mac.zip",
            "https://github.com.evil.example/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://user@github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://github.com:8443/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://objects.githubusercontent.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip",
            "https://github.com/avazbekkhamidovedu-creator/fairplay-download/releases/download/v0.5.4/FairPlay-Mac.zip?x=1",
        ]
        for u in bad {
            #expect(Updater.parseRelease(json: releaseJSON(url: u)) == nil, "url \(u)")
        }
    }

    @Test func draftsPrereleasesAndJunkAreRefused() {
        #expect(Updater.parseRelease(json: releaseJSON(draft: true)) == nil)
        #expect(Updater.parseRelease(json: releaseJSON(prerelease: true)) == nil)
        #expect(Updater.parseRelease(json: Data("not json".utf8)) == nil)
        #expect(Updater.parseRelease(json: Data("[]".utf8)) == nil)
        #expect(Updater.parseRelease(json: Data(#"{"message":"API rate limit exceeded"}"#.utf8)) == nil)
    }

    @Test func twoAssetsWithTheSameNameAreRefused() {
        var obj = (try! JSONSerialization.jsonObject(with: releaseJSON())) as! [String: Any]
        var assets = obj["assets"] as! [[String: Any]]
        assets.append(assets[0])
        obj["assets"] = assets
        #expect(Updater.parseRelease(json: try! JSONSerialization.data(withJSONObject: obj)) == nil)
    }

    @Test func newerIsByNumber() {
        #expect(Updater.isNewer("0.5.4", than: "0.5.3"))
        #expect(Updater.isNewer("0.10.0", than: "0.9.9"))
        #expect(Updater.isNewer("1.0.0", than: "0.99.99"))
        #expect(!Updater.isNewer("0.5.3", than: "0.5.3"))
        #expect(!Updater.isNewer("0.5.2", than: "0.5.3"))
    }

    @Test func plainVersions() {
        #expect(Updater.isPlainVersion("0.5.4"))
        #expect(!Updater.isPlainVersion("0.5"))
        #expect(!Updater.isPlainVersion(" 0.5.4"))
        #expect(!Updater.isPlainVersion("0.5.4 "))
        #expect(Updater.version(fromTag: "v12.0.1") == "12.0.1")
        #expect(Updater.version(fromTag: "V1.2.3") == nil)
    }
}

@Suite struct UpdaterFileTests {
    private func tempFolder() throws -> URL {
        let u = FileManager.default.temporaryDirectory.appendingPathComponent("updater-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: u, withIntermediateDirectories: true)
        return u
    }

    @Test func sha256OfAFile() throws {
        let dir = try tempFolder()
        defer { try? FileManager.default.removeItem(at: dir) }
        let f = dir.appendingPathComponent("a.bin")
        try Data("abc".utf8).write(to: f)
        let abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        #expect(try Updater.sha256Hex(of: f) == abc)
        #expect(Updater.fileMatches(f, sha256: abc))
        #expect(Updater.fileMatches(f, sha256: abc.uppercased()))
        #expect(!Updater.fileMatches(f, sha256: String(repeating: "0", count: 64)))
        #expect(!Updater.fileMatches(dir.appendingPathComponent("missing"), sha256: abc))
        // More than one read piece (1 MB).
        let big = dir.appendingPathComponent("big.bin")
        try Data(repeating: 0x61, count: 3 * 1024 * 1024 + 7).write(to: big)
        #expect(try Updater.sha256Hex(of: big).count == 64)
    }

    @Test func digestPrefix() {
        #expect(Updater.sha256(fromDigest: "sha256:" + String(repeating: "AB", count: 32)) == String(repeating: "ab", count: 32))
        #expect(Updater.sha256(fromDigest: "sha256:" + String(repeating: "a", count: 63)) == nil)
        #expect(Updater.sha256(fromDigest: "sha256:" + String(repeating: "a", count: 65)) == nil)
    }

    private func makeApp(in dir: URL, name: String = "FairPlay.app", id: String = "ai.proctora.student",
                         version: String = "0.5.4") throws -> URL {
        let app = dir.appendingPathComponent(name)
        let contents = app.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
        let info: [String: Any] = ["CFBundleIdentifier": id, "CFBundleShortVersionString": version,
                                   "CFBundleVersion": version, "CFBundleExecutable": "Proctora"]
        let data = try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        try data.write(to: contents.appendingPathComponent("Info.plist"))
        return app
    }

    @Test func infoPlistMustMatch() throws {
        let dir = try tempFolder()
        defer { try? FileManager.default.removeItem(at: dir) }
        let app = try makeApp(in: dir)
        let info = Updater.readInfo(app)
        #expect(Updater.infoMatches(info, bundleId: "ai.proctora.student", version: "0.5.4"))
        #expect(!Updater.infoMatches(info, bundleId: "ai.proctora.student", version: "0.5.5"))
        #expect(!Updater.infoMatches(info, bundleId: "com.example.other", version: "0.5.4"))
        #expect(!Updater.infoMatches(info, bundleId: "", version: "0.5.4"))
        #expect(!Updater.infoMatches(nil, bundleId: "ai.proctora.student", version: "0.5.4"))
        #expect(Updater.readInfo(dir.appendingPathComponent("Nope.app")) == nil)
    }

    @Test func unpackedFolderMustHoldExactlyFairPlayApp() throws {
        let fm = FileManager.default
        let one = try tempFolder()
        defer { try? fm.removeItem(at: one) }
        _ = try makeApp(in: one)
        #expect(Updater.singleApp(in: one)?.lastPathComponent == "FairPlay.app")
        // Resource forks from `ditto --sequesterRsrc` don't count.
        try fm.createDirectory(at: one.appendingPathComponent("__MACOSX"), withIntermediateDirectories: true)
        #expect(Updater.singleApp(in: one) != nil)

        let two = try tempFolder()
        defer { try? fm.removeItem(at: two) }
        _ = try makeApp(in: two)
        try Data().write(to: two.appendingPathComponent("extra.txt"))
        #expect(Updater.singleApp(in: two) == nil)

        let renamed = try tempFolder()
        defer { try? fm.removeItem(at: renamed) }
        _ = try makeApp(in: renamed, name: "Other.app")
        #expect(Updater.singleApp(in: renamed) == nil)

        let link = try tempFolder()
        defer { try? fm.removeItem(at: link) }
        let elsewhere = try tempFolder()
        defer { try? fm.removeItem(at: elsewhere) }
        let real = try makeApp(in: elsewhere)
        try fm.createSymbolicLink(at: link.appendingPathComponent("FairPlay.app"), withDestinationURL: real)
        #expect(Updater.singleApp(in: link) == nil)

        let empty = try tempFolder()
        defer { try? fm.removeItem(at: empty) }
        #expect(Updater.singleApp(in: empty) == nil)
    }

    @Test func replaceableNeedsAWritableRealCopy() throws {
        let dir = try tempFolder()
        defer { try? FileManager.default.removeItem(at: dir) }
        let app = try makeApp(in: dir)
        #expect(Updater.isReplaceable(app))
        #expect(!Updater.isReplaceable(dir.appendingPathComponent("Missing.app")))
        #expect(!Updater.isReplaceable(URL(fileURLWithPath: "/private/var/folders/x/AppTranslocation/ABC/d/FairPlay.app")))
    }
}
