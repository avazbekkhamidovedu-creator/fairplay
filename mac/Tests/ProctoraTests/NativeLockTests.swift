import Testing
import Foundation
import AppKit
import CryptoKit
import WebKit
@testable import Proctora

private final class RecordingLoginItem: LoginItemRegistering {
    var registered = 0, unregistered = 0
    func register() { registered += 1 }
    func unregister() { unregistered += 1 }
}

private final class FailingStore: NativeLockStoring {
    struct Failure: Error {}
    func load() -> NativeLockState? { nil }
    func save(_ state: NativeLockState) throws { throw Failure() }
    func delete() {}
}

@Suite struct NativeLockSetupTests {
    private let serverPrivate = Curve25519.KeyAgreement.PrivateKey()
    private func setup(store: NativeLockStoring, key: Data?, isDev: Bool, status: Int?, body: String = #"{"ok":true}"#,
                       sent: @escaping (Data) -> Void = { _ in }) -> NativeLockSetup {
        var s = NativeLockSetup(store: store, publicKey: key, isDev: isDev, device: { String(repeating: "a", count: 64) },
                        transport: { data, done in sent(data); done(status, Data(body.utf8)) }, loginItem: NoLoginItem(), iterations: 2_000)
        FakeEscrowClock().install(in: &s)
        return s
    }
    private func run(_ s: NativeLockSetup) -> Result<NativeLockState, NativeLockSetupFailure> {
        var out: Result<NativeLockState, NativeLockSetupFailure>?
        s.prepare { out = $0 }
        return out!
    }

