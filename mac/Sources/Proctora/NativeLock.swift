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

    var store: NativeLockStoring
    var publicKey: Data?
    var isDev: Bool
    var device: () -> String
    var transport: Transport
    var loginItem: LoginItemRegistering
    var iterations: Int? = nil
    /// Development builds only, used when no escrow key is built in: a known exit key, so the lock can be left.
    var devKey: Data? = nil
    var newKey: () -> Data = NativeExit.newKey
    var random: () -> Double = { Double.random(in: 0..<1) }
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

    static var live: NativeLockSetup {
        var setup = NativeLockSetup(store: DualNativeLockStore(primary: KeychainNativeLockStore(), secondary: FileNativeLockStore()),
                                    publicKey: Config.nativeExitPublicKey, isDev: Config.isDevBuild,
                                    device: { AppSession.shared.deviceId }, transport: serverTransport,
                                    loginItem: Config.isDevBuild ? NoLoginItem() : SystemLoginItem())
        #if FAIRPLAY_DEV
        setup.devKey = NativeExit.developmentKey
        #endif
        return setup
    }

    /// The exit code a development lock without escrow accepts; nil whenever the code is not knowable.
    func developmentCode(lockId: String) -> String? {
        guard isDev, publicKey == nil, let devKey else { return nil }
        return NativeExit.code(key: devKey, lockId: lockId)
    }

    static func serverTransport(_ body: Data, _ done: @escaping (Int?, Data?) -> Void) {
        var request = URLRequest(url: Config.serverURL.appendingPathComponent("api/native-exit/locks"), timeoutInterval: LoadTimeouts.request)
        request.httpMethod = "POST"
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(Config.version, forHTTPHeaderField: "X-FairPlay-Version")
        request.setValue("mac", forHTTPHeaderField: "X-FairPlay-Platform")
        Net.session.dataTask(with: request) { data, response, _ in
            let status = (response as? HTTPURLResponse)?.statusCode
            DispatchQueue.main.async { done(status, data) }
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
        NativeLockGate.run { store.delete() }
        loginItem.unregister()
    }

    func registerLoginItem() { loginItem.register() }

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
