import Testing
import Foundation
@testable import FairPlayCore

@Suite struct DeviceTestTests {

    private final class Box {
        var now = Date(timeIntervalSince1970: 1_000)
        var begins = 0
        var restores = 0
        var messages: [DeviceTest.Message] = []
    }

    private func make() -> (DeviceTest, Box) {
        let b = Box()
        let t = DeviceTest(clock: { b.now }, begin: { b.begins += 1 }, restore: { b.restores += 1 },
                           emit: { b.messages.append($0) })
        return (t, b)
    }

    private let ok = DeviceTestContext(fromServerPage: true, examActive: false, websiteMode: false)

    @Test func clampRange() {
        #expect(DeviceTest.clamp(nil) == 60)
        #expect(DeviceTest.clamp("30") == 60)
        #expect(DeviceTest.clamp(true) == 60)
        #expect(DeviceTest.clamp(3) == 10)
        #expect(DeviceTest.clamp(-5) == 10)
        #expect(DeviceTest.clamp(500) == 90)
        #expect(DeviceTest.clamp(45) == 45)
        #expect(DeviceTest.clamp(45.4) == 45)
        #expect(DeviceTest.clamp(Double.nan) == 60)
        #expect(DeviceTest.clamp(NSNumber(value: 20)) == 20)
    }

    @Test func startsAndEnds() {
        let (t, b) = make()
        t.start(seconds: 30, context: ok)
        #expect(t.isRunning)
        #expect(b.begins == 1)
        #expect("\(b.messages)" == "\([DeviceTest.Message.started(remaining: 30)])")
    }

    @Test func refusedWhenExamActive() {
        let (t, b) = make()
        t.start(seconds: 30, context: DeviceTestContext(fromServerPage: true, examActive: true, websiteMode: false))
        #expect(!t.isRunning)
        #expect(b.begins == 0 && b.restores == 0)
        #expect(b.messages == [.ended(.refused, remaining: 0)])
    }

    @Test func refusedWhenNotServerPage() {
        let (t, b) = make()
        t.start(seconds: 30, context: DeviceTestContext(fromServerPage: false, examActive: false, websiteMode: false))
        #expect(!t.isRunning)
        #expect(b.begins == 0)
        #expect(b.messages == [.ended(.refused, remaining: 0)])
    }

    @Test func refusedInWebsiteMode() {
        let (t, b) = make()
        t.start(seconds: 30, context: DeviceTestContext(fromServerPage: true, examActive: false, websiteMode: true))
        #expect(!t.isRunning)
        #expect(b.begins == 0)
        #expect(b.messages == [.ended(.refused, remaining: 0)])
    }

    @Test func refusedWhenAlreadyRunningKeepsTheRunningOne() {
        let (t, b) = make()
        t.start(seconds: 30, context: ok)
        b.now.addTimeInterval(10)
        t.start(seconds: 90, context: ok)
        #expect(t.isRunning)
        #expect(b.begins == 1 && b.restores == 0)
        #expect(t.remaining == 20)
        #expect(b.messages.last == .ended(.refused, remaining: 0))
    }

    @Test func timeoutRestores() {
        let (t, b) = make()
        t.start(seconds: 10, context: ok)
        b.now.addTimeInterval(9)
        t.tick()
        #expect(t.isRunning)
        b.now.addTimeInterval(1)
        t.tick()
        #expect(!t.isRunning)
        #expect(b.restores == 1)
        #expect(b.messages.last == .ended(.timeout, remaining: 0))
    }

    @Test func leaveRestoresWithRemaining() {
        let (t, b) = make()
        t.start(seconds: 60, context: ok)
        b.now.addTimeInterval(15)
        t.end(.leave)
        #expect(!t.isRunning)
        #expect(b.restores == 1)
        #expect(b.messages.last == .ended(.leave, remaining: 45))
    }

    @Test func endWhenIdleDoesNothing() {
        let (t, b) = make()
        t.end(.leave)
        t.tick()
        #expect(b.restores == 0 && b.messages.isEmpty)
    }

    @Test func endTwiceRestoresOnce() {
        let (t, b) = make()
        t.start(seconds: 60, context: ok)
        t.end(.leave)
        t.end(.leave)
        #expect(b.restores == 1)
        #expect(b.messages.count == 2)
    }

    @Test func shortBackgroundSurvives() {
        let (t, b) = make()
        t.start(seconds: 60, context: ok)
        t.appBackgrounded()
        b.now.addTimeInterval(2)
        t.tick()
        t.appForegrounded()
        #expect(t.isRunning)
        b.now.addTimeInterval(DeviceTest.backgroundGrace + 5)
        t.tick()
        #expect(t.isRunning)
    }

    @Test func longBackgroundEndsOnTick() {
        let (t, b) = make()
        t.start(seconds: 60, context: ok)
        t.appBackgrounded()
        b.now.addTimeInterval(DeviceTest.backgroundGrace + 1)
        t.tick()
        #expect(!t.isRunning)
        #expect(b.restores == 1)
        #expect(b.messages.last == .ended(.leave, remaining: 55))
    }

    @Test func longBackgroundEndsOnForeground() {
        let (t, b) = make()
        t.start(seconds: 60, context: ok)
        t.appBackgrounded()
        b.now.addTimeInterval(DeviceTest.backgroundGrace + 1)
        t.appForegrounded()
        #expect(!t.isRunning)
        #expect(b.restores == 1)
    }

    @Test func canRestartAfterEnd() {
        let (t, b) = make()
        t.start(seconds: 10, context: ok)
        t.end(.leave)
        t.start(seconds: 10, context: ok)
        #expect(t.isRunning)
        #expect(b.begins == 2 && b.restores == 1)
    }
}