    @Test func escrowSuccessSealsTheKeyThenSavesTheState() throws {
        let store = InMemoryNativeLockStore()
        var body: Data?
        let result = run(setup(store: store, key: serverPrivate.publicKey.rawRepresentation, isDev: false, status: 201, sent: { body = $0 }))
        let state = try result.get()
        #expect(store.load() == state)
        let request = try JSONDecoder().decode(NativeExit.EscrowRequest.self, from: #require(body))
        #expect(request.lock_id == state.lockId)
        #expect(request.device == String(repeating: "a", count: 64))
        let opened = try NativeExit.open(blob: request.blob, lockId: state.lockId, serverPrivate: serverPrivate.rawRepresentation)
        #expect(opened.count == 32)
        #expect(NativeExit.verifies(NativeExit.code(key: opened, lockId: state.lockId), lockId: state.lockId, verifier: state.verifier))
        #expect(!String(decoding: body ?? Data(), as: UTF8.self).contains(opened.base64EncodedString()))
        let stored = try JSONEncoder().encode(try #require(store.load()))
        let text = String(decoding: stored, as: UTF8.self)
        #expect(!text.contains(opened.base64EncodedString()))
        #expect(stored.range(of: opened) == nil)
        #expect(!text.contains("\"key\""))
        #expect(!text.contains(NativeExit.code(key: opened, lockId: state.lockId)))
    }

    @Test func gatewayAndOverloadStatusesAreRetryableRefusalsAreNot() {
        for status in [nil, 429, 502, 503, 504] as [Int?] { #expect(NativeLockSetup.escrowRetryable(status: status)) }
        for status in [200, 400, 403, 409, 422, 500] as [Int?] { #expect(!NativeLockSetup.escrowRetryable(status: status)) }
    }

    @Test func anythingButExactly201OkMeansNoStateAndNoLock() {
        let key = serverPrivate.publicKey.rawRepresentation
        for status in [200, 202, 403, 409, 422, 429, 502, 503, 504, 500] as [Int?] + [nil] {
            let store = InMemoryNativeLockStore()
            let result = run(setup(store: store, key: key, isDev: false, status: status))
            #expect(result == .failure(.escrow), "\(String(describing: status))")
            #expect(store.load() == nil)
        }
        for body in [#"{"ok":false}"#, "{}", "nope", #"{"ok":"yes"}"#] {
            let store = InMemoryNativeLockStore()
            #expect(run(setup(store: store, key: key, isDev: false, status: 201, body: body)) == .failure(.escrow))
            #expect(store.load() == nil)
        }
    }

    @Test func failureMessageIsTheAgreedOne() {
        #expect(NativeLockSetupFailure.escrow.message == "Emergency exit could not be set up; your Mac is still unlocked. Check your internet connection and try again, or contact your instructor.")
        #expect(NativeLockSetupFailure.save.message == "Emergency exit could not be set up; your Mac is still unlocked. Check your internet connection and try again, or contact your instructor.")
    }

    @Test func releaseBuildWithoutTheServerKeyRefusesAndNeverContactsTheServer() {
        let store = InMemoryNativeLockStore()
        var contacted = false
        let result = run(setup(store: store, key: nil, isDev: false, status: 201, sent: { _ in contacted = true }))
        #expect(result == .failure(.keyMissing))
        #expect(NativeLockSetupFailure.keyMissing.message.contains("still unlocked"))
        #expect(!contacted && store.load() == nil)
        let short = run(setup(store: store, key: Data(count: 31), isDev: false, status: 201, sent: { _ in contacted = true }))
        #expect(short == .failure(.keyMissing) && !contacted)
    }

    @Test func devBuildMaySkipTheEscrow() throws {
        let store = InMemoryNativeLockStore()
        var contacted = false
        let state = try run(setup(store: store, key: nil, isDev: true, status: 503, sent: { _ in contacted = true })).get()
        #expect(!contacted)
        #expect(store.load() == state)
    }

    @Test func devLockWithoutEscrowCanBeLeftWithTheKnownCode() throws {
        let store = InMemoryNativeLockStore()
        var s = setup(store: store, key: nil, isDev: true, status: 503)
        s.devKey = Data(repeating: 7, count: 32)
        let state = try run(s).get()
        let code = try #require(s.developmentCode(lockId: state.lockId))
        #expect(code == NativeExit.code(key: Data(repeating: 7, count: 32), lockId: state.lockId))
        #expect(NativeExit.attempt(typed: code, store: store, now: Date()) == .accepted)
    }

    @Test func theKnownDevKeyNeverAppliesToAReleaseSetup() throws {
        var release = setup(store: InMemoryNativeLockStore(), key: nil, isDev: false, status: 201)
        release.devKey = Data(repeating: 7, count: 32)
        #expect(run(release) == .failure(.keyMissing))
        #expect(release.developmentCode(lockId: "K7QM3XPA") == nil)
        var escrowed = setup(store: InMemoryNativeLockStore(), key: serverPrivate.publicKey.rawRepresentation, isDev: true, status: 201)
        escrowed.devKey = Data(repeating: 7, count: 32)
        let state = try run(escrowed).get()
        #expect(escrowed.developmentCode(lockId: state.lockId) == nil)
        #expect(NativeLockSetup.dev.developmentCode(lockId: "K7QM3XPA") == nil)
    }

    @Test func aStateThatCannotBeSavedMeansNoLock() {
        let result = run(setup(store: FailingStore(), key: serverPrivate.publicKey.rawRepresentation, isDev: false, status: 201))
        #expect(result == .failure(.save))
    }

    @Test func aLockFromAnEarlierBootIsDroppedAndItsLoginItemRemoved() {
        var saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date())
        saved.boot = "boot-1"
        let item = RecordingLoginItem()
        var s = setup(store: InMemoryNativeLockStore(saved), key: nil, isDev: true, status: 201)
        s.loginItem = item
        guard case .rebooted = NativeRelaunch.decide(store: s.store, boot: "boot-2") else { Issue.record("expected rebooted"); return }
        s.clear()    // what main.swift does for .rebooted
        #expect(item.unregistered == 1 && s.store.load() == nil)
    }

    @Test func registeringTheLoginItemIsOneCallOnTheSetup() {
        let item = RecordingLoginItem()
        var s = setup(store: InMemoryNativeLockStore(), key: nil, isDev: true, status: 201)
        s.loginItem = item
        s.registerLoginItem()
        #expect(item.registered == 1)
    }

    @Test func clearingTheLockRemovesTheLoginItem() {
        let item = RecordingLoginItem()
        var s = setup(store: InMemoryNativeLockStore(), key: nil, isDev: true, status: 201)
        s.loginItem = item
        s.clear()
        #expect(item.unregistered == 1)
        #expect(s.store.load() == nil)
    }

    @Test func claimAndBindingArePersistedForARelaunch() throws {
        let store = InMemoryNativeLockStore()
        let s = setup(store: store, key: nil, isDev: true, status: 201)
        _ = run(s)
        let binding = NativeDeviceContext.Binding(session: "s1", device: "d", user: "7", contest: "exam")
        s.persist(contest: "exam", binding: binding)
        let saved = try #require(store.load())
        #expect(saved.contest == "exam")
        #expect(saved.binding == NativeLockState.Binding(session: "s1", device: "d", user: "7", contest: "exam"))
        let restored = NativeExamSession(restoring: saved.contest, binding: saved.binding)
        #expect(restored.binding == binding && restored.requiresTerminalReceipt)
    }
}

@Suite struct NativeRelaunchTests {
    @Test func noSavedStateStartsNormally() {
        #expect(NativeRelaunch.decide(store: InMemoryNativeLockStore()) == .normal)
    }

    @Test func aRelockWithTheSavedStateDeletedStillLocksAndNoCodeOpensIt() throws {
        let store = InMemoryNativeLockStore()
        guard case .relock(let state) = NativeRelaunch.decide(store: store, relockRequested: true) else {
            Issue.record("expected relock"); return
        }
        #expect(state.unclean && state.verifier == nil && store.load() == state)
        for typed in ["000000", "123456", "999999"] {
            #expect(NativeExit.attempt(typed: typed, store: store, now: Date()) != .accepted)
        }
        guard case .relock = NativeRelaunch.decide(store: store) else { Issue.record("must stay locked"); return }
    }

    @Test func aSavedStateLocksAgainAndIsMarkedUnclean() throws {
        let binding = NativeLockState.Binding(session: "s", device: "d", user: "u", contest: "c")
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(),
                                    contest: "c", binding: binding)
        let store = InMemoryNativeLockStore(saved)
        guard case .relock(let state) = NativeRelaunch.decide(store: store) else { Issue.record("expected relock"); return }
        #expect(state.unclean && state.binding == binding && state.lockId == saved.lockId && state.verifier == saved.verifier)
        #expect(store.load()?.unclean == true)
        let session = NativeExamSession(restoring: state.contest, binding: state.binding)
        #expect(session.binding == NativeDeviceContext.Binding(binding))
    }

    @Test func wrongAttemptsSurviveTheRelaunch() throws {
        let id = NativeExit.newLockId()
        let saved = NativeLockState(lockId: id, verifier: NativeExit.makeVerifier(code: "123456", lockId: id, iterations: 1000), created: Date())
        let store = InMemoryNativeLockStore(saved)
        let now = Date()
        for _ in 0..<2 { _ = NativeExit.attempt(typed: "000000", store: store, now: now) }
        guard case .relock(let state) = NativeRelaunch.decide(store: store) else { Issue.record("expected relock"); return }
        #expect(state.wrong == 2)
    }

    @Test func aLockFromAnEarlierBootDoesNotLockAgain() {
        var saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date())
        saved.boot = "boot-A"
        let store = InMemoryNativeLockStore(saved)
        guard case .rebooted(let state) = NativeRelaunch.decide(store: store, boot: "boot-B") else {
            Issue.record("expected the home screen after a restart"); return
        }
        #expect(state.lockId == saved.lockId)
    }

    @Test func aKillInTheSameBootStillLocksAgain() {
        var saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date())
        saved.boot = "boot-A"
        let store = InMemoryNativeLockStore(saved)
        guard case .relock = NativeRelaunch.decide(store: store, boot: "boot-A") else {
            Issue.record("expected relock"); return
        }
    }

