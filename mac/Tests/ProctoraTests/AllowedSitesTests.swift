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
        // Not plain host names: scheme, path, port, a "*" not at the front, spaces, empty labels, unicode.
        #expect(AllowedSites.parse(["https://a.com", "a.com/x", "a.com:443", "a.*.com", "*a.com", "**.a.com",
                                    "*.", "*. a.com", "a b.com", "a..com", ".a.com", "", "пример.рф", "abc.de:443"]) == [])
        // A whole top-level domain is never an entry.
        #expect(AllowedSites.parse(["com", "edu", "localhost"]) == [])
        // 2026-10-23 (review): never an IP address, as on Windows.
        #expect(AllowedSites.parse(["10.0.0.5", "[::1]", "fe80::1"]) == [])
        #expect(AllowedSites.parse(["*.Python.ORG.", "*.python.org"]) == ["*.python.org"])
    }

    @Test func plainEntryIsThatHostOnly() {
        let a = ["docs.python.org"]
        #expect(AllowedSites.hostAllowed("docs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("x.docs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("a.b.docs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("evildocs.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("docs.python.org.attacker.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("docs-python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("", allowed: a))
        // The review's cases: a plain shared-hosting or big-company entry never opens its subdomains.
        #expect(AllowedSites.hostAllowed("github.io", allowed: ["github.io"]))
        #expect(!AllowedSites.hostAllowed("someone.github.io", allowed: ["github.io"]))
        #expect(!AllowedSites.hostAllowed("cheat.vercel.app", allowed: ["vercel.app"]))
        #expect(!AllowedSites.hostAllowed("gemini.google.com", allowed: ["google.com"]))
    }

    @Test func wwwAddedOrRemoved() {
        #expect(AllowedSites.hostAllowed("www.cppreference.com", allowed: ["cppreference.com"]))
        #expect(AllowedSites.hostAllowed("cppreference.com", allowed: ["www.cppreference.com"]))
        #expect(AllowedSites.hostAllowed("WWW.cppreference.com.", allowed: ["cppreference.com"]))
        #expect(!AllowedSites.hostAllowed("www2.cppreference.com", allowed: ["cppreference.com"]))
        #expect(!AllowedSites.hostAllowed("www.www.cppreference.com", allowed: ["cppreference.com"]))
        #expect(!AllowedSites.hostAllowed("en.cppreference.com", allowed: ["www.cppreference.com"]))
        // "www.com" never opens the top-level domain "com".
        #expect(!AllowedSites.hostAllowed("com", allowed: ["www.com"]))
    }

    @Test func wildcardEntry() {
        let a = ["*.python.org"]
        #expect(AllowedSites.hostAllowed("python.org", allowed: a))
        #expect(AllowedSites.hostAllowed("docs.python.org", allowed: a))
        #expect(AllowedSites.hostAllowed("a.b.python.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("evilpython.org", allowed: a))
        #expect(!AllowedSites.hostAllowed("python.org.attacker.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("org", allowed: a))
        #expect(frame("https://docs.python.org/3/", a))
        #expect(!frame("http://docs.python.org/3/", a))
    }

    @Test func sharedSuffixWildcardsIgnored() {
        for base in ["com", "net", "org", "edu", "gov", "io", "app", "dev", "cn", "uk", "co.uk", "com.cn",
                     "github.io", "gitlab.io", "vercel.app", "netlify.app", "pages.dev", "web.app",
                     "firebaseapp.com", "herokuapp.com", "blogspot.com", "glitch.me", "replit.app", "repl.co",
                     "onrender.com", "fly.dev", "azurewebsites.net", "cloudfront.net", "appspot.com",
                     "wixsite.com", "notion.site", "github.com", "google.com", "com.au", "co.jp", "ac.kr"] {
            #expect(AllowedSites.normalizedEntry("*." + base) == nil)
            #expect(!AllowedSites.hostAllowed("x." + base, allowed: ["*." + base]))
        }
        #expect(AllowedSites.parse(["*.github.io", "*.CO.UK", "docs.python.org"]) == ["docs.python.org"])
        // IP literals never take a wildcard.
        #expect(AllowedSites.normalizedEntry("*.10.0.0.5") == nil)
        #expect(AllowedSites.normalizedEntry("*.::1") == nil)
        // A real site under a shared suffix may still be a wildcard.
        #expect(AllowedSites.normalizedEntry("*.bbc.co.uk") == "*.bbc.co.uk")
        #expect(AllowedSites.hostAllowed("news.bbc.co.uk", allowed: ["*.bbc.co.uk"]))
        #expect(AllowedSites.isSharedSuffix("github.io"))
        #expect(!AllowedSites.isSharedSuffix("python.org"))
    }

    /// 2026-10-23 (review): more shared hosting, refused also under it ("ends with"), and
    /// Blogger in every country. Same cases as the iPad's and Windows' tests.
    @Test func sharedHostingWildcardsRefusedUnderneathToo() {
        for base in ["s3.amazonaws.com", "s3.us-east-1.amazonaws.com", "x.cloudfront.net", "pythonanywhere.com",
                     "r2.dev", "azureedge.net", "workers.dev", "surge.sh", "neocities.org", "vercel.app", "now.sh",
                     "deno.dev", "streamlit.app", "huggingface.co", "hf.space", "github.dev", "gitpod.io",
                     "codesandbox.io", "stackblitz.io", "ngrok.io", "ngrok-free.app", "trycloudflare.com", "loca.lt",
                     "user.github.io", "a.b.vercel.app", "blogspot.co.uk", "blogspot.de", "blogspot.com.au",
                     "me.blogspot.com", "me.blogspot.co.uk"] {
            #expect(AllowedSites.normalizedEntry("*." + base) == nil)
            #expect(AllowedSites.isSharedSuffix(base))
        }
        // Real sites still take a wildcard.
        #expect(AllowedSites.normalizedEntry("*.python.org") == "*.python.org")
        #expect(AllowedSites.normalizedEntry("*.bbc.co.uk") == "*.bbc.co.uk")
        #expect(AllowedSites.normalizedEntry("*.blogspot-fan.org") == "*.blogspot-fan.org")
        #expect(AllowedSites.normalizedEntry("*.dku.edu.cn") == "*.dku.edu.cn")
        // A plain entry naming one of them is still that host only.
        #expect(AllowedSites.normalizedEntry("bucket.s3.amazonaws.com") == "bucket.s3.amazonaws.com")
    }

    @Test func missingKeyKeepsTheList() {
        let now = ["docs.python.org"]
        #expect(AllowedSites.updated(now, from: ["action": "lock"]) == now)
        #expect(AllowedSites.updated(now, from: ["action": "lock", "allowed_sites": [Any]()]) == [])
        #expect(AllowedSites.updated(now, from: ["action": "session", "allowed_sites": ["x.org"]]) == ["x.org"])
        #expect(AllowedSites.updated(now, from: ["action": "lock", "allowed_sites": "x.org"]) == [])   // malformed: none
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

    @Test func ipLiteralsNeverAllowed() {
        // 2026-10-23 (review): IP-address entries are refused (as on Windows), so no IP host
        // ever matches.
        #expect(AllowedSites.normalizedEntry("10.0.0.5") == nil)
        #expect(AllowedSites.normalizedEntry("[::1]") == nil)
        #expect(!AllowedSites.hostAllowed("10.0.0.5", allowed: ["docs.python.org"]))
        #expect(!AllowedSites.hostAllowed("10.0.0.5", allowed: ["10.0.0.5"]))
        #expect(!frame("https://10.0.0.5/x", ["10.0.0.5"]))
        #expect(!AllowedSites.hostAllowed("::1", allowed: ["::1"]))
        #expect(!AllowedSites.hostAllowed("[::1]", allowed: ["[::1]"]))
        #expect(!frame("https://[::1]/", ["::1"]))
        #expect(AllowedSites.isIPLiteral("192.168.1.1"))
        #expect(AllowedSites.isIPLiteral("fe80::1"))
        #expect(!AllowedSites.isIPLiteral("docs.python.org"))
    }

    @Test func punycodeComparedAsIs() {
        let a = ["xn--80ak6aa92e.com"]
        #expect(AllowedSites.hostAllowed("xn--80ak6aa92e.com", allowed: a))
        #expect(AllowedSites.hostAllowed("www.xn--80ak6aa92e.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("x.xn--80ak6aa92e.com", allowed: a))
        #expect(!AllowedSites.hostAllowed("xn--80ak6aa92f.com", allowed: a))
        // A unicode entry is not a host name WebKit ever gives: it opens nothing.
        #expect(!AllowedSites.hostAllowed("xn--e1afmkfd.xn--p1ai", allowed: ["пример.рф"]))
        #expect(!AllowedSites.hostAllowed("пример.рф", allowed: ["пример.рф"]))
    }

    /// 2026-10-23 (review): the server sends international names as punycode; WebKit gives
    /// the frame's host the same way. Same cases as the iPad's and Windows' tests.
    @Test func punycodeEntriesFromTheServer() {
        let a = AllowedSites.parse(["XN--E1AFMKFD.XN--P1AI", "*.xn--e1afmkfd.xn--p1ai"])
        #expect(a == ["xn--e1afmkfd.xn--p1ai", "*.xn--e1afmkfd.xn--p1ai"])
        #expect(frame("https://xn--e1afmkfd.xn--p1ai/", ["xn--e1afmkfd.xn--p1ai"]))
        #expect(frame("https://www.xn--e1afmkfd.xn--p1ai/x", ["xn--e1afmkfd.xn--p1ai"]))
        #expect(!frame("https://x.xn--e1afmkfd.xn--p1ai/", ["xn--e1afmkfd.xn--p1ai"]))
        #expect(frame("https://x.xn--e1afmkfd.xn--p1ai/", ["*.xn--e1afmkfd.xn--p1ai"]))
        #expect(AllowedSites.normalizedEntry("*.xn--p1ai") == nil)   // a whole top-level domain
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
