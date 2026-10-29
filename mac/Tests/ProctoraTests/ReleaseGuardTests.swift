import Testing
import Foundation
@testable import Proctora

@Suite struct ReleaseGuardTests {

    @Test func releaseRefusesEmptyPins() {
        #expect(!Config.acceptable(isDev: false, scheme: "https", pins: []))
        #expect(Config.acceptable(isDev: false, scheme: "https", pins: ["AAAA"]))
        #expect(!Config.acceptable(isDev: false, scheme: "http", pins: ["AAAA"]))
        #expect(Config.acceptable(isDev: true, scheme: "http", pins: []))
    }

    @Test func updateRefusedDuringDeviceTest() {
        #expect(!Updater.mayUpdateNow(locked: false, examBound: false, devicePreview: true))
        #expect(!Updater.mayUpdateNow(locked: true, examBound: false, devicePreview: false))
        #expect(!Updater.mayUpdateNow(locked: false, examBound: true, devicePreview: false))
        #expect(Updater.mayUpdateNow(locked: false, examBound: false, devicePreview: false))
    }
}
