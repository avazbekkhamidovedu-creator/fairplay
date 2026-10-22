// 2026-10-23: teacher-allowed websites in the exam page's side pane, and the battery script.
import Testing
import Foundation
@testable import Proctora

@Suite struct AllowedSitesTests {

    private func frame(_ s: String, _ allowed: [String]) -> Bool {
        AllowedSites.frameAllowed(URL(string: s)!, allowed: allowed)
    }

    @Test func parseNormalisesAndDrops() {
        #expect(AllowedSites.parse(nil) == [])
        #expect(AllowedSites.parse("docs.python.org") == [])          // not a list
        let mixed: [Any] = ["Docs.Python.ORG", " docs.python.org. ", 7, "cppreference.com", "docs.python.org"]
        #expect(AllowedSites.parse(mixed) == ["docs.python.org", "cppreference.com"])
        // Not plain host names: scheme, path, port, wildcard, spaces, empty labels, unicode.
        #expect(AllowedSites.parse(["https://a.com", "a.com/x", "a.com:443", "*.a.com", "a b.com",
                                    "a..com", ".a.com", "", "пример.рф", "abc.de:443"]) == [])
        // A whole top-level domain is never an entry.
        #expect(AllowedSites.parse(["com", "edu", "localhost"]) == [])
        #expect(AllowedSites.parse(["10.0.0.5", "[::1]"]) == ["10.0.0.5", "::1"])
    }

    @Test func exactHostAndSubdomains() {
        let a = ["docs.python.org"]
        #expect(AllowedSites.hostAllowed("docs.python.org", allowed: a))
        #expect(AllowedSites.hostAllowed("x.docs.python.org", allowed: a))
        #expect(AllowedSites.hostAllowed("a.b.docs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("evildocs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("docs.python.org.attacker.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("evildocs.python.org.attacker.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("docs-python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("", allowed: a))
    }

    @Test func caseAndTrailingDot() {
        #expect(AllowedSites.hostAllowed("DOCS.Python.org", allowed: ["docs.python.org"]))
        #expect(AllowedSites.hostAllowed("docs.python.org.", allowed: ["docs.python.org"]))
        #expect(AllowedSites.hostAllowed("docs.python.org", allowed: ["Docs.Python.Org."]))
        #expect(frame("https://DOCS.PYTHON.ORG./3/", ["docs.python.org"]))
    }

    @Test func emptyListBlocksEverySite() {
        #expect(!AllowedSites.hostAllowed("docs.python.org", allowed: []))
        #expect(!frame("https://docs.python.org/", []))
        // An empty document is not a site.
        #expect(frame("about:blank", []))
    }

    @Test func httpsOnDefaultPortOnly() {
        let a = ["docs.python.org"]
        #expect(frame("https://docs.python.org/3/library/", a))
        #expect(frame("https://docs.python.org:443/3/", a))
        #expect(!frame("http://docs.python.org/3/", a))
        #expect(!frame("https://docs.python.org:8443/", a))
        #expect(!frame("wss://docs.python.org/", a))
        #expect(!frame("ftp://docs.python.org/", a))
        #expect(!frame("data:text/html,hi", a))
        #expect(!frame("javascript:alert(1)", a))
        #expect(!frame("blob:https://docs.python.org/abc", a))
        // A user name in front of the host never helps.
        #expect(!frame("https://docs.python.org@evil.com/", a))
        #expect(!frame("https://user:pw@docs.python.org/", a))
    }

    @Test func aboutPages() {
        #expect(frame("about:blank", ["a.com"]))
        #expect(frame("about:srcdoc", ["a.com"]))
        #expect(frame("about:blank#x", ["a.com"]))
        #expect(!frame("about:config", ["a.com"]))
        #expect(!frame("about:blankx", ["a.com"]))
    }

    @Test func ipLiteralsOnlyExactly() {
        #expect(!AllowedSites.hostAllowed("10.0.0.5", allowed: ["docs.python.org"]))
        #expect(AllowedSites.hostAllowed("10.0.0.5", allowed: ["10.0.0.5"]))
        #expect(!AllowedSites.hostAllowed("10.0.0.50", allowed: ["10.0.0.5"]))
        #expect(!AllowedSites.hostAllowed("1.10.0.0.5", allowed: ["10.0.0.5"]))
        #expect(!AllowedSites.hostAllowed("0.0.5", allowed: ["10.0.0.5"]))
        #expect(frame("https://10.0.0.5/x", ["10.0.0.5"]))
        #expect(!frame("https://10.0.0.6/x", ["10.0.0.5"]))
        #expect(AllowedSites.hostAllowed("::1", allowed: ["::1"]))
        #expect(AllowedSites.hostAllowed("[::1]", allowed: ["[::1]"]))
        #expect(!AllowedSites.hostAllowed("::2", allowed: ["::1"]))
        #expect(AllowedSites.isIPLiteral("192.168.1.1"))
        #expect(AllowedSites.isIPLiteral("fe80::1"))
        #expect(!AllowedSites.isIPLiteral("docs.python.org"))
    }

    @Test func punycodeComparedAsIs() {
        let a = ["xn--80ak6aa92e.com"]
        #expect(AllowedSites.hostAllowed("xn--80ak6aa92e.com", allowed: a))
        #expect(AllowedSites.hostAllowed("www.xn--80ak6aa92e.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("xn--80ak6aa92f.com", allowed: a))
        // A unicode entry is not a host name WebKit ever gives: it opens nothing.
        #expect(!AllowedSites.hostAllowed("xn--e1afmkfd.xn--p1ai", allowed: ["пример.рф"]))
        #expect(!AllowedSites.hostAllowed("пример.рф", allowed: ["пример.рф"]))
    }

    @Test func popupsAndDownloads() {
        let a = ["docs.python.org"]
        #expect(AllowedSites.isAllowedPopup(URL(string: "https://docs.python.org/3/")!, allowed: a))
        #expect(!AllowedSites.isAllowedPopup(URL(string: "about:blank")!, allowed: a))
        #expect(!AllowedSites.isAllowedPopup(URL(string: "https://evil.com/")!, allowed: a))
        #expect(AllowedSites.isAttachment(contentDisposition: "attachment"))
        #expect(AllowedSites.isAttachment(contentDisposition: "Attachment; filename=\"a.pdf\""))
        #expect(!AllowedSites.isAttachment(contentDisposition: "inline"))
        #expect(!AllowedSites.isAttachment(contentDisposition: "inline; filename=\"attachment.pdf\""))
        #expect(!AllowedSites.isAttachment(contentDisposition: nil))
    }
}

