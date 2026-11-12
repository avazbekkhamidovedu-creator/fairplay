import Testing
import Foundation
import CryptoKit
@testable import Proctora

private func hex(_ s: String) -> Data {
    var d = Data()
    var i = s.startIndex
    while i < s.endIndex {
        let j = s.index(i, offsetBy: 2)
        d.append(UInt8(s[i..<j], radix: 16)!)
        i = j
    }
    return d
}

private struct Vector {
    let json: [String: Any]
    static func load(_ file: String = #filePath) -> Vector {
        var dir = URL(fileURLWithPath: file).deletingLastPathComponent()
        while dir.path != "/" {
            let p = dir.appendingPathComponent("docs/native-exit-vectors.json")
            if let data = try? Data(contentsOf: p) {
                return Vector(json: try! JSONSerialization.jsonObject(with: data) as! [String: Any])
            }
            dir.deleteLastPathComponent()
        }
        fatalError("native-exit-vectors.json not found")
    }
    var k: Data { hex(json["k_hex"] as! String) }
    var codes: [[String: String]] { json["codes"] as! [[String: String]] }
    var sealed: [String: String] { json["sealed"] as! [String: String] }
    var negative: [String: Any] { json["negative"] as! [String: Any] }
}

private final class FailingStore: NativeLockStoring {
    var inner: NativeLockState?
    var failSaves = false
    var recorded: [NativeLockState] = []
    init(_ s: NativeLockState) { inner = s }
    func load() -> NativeLockState? { inner }
    func save(_ state: NativeLockState) throws {
        inner = state
        recorded.append(state)
        if failSaves { throw NSError(domain: "t", code: 1) }
    }
    func delete() { inner = nil }
}

@Suite struct NativeExitTests {
    let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    private static let sharedVerifier: NativeLockState.Verifier = {
        NativeExit.makeVerifier(code: NativeExit.code(key: Vector.load().k, lockId: "K7QM3XPA"), lockId: "K7QM3XPA", iterations: 1_000)
    }()

    private func state(lockId: String = "K7QM3XPA") -> (NativeLockState, String) {
        let code = NativeExit.code(key: Vector.load().k, lockId: lockId)
        let v = lockId == "K7QM3XPA" ? Self.sharedVerifier
            : NativeExit.makeVerifier(code: code, lockId: lockId, iterations: 1_000)
        return (NativeLockState(lockId: lockId, verifier: v, created: t0), code)
    }

    private func store(lockId: String = "K7QM3XPA") -> (InMemoryNativeLockStore, String) {
        let (st, code) = state(lockId: lockId)
        return (InMemoryNativeLockStore(st), code)
    }

    private func att(_ typed: String, _ store: NativeLockStoring, _ now: Date) -> NativeExitAttempt {
        NativeExit.attempt(typed: typed, store: store, now: now,
                           monotonic: { .init(boot: "boot-1", seconds: 1_000 + now.timeIntervalSince(self.t0)) })
    }

    private func wrongCode(_ good: String) -> String { good == "000000" ? "000001" : "000000" }

    @Test func codeParity() {
        let v = Vector.load()
        for c in v.codes {
            #expect(NativeExit.code(key: v.k, lockId: c["lock_id"]!) == c["code"]!)
            #expect(NativeExit.display(c["lock_id"]!) == c["display"]!)
        }
    }

    @Test func verifierAcceptsTheVectorCodeAndRejectsOthers() {
        let v = Vector.load()
        for c in v.codes {
            let id = c["lock_id"]!
            let code = NativeExit.code(key: v.k, lockId: id)
            #expect(code == c["code"]!)
            let ver = NativeExit.makeVerifier(code: code, lockId: id, iterations: 1_000)
            #expect(ver.salt.count == 16 && ver.hash.count == 32 && ver.iterations == 1_000)
            #expect(NativeExit.verifies(code, lockId: id, verifier: ver))
            #expect(!NativeExit.verifies(wrongCode(code), lockId: id, verifier: ver))
            #expect(!NativeExit.verifies(code, lockId: id == "ZZZZZZZZ" ? "YYYYYYYY" : "ZZZZZZZZ", verifier: ver))
        }
        #expect(!NativeExit.verifies("123456", lockId: "K7QM3XPA", verifier: nil))
    }

    @Test func verifierSaltIsRandomAndIterationsHaveAFloor() {
        let a = NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPA", iterations: 1_000)
        let b = NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPA", iterations: 1_000)
        #expect(a.salt != b.salt && a.hash != b.hash)
        #expect(NativeExit.calibratedIterations() >= NativeExit.iterationFloor)
    }

    @Test func persistedPayloadsHoldNeitherTheKeyNorTheCode() throws {
        let (st, code) = state()
        let key = Vector.load().k
        let e = JSONEncoder()
        e.dateEncodingStrategy = .secondsSince1970
        let json = try e.encode(st)
        let text = String(decoding: json, as: UTF8.self)
        #expect(json.range(of: key) == nil && !text.contains(key.base64EncodedString()))
        #expect(!text.contains(code) && !text.contains("\"key\""))
        let f = tmpFileStore()
        defer { cleanup(f) }
        try f.save(st)
        let onDisk = try Data(contentsOf: f.url)
        #expect(onDisk.range(of: key) == nil && !String(decoding: onDisk, as: UTF8.self).contains(key.base64EncodedString()))
        #expect(!String(decoding: onDisk, as: UTF8.self).contains(code))
    }

