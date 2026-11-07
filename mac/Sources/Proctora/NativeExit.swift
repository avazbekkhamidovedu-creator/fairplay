import Foundation
import CryptoKit
import Security
import CommonCrypto

enum NativeExitAttempt: Equatable {
    case accepted
    case wrong(remaining: Int)
    case wait(until: Date)
    case malformed
    /// The total attempt cap is used up: only a signed release from the OJ ends this lock.
    case refused
}

struct NativeLockState: Codable, Equatable {
    struct Binding: Codable, Equatable {
        var session: String
        var device: String
        var user: String
        var contest: String
    }

    /// Slow PBKDF2 check of the 6-digit code. The exit key itself is never stored on the Mac.
    struct Verifier: Codable, Equatable {
        var salt: Data
        var iterations: Int
        var hash: Data
    }

    var v: Int = 1
    var lockId: String
    /// nil only for a state written by an older build that stored the key: nobody can type a
    /// code that opens it, so the lock ends only with a signed release from the OJ.
    var verifier: Verifier? = nil
    var created: Date
    var contest: String? = nil
    var binding: Binding? = nil
    var wrong: Int = 0
    var until: Date? = nil
    var unclean: Bool = false
    var lastAttempt: Date? = nil
    /// Counted attempts over the whole lock (never reset by a wait or a right answer path).
    var attempts: Int = 0
    /// Boot session and continuous-clock second at which `until` ends, so the wall clock alone cannot end a wait.
    var waitBoot: String? = nil
    var waitMono: TimeInterval? = nil
    /// The Keychain copy (the only place the verifier lives) was missing when this lock was loaded.
    /// Nothing typed can match then; the penalty is applied once.
    var keychainLost: Bool = false
    /// Seconds of VPN front time used in this lock, so a relaunch does not renew the cap.
    var vpnUsed: TimeInterval = 0

    /// What may be written outside the Keychain: counters and binding, never the verifier.
    var withoutVerifier: NativeLockState {
        var copy = self
        copy.verifier = nil
        return copy
    }
}

extension NativeLockState {
    private enum CodingKeys: String, CodingKey {
        case v, lockId, verifier, created, contest, binding, wrong, until, unclean, lastAttempt, attempts, waitBoot, waitMono, keychainLost, vpnUsed
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        v = try c.decodeIfPresent(Int.self, forKey: .v) ?? 1
        lockId = try c.decode(String.self, forKey: .lockId)
        verifier = try c.decodeIfPresent(Verifier.self, forKey: .verifier)
        created = try c.decode(Date.self, forKey: .created)
        contest = try c.decodeIfPresent(String.self, forKey: .contest)
        binding = try c.decodeIfPresent(Binding.self, forKey: .binding)
        wrong = try c.decodeIfPresent(Int.self, forKey: .wrong) ?? 0
        until = try c.decodeIfPresent(Date.self, forKey: .until)
        unclean = try c.decodeIfPresent(Bool.self, forKey: .unclean) ?? false
        lastAttempt = try c.decodeIfPresent(Date.self, forKey: .lastAttempt)
        attempts = try c.decodeIfPresent(Int.self, forKey: .attempts) ?? 0
        waitBoot = try c.decodeIfPresent(String.self, forKey: .waitBoot)
        waitMono = try c.decodeIfPresent(TimeInterval.self, forKey: .waitMono)
        keychainLost = try c.decodeIfPresent(Bool.self, forKey: .keychainLost) ?? false
        vpnUsed = try c.decodeIfPresent(TimeInterval.self, forKey: .vpnUsed) ?? 0
    }
}

/// One lock for every read-modify-write of the saved lock, so no write is lost between threads.
enum NativeLockGate {
    private static let lock = NSRecursiveLock()
    static func run<T>(_ body: () throws -> T) rethrows -> T {
        lock.lock(); defer { lock.unlock() }
        return try body()
    }
}

protocol NativeLockStoring {
    func load() -> NativeLockState?
    func save(_ state: NativeLockState) throws
    func delete()
}

final class InMemoryNativeLockStore: NativeLockStoring {
    private(set) var state: NativeLockState?
    init(_ state: NativeLockState? = nil) { self.state = state }
    func load() -> NativeLockState? { state }
    func save(_ state: NativeLockState) throws { self.state = state }
    func delete() { state = nil }
}

