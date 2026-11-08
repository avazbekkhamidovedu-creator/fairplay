import Foundation
import Testing
@testable import Proctora

@Suite struct LoadOutcomeTests {
    @Test func anyHTTPStatusIsTheSitesOwnPageNotAFailure() {
        for status in [200, 301, 400, 403, 404, 410, 429, 500, 502, 503] {
            #expect(LoadOutcome.classify(status: status, error: nil) == .answered, "\(status)")
        }
        let timeout = NSError(domain: NSURLErrorDomain, code: NSURLErrorTimedOut)
        #expect(LoadOutcome.classify(status: 404, error: timeout) == .answered)
    }

    @Test func onlyNetworkClassErrorsAreUnreachable() {
        for code in [NSURLErrorTimedOut, NSURLErrorCannotFindHost, NSURLErrorCannotConnectToHost, NSURLErrorNetworkConnectionLost,
                     NSURLErrorDNSLookupFailed, NSURLErrorNotConnectedToInternet, NSURLErrorSecureConnectionFailed,
                     NSURLErrorServerCertificateUntrusted, NSURLErrorServerCertificateHasBadDate] {
            #expect(LoadOutcome.classify(status: nil, error: NSError(domain: NSURLErrorDomain, code: code)) == .unreachable, "\(code)")
        }
        for code in [NSURLErrorBadServerResponse, NSURLErrorHTTPTooManyRedirects, NSURLErrorZeroByteResource,
                     NSURLErrorResourceUnavailable, NSURLErrorCannotParseResponse, NSURLErrorCannotDecodeContentData, NSURLErrorBadURL] {
            #expect(LoadOutcome.classify(status: nil, error: NSError(domain: NSURLErrorDomain, code: code)) == .other, "\(code)")
        }
        #expect(LoadOutcome.classify(status: nil, error: NSError(domain: "WebKitErrorDomain", code: 102)) == .other)
        #expect(LoadOutcome.classify(status: nil, error: NSError(domain: "WebKitErrorDomain", code: NSURLErrorTimedOut)) == .other)
        #expect(LoadOutcome.classify(status: nil, error: nil) == .other)
    }

    @Test func theVPNPromptIsSuppressedWhenAVPNIsUpOrTheHostAnsweredRecently() {
        let now = Date(timeIntervalSince1970: 10_000)
        #expect(VPNPromptGate.allowed(vpnInterfaceUp: false, hostAnsweredAt: nil, now: now))
        #expect(!VPNPromptGate.allowed(vpnInterfaceUp: true, hostAnsweredAt: nil, now: now))
        #expect(!VPNPromptGate.allowed(vpnInterfaceUp: false, hostAnsweredAt: now.addingTimeInterval(-30), now: now))
        #expect(VPNPromptGate.allowed(vpnInterfaceUp: false, hostAnsweredAt: now.addingTimeInterval(-VPNPromptGate.answeredWindow - 1), now: now))
    }

    @Test func theFailureCountClearsAfterAGoodResponse() {
        var retry = LoadRetryPolicy()
        _ = retry.failed(); _ = retry.failed()
        #expect(retry.failures == 2)
        retry.succeeded()
        #expect(retry.failures == 0)
        let prompt = retry.shouldPrompt(vpnInstalled: true)
        #expect(!prompt)
    }

    @Test func theOJsOwnErrorTextIsShownOnlyForHTTPErrors() {
        func text(_ status: Int?, _ json: String) -> String? { ProtectedBrowserController.ojErrorText(status: status, data: Data(json.utf8)) }
        #expect(text(404, #"{"error":"No such problem"}"#) == "No such problem")
        #expect(text(403, #"{"error":"a\u0000b"}"#) == "ab")
        #expect(text(200, #"{"error":"x"}"#) == nil)
        #expect(text(nil, #"{"error":"x"}"#) == nil)
        #expect(text(500, "<html>boom</html>") == nil)
        #expect(text(400, #"{"error":5}"#) == nil)
    }
}
