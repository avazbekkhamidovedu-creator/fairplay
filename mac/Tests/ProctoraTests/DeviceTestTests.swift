import Testing
import Foundation
@testable import Proctora

private final class FakeEffects: DeviceTestEffects {
    var began = 0
    var restored = 0
    var messages: [[String: Any]] = []
    func deviceTestBegin() { began += 1 }
    func deviceTestRestore() { restored += 1 }
    func deviceTestNotify(_ message: [String: Any]) { messages.append(message) }
    var last: [String: Any]? { messages.last }
}

private final class Clock {
    var now: TimeInterval = 1000
}

private let ok = DeviceTestSession.Context(examActive: false, fromExamPage: true, websiteExam: false)

private func make() -> (DeviceTestSession, FakeEffects, Clock) {
    let fx = FakeEffects(), clock = Clock()
    return (DeviceTestSession(clock: { clock.now }, effects: fx), fx, clock)
}

@Suite struct DeviceTestTests {

    @Test func restoreLeavesARealLockAlone() {
        #expect(DeviceTestSession.restoresScreen(locked: false))
        #expect(!DeviceTestSession.restoresScreen(locked: true))
    }

    @Test func clampsSeconds() {
        #expect(DeviceTestSession.clamp(nil) == 60)
        #expect(DeviceTestSession.clamp("30") == 60)
        #expect(DeviceTestSession.clamp(true) == 60)
        #expect(DeviceTestSession.clamp(Double.nan) == 60)
        #expect(DeviceTestSession.clamp(0) == 10)
        #expect(DeviceTestSession.clamp(-5) == 10)
        #expect(DeviceTestSession.clamp(10) == 10)
        #expect(DeviceTestSession.clamp(45) == 45)
        #expect(DeviceTestSession.clamp(90) == 90)
        #expect(DeviceTestSession.clamp(600) == 90)
        #expect(DeviceTestSession.clamp(Double.infinity) == 60)
        #expect(DeviceTestSession.clamp(45.5) == 46)
        #expect(DeviceTestSession.clamp(45.4) == 45)
        #expect(DeviceTestSession.clamp(1e30) == 90)
        #expect(DeviceTestSession.clamp(-1e30) == 10)
    }

    @Test func startReportsClampedRemaining() {
        let (s, fx, _) = make()
        #expect(s.start(seconds: 1000, context: ok))
        #expect(s.isRunning)
        #expect(fx.began == 1)
        #expect("\(fx.last?["state"] ?? "")" == "started")
        #expect("\(fx.last?["remaining"] ?? "")" == "90")
        #expect("\(fx.last?["type"] ?? "")" == "deviceTest")
    }

    private func expectRefused(_ context: DeviceTestSession.Context, _ label: String) {
        let (s, fx, _) = make()
        #expect(!s.start(seconds: 30, context: context), "\(label)")
        #expect(!s.isRunning, "\(label)")
        #expect(fx.began == 0 && fx.restored == 0, "\(label)")
        #expect("\(fx.last?["state"] ?? "")" == "ended", "\(label)")
        #expect("\(fx.last?["reason"] ?? "")" == "refused", "\(label)")
    }

    @Test func refusals() {
        expectRefused(.init(examActive: true, fromExamPage: true, websiteExam: false), "exam lock or session")
        expectRefused(.init(examActive: false, fromExamPage: false, websiteExam: false), "foreign origin")
        expectRefused(.init(examActive: false, fromExamPage: true, websiteExam: true), "website exam")
    }

    @Test func secondStartIsRefusedAndTheFirstKeepsRunning() {
        let (s, fx, _) = make()
        s.start(seconds: 30, context: ok)
        #expect(!s.start(seconds: 30, context: ok))
        #expect(s.isRunning)
        #expect(fx.began == 1 && fx.restored == 0)
        #expect("\(fx.last?["reason"] ?? "")" == "refused")
    }

    @Test func timeoutEndsAndRestores() {
        let (s, fx, clock) = make()
        s.start(seconds: 20, context: ok)
        clock.now += 19.5
        s.tick()
        #expect(s.isRunning)
        #expect(s.remaining == 1)
        clock.now += 0.5
        s.tick()
        #expect(!s.isRunning)
        #expect(fx.restored == 1)
        #expect("\(fx.last?["reason"] ?? "")" == "timeout")
        #expect("\(fx.last?["state"] ?? "")" == "ended")
    }

    @Test func everyEndPathRestoresOnce() {
        for reason: DeviceTestSession.Reason in [.leave, .error, .timeout] {
            let (s, fx, _) = make()
            s.start(seconds: 30, context: ok)
            s.end(reason)
            s.end(.leave)
            #expect(fx.restored == 1, "\(reason)")
            #expect("\(fx.last?["reason"] ?? "")" == reason.rawValue)
            #expect(!s.isRunning)
        }
    }

    @Test func endWhenNotRunningDoesNothing() {
        let (s, fx, _) = make()
        s.end(.leave)
        s.tick()
        #expect(fx.restored == 0 && fx.messages.isEmpty)
    }

    @Test func canRunAgainAfterAnEnd() {
        let (s, fx, clock) = make()
        s.start(seconds: 10, context: ok)
        clock.now += 11
        s.tick()
        #expect(s.start(seconds: 10, context: ok))
        #expect(fx.began == 2)
        #expect(s.remaining == 10)
    }

    @Test func realLockEndsTheTestWithLeave() {
        let (s, fx, _) = make()
        s.start(seconds: 30, context: ok)
        s.end(.leave)
        #expect(fx.restored == 1)
        #expect("\(fx.last?["reason"] ?? "")" == "leave")
    }

    @Test func previewKeyPressesAreNotForwarded() {
        #expect(!DeviceTestSession.forwardsEvents(previewRunning: true))
        #expect(DeviceTestSession.forwardsEvents(previewRunning: false))
    }

    @Test func noWatchdogMarkerIsTouched() {
        let before = FileManager.default.fileExists(atPath: Watchdog.markerURL.path)
        let (s, _, _) = make()
        s.start(seconds: 30, context: ok)
        s.end(.leave)
        #expect(FileManager.default.fileExists(atPath: Watchdog.markerURL.path) == before)
    }
}