    @Test func theWatchdogRelockAlwaysLocks() {
        var saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date())
        saved.boot = "boot-A"
        let store = InMemoryNativeLockStore(saved)
        guard case .relock = NativeRelaunch.decide(store: store, relockRequested: true, boot: "boot-B") else {
            Issue.record("expected relock"); return
        }
    }

    @Test func aLockWithoutASavedBootLocksAgain() {
        // An older build's lock, or one whose Keychain copy was lost: the wall clock never ends it.
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(timeIntervalSince1970: 1_800_000_000))
        guard case .relock = NativeRelaunch.decide(store: InMemoryNativeLockStore(saved), boot: "boot-B") else {
            Issue.record("expected relock"); return
        }
    }

    @Test func theBootIsSavedAndSurvivesEncoding() throws {
        var s = NativeLockState(lockId: "K7QM3XPA", created: Date(timeIntervalSince1970: 1_800_000_000))
        s.boot = "boot-A"
        let back = try JSONDecoder().decode(NativeLockState.self, from: JSONEncoder().encode(s))
        #expect(back.boot == "boot-A")
        // Only the Keychain copy (first) counts; an edited file copy cannot supply the boot.
        #expect(DualNativeLockStore.merged(s, NativeLockState(lockId: "K7QM3XPA", created: s.created)).boot == "boot-A")
        #expect(DualNativeLockStore.merged(NativeLockState(lockId: "K7QM3XPA", created: s.created), s).boot == nil)
    }
}

