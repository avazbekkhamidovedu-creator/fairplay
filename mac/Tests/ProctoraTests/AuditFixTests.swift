import Testing
import Foundation
import CoreGraphics
@testable import Proctora

@Suite struct AuditFixTests {

    private func token(_ uid: String) -> String {
        let raw = Data("\(uid):student:9999999999:0:abcd".utf8).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        return "\(raw).sig"
    }

    @Test func sealedUsesPageTokenOnlyForTheSameUser() {
        let a = token("42"), a2 = token("42") + "x", b = token("77")
        #expect(Sealed.tokenUserId(a) == "42")
        #expect(Sealed.tokenUserId("garbage") == nil)
        let other = Sealed.chooseToken(page: b, stored: a)
        #expect(other.token == a && !other.matches && !other.borrowed)
        let same = Sealed.chooseToken(page: a2, stored: a)
        #expect(same.token == a2 && same.matches && !same.borrowed)
        let none = Sealed.chooseToken(page: nil, stored: a)
        #expect(none.token == a)
        let borrowed = Sealed.chooseToken(page: b, stored: nil)
        #expect(borrowed.token == b && borrowed.borrowed && !borrowed.matches)
    }

    @Test func borrowedTokenNeverSetsAFileAside() {
        #expect(Sealed.outcome(status: 404, borrowed: true) == .keep)
        #expect(Sealed.outcome(status: 404) == .reject)
        #expect(Sealed.outcome(status: 200, borrowed: true) == .done)
    }

    @Test func openBookTopLevelFilesAreBlocked() {
        let scope = CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: true)
        for path in ["/files", "/files/9", "/files/folder/users_1", "/files/folder/courses_999"] {
            let segs = CanvasNavigation.segments(encodedPath: path)!
            #expect(CanvasNavigation.decide(segments: segs, scope: scope, phase: .quiz) == .block, "\(path)")
        }
        let ok = CanvasNavigation.segments(encodedPath: "/courses/123/files/5")!
        #expect(CanvasNavigation.decide(segments: ok, scope: scope, phase: .quiz) == .allow)
    }

    @Test func finishIsBoundToTheExamItem() {
        let scope = CanvasScope(courseId: "123", itemKind: "quizzes", itemId: "456", openBook: false)
        let re = CanvasFinish.compile(#"/quizzes/\d+/submissions/\d+"#)
        func hit(_ s: String) -> Bool { CanvasFinish.matches(URL(string: s)!, regex: re, scope: scope) }
        #expect(hit("https://c.edu/courses/123/quizzes/456/submissions/9"))
        #expect(!hit("https://c.edu/courses/123/quizzes/999/submissions/9"))
        #expect(!hit("https://c.edu/courses/999/quizzes/456/submissions/9"))
    }

    @Test func overlayMinimumSizeAndSystemOwners() {
        let level = 2_000
        func win(_ pid: Int, _ owner: String, _ w: Int, _ h: Int, alpha: Double = 1) -> [String: Any] {
            [kCGWindowOwnerPID as String: NSNumber(value: pid), kCGWindowLayer as String: NSNumber(value: level),
             kCGWindowOwnerName as String: owner, kCGWindowAlpha as String: NSNumber(value: alpha),
             kCGWindowBounds as String: ["Width": w, "Height": h]]
        }
        let list = [win(10, "Small", 12, 12), win(11, "Hairline", 1, 400), win(12, "Clear", 300, 200, alpha: 0),
                    win(13, "Window Server", 30, 30), win(14, "Dock", 300, 20), win(15, "SystemUIServer", 30, 22),
                    win(16, "ControlCenter", 30, 22), win(17, "loginwindow", 300, 200), win(99, "FairPlay", 300, 200)]
        #expect(SystemChecks.overlays(in: list, myPid: 99, myLevel: level) == [SystemChecks.Overlay(pid: 10, owner: "Small")])
    }

    @Test func unresolvedInputBundleFallsBackToApplePrefix() {
        #expect(SystemChecks.inputMethodBundleIsApple(nil, bundleId: "com.apple.inputmethod.SCIM"))
        #expect(SystemChecks.inputMethodBundleIsApple(nil, bundleId: "com.apple.keylayout.ABC"))
        #expect(!SystemChecks.inputMethodBundleIsApple(nil, bundleId: "com.sogou.inputmethod"))
        #expect(!SystemChecks.inputMethodBundleIsApple(nil))
        #expect(SystemChecks.inputMethodBundleIsApple(URL(fileURLWithPath: "/System/Library/Input Methods/X.app")))
        #expect(!SystemChecks.inputMethodBundleIsApple(URL(fileURLWithPath: "/Applications/X.app"), bundleId: "com.apple.inputmethod.Fake"))
    }
}
