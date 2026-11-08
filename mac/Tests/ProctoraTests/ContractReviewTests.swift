import Foundation
import Testing
@testable import Proctora

@Suite struct ContractReviewTests {
    @Test func theCertificateIsIssuedAgainBeforeItRunsOut() {
        let h: TimeInterval = 3600
        #expect(CertificateRenewal.needsRenewal(remaining: 23 * h, threshold: CertificateRenewal.beforeStart))
        #expect(!CertificateRenewal.needsRenewal(remaining: 25 * h, threshold: CertificateRenewal.beforeStart))
        #expect(CertificateRenewal.needsRenewal(remaining: 5 * h, threshold: CertificateRenewal.duringExam))
        #expect(!CertificateRenewal.needsRenewal(remaining: 7 * h, threshold: CertificateRenewal.duringExam))
        #expect(!CertificateRenewal.needsRenewal(remaining: nil, threshold: CertificateRenewal.duringExam))
        let now = Date(timeIntervalSince1970: 100_000)
        #expect(CertificateRenewal.shouldTryNow(remaining: 5 * h, inFlight: false, now: now, notBefore: nil))
        #expect(!CertificateRenewal.shouldTryNow(remaining: 5 * h, inFlight: true, now: now, notBefore: nil))
        #expect(!CertificateRenewal.shouldTryNow(remaining: 5 * h, inFlight: false, now: now, notBefore: now.addingTimeInterval(30)))
        #expect(CertificateRenewal.shouldTryNow(remaining: 5 * h, inFlight: false, now: now, notBefore: now.addingTimeInterval(-1)))
        #expect(!CertificateRenewal.shouldTryNow(remaining: 30 * h, inFlight: false, now: now, notBefore: nil))
        #expect(CertificateRenewal.shouldTryNow(remaining: -10, inFlight: false, now: now, notBefore: nil))
    }

    @Test func renewalFailuresBackOffAndAreCapped() {
        #expect(CertificateRenewal.backoff(afterFailures: 1) == 30)
        #expect(CertificateRenewal.backoff(afterFailures: 2) == 60)
        #expect(CertificateRenewal.backoff(afterFailures: 4) == 240)
        #expect(CertificateRenewal.backoff(afterFailures: 20) == CertificateRenewal.retryCap)
    }

    private func context(_ state: String, contest: String = "exam") throws -> NativeDeviceContext {
        let json: [String: Any] = ["v": 2, "nonce": "n", "expires": 1, "session": "s1", "device": "d", "user": "7",
            "contest": contest, "participation": "p", "state": state, "lease_until": 0]
        return try JSONDecoder().decode(NativeDeviceContext.self, from: JSONSerialization.data(withJSONObject: json))
    }

    @Test func aRefusedClaimFreesTheExamKeyAndAnotherExamCanStart() {
        var s = NativeExamSession()
        let first = s.beginClaim("exam-a")
        let other = s.beginClaim("exam-b")
        #expect(first && !other)
        s.rejectedClaim()
        #expect(s.contest == nil && !s.requiresTerminalReceipt)
        let afterwards = s.beginClaim("exam-b")
        #expect(afterwards)
    }

    @Test func aClaimThatWasAcceptedIsNeverForgotten() throws {
        var s = NativeExamSession()
        _ = s.beginClaim("exam")
        let accepted = s.accept(try context("active"))
        #expect(accepted)
        s.rejectedClaim()
        #expect(s.contest == "exam" && s.binding != nil)
        let other = s.beginClaim("other")
        #expect(!other)
    }

    @Test func finishingCanEstablishABindingAfterALostClaimReply() throws {
        var s = NativeExamSession()
        _ = s.beginClaim("exam")
        let finishing = s.accept(try context("finishing"))
        #expect(finishing && s.binding != nil)
        var t = NativeExamSession()
        _ = t.beginClaim("exam")
        let c = t.accept(try context("completed")), r = t.accept(try context("released")), j = t.accept(try context("rejected"))
        #expect(!c && !r && !j)
    }

    @Test func pagePathsCompareAsPercentEncodedText() {
        let policy = ProtectedBrowserPolicy(start: "https://oj.example.edu", additionalOrigins: "",
                                            forbiddenOrigin: URL(string: "https://fairplay.example.edu")!,
                                            referenceSites: ["https://en.wikipedia.org/wiki/C%2B%2B", "https://x.org/a%2fb/"])!
        #expect(policy.allows(URL(string: "https://en.wikipedia.org/wiki/C%2B%2B")!, mainFrame: true))
        #expect(policy.allows(URL(string: "https://en.wikipedia.org/wiki/C%2b%2b")!, mainFrame: true))
        #expect(policy.allows(URL(string: "https://en.wikipedia.org/wiki/C%2B%2B/History")!, mainFrame: true))
        #expect(!policy.allows(URL(string: "https://en.wikipedia.org/wiki/C")!, mainFrame: true))
        #expect(policy.allows(URL(string: "https://x.org/a%2Fb/c")!, mainFrame: true))
        #expect(!policy.allows(URL(string: "https://x.org/a/b/c")!, mainFrame: true))
        #expect(ExternalExamController.SiteRule.normalizedEscapes("/a%2fb%zz%4") == "/a%2Fb%zz%4")
    }
}
