import Foundation
import ServiceManagement

protocol LoginItemRegistering {
    func register()
    func unregister()
}

struct NoLoginItem: LoginItemRegistering {
    func register() {}
    func unregister() {}
}

/// Reopens FairPlay after a reboot while a native lock is saved (macOS 13+).
struct SystemLoginItem: LoginItemRegistering {
    func register() {
        do { try SMAppService.mainApp.register() }
        catch { DebugLog.log("lock", "login item not registered: \(error)") }
    }
    func unregister() { try? SMAppService.mainApp.unregister() }
}

enum NativeLockSetupFailure: Error, Equatable {
    case keyMissing, escrow, save

    var message: String {
        switch self {
        case .keyMissing:
            return "This copy of FairPlay has no emergency exit configured, so it will not lock your Mac. Install the latest FairPlay from DKU OJ. Your Mac is still unlocked."
        case .escrow, .save:
            return NativeLockSetup.escrowMessage
        }
    }
}

/// Creates the per-lock exit key, escrows it with the FairPlay server and saves only a slow verifier
/// of its 6-digit code on this Mac before anything is locked; the key is wiped as soon as it is sealed. Any failure leaves the Mac unlocked and no state behind.
struct NativeLockSetup {
    typealias Transport = (_ body: Data, _ done: @escaping (_ status: Int?, _ data: Data?) -> Void) -> Void

    static let escrowMessage = "Emergency exit could not be set up; your Mac is still unlocked. Check your internet connection and try again, or contact your instructor."
    static let preparingMessage = "Preparing your secure exam session..."
    static let retryBudget: TimeInterval = 60
    static let firstRetryRange: ClosedRange<TimeInterval> = 1...5

    /// Only an overloaded server or a lost answer is worth another try; a refusal (403/409/422) never is.
    static func escrowRetryable(status: Int?) -> Bool { status == nil || status == 429 || status == 503 }

    /// First wait is random 1-5 s, then the shared backoff steps with full jitter.
    func retryWait(afterFailure count: Int) -> TimeInterval {
        let r = min(max(random(), 0), 1)
        if count <= 1 { return Self.firstRetryRange.lowerBound + r * (Self.firstRetryRange.upperBound - Self.firstRetryRange.lowerBound) }
        return NativeRenewSchedule.equalJitter(step: NativeRenewSchedule.step(forFailure: count - 1), random: r)
    }

    typealias RegisterTransport = (_ body: Data, _ done: @escaping (_ status: Int?, _ data: Data?, _ retryAfter: TimeInterval?) -> Void) -> Void

