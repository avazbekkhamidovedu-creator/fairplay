import Testing
import Foundation
import CryptoKit
import Security
@testable import FairPlayCore

private final class SoftBacking: DeviceKeyBacking {
    let priv = P256.Signing.PrivateKey()
    var publicKeyDER: Data { priv.publicKey.derRepresentation }
    let hardwareBacked = false
    func signDER(_ message: Data) throws -> Data { try priv.signature(for: message).derRepresentation }
}

private final class MemoryStore: DeviceKeyStore {
    var mode: String?
    var backing: DeviceKeyBacking?
    var creates = 0
    var failCreate = false
    var failMode = false
    func load() throws -> DeviceKeyBacking? { backing }
    func create() throws -> DeviceKeyBacking {
        if failCreate { throw DeviceKeyError("no secure enclave and keychain refused") }
        creates += 1
        let b = SoftBacking()
        backing = b
        return b
    }
    func readMode() throws -> String? { mode }
    func writeMode(_ m: String) throws {
        if failMode { throw DeviceKeyError("keychain locked") }
        mode = m
    }
}

private func startMessage(_ nonce: String) -> (String) -> String { { "start|\(nonce)|\($0)" } }

@Suite struct DeviceKeyTests {
    // Same vector as server/app/device_key.py derive_ids (computed there).
    let vectorDER = Signing.data(hex: "3059301306072a8648ce3d020106082a8648ce3d03010703420004ef880c8ef816881796c188e571b01ac6f70c52627456dae486758effd9c63ddd5cdbc11e24883a12598242a95875b96a8854f64bff9ac9a33810b81f154f54e5")!

    @Test func derivedIdMatchesServerVector() {
        #expect(DeviceKey.deviceId(publicKeyDER: vectorDER) == "1fb1316d8c1236fc506368dac0a4ccf7d3b1fc80")
        #expect(DeviceKey.base64URL(vectorDER) == "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE74gMjvgWiBeWwYjlcbAaxvcMUmJ0VtrkhnWO_9nGPd1c28EeJIg6ElmCQqlYdblqiFT2S_-ayaM4ELgfFU9U5Q")
    }

    @Test func signatureVerifiesWithDomainPrefix() throws {
        let backing = SoftBacking()
        let key = DeviceKey(backing)
        #expect(key.publicKeyDER.count == 91)
        let msg = "start|n0nce12345|abc"
        let der = try key.sign(msg)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: der)
        #expect(backing.priv.publicKey.isValidSignature(sig, for: Data(("fp-device-v1|" + msg).utf8)))
        #expect(!backing.priv.publicKey.isValidSignature(sig, for: Data(msg.utf8)))
        let pub = try P256.Signing.PublicKey(derRepresentation: key.publicKeyDER)
        #expect(pub.derRepresentation == key.publicKeyDER)
        #expect(!key.publicKeyBase64URL.contains("=") && !key.publicKeyBase64URL.contains("+"))
    }

    @Test func newInstallUsesDerivedId() throws {
        let store = MemoryStore()
        let b = DeviceBinding.resolve(legacyId: "ipad-legacy0001", evidence: { .absent }, store: store)
        let key = try #require(b.key)
        #expect(b.failure == nil)
        #expect(b.deviceId == key.derivedDeviceId)
        #expect(store.mode == "derived")
        let p = b.proof(message: startMessage("nnnnnnnn1"))
        #expect(p.deviceId == key.derivedDeviceId)
        #expect(p.publicKey == key.publicKeyBase64URL)
        let raw = try #require(p.signature)
        let padded = raw.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            + String(repeating: "=", count: (4 - raw.count % 4) % 4)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: try #require(Data(base64Encoded: padded)))
        let pub = try P256.Signing.PublicKey(derRepresentation: key.publicKeyDER)
        #expect(pub.isValidSignature(sig, for: Data(("fp-device-v1|start|nnnnnnnn1|" + key.derivedDeviceId).utf8)))
    }

    @Test func legacyInstallKeepsLegacyIdWithKey() throws {
        let store = MemoryStore()
        let b = DeviceBinding.resolve(legacyId: "ipad-legacy0001", evidence: { .present }, store: store)
        let key = try #require(b.key)
        #expect(b.deviceId == "ipad-legacy0001")
        #expect(store.mode == "legacy")
        let p = b.proof(message: startMessage("nnnnnnnn1"))
        #expect(p.deviceId == "ipad-legacy0001")
        let raw = try #require(p.signature)
        let padded = raw.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            + String(repeating: "=", count: (4 - raw.count % 4) % 4)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: try #require(Data(base64Encoded: padded)))
        let pub = try P256.Signing.PublicKey(derRepresentation: key.publicKeyDER)
        #expect(pub.isValidSignature(sig, for: Data("fp-device-v1|start|nnnnnnnn1|ipad-legacy0001".utf8)))
    }

    @Test func modeAndKeyAreNeverRegenerated() {
        let store = MemoryStore()
        let first = DeviceBinding.resolve(legacyId: "L-legacy-id", evidence: { .present }, store: store)
        let again = DeviceBinding.resolve(legacyId: "L-legacy-id", evidence: { .absent }, store: store)
        #expect(store.creates == 1)
        #expect(again.deviceId == "L-legacy-id")
        #expect(again.key?.publicKeyDER == first.key?.publicKeyDER)
    }

    @Test func keyFailureFallsBackToLegacyProof() {
        let store = MemoryStore()
        store.failCreate = true
        let b = DeviceBinding.resolve(legacyId: "ipad-legacy0001", evidence: { .absent }, store: store)
        #expect(b.key == nil)
        #expect(b.failure != nil)
        #expect(b.deviceId == "ipad-legacy0001")
        let p = b.proof(message: startMessage("nnnnnnnn1"))
        #expect(p.deviceId == "ipad-legacy0001" && p.publicKey == nil && p.signature == nil)
    }

    @Test func unreadableStateFallsBackWithoutKey() {
        let unknown = DeviceBinding.resolve(legacyId: "L-legacy-id", evidence: { .unknown }, store: MemoryStore())
        #expect(unknown.key == nil && unknown.failure != nil)
        let store = MemoryStore()
        store.failMode = true
        let b = DeviceBinding.resolve(legacyId: "L-legacy-id", evidence: { .present }, store: store)
        #expect(b.key == nil && b.deviceId == "L-legacy-id" && store.creates == 0)
    }

    @Test func realKeychainRoundTripWhenAvailable() throws {
        let ns = "ai.proctora.test.\(UUID().uuidString)"
        let store = KeychainDeviceKeyStore(namespace: ns)
        guard let backing = try? store.create() else { return }
        defer {
            SecItemDelete([kSecClass as String: kSecClassKey,
                           kSecAttrApplicationTag as String: Data("\(ns).devicekey.v1".utf8)] as CFDictionary)
        }
        let key = DeviceKey(backing)
        #expect(key.publicKeyDER.count == 91)
        let again = try #require(try store.load())
        #expect(again.publicKeyDER == key.publicKeyDER)
        let pub = try P256.Signing.PublicKey(derRepresentation: key.publicKeyDER)
        let sig = try P256.Signing.ECDSASignature(derRepresentation: try key.sign("m"))
        #expect(pub.isValidSignature(sig, for: Data("fp-device-v1|m".utf8)))
    }
}