    @Test func aStateFromTheOlderBuildThatStoredTheKeyStaysLockedAndIsScrubbed() throws {
        let key = Vector.load().k
        let old = "{\"v\":1,\"lockId\":\"K7QM3XPA\",\"key\":\"\(key.base64EncodedString())\",\"created\":1800000000,\"wrong\":0,\"unclean\":false}"
        let f = tmpFileStore()
        defer { cleanup(f) }
        try FileManager.default.createDirectory(at: f.url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(old.utf8).write(to: f.url)
        let loaded = try #require(f.load())
        #expect(loaded.verifier == nil)
        let code = NativeExit.code(key: key, lockId: "K7QM3XPA")
        // No verifier and not a lost Keychain: nothing to guess against, so nothing is counted.
        #expect(att(code, f, t0) == .noVerifier)
        #expect(f.load()?.attempts == 0)
        let rewritten = try Data(contentsOf: f.url)
        #expect(rewritten.range(of: key) == nil && !String(decoding: rewritten, as: UTF8.self).contains(key.base64EncodedString()))
    }

    @Test func sealReproducesVector() throws {
        let s = Vector.load().sealed
        let blob = try NativeExit.seal(key: hex(s["k_hex"]!), lockId: s["lock_id"]!,
                                       serverPublic: hex(s["server_public_hex"]!),
                                       ephemeralPrivate: hex(s["ephemeral_private_hex"]!),
                                       nonce: hex(s["nonce_hex"]!))
        #expect(blob == s["blob_base64"]!)
        let k = try NativeExit.open(blob: blob, lockId: s["lock_id"]!, serverPrivate: hex(s["server_private_hex"]!))
        #expect(k == hex(s["k_hex"]!))
    }

    @Test func randomSealOpens() throws {
        let server = Curve25519.KeyAgreement.PrivateKey()
        let k = NativeExit.newKey()
        let blob = try NativeExit.seal(key: k, lockId: "AAAAAAAA", serverPublic: server.publicKey.rawRepresentation)
        #expect(try NativeExit.open(blob: blob, lockId: "AAAAAAAA", serverPrivate: server.rawRepresentation) == k)
    }

    @Test func tamperedBlobFails() {
        let v = Vector.load()
        let priv = hex(v.sealed["server_private_hex"]!)
        let blob = v.sealed["blob_base64"]!
        #expect(throws: (any Error).self) {
            try NativeExit.open(blob: blob, lockId: v.negative["wrong_lock_id"] as! String, serverPrivate: priv)
        }
        var raw = Data(base64Encoded: blob)!
        raw[v.negative["flipped_byte_index"] as! Int] ^= 1
        #expect(raw.base64EncodedString() == v.negative["flipped_blob_base64"] as! String)
        #expect(throws: (any Error).self) {
            try NativeExit.open(blob: raw.base64EncodedString(), lockId: "K7QM3XPA", serverPrivate: priv)
        }
    }

    @Test func lockIdShape() {
        var seen = Set<String>()
        for _ in 0..<200 {
            let id = NativeExit.newLockId()
            #expect(id.count == 8)
            #expect(id.allSatisfy { NativeExit.alphabet.contains($0) })
            seen.insert(id)
        }
        #expect(seen.count == 200)
        #expect(NativeExit.alphabet.count == 31)
        #expect(NativeExit.newKey().count == 32)
    }

    @Test func displayAndParse() {
        #expect(NativeExit.display("K7QM3XPA") == "K7QM-3XPA")
        #expect(NativeExit.parseLockId("K7QM-3XPA") == "K7QM3XPA")
        #expect(NativeExit.parseLockId("k7qm3xpa") == "K7QM3XPA")
        #expect(NativeExit.parseLockId("K7QM-3XP") == nil)
        #expect(NativeExit.parseLockId("K7QM-3XPI") == nil)
        #expect(NativeExit.parseLockId("K7QM-3XP0") == nil)
    }

    @Test func escrowBodyShape() throws {
        let body = try NativeExit.escrowBody(lockId: "K7QM3XPA", blob: "b", device: "dev-1")
        let obj = try JSONSerialization.jsonObject(with: body) as! [String: String]
        #expect(Set(obj.keys) == ["lock_id", "blob", "device", "signature"])
        #expect(obj["signature"] == AppSession.sign("nativelock|K7QM3XPA|dev-1"))
        #expect(obj["signature"]!.count == 64)
    }

    @Test func correctCodeAccepted() {
        let (s, good) = store()
        #expect(att(good, s, t0) == .accepted)
    }

    @Test func malformedDoesNotCount() {
        let (s, good) = store()
        for bad in ["", "12345", "1234567", "12345a", "١٢٣٤٥٦", " 12345", good + " "] {
            #expect(att(bad, s, t0) == .malformed)
        }
        #expect(s.load()?.wrong == 0)
    }

    @Test func fiveWrongThenWait() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for i in 1...4 {
            #expect(att(bad, s, t0) == .wrong(remaining: 5 - i))
        }
        let until = t0.addingTimeInterval(300)
        #expect(att(bad, s, t0) == .wait(until: until))
        #expect(att(good, s, t0.addingTimeInterval(100)) == .wait(until: until))
        #expect(att(good, s, t0.addingTimeInterval(301)) == .accepted)
        #expect(s.load()?.until == nil)
        #expect(s.load()?.wrong == 0)
    }

    @Test func counterAndWaitSurviveReload() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for _ in 1...3 { _ = att(bad, s, t0) }
        let relaunched = InMemoryNativeLockStore(s.load())
        #expect(relaunched.load()?.wrong == 3)
        #expect(att(bad, relaunched, t0) == .wrong(remaining: 1))
        _ = att(bad, relaunched, t0)
        let again = InMemoryNativeLockStore(relaunched.load())
        #expect(att(good, again, t0.addingTimeInterval(10))
                == .wait(until: t0.addingTimeInterval(300)))
    }

    @Test func persistedBeforeCompare() {
        var (st, good) = state()
        st.wrong = 0
        let f = FailingStore(st)
        f.failSaves = true
        let r = att(good, f, t0)
        #expect(r != .accepted)
        #expect(f.recorded.first?.wrong == 1)
        #expect(f.load()?.wrong == 1)
    }

    @Test func clockBackwardsExtendsWait() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for _ in 1...5 { _ = att(bad, s, t0) }
        let back = t0.addingTimeInterval(-1000)
        let r = att(good, s, back)
        #expect(r == .wait(until: t0.addingTimeInterval(1300)))
        #expect(att(good, s, t0.addingTimeInterval(400)) == .wait(until: t0.addingTimeInterval(1300)))
    }

    private func attWith(_ typed: String, _ store: NativeLockStoring, wall: Date, boot: String = "boot-1", mono: TimeInterval) -> NativeExitAttempt {
        NativeExit.attempt(typed: typed, store: store, now: wall, monotonic: { .init(boot: boot, seconds: mono) })
    }

    @Test func movingTheWallClockForwardCannotEndAWait() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for _ in 1...5 { _ = attWith(bad, s, wall: t0, mono: 1_000) }
        let jumped = attWith(good, s, wall: t0.addingTimeInterval(86_400), mono: 1_010)
        #expect(jumped == .wait(until: t0.addingTimeInterval(86_400 + 290)))
        #expect(attWith(good, s, wall: t0.addingTimeInterval(86_400 + 100), mono: 1_100) != .accepted)
        #expect(attWith(good, s, wall: t0.addingTimeInterval(86_400), mono: 1_301) == .accepted)
    }

    @Test func aRebootRestartsTheWaitInFullWhateverTheWallClockSays() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for _ in 1...5 { _ = attWith(bad, s, wall: t0, mono: 1_000) }
        let after = t0.addingTimeInterval(86_400)
        #expect(attWith(good, s, wall: after, boot: "boot-2", mono: 5) == .wait(until: after.addingTimeInterval(300)))
        #expect(s.load()?.waitBoot == "boot-2")
        #expect(attWith(good, s, wall: after.addingTimeInterval(299), boot: "boot-2", mono: 304) != .accepted)
        #expect(attWith(good, s, wall: after.addingTimeInterval(301), boot: "boot-2", mono: 306) == .accepted)
    }

    @Test func aStateWithoutAnAnchorIsTreatedLikeAReboot() {
        let (s, good) = store()
        var st = s.load()!
        st.until = t0.addingTimeInterval(10)
        st.lastAttempt = t0
        try? s.save(st)
        #expect(attWith(good, s, wall: t0.addingTimeInterval(1_000), mono: 50) == .wait(until: t0.addingTimeInterval(1_300)))
    }

    @Test func theTotalCapRefusesWhileTheHourIsFullAndChecksAgainOnceItFreesUp() {
        let (s, good) = store()
        let bad = wrongCode(good)
        var wall = t0
        var mono: TimeInterval = 1_000
        for _ in 1...NativeExit.maxTotalAttempts {
            let r = attWith(bad, s, wall: wall, mono: mono)
            #expect(r != .accepted && r != .refused)
            if case .wait = r { wall = wall.addingTimeInterval(301); mono += 301 }
        }
        #expect(s.load()?.attempts == NativeExit.maxTotalAttempts)
        #expect(attWith(good, s, wall: wall, mono: mono) == .refused)
        #expect(attWith(good, s, wall: wall.addingTimeInterval(1_800), mono: mono + 1_800) == .refused)
        #expect(attWith(good, s, wall: t0.addingTimeInterval(3_599), mono: 1_000 + 3_599) == .refused)
        #expect(attWith(good, s, wall: wall.addingTimeInterval(86_400), mono: mono + 86_400) == .accepted)
    }

    @Test func theGuessLimitRollsInsteadOfResettingAtAFixedPoint() {
        let (s, good) = store()
        let bad = wrongCode(good)
        var st = s.load()!
        st.attempts = 0
        st.guessLog = (0..<NativeExit.hourlyGuessCap).map {
            .init(at: t0.addingTimeInterval(Double($0) * 60), boot: "boot-1", mono: 1_000 + Double($0) * 60)
        }
        try? s.save(st)
        let freeAt = t0.addingTimeInterval(3_600)
        #expect(attWith(bad, s, wall: t0.addingTimeInterval(1_000), mono: 2_000) == .wait(until: freeAt))
        guard case .wrong = attWith(bad, s, wall: freeAt.addingTimeInterval(1), mono: 4_601) else { Issue.record("one slot should have freed"); return }
        #expect(s.load()?.guessLog.count == NativeExit.hourlyGuessCap)
        #expect(attWith(bad, s, wall: freeAt.addingTimeInterval(2), mono: 4_602) == .wait(until: t0.addingTimeInterval(3_660)))
    }

    @Test func mergingTheCopiesKeepsTheUnionOfTheNewestGuesses() {
        let (a, _) = state(), (b, _) = state()
        var x = a, y = b
        x.guessLog = (0..<10).map { .init(at: t0.addingTimeInterval(Double($0)), boot: "b", mono: Double($0)) }
        y.guessLog = (5..<20).map { .init(at: t0.addingTimeInterval(Double($0)), boot: "b", mono: Double($0)) }
        let m = DualNativeLockStore.merged(x, y)
        #expect(m.guessLog.count == NativeExit.hourlyGuessCap)
        #expect(m.guessLog.first?.at == t0.addingTimeInterval(5) && m.guessLog.last?.at == t0.addingTimeInterval(19))
    }

    @Test func aTicketReleasesOnlyWhileTheSameLockIsStillSaved() {
        let (s, good) = store()
        let (result, ticket) = NativeExit.attemptTicketed(typed: good, store: s, now: t0, monotonic: { .init(boot: "b", seconds: 1) })
        #expect(result == .accepted && ticket != nil)
        let (replacement, _) = state(lockId: "ZZZZZZZZ")
        try? s.save(replacement)
        var released = false
        #expect(!NativeExit.releaseIfCurrent(ticket, store: s) { released = true })
        #expect(!released && s.load() == replacement)
        let (again, _) = store()
        let (_, fresh) = NativeExit.attemptTicketed(typed: good, store: again, now: t0, monotonic: { .init(boot: "b", seconds: 1) })
        #expect(NativeExit.releaseIfCurrent(fresh, store: again) { released = true } && released)
        #expect(!NativeExit.releaseIfCurrent(nil, store: again) { released = false } && released)
    }

    @Test func totalAttemptsSurviveAWaitExpiryAndAreNeverReset() {
        let (s, good) = store()
        let bad = wrongCode(good)
        for _ in 1...5 { _ = attWith(bad, s, wall: t0, mono: 1_000) }
        _ = attWith(bad, s, wall: t0.addingTimeInterval(400), mono: 1_400)
        #expect(s.load()?.wrong == 1 && s.load()?.attempts == 6)
        #expect(attWith(good, s, wall: t0.addingTimeInterval(401), mono: 1_401) == .accepted)
        #expect(s.load()?.attempts == 7)
    }

    @Test func nativeStoreNamesDifferBetweenDevelopmentAndProduction() {
        #expect(KeychainNativeLockStore.service(dev: true, isolated: false) != KeychainNativeLockStore.service(dev: false, isolated: false))
        #expect(KeychainNativeLockStore.service(dev: false, isolated: false) == KeychainNativeLockStore.productionService)
        #expect(FileNativeLockStore.defaultURL(dev: true, isolated: false) != FileNativeLockStore.defaultURL(dev: false, isolated: false))
        #expect(FileNativeLockStore.defaultURL(dev: true, isolated: false).path.contains("Proctora-Dev"))
        #expect(FileNativeLockStore.defaultURL(dev: false, isolated: false).path.hasSuffix("Proctora/native-lock.json"))
        #expect(KeychainNativeLockStore().service == KeychainNativeLockStore.service(dev: Config.isDevBuild))
    }

    @Test func isolatedTestBuildNeverSharesTheDevelopmentLockStore() {
        #expect(KeychainNativeLockStore.service(dev: true, isolated: true) != KeychainNativeLockStore.service(dev: true, isolated: false))
        #expect(KeychainNativeLockStore.service(dev: true, isolated: true) != KeychainNativeLockStore.productionService)
        #expect(FileNativeLockStore.defaultURL(dev: true, isolated: true) != FileNativeLockStore.defaultURL(dev: true, isolated: false))
        #expect(FileNativeLockStore.defaultURL(dev: true, isolated: true).path.contains("Proctora-Dev-Isolated"))
    }

    @Test func leaveSheetSaysWhenNoTypedCodeCanOpenTheLock() {
        let (withVerifier, _) = state()
        #expect(NativeExit.leaveCodeText(withVerifier) == NativeExit.display(withVerifier.lockId))
        let relocked = NativeLockState(lockId: "K7QM3XPA", created: Date(), unclean: true)
        #expect(NativeExit.leaveCodeText(relocked).hasPrefix("K7QM-3XPA"))
        #expect(NativeExit.leaveCodeText(relocked).contains("release this Mac"))
        #expect(NativeExit.leaveCodeText(nil) == "")
    }

    @Test func keychainRoundTrip() throws {
        let store = KeychainNativeLockStore(service: "ai.proctora.student.native-lock.test-\(UUID().uuidString)")
        defer { store.delete() }
        var st = NativeLockState(lockId: "K7QM3XPA", verifier: state().0.verifier, created: Date(timeIntervalSince1970: 1_800_000_000),
                                 contest: "c1", binding: .init(session: "s", device: "d", user: "u", contest: "c1"))
        try store.save(st)
        #expect(store.load() == st)
        for i in 1...50 {
            st.wrong = i % 5
            try store.save(st)
            #expect(store.load()?.wrong == i % 5)
        }
        store.delete()
        #expect(store.load() == nil)
    }

    private func tmpFileStore() -> FileNativeLockStore {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("fp-nl-\(UUID().uuidString)")
        return FileNativeLockStore(url: dir.appendingPathComponent("Proctora/native-lock.json"))
    }

    private func cleanup(_ f: FileNativeLockStore) {
        f.delete()
        try? FileManager.default.removeItem(at: f.url.deletingLastPathComponent().deletingLastPathComponent())
    }

    private func base() -> NativeLockState {
        state().0
    }

    @Test func fileStoreRoundTripAndPermissions() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        var st = base()
        st.wrong = 3
        try f.save(st)
        #expect(f.load() == st.withoutVerifier)
        let attrs = try FileManager.default.attributesOfItem(atPath: f.url.path)
        #expect((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        try f.save(st)
        #expect(try FileManager.default.contentsOfDirectory(atPath: f.url.deletingLastPathComponent().path).count == 1)
        f.delete()
        #expect(f.load() == nil)
    }

    private func dualStore(_ k: NativeLockStoring, _ f: NativeLockStoring, at now: Date? = nil) -> DualNativeLockStore {
        let at = now ?? t0
        return DualNativeLockStore(primary: k, secondary: f, now: { at },
                                   monotonic: { .init(boot: "boot-1", seconds: 1_000 + at.timeIntervalSince(self.t0)) })
    }

    @Test func dualRestoresFromEitherCopy() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = dualStore(k, f)
        var st = base()
        st.wrong = 2
        try dual.save(st)
        f.delete()
        #expect(dual.load() == st)
        #expect(f.load() == st.withoutVerifier)
        try Data("garbage".utf8).write(to: f.url)
        #expect(dual.load() == st)
        #expect(f.load() == st.withoutVerifier)
    }

    @Test func aMissingKeychainCopyWithAFileCopyIsTamperingAndStartsTheWait() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = dualStore(k, f, at: t0.addingTimeInterval(20))
        var st = base()
        st.wrong = 2
        try dual.save(st)
        k.delete()
        let loaded = try #require(dual.load())
        #expect(loaded.wrong == 0)
        #expect(loaded.until == t0.addingTimeInterval(320))
        #expect(loaded.attempts == NativeExit.maxWrong)
        #expect(loaded.verifier == nil && loaded.keychainLost)
        #expect(k.load() == nil && f.load() == loaded)
        #expect(att(state().1, dual, t0.addingTimeInterval(21)) == .wait(until: t0.addingTimeInterval(320)))
        for _ in 0..<3 { _ = dual.load() }
        #expect(dual.load()?.attempts == NativeExit.maxWrong)
        let good = state().1
        #expect(att(good, dual, t0.addingTimeInterval(1_000)) != .accepted)
        #expect(k.load() == nil)
    }

    @Test func withoutTheKeychainCopyNoTypedCodeEverMatches() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = dualStore(k, f)
        let good = state().1
        try dual.save(base())
        k.delete()
        var now = t0
        for _ in 0..<6 {
            now = now.addingTimeInterval(NativeExit.waitSeconds + 1)
            #expect(att(good, dual, now) != .accepted)
        }
        #expect(f.load()?.verifier == nil)
    }

    @Test func fileBytesNeverHoldTheVerifier() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        var st = base()
        st.contest = "exam"
        st.binding = .init(session: "s1", device: "d", user: "7", contest: "exam")
        st.wrong = 3
        try f.save(st)
        let text = String(decoding: try Data(contentsOf: f.url), as: UTF8.self)
        for word in ["verifier", "salt", "hash", "iterations"] { #expect(!text.contains(word), "\(word)") }
        let dual = dualStore(InMemoryNativeLockStore(), f)
        try dual.save(st)
        let again = String(decoding: try Data(contentsOf: f.url), as: UTF8.self)
        for word in ["verifier", "salt", "hash"] { #expect(!again.contains(word), "\(word)") }
    }

    @Test func countersAndBindingRestoreFromTheFileWhenTheKeychainIsGone() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = dualStore(k, f)
        var st = base()
        st.contest = "exam"
        st.binding = .init(session: "s1", device: "d", user: "7", contest: "exam")
        st.attempts = 4
        try dual.save(st)
        k.delete()
        let loaded = try #require(dual.load())
        #expect(loaded.contest == "exam" && loaded.binding == st.binding && loaded.lockId == st.lockId)
        #expect(loaded.attempts == 4 + NativeExit.maxWrong)
    }

    @Test func anOldFileWithAVerifierIsStrippedOnLoad() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let e = JSONEncoder()
        e.dateEncodingStrategy = .secondsSince1970
        try FileManager.default.createDirectory(at: f.url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try e.encode(base()).write(to: f.url)
        #expect(String(decoding: try Data(contentsOf: f.url), as: UTF8.self).contains("verifier"))
        #expect(f.load()?.verifier == nil)
        #expect(!String(decoding: try Data(contentsOf: f.url), as: UTF8.self).contains("salt"))
    }

    @Test func theKeychainItemIsNeverSynchronisedAndOnlyUsesTheDataProtectionKeychainWhenAllowed() {
        let legacy = KeychainNativeLockStore(service: "x", dataProtection: false).query(dataProtection: false)
        #expect(legacy[kSecAttrSynchronizable as String] as? Bool == false)
        #expect(legacy[kSecUseDataProtectionKeychain as String] == nil)
        #expect(legacy[kSecAttrAccess as String] == nil)
        let dp = KeychainNativeLockStore(service: "x", dataProtection: true).query(dataProtection: true)
        #expect(dp[kSecUseDataProtectionKeychain as String] as? Bool == true)
        #expect(!KeychainNativeLockStore.signedWithKeychainEntitlement)
    }

    @Test func deletingOneCopyCannotResetCounter() {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore(base())
        let dual = dualStore(k, f)
        let good = state().1
        let bad = wrongCode(good)
        for _ in 1...3 { _ = att(bad, dual, t0) }
        f.delete()
        #expect(att(bad, dual, t0) == .wrong(remaining: 1))
        k.delete()
        #expect(att(bad, dual, t0) == .wait(until: t0.addingTimeInterval(300)))
        f.delete()
        #expect(dual.load() == nil || dual.load()?.until != nil)
    }

    @Test func deletingFileDuringWaitKeepsWait() {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore(base())
        let dual = dualStore(k, f)
        let good = state().1
        for _ in 1...5 { _ = att(wrongCode(good), dual, t0) }
        f.delete()
        #expect(att(good, dual, t0.addingTimeInterval(5)) == .wait(until: t0.addingTimeInterval(300)))
        k.delete()
        let later = dualStore(k, f, at: t0.addingTimeInterval(6))
        #expect(att(good, later, t0.addingTimeInterval(6)) == .wait(until: t0.addingTimeInterval(306)))
    }

    @Test func tamperedFileCannotLoosenKeychainCopy() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = DualNativeLockStore(primary: k, secondary: f)
        var st = base()
        st.wrong = 4
        st.until = t0.addingTimeInterval(300)
        st.lastAttempt = t0
        st.unclean = true
        try dual.save(st)
        var loose = base()
        loose.wrong = 0
        try f.save(loose)
        #expect(dual.load() == st)
        #expect(f.load() == st.withoutVerifier)
        var loosenKeychain = base()
        loosenKeychain.wrong = 0
        try k.save(loosenKeychain)
        try f.save(st)
        #expect(dual.load() == st)
    }

    @Test func mergeNeverLoosens() {
        var a = base(), b = base()
        a.wrong = 3; b.wrong = 1
        a.until = t0.addingTimeInterval(10); b.until = t0.addingTimeInterval(50)
        a.lastAttempt = t0.addingTimeInterval(7); b.lastAttempt = t0.addingTimeInterval(2)
        b.unclean = true
        for m in [DualNativeLockStore.merged(a, b), DualNativeLockStore.merged(b, a)] {
            #expect(m.wrong == 3)
            #expect(m.until == t0.addingTimeInterval(50))
            #expect(m.lastAttempt == t0.addingTimeInterval(7))
            #expect(m.unclean)
        }
        var c = base()
        c.until = nil
        #expect(DualNativeLockStore.merged(c, a).until == a.until)
        #expect(DualNativeLockStore.merged(a, c).until == a.until)
    }

    @Test func dualSaveFailsOnlyIfBothFail() throws {
        let a = FailingStore(base()), b = FailingStore(base())
        let dual = DualNativeLockStore(primary: a, secondary: b)
        a.failSaves = true
        try dual.save(base())
        b.failSaves = true
        #expect(throws: (any Error).self) { try dual.save(base()) }
    }

    @Test func dualDeleteRemovesBoth() throws {
        let f = tmpFileStore()
        defer { cleanup(f) }
        let k = InMemoryNativeLockStore()
        let dual = DualNativeLockStore(primary: k, secondary: f)
        try dual.save(base())
        dual.delete()
        #expect(k.load() == nil)
        #expect(f.load() == nil)
        #expect(dual.load() == nil)
    }

    @Test func dualWithRealKeychainAndFile() throws {
        let f = tmpFileStore()
        let k = KeychainNativeLockStore(service: "ai.proctora.student.native-lock.test-\(UUID().uuidString)")
        defer { cleanup(f); k.delete() }
        let dual = DualNativeLockStore(primary: k, secondary: f)
        var st = base()
        st.wrong = 2
        try dual.save(st)
        f.delete()
        #expect(dual.load() == st)
        #expect(f.load() == st.withoutVerifier)
    }
}