    var store: NativeLockStoring
    var publicKey: Data?
    var isDev: Bool
    var device: () -> String
    var transport: Transport
    /// Like `transport` but also reports Retry-After; the background registration uses it when set.
    var registerTransport: RegisterTransport? = nil
    let registrar = NativeLockRegistrar()
    var loginItem: LoginItemRegistering
    var iterations: Int? = nil
    /// Development builds only, used when no escrow key is built in: a known exit key, so the lock can be left.
    var devKey: Data? = nil
    var newKey: () -> Data = NativeExit.newKey
    var random: () -> Double = { Double.random(in: 0..<1) }
    var now: () -> Date = Date.init
    var uptime: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    var background: (@escaping () -> Void) -> Void = { work in
        nonisolated(unsafe) let work = work
        DispatchQueue.global(qos: .userInitiated).async { work() }
    }
    var onMain: (@escaping () -> Void) -> Void = { work in
        nonisolated(unsafe) let work = work
        DispatchQueue.main.async { work() }
    }
    var wait: (TimeInterval, @escaping () -> Void) -> Void = { delay, fire in
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: fire)
    }

    static var dev: NativeLockSetup {
        NativeLockSetup(store: InMemoryNativeLockStore(), publicKey: nil, isDev: true, device: { "" },
                        transport: { _, done in done(201, Data(#"{"ok":true}"#.utf8)) }, loginItem: NoLoginItem(),
                        iterations: 2_000)
    }

    static var live: NativeLockSetup { liveSetup }

    private static let liveSetup: NativeLockSetup = {
        var setup = NativeLockSetup(store: DualNativeLockStore(primary: KeychainNativeLockStore(), secondary: FileNativeLockStore()),
                                    publicKey: Config.nativeExitPublicKey, isDev: Config.isDevBuild,
                                    device: { AppSession.shared.deviceId }, transport: serverTransport,
                                    loginItem: Config.isDevBuild ? NoLoginItem() : SystemLoginItem())
        setup.registerTransport = serverRegisterTransport
        #if FAIRPLAY_DEV
        setup.devKey = NativeExit.developmentKey
        #endif
        return setup
    }()

    /// The exit code a development lock without escrow accepts; nil whenever the code is not knowable.
    func developmentCode(lockId: String) -> String? {
        guard isDev, publicKey == nil, let devKey else { return nil }
        return NativeExit.code(key: devKey, lockId: lockId)
    }

    static func serverTransport(_ body: Data, _ done: @escaping (Int?, Data?) -> Void) {
        serverRegisterTransport(body) { status, data, _ in done(status, data) }
    }

    static func serverRegisterTransport(_ body: Data, _ done: @escaping (Int?, Data?, TimeInterval?) -> Void) {
        var request = URLRequest(url: Config.serverURL.appendingPathComponent("api/native-exit/locks"), timeoutInterval: LoadTimeouts.request)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(Config.version, forHTTPHeaderField: "X-FairPlay-Version")
        request.setValue("mac", forHTTPHeaderField: "X-FairPlay-Platform")
        Net.session.dataTask(with: request) { data, response, _ in
            let http = response as? HTTPURLResponse
            let retryAfter = NativeRenewSchedule.retryAfter(http?.value(forHTTPHeaderField: "Retry-After"))
            DispatchQueue.main.async { done(http?.statusCode, data, retryAfter) }
        }.resume()
    }

    /// Exactly HTTP 201 {"ok":true}; everything else (403/409/422/429/503, transport error) is a failure.
    static func escrowSucceeded(status: Int?, data: Data?) -> Bool {
        guard status == 201, let data, data.count <= 4096,
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              (object["ok"] as? Bool) == true else { return false }
        return true
    }

    /// Key, code and the slow verifier are made on `background`; everything else (escrow, store, result) runs
    /// on `onMain`. The key is wiped before it leaves the worker, and nothing is stored before the 201.
    func prepare(now: Date = Date(), progress: @escaping () -> Void = {}, completion: @escaping (Result<NativeLockState, NativeLockSetupFailure>) -> Void) {
        progress()
        let publicKey = self.publicKey, isDev = self.isDev, iterations = self.iterations, newKey = self.newKey
        let devKey = isDev && publicKey == nil ? self.devKey : nil
        let deviceId = device()
        nonisolated(unsafe) let work = { [self] in
            var key = devKey ?? newKey()
            let lockId = NativeExit.newLockId()
            let verifier = NativeExit.makeVerifier(code: NativeExit.code(key: key, lockId: lockId), lockId: lockId,
                                                   iterations: iterations ?? NativeExit.calibratedIterations())
            let state = NativeLockState(lockId: lockId, verifier: verifier, created: now)
            var body: Data?
            let keyOK = publicKey.map { $0.count == 32 } ?? false
            if keyOK, let publicKey, let blob = try? NativeExit.seal(key: key, lockId: lockId, serverPublic: publicKey) {
                body = try? NativeExit.escrowBody(lockId: lockId, blob: blob, device: deviceId)
            }
            key.resetBytes(in: 0..<key.count)
            nonisolated(unsafe) let sealed = body
            self.onMain {
                guard keyOK else {
                    if isDev { self.finishSetup(state, completion) } else { completion(.failure(.keyMissing)) }
                    return
                }
                guard let sealed else { completion(.failure(.escrow)); return }
                self.escrow(sealed, state: state, progress: progress, completion: completion)
            }
        }
        background(work)
    }

    private func finishSetup(_ state: NativeLockState, _ completion: (Result<NativeLockState, NativeLockSetupFailure>) -> Void) {
        do { try NativeLockGate.run { try store.save(state) } } catch { completion(.failure(.save)); return }
        completion(.success(state))
    }

    private func escrow(_ body: Data, state: NativeLockState, progress: @escaping () -> Void,
                        completion: @escaping (Result<NativeLockState, NativeLockSetupFailure>) -> Void) {
        let deadline = uptime() + Self.retryBudget
        func send(failures: Int) {
            transport(body) { status, data in
                if Self.escrowSucceeded(status: status, data: data) { self.finishSetup(state, completion); return }
                let pause = self.retryWait(afterFailure: failures + 1)
                guard Self.escrowRetryable(status: status), self.uptime() + pause <= deadline else {
                    completion(.failure(.escrow))
                    return
                }
                progress()
                self.wait(pause) { send(failures: failures + 1) }
            }
        }
        send(failures: 0)
    }

    /// Deletes both copies and stops reopening the app after a reboot.
    func clear() {
        NativeLockGate.run {
            registrar.cancel()
            store.delete()
        }
        loginItem.unregister()
    }

    func registerLoginItem() { loginItem.register() }

    /// A relaunched lock with no verifier (state recreated after a crash, or the Keychain copy lost) is not known to the server, or
    /// not with a key this Mac still has. Registers it in the background; the verifier counts as saved only when it reads back
    /// from the store the app trusts (the Keychain), only if the saved lock is still this one, and only while this lock session
    /// is current. An id the server already holds under another key (409) is replaced by a fresh id and key. Transient failures
    /// are retried with long waits, a bounded number of times per run; after that the run pauses and is re-armed by a timer or the
    /// next Leave sheet. Only 403/422 or a missing key stop it for good. The code still comes only from the server or the instructor.
    func registerMissingVerifier(for saved: NativeLockState) {
        guard saved.verifier == nil else { return }
        guard let publicKey, publicKey.count == 32 else {
            DebugLog.log("lock", "background registration not started: no usable server key in this build")
            registrar.markNoKey()
            return
        }
        var notYet: TimeInterval?
        let started: Int? = NativeLockGate.run {
            guard let current = store.load(), current.lockId == saved.lockId, current.verifier == nil else { return nil }
            if let notBefore = current.retryNotBefore, now() < notBefore {
                notYet = notBefore.timeIntervalSince(now())
                registrar.markPaused()
                return nil
            }
            return registrar.begin()
        }
        if let notYet { rearmAfter(max(notYet, 1)) }
        guard let token = started else { return }
        let originalId = saved.lockId
        let deviceId = device()
        let send = registerTransport ?? { body, done in transport(body) { done($0, $1, nil) } }

        func prepare(_ lockId: String, then use: @escaping (Registration?) -> Void) {
            let iterations = self.iterations, newKey = self.newKey
            nonisolated(unsafe) let work = {
                var key = newKey()
                let verifier = NativeExit.makeVerifier(code: NativeExit.code(key: key, lockId: lockId), lockId: lockId,
                                                       iterations: iterations ?? NativeExit.calibratedIterations())
                var made: Registration?
                if let blob = try? NativeExit.seal(key: key, lockId: lockId, serverPublic: publicKey),
                   let body = try? NativeExit.escrowBody(lockId: lockId, blob: blob, device: deviceId) {
                    made = Registration(lockId: lockId, verifier: verifier, body: body)
                }
                key.resetBytes(in: 0..<key.count)
                nonisolated(unsafe) let result = made
                self.onMain { use(result) }
            }
            background(work)
        }

        func stop(_ status: NativeLockRegistrar.Status, _ why: String) {
            DebugLog.log("lock", "background registration stopped: \(why)")
            registrar.stop(token, as: status)
            if status == .paused { rearmAfter(NativeLockRegistrar.rearmInterval) }
        }

        func holdOff(_ pause: TimeInterval) {
            NativeLockGate.run {
                guard registrar.isCurrent(token), var st = store.load(), st.lockId == originalId, st.verifier == nil else { return }
                let until = now().addingTimeInterval(pause)
                guard (st.retryNotBefore ?? .distantPast) < until else { return }
                st.retryNotBefore = until
                try? store.save(st)
            }
        }

        func attempt(_ reg: Registration, failures: Int, fresh: Int) {
            NativeLockGate.run {
                guard registrar.isCurrent(token) else { return }
                send(reg.body) { status, data, retryAfter in
                    guard registrar.isCurrent(token) else { return }
                    if Self.escrowSucceeded(status: status, data: data) {
                        switch commit(reg) {
                        case .saved, .gone: registrar.finish(token)
                        case .failed: retry(reg, failures: failures, fresh: fresh, retryAfter: nil, why: "the exit code could not be saved in the Keychain", serverAnswered: false)
                        }
                        return
                    }
                    if status == 409 {
                        guard fresh < NativeLockRegistrar.freshIdLimit else { stop(.paused, "the server keeps reporting this id as taken"); return }
                        prepare(NativeExit.newLockId()) { next in
                            guard registrar.isCurrent(token) else { return }
                            guard let next else { stop(.noKey, "the exit key could not be sealed"); return }
                            attempt(next, failures: 0, fresh: fresh + 1)
                        }
                        return
                    }
                    guard NativeLockRegistrar.retryable(status: status) else {
                        stop(.refused, "the server refused it (\(status ?? 0)); not retrying")
                        return
                    }
                    retry(reg, failures: failures, fresh: fresh, retryAfter: retryAfter, why: "server answer \(status ?? 0)")
                }
            }
        }

        func retry(_ reg: Registration, failures: Int, fresh: Int, retryAfter: TimeInterval?, why: String, serverAnswered: Bool = true) {
            let pause = NativeLockRegistrar.wait(afterFailure: failures + 1, random: random(), retryAfter: retryAfter)
            if serverAnswered { holdOff(pause) }
            guard failures + 1 < NativeLockRegistrar.attemptsPerRun else {
                stop(.paused, "\(NativeLockRegistrar.attemptsPerRun) tries failed (last: \(why)); trying again later")
                return
            }
            wait(pause) { attempt(reg, failures: failures + 1, fresh: fresh) }
        }

        enum Committed { case saved, gone, failed }
        func commit(_ reg: Registration) -> Committed {
            NativeLockGate.run {
                guard registrar.isCurrent(token), let prior = store.load(), prior.lockId == originalId, prior.verifier == nil else { return .gone }
                var next = prior
                next.lockId = reg.lockId
                next.verifier = reg.verifier
                next.keychainLost = false
                next = Self.budgetAfterRegistration(next)
                do { try store.save(next) } catch { try? store.save(prior); return .failed }
                guard let trusted = store.loadTrusted(), trusted.lockId == reg.lockId, trusted.verifier == reg.verifier else {
                    try? store.save(prior)
                    return .failed
                }
                registrar.noteRegistrationChanged()
                return .saved
            }
        }

        func rearmAfter(_ delay: TimeInterval) {
            let slot = registrar.newRearm()
            wait(delay) {
                guard registrar.isCurrentRearm(slot) else { return }
                background {
                    let current = store.load()
                    onMain { if let current { registerMissingVerifier(for: current) } }
                }
            }
        }

        prepare(originalId) { first in
            guard registrar.isCurrent(token) else { return }
            guard let first else { stop(.noKey, "the exit key could not be sealed"); return }
            attempt(first, failures: 0, fresh: 0)
        }
    }

    /// The wait, counters and hourly guess ledger carry over to a fresh id: deleting the Keychain item and registering
    /// again must not hand out new guesses. One round of attempts is still left, because the verifier is new and
    /// independent of every earlier guess, so the right code works once the wait is over.
    static func budgetAfterRegistration(_ s: NativeLockState) -> NativeLockState {
        var t = s
        t.attempts = min(t.attempts, NativeExit.maxTotalAttempts - NativeExit.maxWrong)
        t.retryNotBefore = nil
        return t
    }

    struct LeaveSnapshot {
        let lock: NativeLockState?
        let text: String
        let development: String?
        /// `lockIdChanges` read before the lock was; a later value means `text` is stale.
        var version = 0
    }

    /// For the Leave sheet; call off the main thread. Also gives a paused or never-started registration its next try.
    func leaveSnapshot() -> LeaveSnapshot {
        let version = registrar.lockIdChanges
        let lock = store.load()
        if let lock { registerMissingVerifier(for: lock) }
        let development = lock.flatMap { developmentCode(lockId: $0.lockId) }
        return LeaveSnapshot(lock: lock, text: NativeExit.leaveCodeText(lock, registration: registrar.status), development: development, version: version)
    }

    /// Reads the current code text for a Leave sheet that is already open; `done` runs on main.
    func reloadLeaveCode(_ done: @escaping (_ text: String, _ development: String?) -> Void) {
        background {
            let snapshot = leaveSnapshot()
            onMain { done(snapshot.text, snapshot.development) }
        }
    }

    private struct Registration {
        let lockId: String
        let verifier: NativeLockState.Verifier
        let body: Data
    }

    func persist(contest: String?, binding: NativeDeviceContext.Binding?) {
        NativeLockGate.run {
            guard var state = store.load() else { return }
            let stored = binding.map { NativeLockState.Binding(session: $0.session, device: $0.device, user: $0.user, contest: $0.contest) }
            guard state.contest != contest || state.binding != stored else { return }
            state.contest = contest
            state.binding = stored
            try? store.save(state)
        }
    }

    func savedVPNUsed() -> TimeInterval {
        NativeLockGate.run { store.load()?.vpnUsed ?? 0 }
    }

    func persistVPNUsed(_ seconds: TimeInterval) {
        NativeLockGate.run {
            guard var state = store.load(), seconds > state.vpnUsed else { return }
            state.vpnUsed = seconds
            try? store.save(state)
        }
    }

    func markRecovered() {
        NativeLockGate.run {
            guard var state = store.load(), state.unclean else { return }
            state.unclean = false
            try? store.save(state)
        }
    }
}

/// Which background registration is current. Cancelled when the lock is cleared, so a released lock's
/// retry can never register or save anything once a new lock exists.
final class NativeLockRegistrar {
    static let firstWait: TimeInterval = 60
    static let waitCap: TimeInterval = 900
    static let retryAfterCap: TimeInterval = 86_400
    static let freshIdLimit = 3
    static let attemptsPerRun = 12
    static let rearmInterval: TimeInterval = 1_800

    enum Status: Equatable {
        case idle, retrying
        /// Out of tries for now; a timer or the next Leave sheet starts again.
        case paused
        /// 403/422: the server will not take this lock. Final until the lock is cleared.
        case refused
        /// No usable server key in this build. Final until the lock is cleared.
        case noKey
    }

    private let mutex = NSLock()
    private var session = 0
    private var active = false
    private var stopped = Status.idle
    private var idChanges = 0

    var status: Status { mutex.lock(); defer { mutex.unlock() }; return active ? .retrying : stopped }
    var isRetrying: Bool { status == .retrying }

    /// Bumped whenever a registration was saved (a new lock id, or the verifier and budget restored under the same id),
    /// so an open Leave sheet can tell what it shows is stale.
    var lockIdChanges: Int { mutex.lock(); defer { mutex.unlock() }; return idChanges }
    func noteRegistrationChanged() { mutex.lock(); idChanges += 1; mutex.unlock() }

    private var rearm = 0

    /// One pending re-arm timer at a time; a newer one, or `cancel()`, makes the older one do nothing.
    func newRearm() -> Int { mutex.lock(); defer { mutex.unlock() }; rearm += 1; return rearm }
    func isCurrentRearm(_ slot: Int) -> Bool { mutex.lock(); defer { mutex.unlock() }; return rearm == slot }

    /// nil while one is already running or the server has refused this lock for good.
    func begin() -> Int? {
        mutex.lock(); defer { mutex.unlock() }
        guard !active, stopped != .refused, stopped != .noKey else { return nil }
        session += 1
        active = true
        stopped = .idle
        return session
    }

    func isCurrent(_ token: Int) -> Bool { mutex.lock(); defer { mutex.unlock() }; return active && session == token }

    func finish(_ token: Int) {
        mutex.lock(); defer { mutex.unlock() }
        if session == token { active = false }
    }

    func stop(_ token: Int, as status: Status) {
        mutex.lock(); defer { mutex.unlock() }
        if session == token, active { active = false; stopped = status }
    }

    func markPaused() {
        mutex.lock(); defer { mutex.unlock() }
        if !active, stopped == .idle { stopped = .paused }
    }

    func markNoKey() {
        mutex.lock(); defer { mutex.unlock() }
        if !active { stopped = .noKey }
    }

    func cancel() {
        mutex.lock(); defer { mutex.unlock() }
        session += 1
        rearm += 1
        active = false
        stopped = .idle
    }

    /// Only a refusal (403/422, answered by 409 separately) is final; no answer, 429, 5xx and anything unexpected are tried again.
    static func retryable(status: Int?) -> Bool { status != 403 && status != 422 }

    /// 60 s doubling to 15 min with half-step jitter; a server Retry-After is never undercut (up to a day).
    static func wait(afterFailure count: Int, random: Double, retryAfter: TimeInterval?) -> TimeInterval {
        let step = min(waitCap, firstWait * pow(2, Double(max(count, 1) - 1)))
        let jittered = step / 2 + min(max(random, 0), 1) * step / 2
        return max(jittered, min(retryAfter ?? 0, retryAfterCap))
    }
}

/// What the app does at launch (main.swift): a saved native lock always locks again first.
enum NativeRelaunch: Equatable {
    case normal
    case relock(NativeLockState)

    /// `relockRequested`: the watchdog reopened the app because it died while locked. If the saved
    /// state has been deleted meanwhile the Mac still locks, in a state no typed code can open;
    /// only a signed release from the OJ ends it.
    static func decide(store: NativeLockStoring, relockRequested: Bool = false, now: Date = Date()) -> NativeRelaunch {
        NativeLockGate.run {
            guard var state = store.load() else {
                guard relockRequested else { return .normal }
                let state = NativeLockState(lockId: NativeExit.newLockId(), created: now, unclean: true)
                try? store.save(state)
                return .relock(state)
            }
            state.unclean = true
            try? store.save(state)
            return .relock(state)
        }
    }
}

extension NativeDeviceContext.Binding {
    init(_ saved: NativeLockState.Binding) {
        self.init(session: saved.session, device: saved.device, user: saved.user, contest: saved.contest)
    }
}

/// The native lock's mode flags, apart from the OS effects so they can be tested with fakes.
/// While active, nothing but an explicit release may unlock, and the page-driven "preunlock" is refused.
struct NativeLockMode: Equatable {
    private(set) var active = false
    private(set) var releasing = false

    mutating func begin() { active = true }

    /// `recovering`: a relaunch keeps the mode even when the keyboard tap could not start.
    mutating func acquired(_ ok: Bool, recovering: Bool) -> Bool {
        if !ok && !recovering { active = false }
        return ok
    }

    var allowsUnlock: Bool { !active || releasing }
    mutating func beginRelease() { if active { releasing = true } }
    mutating func endRelease() { releasing = false; active = false }
}