@Suite struct NativeLockModeTests {
    @Test func activeModeRefusesEveryUnlockButItsOwnRelease() {
        var mode = NativeLockMode()
        #expect(mode.allowsUnlock)
        mode.begin()
        let ok = mode.acquired(true, recovering: false)
        #expect(ok)
        #expect(mode.active && !mode.allowsUnlock)
        mode.beginRelease()
        #expect(mode.allowsUnlock)
        mode.endRelease()
        #expect(!mode.active && mode.allowsUnlock)
    }

    @Test func failedFreshLockLeavesNoMode_failedRelaunchKeepsIt() {
        var fresh = NativeLockMode()
        fresh.begin()
        let freshOk = fresh.acquired(false, recovering: false)
        #expect(!freshOk && !fresh.active)
        var relaunch = NativeLockMode()
        relaunch.begin()
        let relaunchOk = relaunch.acquired(false, recovering: true)
        #expect(!relaunchOk && relaunch.active && !relaunch.allowsUnlock)
    }

    @Test func nativeLockRefusesPreunlockInEveryState() {
        for locked in [true, false] {
            for preLocked in [true, false] {
                for hasExam in [true, false] {
                    #expect(PreLockState.onPreUnlock(locked: locked, preLocked: preLocked, hasExam: hasExam, nativeLock: true) == .refuse)
                }
            }
        }
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: false) == .unlock)
    }
}

@Suite struct NativeWatchdogTests {
    @Test func nativeModeComesFromTheLaunchArgument() {
        #expect(Watchdog.arguments(parent: 42, preLock: false, native: true) == ["--watchdog", "42", "--native"])
        let parsed = Watchdog.parseArguments(["FairPlayGuard"] + Watchdog.arguments(parent: 42, preLock: false, native: true))
        #expect(parsed?.native == true && parsed?.preLock == false && parsed?.parent == 42)
        #expect(Watchdog.parseArguments(["FairPlayGuard", "--watchdog", "42"])?.native == false)
    }

    @Test func appGoneAlwaysRelocksUnlessItSaidReleased() {
        #expect(Watchdog.nativeStep(parentAlive: false, released: false) == .relock)
        #expect(Watchdog.nativeStep(parentAlive: false, released: true) == .exit)
        #expect(Watchdog.nativeStep(parentAlive: true, released: true) == .exit)
        #expect(Watchdog.nativeStep(parentAlive: true, released: false) == .keepWatching)
    }

