import Testing
import Foundation
@testable import Proctora

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

    @Test func digitsOnlyFiltersAndCaps() {
        #expect(ExitCodeInput.digitsOnly("12a3-4 5") == "12345")
        #expect(ExitCodeInput.digitsOnly("1234567890") == "123456")
        #expect(ExitCodeInput.digitsOnly("１２３") == "")
        #expect(ExitCodeInput.digitsOnly("") == "")
    }

    @Test func submitNeedsFullCode() {
        #expect(ExitCodeInput.canSubmit("123456", allowShort: false))
        #expect(!ExitCodeInput.canSubmit("1234", allowShort: false))
        #expect(!ExitCodeInput.canSubmit("", allowShort: true))
        #expect(ExitCodeInput.canSubmit("0000", allowShort: true))
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
