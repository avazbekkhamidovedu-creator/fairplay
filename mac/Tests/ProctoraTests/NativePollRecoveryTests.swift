import Foundation
import AppKit
import Testing
import WebKit
import CryptoKit
@testable import Proctora

@MainActor @Suite struct NativePollRecoveryTests {
    private final class Clock {
        struct Item { let id: Int; let at: TimeInterval; let fire: () -> Void }
        var now: TimeInterval = 0
        var items: [Item] = []
        var next = 0
        var released = 0
        var closed = 0
        func schedule(_ delay: TimeInterval, _ fire: @escaping () -> Void) -> () -> Void {
            next += 1
            let id = next
            items.append(Item(id: id, at: now + delay, fire: fire))
            return { [weak self] in self?.items.removeAll { $0.id == id } }
        }
        func advance(_ seconds: TimeInterval) {
            let target = now + seconds
            while let due = items.filter({ $0.at <= target }).min(by: { $0.at < $1.at }) {
                items.removeAll { $0.id == due.id }
                now = due.at
                due.fire()
            }
            now = target
        }
    }

    private final class Server {
        let key = Curve25519.Signing.PrivateKey()
        let device = String(repeating: "ab", count: 32)
        let session = UUID().uuidString
        var paths: [String] = []
        enum Reply { case state(String, hold: Bool = false), status(Int, [String: Any]) }
        var reply: (String) -> Reply = { _ in .state("active") }

        func nonce(_ request: URLRequest) -> String {
            if let body = request.httpBody.flatMap({ String(data: $0, encoding: .utf8) }),
               let part = body.split(separator: "&").first(where: { $0.hasPrefix("nonce=") }) { return String(part.dropFirst(6)) }
            return URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems?.first { $0.name == "nonce" }?.value ?? ""
        }

        func signed(_ state: String, nonce: String, hold: Bool = false) -> [String: Any] {
            var payload: [String: Any] = ["v": 2, "nonce": nonce, "expires": Int(Date().timeIntervalSince1970) + 30, "session": session,
                "device": device, "user": "7", "contest": "exam", "participation": "p", "state": state, "lease_until": 0]
            if hold { payload["hold"] = true }
            let bytes = try! JSONSerialization.data(withJSONObject: payload)
            return ["payload": bytes.base64EncodedString(), "signature": try! key.signature(for: bytes).base64EncodedString()]
        }

        func transport(_ request: URLRequest, _ done: @escaping (Data?, URLResponse?, Error?) -> Void) {
            let path = String(request.url!.path.dropFirst("/api/fairplay/native/".count))
            paths.append(path)
            let status: Int, envelope: [String: Any]
            switch reply(path) {
            case let .state(name, hold): status = 200; envelope = signed(name, nonce: nonce(request), hold: hold)
            case let .status(code, body): status = code; envelope = body
            }
            let data = try? JSONSerialization.data(withJSONObject: envelope)
            done(data, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil), nil)
        }
    }

    private func descendants(_ root: NSView) -> [NSView] { [root] + root.subviews.flatMap { descendants($0) } }
    private func waitUntil(_ condition: () -> Bool) async -> Bool {
        for _ in 0..<300 {
            if condition() { return true }
            try? await Task.sleep(for: .milliseconds(20))
        }
        return condition()
    }

    private func start(_ clock: Clock, _ server: Server, unclean: Bool) async -> (ProtectedBrowserController, NSView) {
        let binding = NativeLockState.Binding(session: server.session, device: server.device, user: "7", contest: "exam")
        let saved = NativeLockState(lockId: NativeExit.newLockId(), created: Date(), contest: "exam", binding: binding, unclean: unclean)
        var effects = ProtectedBrowserController.Effects(checks: { [] }, acquire: { false }, ownsLock: { true },
                                                         release: { clock.released += 1 }, load: { _, _ in })
        effects.setup = NativeLockSetup.dev.with(store: InMemoryNativeLockStore(saved))
        effects.random = { 0.5 }
        effects.schedule = { clock.schedule($0, $1) }
        effects.policyKey = server.key.publicKey.rawRepresentation
        effects.deviceFingerprint = server.device
        effects.nativeTransport = { server.transport($0, $1) }
        let root = NSView(frame: NSRect(x: 0, y: 0, width: 1024, height: 768))
        let controller = ProtectedBrowserController(busy: { true }, didClose: { clock.closed += 1 }, effects: effects)
        controller.resumeNativeLock(saved, in: root)
        _ = await waitUntil { descendants(root).contains { $0 is WKWebView } }
        return (controller, root)
    }

    @Test func thePollSurvivesASecurityCheckOverlayWhileOnHold() async throws {
        let clock = Clock(), server = Server()
        server.reply = { _ in .state("active", hold: true) }
        let (controller, _) = await start(clock, server, unclean: false)
        defer { controller.lockEnded() }
        clock.advance(0)
        #expect(await waitUntil { controller.isOnHold })
        controller.setHeld(true)
        let before = server.paths.count
        clock.advance(60)
        #expect(server.paths.count == before, "nothing is sent while the overlay is up")
        controller.setHeld(false)
        #expect(!clock.items.isEmpty, "clearing the overlay restarts the poll while the exam is on hold")
        clock.advance(60)
        #expect(await waitUntil { server.paths.count > before })
        #expect(controller.isOpen)
    }

    @Test func aRefusedRecoverAfterACrashFallsBackToTheBoundSessionRenewWhichUnlocks() async throws {
        let clock = Clock(), server = Server()
        server.reply = { path in path.hasPrefix("recover/") ? .status(409, ["reason": "other_device", "error": "Moved."]) : .state("released") }
        let (controller, root) = await start(clock, server, unclean: true)
        #expect(controller.pendingUncleanRecover)
        clock.advance(0)
        #expect(await waitUntil { server.paths.count == 1 })
        #expect(server.paths == ["recover/exam"])
        #expect(await waitUntil { !clock.items.isEmpty })
        #expect(controller.isOpen && clock.released == 0)
        clock.advance(60)
        #expect(await waitUntil { clock.released == 1 })
        #expect(server.paths == ["recover/exam", "session/\(server.session)/renew"])
        #expect(!controller.isOpen)
        #expect(!descendants(root).contains { $0 is WKWebView })
    }

    @Test func aSuccessfulFirstRecoverAfterACrashStillClearsThePendingFlag() async throws {
        let clock = Clock(), server = Server()
        server.reply = { _ in .state("active") }
        let (controller, _) = await start(clock, server, unclean: true)
        defer { controller.lockEnded() }
        clock.advance(0)
        #expect(await waitUntil { !controller.pendingUncleanRecover })
        #expect(server.paths.first == "recover/exam")
    }

    @Test func aReleasedStateThroughTheRealRequestPathClosesTheLockAndReturnsHome() async throws {
        let clock = Clock(), server = Server()
        server.reply = { _ in .state("released") }
        let (controller, root) = await start(clock, server, unclean: false)
        clock.advance(0)
        #expect(await waitUntil { clock.released == 1 })
        #expect(!controller.isOpen)
        #expect(clock.closed == 1)
        #expect(!descendants(root).contains { $0 is WKWebView })
        #expect(clock.items.isEmpty)
    }
}