    @Test func onlyTheReleaseByteCountsAsReleased() {
        #expect(Watchdog.readReleased([Watchdog.releaseByte]))
        #expect(Watchdog.readReleased([0, Watchdog.releaseByte]))
        #expect(!Watchdog.readReleased([]))
        #expect(!Watchdog.readReleased([0x00, 0x0a, 0x20]))
    }

    @Test func aNativeLockKeepsItsWatchdogWhateverHappensToTheMarker() {
        #expect(Watchdog.shouldKeepWatchdog(native: true, markerExists: false))
        #expect(Watchdog.shouldKeepWatchdog(native: true, markerExists: true))
        #expect(Watchdog.shouldKeepWatchdog(native: false, markerExists: true))
        #expect(!Watchdog.shouldKeepWatchdog(native: false, markerExists: false))
    }

    @Test func nativeParentGoneNeverTakesThePreLockSilentPath() {
        let preLockMarker = Watchdog.markerText(exam: nil, preLock: true)
        #expect(Watchdog.onParentGone(preLockMode: true, markerText: preLockMarker, nativeMode: true) == .reportAndReopen)
    }
}

@MainActor @Suite struct NativeLockControllerTests {
    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }
    private final class Probe { var locked = false, acquires = 0, released = 0 }

    private func start(_ setup: NativeLockSetup, probe: Probe, root: NSView) -> ProtectedBrowserController {
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { probe.acquires += 1; probe.locked = true; return true },
            ownsLock: { probe.locked }, release: { probe.locked = false; probe.released += 1 }, load: { _, _ in })
        effects.setup = setup
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        let fields = descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }
        fields[0].stringValue = "https://university.example.edu/"
        descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }?.performClick(nil)
        return controller
    }

    @Test func escrowFailureNeverLocksAndShowsTheAgreedMessage() async throws {
        let probe = Probe()
        let store = InMemoryNativeLockStore()
        let key = Curve25519.KeyAgreement.PrivateKey().publicKey.rawRepresentation
        var setup = NativeLockSetup(store: store, publicKey: key, isDev: false, device: { "d" },
                                    transport: { _, done in done(503, nil) }, loginItem: NoLoginItem(), iterations: 2_000)
        FakeEscrowClock().install(in: &setup)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = start(setup, probe: probe, root: root)
        defer { controller.lockEnded() }
        let texts = descendants(root).compactMap { $0 as? NSTextField }.map(\.stringValue)
        #expect(texts.contains("Emergency exit could not be set up; your Mac is still unlocked. Check your internet connection and try again, or contact your instructor."))
        #expect(probe.acquires == 0 && !probe.locked)
        #expect(store.load() == nil)
        #expect(!descendants(root).contains { $0 is WKWebView })
    }

    @Test func missingServerKeyInAReleaseBuildNeverLocks() async throws {
        let probe = Probe()
        let store = InMemoryNativeLockStore()
        var setup = NativeLockSetup(store: store, publicKey: nil, isDev: false, device: { "d" },
                                    transport: { _, done in done(201, Data(#"{"ok":true}"#.utf8)) }, loginItem: NoLoginItem(), iterations: 2_000)
        FakeEscrowClock().install(in: &setup)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = start(setup, probe: probe, root: root)
        defer { controller.lockEnded() }
        #expect(probe.acquires == 0 && store.load() == nil)
        #expect(descendants(root).compactMap { $0 as? NSTextField }.contains { $0.stringValue.contains("will not lock your Mac") })
    }

    @Test func successfulEscrowSavesTheStateThenLocksAndRegistersTheLoginItem() async throws {
        let probe = Probe()
        let store = InMemoryNativeLockStore()
        let item = RecordingLoginItem()
        let key = Curve25519.KeyAgreement.PrivateKey().publicKey.rawRepresentation
        let setup = NativeLockSetup(store: store, publicKey: key, isDev: false, device: { "d" },
                                    transport: { _, done in
                                        #expect(store.load() == nil)
                                        done(201, Data(#"{"ok":true}"#.utf8))
                                    }, loginItem: item, iterations: 2_000)
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = start(setup, probe: probe, root: root)
        defer { controller.lockEnded() }
        #expect(await waitUntil { probe.locked })
        #expect(store.load() != nil)
        // A same-boot log-out and log-in must reopen FairPlay into the lock: the item is registered while the lock is held.
        #expect(await waitUntil { item.registered == 1 })
        #expect(item.unregistered == 0)
    }

    @Test func aLockThatCannotBeTakenDeletesTheSavedState() async throws {
        let store = InMemoryNativeLockStore()
        let item = RecordingLoginItem()
        var setup = NativeLockSetup.dev
        setup.store = store; setup.loginItem = item
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { false }, ownsLock: { false }, release: {}, load: { _, _ in })
        effects.setup = setup
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { false }, didClose: {}, effects: effects)
        controller.show(in: root)
        defer { controller.lockEnded() }
        descendants(root).compactMap { $0 as? NSTextField }.filter { $0.isEditable }[0].stringValue = "https://university.example.edu/"
        descendants(root).compactMap { $0 as? NSButton }.first { $0.title == "Lock and open" }?.performClick(nil)
        #expect(await waitUntil { store.load() == nil && item.unregistered > 0 })
        #expect(item.registered == 0)
    }

    @Test func relaunchRestoresTheSavedBindingAndReportsUnclean() async throws {
        let binding = NativeLockState.Binding(session: "s1", device: "d", user: "7", contest: "exam")
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(),
                                    contest: "exam", binding: binding, unclean: true)
        let store = InMemoryNativeLockStore(saved)
        var setup = NativeLockSetup.dev
        setup.store = store
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { false }, ownsLock: { true }, release: {}, load: { _, _ in })
        effects.setup = setup
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { true }, didClose: {}, effects: effects)
        controller.resumeNativeLock(saved, in: root)
        defer { controller.lockEnded() }
        #expect(controller.isOpen)
        #expect(controller.restoredBinding == NativeDeviceContext.Binding(binding))
        #expect(await waitUntil { descendants(root).contains { $0 is WKWebView } })
        #expect(store.load() == saved)
    }
}