struct KeychainNativeLockStore: NativeLockStoring {
    static let productionService = "ai.proctora.student.native-lock"
    static let developmentService = "ai.proctora.student.native-lock.dev"

    /// A development build never reads, writes or clears the production item.
    static func service(dev: Bool) -> String { dev ? developmentService : productionService }
    struct Failure: Error, Equatable { let status: OSStatus }

    let service: String
    private let account = "lock"
    private static let retries = 5
    private static let retryDelay: TimeInterval = 0.03

    /// The data-protection keychain needs a signed build with an application identifier or keychain
    /// group entitlement; unsigned and test builds use the legacy file keychain.
    let dataProtection: Bool

    static var signedWithKeychainEntitlement: Bool {
        guard let task = SecTaskCreateFromSelf(nil) else { return false }
        for name in ["com.apple.application-identifier", "keychain-access-groups"] {
            if SecTaskCopyValueForEntitlement(task, name as CFString, nil) != nil { return true }
        }
        return false
    }

    init(service: String = KeychainNativeLockStore.service(dev: Config.isDevBuild),
         dataProtection: Bool = KeychainNativeLockStore.signedWithKeychainEntitlement) {
        self.service = service
        self.dataProtection = dataProtection
    }

    /// Never synchronised, no custom access list (only the creating app), data-protection keychain when allowed.
    func query(dataProtection: Bool) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                kSecAttrService as String: service,
                                kSecAttrAccount as String: account,
                                kSecAttrSynchronizable as String: false]
        if dataProtection { q[kSecUseDataProtectionKeychain as String] = true }
        return q
    }

    private var query: [String: Any] { query(dataProtection: dataProtection) }

    // The login keychain can report errSecItemNotFound for a moment after a successful
    // add or update while other threads of the same process touch the keychain, so a
    // missing item is retried briefly before it is believed.
    /// Replaceable in tests to simulate a failed add.
    var itemAdd: ([String: Any]) -> OSStatus = { SecItemAdd($0 as CFDictionary, nil) }
    var itemDelete: ([String: Any]) -> Void = { SecItemDelete($0 as CFDictionary) }
    var readItem: (([String: Any]) -> Read)? = nil

    enum Read { case found(NativeLockState), notFound, failed }

    func load() -> NativeLockState? {
        NativeLockGate.run {
            switch read(query) {
            case .found(let state): return state
            case .failed: return nil
            case .notFound: break
            }
            // Only a definite "not found" in the data-protection keychain looks at the legacy item an
            // earlier build wrote; it is moved once and never replaces an existing protected item.
            guard dataProtection, case .found(let old) = read(query(dataProtection: false)) else { return nil }
            if case .notFound = read(query) {
                if (try? save(old)) != nil { itemDelete(query(dataProtection: false)) }
            }
            return old
        }
    }

    func read(_ base: [String: Any]) -> Read {
        if let readItem { return readItem(base) }
        var q = base
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        for attempt in 0..<Self.retries {
            var out: CFTypeRef?
            let status = SecItemCopyMatching(q as CFDictionary, &out)
            if status == errSecSuccess {
                guard let data = out as? Data, let state = try? Self.decoder.decode(NativeLockState.self, from: data) else { return .failed }
                return .found(state)
            }
            if status != errSecItemNotFound { return .failed }
            if attempt == Self.retries - 1 { return .notFound }
            Thread.sleep(forTimeInterval: Self.retryDelay)
        }
        return .notFound
    }

    /// Any existing item is deleted first so a pre-planted one with a wider access list cannot survive
    /// as the stored copy. If the add then fails the error is thrown and the verifier is simply gone.
    func save(_ state: NativeLockState) throws {
        let data = try Self.encoder.encode(state)
        try NativeLockGate.run {
            var last: OSStatus = errSecSuccess
            for _ in 0..<Self.retries {
                itemDelete(query)
                var add = query
                add[kSecValueData as String] = data
                add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
                last = itemAdd(add)
                if last == errSecSuccess { return }
                if last != errSecItemNotFound && last != errSecDuplicateItem { break }
                Thread.sleep(forTimeInterval: Self.retryDelay)
            }
            throw Failure(status: last)
        }
    }

    func delete() {
        itemDelete(query)
        if dataProtection { itemDelete(query(dataProtection: false)) }
    }

    private static var encoder: JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .secondsSince1970
        return e
    }
    private static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .secondsSince1970
        return d
    }
}