@Suite struct NativeLockGateTests {
    private final class SlowStore: NativeLockStoring {
        var state: NativeLockState?
        func load() -> NativeLockState? { state }
        func save(_ s: NativeLockState) throws { state = s }
        func delete() { state = nil }
    }

    @Test func concurrentWritersNeverLoseAnUpdate() async {
        let store = SlowStore()
        store.state = NativeLockState(lockId: "K7QM3XPA", created: Date(timeIntervalSince1970: 1_800_000_000))
        nonisolated(unsafe) let shared = store
        let setup = NativeLockSetup.dev.with(store: store)
        nonisolated(unsafe) let gated = setup
        await withTaskGroup(of: Void.self) { group in
            for i in 0..<200 {
                group.addTask {
                    gated.persist(contest: "c\(i % 2)", binding: nil)
                    NativeLockGate.run {
                        var s = shared.load()!
                        s.attempts += 1
                        try? shared.save(s)
                    }
                    gated.markRecovered()
                }
            }
        }
        #expect(store.state?.attempts == 200)
    }

    @Test func attemptsPersistBeforeTheCompareEvenWhenAnotherWriterInterleaves() throws {
        let key = Data(repeating: 9, count: 32)
        let lockId = "K7QM3XPA"
        let code = NativeExit.code(key: key, lockId: lockId)
        let wrong = code == "111111" ? "222222" : "111111"
        let store = SlowStore()
        store.state = NativeLockState(lockId: lockId, verifier: NativeExit.makeVerifier(code: code, lockId: lockId, iterations: 1000),
                                      created: Date(timeIntervalSince1970: 1_800_000_000))
        let now = Date(timeIntervalSince1970: 1_800_000_100)
        let mono = NativeExit.Monotonic(boot: "b", seconds: 5_000)
        #expect(NativeExit.attempt(typed: wrong, store: store, now: now, monotonic: { mono }) == .wrong(remaining: 4))
        #expect(store.state?.attempts == 1)
        let setup = NativeLockSetup.dev.with(store: store)
        setup.persist(contest: "exam", binding: nil)
        #expect(NativeExit.attempt(typed: code, store: store, now: now, monotonic: { mono }) == .accepted)
        #expect(store.state?.contest == "exam" && store.state?.attempts == 2 && store.state?.wrong == 0)
    }
}