@Suite struct LoadRetryPolicyTests {
    @Test func retriesStartQuickAndNeverGetSlowerThanThirtySeconds() {
        var policy = LoadRetryPolicy()
        let waits = (0..<8).map { _ in policy.failed() }
        #expect(waits == [2, 4, 8, 15, 30, 30, 30, 30])
        policy.succeeded()
        let afterSuccess = policy.failed()
        #expect(afterSuccess == 2)
    }

    @Test func vpnPromptComesOncePerLockAndOnlyWithAClientInstalled() {
        var policy = LoadRetryPolicy()
        _ = policy.failed()
        let early = policy.shouldPrompt(vpnInstalled: true)
        _ = policy.failed()
        let noClient = policy.shouldPrompt(vpnInstalled: false)
        let first = policy.shouldPrompt(vpnInstalled: true)
        let again = policy.shouldPrompt(vpnInstalled: true)
        #expect(!early && !noClient && first && !again)
    }

    @Test func onlyTheTwoCiscoClientsAreOffered() {
        #expect(VPNLauncher.installed(exists: { _ in false }) == nil)
        #expect(VPNLauncher.installed(exists: { $0.hasSuffix("Cisco Secure Client.app") }, trusted: { _ in true })?.name == "Cisco Secure Client")
        #expect(VPNLauncher.installed(exists: { $0.contains("AnyConnect") }, trusted: { _ in true })?.name == "Cisco AnyConnect")
        #expect(Allowlist.keepsId("com.cisco.secureclient.gui"))
        #expect(Allowlist.keepsId("com.cisco.Cisco-AnyConnect-Secure-Mobility-Client"))
        #expect(!Allowlist.keepsId("com.cisco.other"))
    }