struct FileNativeLockStore: NativeLockStoring {
    struct Failure: Error { let errno: Int32 }

    let url: URL

    init(url: URL) { self.url = url }

    static func folderName(dev: Bool) -> String { dev ? "Proctora-Dev" : "Proctora" }

    static func defaultURL(dev: Bool = Config.isDevBuild) -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
        return base.appendingPathComponent(folderName(dev: dev), isDirectory: true).appendingPathComponent("native-lock.json")
    }

    init() { self.init(url: Self.defaultURL()) }

    func load() -> NativeLockState? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        let d = JSONDecoder()
        d.dateDecodingStrategy = .secondsSince1970
        guard let state = try? d.decode(NativeLockState.self, from: data) else { return nil }
        if state.verifier != nil { try? save(state) }
        return state.withoutVerifier
    }

    func save(_ state: NativeLockState) throws {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .secondsSince1970
        let data = try e.encode(state.withoutVerifier)
        let dir = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let tmp = dir.appendingPathComponent(".\(url.lastPathComponent).\(UUID().uuidString).tmp")
        let fd = open(tmp.path, O_WRONLY | O_CREAT | O_EXCL, 0o600)
        guard fd >= 0 else { throw Failure(errno: errno) }
        var failure: Int32?
        data.withUnsafeBytes { buf in
            var off = 0
            while off < buf.count {
                let n = write(fd, buf.baseAddress! + off, buf.count - off)
                if n < 0 { failure = errno; return }
                off += n
            }
        }
        if failure == nil, fsync(fd) != 0 { failure = errno }
        close(fd)
        if failure == nil, rename(tmp.path, url.path) != 0 { failure = errno }
        if let f = failure {
            unlink(tmp.path)
            throw Failure(errno: f)
        }
    }

    func delete() { unlink(url.path) }
}

struct DualNativeLockStore: NativeLockStoring {
    struct Failure: Error {}

    let primary: NativeLockStoring
    let secondary: NativeLockStoring
    var now: () -> Date
    var monotonic: () -> NativeExit.Monotonic

    init(primary: NativeLockStoring, secondary: NativeLockStoring, now: @escaping () -> Date = Date.init,
         monotonic: @escaping () -> NativeExit.Monotonic = NativeExit.systemMonotonic) {
        self.primary = primary
        self.secondary = secondary
        self.now = now
        self.monotonic = monotonic
    }

    /// The Keychain copy is gone while the file copy is still there: someone removed it, or it is
    /// unreadable. No verifier exists any more, so no typed code can match; only an OJ release ends
    /// the lock. The counters are not reset; the wait starts now and one round of attempts is used up.
    static func penalised(_ s: NativeLockState, now: Date, mono: NativeExit.Monotonic) -> NativeLockState {
        var t = s.withoutVerifier
        let until = max(s.until ?? .distantPast, now.addingTimeInterval(NativeExit.waitSeconds))
        t.until = until
        t.waitBoot = mono.boot
        t.waitMono = mono.seconds + until.timeIntervalSince(now)
        t.lastAttempt = max(s.lastAttempt ?? now, now)
        t.wrong = 0
        t.attempts = min(NativeExit.maxTotalAttempts, s.attempts + NativeExit.maxWrong)
        t.keychainLost = true
        return t
    }

    /// The Keychain copy (`a`) is the base and the only source of a verifier.
    static func merged(_ a: NativeLockState, _ b: NativeLockState) -> NativeLockState {
        guard a.lockId == b.lockId else { return a }
        var m = a
        m.wrong = max(a.wrong, b.wrong)
        let later = (b.until ?? .distantPast) > (a.until ?? .distantPast) ? b : a
        m.until = [a.until, b.until].compactMap { $0 }.max()
        m.waitBoot = later.waitBoot
        m.waitMono = later.waitMono
        m.attempts = max(a.attempts, b.attempts)
        m.lastAttempt = [a.lastAttempt, b.lastAttempt].compactMap { $0 }.max()
        m.unclean = a.unclean || b.unclean
        m.vpnUsed = max(a.vpnUsed, b.vpnUsed)
        return m
    }