@Suite struct KeychainHardeningTests {
    private final class FakeKeychain {
        var items: [Bool: Data] = [:]
        var readFailure: [Bool: Bool] = [:]
        var addStatus: OSStatus = errSecSuccess
        var deletes: [Bool] = []
        func isDP(_ q: [String: Any]) -> Bool { (q[kSecUseDataProtectionKeychain as String] as? Bool) == true }
        func install(in store: inout KeychainNativeLockStore) {
            store.readItem = { [self] q in
                let dp = isDP(q)
                if readFailure[dp] == true { return .failed }
                guard let data = items[dp] else { return .notFound }
                let d = JSONDecoder(); d.dateDecodingStrategy = .secondsSince1970
                return (try? d.decode(NativeLockState.self, from: data)).map { .found($0) } ?? .failed
            }
            store.itemDelete = { [self] q in deletes.append(isDP(q)); items[isDP(q)] = nil }
            store.itemAdd = { [self] q in
                guard addStatus == errSecSuccess else { return addStatus }
                if items[isDP(q)] != nil { return errSecDuplicateItem }
                items[isDP(q)] = q[kSecValueData as String] as? Data
                return errSecSuccess
            }
        }
        func put(_ state: NativeLockState, dp: Bool) {
            let e = JSONEncoder(); e.dateEncodingStrategy = .secondsSince1970
            items[dp] = try? e.encode(state)
        }
    }
    private func state(_ id: String = "K7QM3XPA", attempts: Int = 0) -> NativeLockState {
        NativeLockState(lockId: id, created: Date(timeIntervalSince1970: 1_800_000_000), attempts: attempts)
    }
    private func store(_ k: FakeKeychain, dp: Bool) -> KeychainNativeLockStore {
        var s = KeychainNativeLockStore(service: "t", dataProtection: dp)
        k.install(in: &s)
        return s
    }

