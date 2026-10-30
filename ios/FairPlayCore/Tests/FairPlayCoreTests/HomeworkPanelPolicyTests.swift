import Testing
import Foundation
@testable import FairPlayCore

@Suite struct HomeworkPanelPolicyTests {

    @Test func opensOnlyWhenIdleAndParsed() {
        #expect(HomeworkPanelPolicy.open(examActive: false, deviceTestRunning: false, panelOpen: false, parsed: true) == .start)
    }

    @Test func examAndDeviceTestRefuseFirst() {
        let busy = HomeworkPanelPolicy.Open.refuse(HomeworkLogin.notDuringExam)
        #expect(HomeworkPanelPolicy.open(examActive: true, deviceTestRunning: false, panelOpen: true, parsed: false) == busy)
        #expect(HomeworkPanelPolicy.open(examActive: false, deviceTestRunning: true, panelOpen: false, parsed: true) == busy)
    }

    @Test func secondOpenOrBadMessageAnswersStartFailed() {
        let failed = HomeworkPanelPolicy.Open.refuse(HomeworkLogin.startFailed)
        #expect(HomeworkPanelPolicy.open(examActive: false, deviceTestRunning: false, panelOpen: true, parsed: true) == failed)
        #expect(HomeworkPanelPolicy.open(examActive: false, deviceTestRunning: false, panelOpen: false, parsed: false) == failed)
    }

    @Test func hiddenWhileAnyCoverIsUp() {
        #expect(!HomeworkPanelPolicy.hidden(blockingShown: false, privacyShown: false))
        #expect(HomeworkPanelPolicy.hidden(blockingShown: true, privacyShown: false))
        #expect(HomeworkPanelPolicy.hidden(blockingShown: false, privacyShown: true))
        #expect(HomeworkPanelPolicy.hidden(blockingShown: true, privacyShown: true))
    }

    @Test func closesOnLockSessionExternalAndDeviceTest() {
        for a in ["lock", "prelock", "session", "openExternal", "deviceTestStart"] { #expect(HomeworkPanelPolicy.closes(on: a)) }
        for a in ["hello", "openHomeworkLogin", "clearHomeworkLogin", "open_box", "close_box"] { #expect(!HomeworkPanelPolicy.closes(on: a)) }
    }
}