    func load() -> NativeLockState? {
        NativeLockGate.run {
            let a = primary.load(), b = secondary.load()
            switch (a, b) {
            case (nil, nil):
                return nil
            case let (x?, nil):
                try? secondary.save(x)
                return x
            case let (nil, y?):
                if y.keychainLost { return y.withoutVerifier }
                let t = Self.penalised(y, now: now(), mono: monotonic())
                try? secondary.save(t)
                return t
            case let (x?, y?):
                let m = Self.merged(x, y)
                if m != x { try? primary.save(m) }
                if m.withoutVerifier != y { try? secondary.save(m) }
                return m
            }
        }
    }

    func save(_ state: NativeLockState) throws {
        try NativeLockGate.run {
            var errors = 0
            if state.keychainLost { errors += 1 } else {
                do { try primary.save(state) } catch { errors += 1 }
            }
            do { try secondary.save(state) } catch { errors += 1 }
            if errors == 2 { throw Failure() }
        }
    }

    func delete() {
        NativeLockGate.run {
            primary.delete()
            secondary.delete()
        }
    }
}

enum NativeExit {
    static let alphabet = Array("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    static let lockIdLength = 8
    static let maxWrong = 5
    static let waitSeconds: TimeInterval = 300
    static let maxTotalAttempts = 15
    static let codePrefix = "fairplay-native-exit|v1|"
    static let sealInfo = "fairplay-native-exit-seal|v1"

    static let verifierInfo = "fairplay-native-exit-verifier|v1|"
    static let iterationFloor = 600_000
    static let iterationCeiling = 20_000_000
    static let verifierTarget: TimeInterval = 1.0

    struct SealError: Error {}

    static func derive(code: String, lockId: String, salt: Data, iterations: Int) -> Data? {
        guard iterations > 0, iterations <= Int(UInt32.max) else { return nil }
        let info = Array((verifierInfo + lockId).utf8) + Array(salt)
        let password = Array(code.utf8)
        var out = [UInt8](repeating: 0, count: 32)
        let status = password.withUnsafeBufferPointer { pw in
            pw.withMemoryRebound(to: Int8.self) { pwc in
                CCKeyDerivationPBKDF(CCPBKDFAlgorithm(kCCPBKDF2), pwc.baseAddress, pwc.count,
                                     info, info.count, CCPseudoRandomAlgorithm(kCCPRFHmacAlgSHA256),
                                     UInt32(iterations), &out, out.count)
            }
        }
        return status == Int32(kCCSuccess) ? Data(out) : nil
    }

    private static var calibrated: Int?

    /// About `verifierTarget` seconds of PBKDF2 on this machine, never below the floor.
    static func calibratedIterations() -> Int {
        if let calibrated { return calibrated }
        let probe = 100_000
        let start = DispatchTime.now().uptimeNanoseconds
        _ = derive(code: "000000", lockId: "AAAAAAAA", salt: Data(count: 16), iterations: probe)
        let seconds = max(Double(DispatchTime.now().uptimeNanoseconds - start) / 1e9, 0.001)
        let n = min(iterationCeiling, max(iterationFloor, Int(Double(probe) * verifierTarget / seconds)))
        calibrated = n
        return n
    }

    static func makeVerifier(code: String, lockId: String, iterations: Int) -> NativeLockState.Verifier {
        let salt = randomBytes(16)
        let hash = derive(code: code, lockId: lockId, salt: salt, iterations: iterations) ?? Data()
        return NativeLockState.Verifier(salt: salt, iterations: iterations, hash: hash)
    }

    static func verifies(_ typed: String, lockId: String, verifier: NativeLockState.Verifier?) -> Bool {
        guard let v = verifier, v.hash.count == 32, v.salt.count == 16, v.iterations > 0,
              let h = derive(code: typed, lockId: lockId, salt: v.salt, iterations: v.iterations) else { return false }
        var diff: UInt8 = 0
        for (x, y) in zip(h, v.hash) { diff |= x ^ y }
        return diff == 0
    }

    static func randomBytes(_ n: Int) -> Data {
        var b = [UInt8](repeating: 0, count: n)
        guard SecRandomCopyBytes(kSecRandomDefault, n, &b) == errSecSuccess else {
            fatalError("secure random unavailable")
        }
        return Data(b)
    }

    static func newKey() -> Data { randomBytes(32) }

    #if FAIRPLAY_DEV
    static let developmentKey = Data(SHA256.hash(data: Data("fairplay-development-native-exit-key".utf8)))
    #endif

    static func newLockId() -> String {
        var out = ""
        let limit = 256 - (256 % alphabet.count)
        while out.count < lockIdLength {
            for byte in randomBytes(16) where Int(byte) < limit && out.count < lockIdLength {
                out.append(alphabet[Int(byte) % alphabet.count])
            }
        }
        return out
    }

    static func display(_ lockId: String) -> String {
        guard lockId.count == lockIdLength else { return lockId }
        return lockId.prefix(4) + "-" + lockId.suffix(4)
    }

    static func parseLockId(_ input: String) -> String? {
        let s = input.uppercased().replacingOccurrences(of: "-", with: "")
        guard s.count == lockIdLength, s.allSatisfy({ alphabet.contains($0) }) else { return nil }
        return s
    }

    static func code(key: Data, lockId: String) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: Data((codePrefix + lockId).utf8),
                                                  using: SymmetricKey(data: key))
        let b = Array(mac.prefix(4))
        let n = (UInt32(b[0]) << 24 | UInt32(b[1]) << 16 | UInt32(b[2]) << 8 | UInt32(b[3])) & 0x7fff_ffff
        return String(format: "%06d", Int(n % 1_000_000))
    }

    private static func aeadKey(_ shared: SharedSecret, lockId: String) -> SymmetricKey {
        shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: Data(lockId.utf8),
                                       sharedInfo: Data(sealInfo.utf8), outputByteCount: 32)
    }

    static func seal(key: Data, lockId: String, serverPublic: Data,
                     ephemeralPrivate: Data? = nil, nonce: Data? = nil) throws -> String {
        guard key.count == 32 else { throw SealError() }
        let eph = try ephemeralPrivate.map { try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: $0) }
            ?? Curve25519.KeyAgreement.PrivateKey()
        let peer = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: serverPublic)
        let shared = try eph.sharedSecretFromKeyAgreement(with: peer)
        let n = try nonce.map { try ChaChaPoly.Nonce(data: $0) } ?? ChaChaPoly.Nonce()
        let box = try ChaChaPoly.seal(key, using: aeadKey(shared, lockId: lockId), nonce: n,
                                      authenticating: Data(lockId.utf8))
        return (eph.publicKey.rawRepresentation + box.combined).base64EncodedString()
    }

    static func open(blob: String, lockId: String, serverPrivate: Data) throws -> Data {
        guard let raw = Data(base64Encoded: blob), raw.count == 92 else { throw SealError() }
        let priv = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: serverPrivate)
        let peer = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: raw.prefix(32))
        let shared = try priv.sharedSecretFromKeyAgreement(with: peer)
        let box = try ChaChaPoly.SealedBox(combined: raw.dropFirst(32))
        return try ChaChaPoly.open(box, using: aeadKey(shared, lockId: lockId),
                                   authenticating: Data(lockId.utf8))
    }

    struct EscrowRequest: Codable, Equatable {
        var lock_id: String
        var blob: String
        var device: String
        var signature: String
    }

    static func escrowSignature(lockId: String, device: String) -> String {
        AppSession.sign("nativelock|\(lockId)|\(device)")
    }

    static func escrowRequest(lockId: String, blob: String, device: String) -> EscrowRequest {
        EscrowRequest(lock_id: lockId, blob: blob, device: device,
                      signature: escrowSignature(lockId: lockId, device: device))
    }

    static func escrowBody(lockId: String, blob: String, device: String) throws -> Data {
        try JSONEncoder().encode(escrowRequest(lockId: lockId, blob: blob, device: device))
    }

    struct Monotonic: Equatable {
        var boot: String
        var seconds: TimeInterval
    }

    /// Continuous clock (keeps counting while the Mac sleeps) plus the boot session it belongs to.
    static func systemMonotonic() -> Monotonic {
        var size = 0
        sysctlbyname("kern.bootsessionuuid", nil, &size, nil, 0)
        var buf = [CChar](repeating: 0, count: max(size, 1))
        let boot = size > 0 && sysctlbyname("kern.bootsessionuuid", &buf, &size, nil, 0) == 0
            ? String(cString: buf) : "unknown"
        return Monotonic(boot: boot, seconds: Double(clock_gettime_nsec_np(CLOCK_MONOTONIC)) / 1e9)
    }

    /// Wait left on a lockout, from the stored evidence. After a reboot the continuous clock has
    /// no common origin, so the wait restarts in full (the stricter of the wall clock and a fixed
    /// re-penalty). Within one boot the longer of the wall clock and the continuous clock counts,
    /// so moving the wall clock forward cannot end it.
    static func effectiveUntil(_ st: NativeLockState, now: Date, mono: Monotonic) -> (until: Date, changed: Bool, reanchor: Bool)? {
        guard var until = st.until else { return nil }
        var changed = false, reanchor = false
        if let last = st.lastAttempt, now < last {
            until = until.addingTimeInterval(last.timeIntervalSince(now))
            changed = true
        }
        if st.waitBoot == mono.boot, let end = st.waitMono {
            let left = end - mono.seconds
            if left > 0 { until = max(until, now.addingTimeInterval(left)) }
        } else {
            until = max(until, now.addingTimeInterval(waitSeconds))
            changed = true
            reanchor = true
        }
        return (until, changed, reanchor)
    }

    /// The same attempt, with the PBKDF2 comparison off the main thread; `completion` runs on main.
    /// The clock reading is taken by the caller so nothing main-bound is touched on the worker.
    static func attemptInBackground(typed: String, store: NativeLockStoring, now: Date, monotonic: Monotonic,
                                    completion: @escaping (NativeExitAttempt) -> Void) {
        nonisolated(unsafe) let store = store
        DispatchQueue.global(qos: .userInitiated).async {
            let result = attempt(typed: typed, store: store, now: now, monotonic: { monotonic })
            DispatchQueue.main.async { completion(result) }
        }
    }

    /// No stored lock returns .malformed (nothing to compare against).
    /// Reading and counting happen under the store gate and are saved before the slow comparison, which
    /// runs outside it so other store access never waits for PBKDF2; the right-code reset re-reads under it.
    static func attempt(typed: String, store: NativeLockStoring, now: Date,
                        monotonic: () -> Monotonic = NativeExit.systemMonotonic) -> NativeExitAttempt {
        guard typed.utf8.count == 6, typed.utf8.allSatisfy({ $0 >= 48 && $0 <= 57 }) else { return .malformed }
        enum Step { case done(NativeExitAttempt), compare(NativeLockState, NativeExitAttempt) }
        let step: Step = NativeLockGate.run {
            guard var st = store.load() else { return .done(.malformed) }
            if st.attempts >= maxTotalAttempts { return .done(.refused) }
            let mono = monotonic()

            if let eff = effectiveUntil(st, now: now, mono: mono) {
                if eff.changed {
                    st.until = eff.until
                    if eff.reanchor {
                        st.waitBoot = mono.boot
                        st.waitMono = mono.seconds + max(0, eff.until.timeIntervalSince(now))
                    }
                    if st.lastAttempt.map({ now < $0 }) ?? false { st.lastAttempt = now }
                    try? store.save(st)
                }
                if now < eff.until { return .done(.wait(until: eff.until)) }
                st.until = nil
                st.waitBoot = nil
                st.waitMono = nil
                st.wrong = 0
            }

            st.wrong += 1
            st.attempts += 1
            st.lastAttempt = max(now, st.lastAttempt ?? now)
            let lockedOut = st.wrong >= maxWrong
            if lockedOut {
                st.until = st.lastAttempt!.addingTimeInterval(waitSeconds)
                st.waitBoot = mono.boot
                st.waitMono = mono.seconds + waitSeconds
                st.wrong = 0
            }
            let remaining = lockedOut ? 0 : maxWrong - st.wrong
            let miss: NativeExitAttempt = st.until.map { .wait(until: $0) } ?? .wrong(remaining: remaining)
            do { try store.save(st) } catch { return .done(miss) }
            return .compare(st, miss)
        }
        let counted: NativeLockState, miss: NativeExitAttempt
        switch step {
        case .done(let result): return result
        case .compare(let state, let missed): counted = state; miss = missed
        }
        guard verifies(typed, lockId: counted.lockId, verifier: counted.verifier) else { return miss }
        NativeLockGate.run {
            guard var latest = store.load(), latest.lockId == counted.lockId else { return }
            latest.wrong = 0
            latest.until = nil
            latest.waitBoot = nil
            latest.waitMono = nil
            try? store.save(latest)
        }
        return .accepted
    }
}