    @Test func saveDeletesAnyExistingItemFirst() throws {
        let k = FakeKeychain()
        k.put(state("PLANTED22"), dp: false)
        let s = store(k, dp: false)
        try s.save(state())
        #expect(k.deletes.first == false)
        #expect(s.load()?.lockId == "K7QM3XPA")
    }

    @Test func aFailedAddAfterTheDeleteSurfacesTheError() {
        let k = FakeKeychain()
        k.put(state(), dp: false)
        k.addStatus = errSecInteractionNotAllowed
        let s = store(k, dp: false)
        #expect(throws: KeychainNativeLockStore.Failure.self) { try s.save(state()) }
        #expect(s.load() == nil)
    }

    @Test func failedKeychainAddLeavesTheFileCopyAndNoVerifier() throws {
        let k = FakeKeychain()
        k.addStatus = errSecInteractionNotAllowed
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let file = FileNativeLockStore(url: dir.appendingPathComponent("native-lock.json"))
        let dual = DualNativeLockStore(primary: store(k, dp: false), secondary: file)
        var st = state(attempts: 2)
        st.verifier = NativeExit.makeVerifier(code: "123456", lockId: st.lockId, iterations: 1000)
        try dual.save(st)
        let loaded = try #require(dual.load())
        #expect(loaded.attempts >= 2 && loaded.verifier == nil)
    }

