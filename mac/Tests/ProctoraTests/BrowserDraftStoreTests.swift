import Foundation
import CryptoKit
import Testing
@testable import Proctora

@Suite struct BrowserDraftStoreTests {
    @Test func encryptedScopedRecoverySurvivesRecreation() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let key = SymmetricKey(size: .bits256)
        let scope = BrowserDraftScope(origin: "http://10.200.31.64", user: "123", context: "exam-9", problem: "p1")
        let fields = ["source": "print('private answer')"]
        try BrowserDraftStore(folder: folder, key: { key }).save(scope: scope, fields: fields)
        let store = BrowserDraftStore(folder: folder, key: { key })
        #expect(try store.load(scope: scope)?.fields == fields)
        let other = BrowserDraftScope(origin: scope.origin, user: "999", context: scope.context, problem: scope.problem)
        #expect(try store.load(scope: other) == nil)
        let bytes = try Data(contentsOf: folder.appendingPathComponent(scope.filename))
        #expect(!String(decoding: bytes, as: UTF8.self).contains("private answer"))
        #expect(try store.load(scope: scope, now: Date().addingTimeInterval(8 * 86400)) == nil)
    }
    @Test func tamperedDraftFailsClosed() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let key = SymmetricKey(size: .bits256)
        let store = BrowserDraftStore(folder: folder, key: { key })
        let scope = BrowserDraftScope(origin: "https://canvas.duke.edu", user: "1", context: "2", problem: "3")
        try store.save(scope: scope, fields: ["answer": "draft"])
        let url = folder.appendingPathComponent(scope.filename)
        var bytes = try Data(contentsOf: url); bytes[bytes.count-1] ^= 1
        try bytes.write(to: url)
        #expect(throws: (any Error).self) { try store.load(scope: scope) }
    }
    @Test func preflightCachesTheKeyBeforeAnyAnswerWrites() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        var reads = 0
        let key = SymmetricKey(size: .bits256)
        let store = BrowserDraftStore(folder: folder, key: {
            reads += 1
            guard reads == 1 else { throw BrowserDraftStore.Failure.keychain }
            return key
        })
        try store.prepare()
        let scope = BrowserDraftScope(origin: "https://example.edu", user: "1", context: "exam", problem: "1")
        try store.save(scope: scope, fields: ["answer": "first"])
        try store.save(scope: scope, fields: ["answer": "latest"])
        #expect(try store.load(scope: scope)?.fields["answer"] == "latest")
        #expect(reads == 1)
    }
}