    @Test func onlyThePinnedGuiAppsMayHoldTheFront() {
        func ok(_ id: String?, _ path: String?, signed: Bool = true) -> Bool {
            VPNLauncher.mayHoldFront(bundleId: id, bundleURL: path.map { URL(fileURLWithPath: $0) }, signedByCisco: { signed })
        }
        let secure = VPNLauncher.clients[0], legacy = VPNLauncher.clients[1]
        #expect(ok(secure.bundleId, secure.path))
        #expect(ok(legacy.bundleId, legacy.path))
        #expect(!ok(secure.bundleId, secure.path, signed: false))
        #expect(!ok("com.cisco.secureclient.dart", "/Applications/Cisco/Cisco Secure Client - DART.app"))
        #expect(!ok("com.cisco.secureclient.uninstaller", "/Applications/Cisco/Uninstall Cisco Secure Client.app"))
        #expect(!ok("com.cisco.secureclient.dartuninstaller", "/Applications/Cisco/Uninstall Cisco Secure Client - DART.app"))
        #expect(!ok("com.cisco.other", secure.path))
        #expect(!ok(secure.bundleId, "/Users/student/Downloads/Cisco Secure Client.app"))
        #expect(!ok(secure.bundleId, legacy.path))
        #expect(!ok(nil, secure.path) && !ok(secure.bundleId, nil))
    }

    @Test func vpnUsedSecondsSurviveARelaunch() {
        var b = VPNWindowBudget()
        let t = Date()
        _ = b.request(now: t, grace: 300)
        #expect(b.committed == 300)
        b.close(now: t.addingTimeInterval(100))
        #expect(b.committed == 100)
        let again = VPNWindowBudget(used: 880)
        #expect(!again.canOpen(now: t, cap: 880) && again.canOpen(now: t))
        #expect(VPNWindowBudget(used: 5000).used == VPNLauncher.totalCap)
        var s = NativeLockState(lockId: "a", created: t); s.vpnUsed = 700
        var o = s; o.vpnUsed = 100
        #expect(DualNativeLockStore.merged(o, s).vpnUsed == 700)
    }

    @Test func aBundleAtTheCiscoPathIsOpenedOnlyWhenItsSignatureIsTrusted() {
        #expect(VPNLauncher.installed(exists: { _ in true }, trusted: { _ in false }) == nil)
        #expect(VPNLauncher.installed(exists: { _ in true }, trusted: { _ in true })?.name == "Cisco Secure Client")
        let client = VPNLauncher.clients[0]
        #expect(!VPNLauncher.open(client, trusted: { _ in false }))
    }

    @Test func requirementNamesAppleAnchorAndEveryCiscoTeam() {
        let r = VPNLauncher.requirement
        #expect(r.hasPrefix("anchor apple generic and ("))
        for id in VPNLauncher.ciscoTeamIDs { #expect(r.contains("certificate leaf[subject.OU] = \"\(id)\"")) }
        #expect(!VPNLauncher.ciscoTeamIDs.isEmpty)
    }

    @Test func aBundleWithoutACiscoSignatureIsNotTrusted() {
        #expect(!VPNLauncher.isTrusted(path: "/System/Applications/Calculator.app"))
        #expect(!VPNLauncher.isTrusted(path: "/nonexistent/Cisco Secure Client.app"))
    }

    @Test func vpnWindowsShareOneCapAndNeverOverlap() {
        var b = VPNWindowBudget()
        let t0 = Date(timeIntervalSince1970: 1_000)
        func at(_ s: TimeInterval) -> Date { t0.addingTimeInterval(s) }
        #expect(b.request(now: at(0)) != nil)
        #expect(b.request(now: at(10)) == nil)
        b.close(now: at(100))
        #expect(b.request(now: at(110)) != nil)
        let stillOpen1 = b.isOpen(now: at(411))
        #expect(!stillOpen1)
        #expect(b.request(now: at(500)) != nil)
        let stillOpen2 = b.isOpen(now: at(801))
        #expect(!stillOpen2)
        #expect(b.used == 700)
        #expect(b.request(now: at(900)) == at(1100))
        _ = b.isOpen(now: at(1200))
        #expect(b.request(now: at(1300)) == nil)
        #expect(!b.canOpen(now: at(1300)))
    }
}