    @Test func legacyItemIsUsedOnlyWhenTheProtectedOneIsDefinitelyMissing() {
        let k = FakeKeychain()
        k.put(state("LEGACY222", attempts: 1), dp: false)
        let s = store(k, dp: true)
        #expect(s.load()?.lockId == "LEGACY222")
        #expect(k.items[true] != nil && k.items[false] == nil)
    }

    @Test func aReadErrorOnTheProtectedItemNeverFallsBackToLegacy() {
        let k = FakeKeychain()
        k.put(state("LEGACY222"), dp: false)
        k.put(state("PROTECTED2"), dp: true)
        k.readFailure[true] = true
        let s = store(k, dp: true)
        #expect(s.load() == nil)
        #expect(k.items[false] != nil)
    }

    @Test func aMigratedLegacyItemNeverOverwritesAProtectedOne() {
        let k = FakeKeychain()
        k.put(state("LEGACY222"), dp: false)
        k.put(state("PROTECTED2"), dp: true)
        let s = store(k, dp: true)
        #expect(s.load()?.lockId == "PROTECTED2")
        k.items[true] = nil
        k.readFailure[true] = false
        #expect(s.load()?.lockId == "LEGACY222")
    }

    @Test func requiringTheKeychainEntitlementBlocksOnlyWhenMissing() {
        let C = ProtectedBrowserController.self
        let msg = "This copy of FairPlay is not set up for secure exams. Contact your instructor. Your Mac was not locked."
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: true, isDevBuild: false,
                               requireDataProtection: true, hasKeychainEntitlement: false) == msg)
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: true, isDevBuild: false,
                               requireDataProtection: true, hasKeychainEntitlement: true) == nil)
        #expect(C.startBlocker(customSetup: false, policyKeyPresent: true, isDevBuild: false,
                               requireDataProtection: false, hasKeychainEntitlement: false) == nil)
        #expect(!Config.requiresDataProtectionKeychain)
    }

    @Test func rightCodeResetNeverResurrectsAClearedLock() {
        let key = Data(repeating: 9, count: 32)
        let lockId = "K7QM3XPA"
        let code = NativeExit.code(key: key, lockId: lockId)
        let store = InMemoryNativeLockStore(NativeLockState(
            lockId: lockId, verifier: NativeExit.makeVerifier(code: code, lockId: lockId, iterations: 1000),
            created: Date(timeIntervalSince1970: 1_800_000_000)))
        let now = Date(timeIntervalSince1970: 1_800_000_100)
        struct ClearingStore: NativeLockStoring {
            let inner: InMemoryNativeLockStore
            let clearBeforeSecondLoad: () -> Bool
            func load() -> NativeLockState? { clearBeforeSecondLoad() ? nil : inner.load() }
            func save(_ s: NativeLockState) throws { try inner.save(s) }
            func delete() { inner.delete() }
        }
        var loads = 0
        let racing = ClearingStore(inner: store, clearBeforeSecondLoad: { loads += 1; return loads > 1 })
        _ = NativeExit.attempt(typed: code, store: racing, now: now, monotonic: { .init(boot: "b", seconds: 1) })
        let counted = store.load()
        #expect(counted?.attempts == 1 && counted?.wrong == 1)
        store.delete()
        loads = 0
        let after = ClearingStore(inner: store, clearBeforeSecondLoad: { false })
        #expect(NativeExit.attempt(typed: code, store: after, now: now, monotonic: { .init(boot: "b", seconds: 1) }) == .malformed)
        #expect(store.load() == nil)
    }
}


