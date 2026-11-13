import Foundation
import Testing
@testable import Proctora

@Suite struct ExamLaunchTests {
    @Test func onlyVersionedExamReferences() {
        #expect(ExamLaunch.parse(URL(string: "fairplay://launch?v=1&exam=c000002")!) == "c000002")
        for raw in ["https://launch?v=1&exam=c1", "fairplay://evil?v=1&exam=c1", "fairplay://launch/path?v=1&exam=c1", "fairplay://launch?v=1&exam=c1&server=https://evil.test", "fairplay://launch?v=1&exam=c1&exam=c2", "fairplay://launch?v=2&exam=c1", "fairplay://user@launch?v=1&exam=c1", "fairplay://launch?v=1&exam=..%2Fteacher", "fairplay://launch?v=1&exam=c1#other"] {
            #expect(ExamLaunch.parse(URL(string: raw)!) == nil)
        }
    }
    @Test func strictFilesAndPinnedDestination() {
        #expect(ExamLaunch.parseFile(Data(#"{"version":1,"exam_key":"c000002"}"#.utf8)) == "c000002")
        for raw in [#"{"version":true,"exam_key":"c1"}"#, #"{"version":1,"exam_key":"../teacher"}"#, #"{"version":1,"exam_key":"c1","server":"https://evil.test"}"#] {
            #expect(ExamLaunch.parseFile(Data(raw.utf8)) == nil)
        }
        #expect(ExamLaunch.parseFile(Data(repeating: 32, count: 4097)) == nil)
    }
    @Test func legacyCodeRouteIsRecognisedOnlyOnTheServer() {
        let server = URL(string: "https://beta.example.test")!
        #expect(Config.isLegacyCodeRoute(URL(string: "https://beta.example.test/student/login"), server: server))
        #expect(Config.isLegacyCodeRoute(URL(string: "https://beta.example.test/student/login/?launch_exam=c1"), server: server))
        #expect(!Config.isLegacyCodeRoute(URL(string: "https://beta.example.test/student/device-test"), server: server))
        #expect(!Config.isLegacyCodeRoute(URL(string: "https://other.example/student/login"), server: server))
        #expect(!Config.isLegacyCodeRoute(nil, server: server))
    }
    @Test func relaunchWithoutAttemptOpensTheNativeHome() {
        #expect(MainWindowController.launchURL() == nil)
    }
    @Test func activeSessionsAreNeverReplaced() {
        #expect(ExamLaunch.mayOpen(locked: false, prelocked: false, bound: false, external: false, preview: false))
        for flags in [[true,false,false,false,false], [false,true,false,false,false], [false,false,true,false,false], [false,false,false,true,false], [false,false,false,false,true]] {
            #expect(!ExamLaunch.mayOpen(locked: flags[0], prelocked: flags[1], bound: flags[2], external: flags[3], preview: flags[4]))
        }
    }
}
