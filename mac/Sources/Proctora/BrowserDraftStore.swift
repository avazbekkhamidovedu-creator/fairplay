import Foundation
import CryptoKit
import Security

/// Recovery copies, not submission receipts. No passwords, cookies or login tokens.
struct BrowserDraftScope: Codable, Equatable {
    let origin: String
    let user: String
    let context: String
    let problem: String
    var valid: Bool {
        guard let url = URL(string: origin), ProtectedBrowserPolicy.origin(url) == origin else { return false }
        return [user, context, problem].allSatisfy { !$0.isEmpty && $0.utf8.count <= 256 && !$0.contains("\n") }
    }
    var binding: Data { try! JSONEncoder().encode([origin, user, context, problem]) }
    var filename: String { SHA256.hash(data: binding).map { String(format: "%02x", $0) }.joined() + ".draft" }
}

final class BrowserDraftStore {
    struct Record: Codable { let scope: BrowserDraftScope; let savedAt: Date; let fields: [String: String] }
    enum Failure: Error { case invalid, keychain, tooLarge }
    let folder: URL
    private let keyProvider: (() throws -> SymmetricKey)?
    private var preparedKey: SymmetricKey?
    private let keyLock = NSLock()
    init(folder: URL? = nil, key: (() throws -> SymmetricKey)? = nil) {
        self.folder = folder ?? Sealed.folder.deletingLastPathComponent().appendingPathComponent("website-drafts")
        self.keyProvider = key
    }
    /// Called before acquiring the lock. UI is opt-in and never used by save/load.
    func prepare(allowInteraction: Bool = false) throws {
        _ = try encryptionKey(allowInteraction: allowInteraction)
    }
    private func encryptionKey(allowInteraction: Bool = false) throws -> SymmetricKey {
        keyLock.lock(); defer { keyLock.unlock() }
        if let preparedKey { return preparedKey }
        let result = try keyProvider?() ?? Self.keychainKey(allowInteraction: allowInteraction)
        preparedKey = result
        return result
    }
    func save(scope: BrowserDraftScope, fields: [String: String], now: Date = Date()) throws {
        guard scope.valid, !fields.isEmpty, fields.count <= 500,
              fields.keys.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 256 }) else { throw Failure.invalid }
        let plain = try JSONEncoder().encode(Record(scope: scope, savedAt: now, fields: fields))
        guard plain.count <= 5 * 1024 * 1024 else { throw Failure.tooLarge }
        let data = try AES.GCM.seal(plain, using: encryptionKey(), authenticating: scope.binding).combined!
        let fm = FileManager.default
        try fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let url = folder.appendingPathComponent(scope.filename)
        try data.write(to: url, options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        prune(now: now)
    }
    func load(scope: BrowserDraftScope, now: Date = Date()) throws -> Record? {
        guard scope.valid else { throw Failure.invalid }
        let url = folder.appendingPathComponent(scope.filename)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        let bytes = try Data(contentsOf: url)
        guard bytes.count <= 5 * 1024 * 1024 + 1024 else { throw Failure.tooLarge }
        let data = try AES.GCM.open(AES.GCM.SealedBox(combined: bytes), using: encryptionKey(), authenticating: scope.binding)
        let record = try JSONDecoder().decode(Record.self, from: data)
        guard record.scope == scope, now.timeIntervalSince(record.savedAt) < 7 * 86400,
              record.savedAt.timeIntervalSince(now) < 60 else { return nil }
        return record
    }
    private func prune(now: Date) {
        let fm = FileManager.default
        let urls = (try? fm.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        let entries = urls.filter { $0.pathExtension == "draft" }.map { ($0, (try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast) }.sorted { $0.1 > $1.1 }
        for (index, entry) in entries.enumerated() where index >= 100 || now.timeIntervalSince(entry.1) > 7 * 86400 {
            try? fm.removeItem(at: entry.0)
        }
    }
    static func keychainKey(allowInteraction: Bool = false) throws -> SymmetricKey {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Config.deviceKeyNamespace + ".website-drafts", kSecAttrAccount as String: "encryption"]
        var read = query; read[kSecReturnData as String] = true
        if !allowInteraction { read[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail }
        var item: CFTypeRef?
        let status = SecItemCopyMatching(read as CFDictionary, &item)
        if status == errSecSuccess, let bytes = item as? Data, bytes.count == 32 { return SymmetricKey(data: bytes) }
        guard status == errSecItemNotFound else { throw Failure.keychain }
        var bytes = Data(count: 32)
        let result = bytes.withUnsafeMutableBytes { SecRandomCopyBytes(kSecRandomDefault, 32, $0.baseAddress!) }
        guard result == errSecSuccess else { throw Failure.keychain }
        var insert = query
        insert[kSecValueData as String] = bytes
        insert[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let added = SecItemAdd(insert as CFDictionary, nil)
        if added == errSecDuplicateItem { return try keychainKey(allowInteraction: allowInteraction) }
        guard added == errSecSuccess else { throw Failure.keychain }
        return SymmetricKey(data: bytes)
    }
}
