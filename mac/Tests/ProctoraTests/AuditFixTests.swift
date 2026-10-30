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

    @Test func overlayIgnoresSizeAndUnresolvedInputBundleFailsClosed() {
        let level = 2_000
        let list: [[String: Any]] = [
            [kCGWindowOwnerPID as String: NSNumber(value: 10), kCGWindowLayer as String: NSNumber(value: level),
             kCGWindowOwnerName as String: "Tiny", kCGWindowBounds as String: ["Width": 2, "Height": 2]]]
        #expect(SystemChecks.overlays(in: list, myPid: 99, myLevel: level).count == 1)
        #expect(!SystemChecks.inputMethodBundleIsApple(nil))
        #expect(SystemChecks.inputMethodBundleIsApple(URL(fileURLWithPath: "/System/Library/Input Methods/X.app")))
        #expect(!SystemChecks.inputMethodBundleIsApple(URL(fileURLWithPath: "/Applications/X.app")))
    }
}