@Suite struct BatteryScriptTests {

    @Test func formatsLevelAndCharging() {
        #expect(BatteryScript.make(level: 0.57, charging: true)
                == "window.fairplayBattery = {level: 0.57, charging: true}; window.dispatchEvent(new Event(\"fairplay-battery\"));")
        #expect(BatteryScript.make(level: 1, charging: false)?.contains("{level: 1.00, charging: false}") == true)
        #expect(BatteryScript.make(level: 0, charging: false)?.contains("{level: 0.00, charging: false}") == true)
        #expect(BatteryScript.make(level: 0.05, charging: false)?.contains("level: 0.05,") == true)
    }

    @Test func roundsToTwoDecimals() {
        #expect(BatteryScript.make(level: 0.123456, charging: false)?.contains("level: 0.12,") == true)
        #expect(BatteryScript.make(level: 0.996, charging: false)?.contains("level: 1.00,") == true)
        #expect(BatteryScript.make(level: 0.005, charging: false)?.contains("level: 0.01,") == true)
        #expect(BatteryScript.make(level: 0.004, charging: false)?.contains("level: 0.00,") == true)
    }

    @Test func clampsAndRejects() {
        #expect(BatteryScript.make(level: 1.7, charging: true)?.contains("level: 1.00,") == true)
        #expect(BatteryScript.make(level: -1, charging: false) == nil)        // unknown
        #expect(BatteryScript.make(level: -0.01, charging: false) == nil)
        #expect(BatteryScript.make(level: .nan, charging: false) == nil)
        #expect(BatteryScript.make(level: .infinity, charging: false) == nil)
    }
}