@Suite struct NativeExitStaleAcceptTests {
    private final class SwappingStore: NativeLockStoring {
        var loads = 0
        let first: NativeLockState, later: NativeLockState
        var saved: [NativeLockState] = []
        init(first: NativeLockState, later: NativeLockState) { self.first = first; self.later = later }
        func load() -> NativeLockState? { loads += 1; return loads <= 1 ? first : later }
        func save(_ state: NativeLockState) throws { saved.append(state) }
        func delete() {}
    }

    @Test func aRightCodeForALockThatWasReplacedMeanwhileIsNotAccepted() {
        let t = Date(timeIntervalSince1970: 1_000)
        let old = NativeLockState(lockId: "K7QM3XPA", verifier: NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPA", iterations: 2_000), created: t)
        let new = NativeLockState(lockId: "K7QM3XPB", verifier: NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPB", iterations: 2_000), created: t)
        let store = SwappingStore(first: old, later: new)
        let r = NativeExit.attempt(typed: "123456", store: store, now: t, monotonic: { .init(boot: "b", seconds: 1) })
        #expect(r == .stale)
        #expect(store.saved.allSatisfy { $0.lockId == "K7QM3XPA" })
    }

    @Test func aRightCodeWhoseLockWasClearedMeanwhileIsNotAccepted() {
        let t = Date(timeIntervalSince1970: 1_000)
        let old = NativeLockState(lockId: "K7QM3XPA", verifier: NativeExit.makeVerifier(code: "123456", lockId: "K7QM3XPA", iterations: 2_000), created: t)
        final class Vanishing: NativeLockStoring {
            var loads = 0; let s: NativeLockState
            init(_ s: NativeLockState) { self.s = s }
            func load() -> NativeLockState? { loads += 1; return loads <= 1 ? s : nil }
            func save(_ state: NativeLockState) throws {}
            func delete() {}
        }
        #expect(NativeExit.attempt(typed: "123456", store: Vanishing(old), now: t, monotonic: { .init(boot: "b", seconds: 1) }) == .stale)
    }
}
