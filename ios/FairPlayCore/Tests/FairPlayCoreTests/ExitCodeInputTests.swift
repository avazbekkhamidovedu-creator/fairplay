import Testing
import Foundation
@testable import FairPlayCore

@Suite struct ExitCodeInputTests {
    @Test func exactlySixDigits() {
        #expect(ExitCodeInput.normalized("123456") == "123456")
        #expect(ExitCodeInput.normalized("  123456\n") == "123456")
        #expect(ExitCodeInput.normalized("12345") == nil)
        #expect(ExitCodeInput.normalized("1234567") == nil)
        #expect(ExitCodeInput.normalized("12 3456") == nil)
        #expect(ExitCodeInput.normalized("12345a") == nil)
        #expect(ExitCodeInput.normalized("１２３４５６") == nil)
        #expect(ExitCodeInput.normalized("") == nil)
    }

    @Test func retryAfterFromBody() {
        #expect(ExitCodeInput.retryAfterSeconds(json: ["retry_after": 120]) == 120)
        #expect(ExitCodeInput.retryAfterSeconds(json: ["detail": ["retry_after": 299, "code": "too_many_codes"]]) == 299)
        #expect(ExitCodeInput.retryAfterSeconds(json: ["retry_after": "30"]) == 30)
        #expect(ExitCodeInput.retryAfterSeconds(json: ["retry_after": 0]) == nil)
        #expect(ExitCodeInput.retryAfterSeconds(json: ["retry_after": 999_999]) == 3600)
        #expect(ExitCodeInput.retryAfterSeconds(json: nil) == nil)
    }

    @Test func waitText() {
        #expect(ExitCodeInput.waitText(seconds: 45) == "45 s")
        #expect(ExitCodeInput.waitText(seconds: 300) == "5 min")
        #expect(ExitCodeInput.waitText(seconds: 90) == "1 min 30 s")
        #expect(ExitCodeInput.waitText(seconds: 0) == "1 s")
    }
}

@Suite struct ExitSheetLogicTests {
    @Test func digitsOnlyFiltersAndCaps() {
        #expect(ExitSheetLogic.digitsOnly("12a3 4-5678") == "123456")
        #expect(ExitSheetLogic.digitsOnly("１２３") == "")
        #expect(ExitSheetLogic.digitsOnly("") == "")
    }

    @Test func submitNeedsSixDigitsAndIdleAndNoWait() {
        #expect(ExitSheetLogic.canSubmit("123456", busy: false, waitSeconds: 0))
        #expect(!ExitSheetLogic.canSubmit("12345", busy: false, waitSeconds: 0))
        #expect(!ExitSheetLogic.canSubmit("123456", busy: true, waitSeconds: 0))
        #expect(!ExitSheetLogic.canSubmit("123456", busy: false, waitSeconds: 30))
    }

    @Test func messages() {
        #expect(ExitSheetLogic.message(for: .ok, waitSeconds: 0) == nil)
        #expect(ExitSheetLogic.message(for: .wrong, waitSeconds: 0)?.offersOffline == false)
        #expect(ExitSheetLogic.message(for: .tooMany, waitSeconds: 300)?.text.contains("5 min") == true)
        #expect(ExitSheetLogic.message(for: .unreachable, waitSeconds: 0)?.offersOffline == true)
        #expect(ExitSheetLogic.message(for: .serverError(500), waitSeconds: 0)?.text.contains("500") == true)
    }

    @Test func exitedMessageShape() {
        let m = ExitSheetLogic.exitedMessage(attemptId: "abc123")
        #expect(m["type"] as? String == "exited")
        #expect(m["attempt_id"] as? String == "abc123")
        #expect(ExitSheetLogic.exitedMessage(attemptId: nil)["attempt_id"] == nil)
    }

    @Test func homeFallback() {
        #expect(ExitSheetLogic.needsHomeFallback(urlAtExit: "https://s/student/exam/1", urlNow: "https://s/student/exam/1", nowIsServerPage: true))
        #expect(!ExitSheetLogic.needsHomeFallback(urlAtExit: "https://s/student/exam/1", urlNow: "https://s/student", nowIsServerPage: true))
        #expect(ExitSheetLogic.needsHomeFallback(urlAtExit: "https://s/student/exam/1", urlNow: "https://other/", nowIsServerPage: false))
        #expect(ExitSheetLogic.needsHomeFallback(urlAtExit: nil, urlNow: nil, nowIsServerPage: true))
    }
}
