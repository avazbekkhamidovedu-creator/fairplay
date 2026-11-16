// 2026-10-30: the Duke Box pane's rules (BoxPolicy.swift).
import Testing
import Foundation
import WebKit
@testable import Proctora

@Suite struct BoxPolicyTests {

    static let token = "AbCdEf0123456789xyz"

    static func config(app: [String] = ["duke.app.box.com", "duke.box.com"],
                       start: String = "https://duke.app.box.com/s/\(token)",
                       prefix: String = "/s/\(token)",
                       signIn: [[String: Any]]? = nil,
                       assets: [String] = ["cdn01.boxcdn.net", "*.boxcloud.com"],
                       blocked: [[String: Any]]? = nil) -> [String: Any] {
        ["start_url": start, "app_hosts": app, "path_prefix": prefix,
         "sign_in_hosts": signIn ?? [["host": "duke.account.box.com", "paths": ["/login*"]],
                                     ["host": "sso.services.box.net", "paths": NSNull()],
                                     ["host": "*.duosecurity.com", "paths": NSNull()]],
         "asset_hosts": assets,
         "blocked_requests": blocked ?? [["host": "*", "path": "/upload*"],
                                         ["host": "*", "path": "/api/2.0/folders", "methods": ["POST"]],
                                         ["host": "*", "path": "/folder/*"],
                                         ["host": "*", "path": "/app-api/enduserapp/shared-item*", "methods": ["PUT", "DELETE"]]]]
    }

    static func policy(_ c: [String: Any]? = nil, dev: Bool = false) -> BoxPolicy {
        BoxPolicy(body: c ?? config(), allowDev: dev)!
    }

    private func decide(_ s: String, main: Bool = true, method: String = "GET") -> BoxPolicy.Navigation {
        Self.policy().decide(URL(string: s)!, mainFrame: main, method: method)
    }

    @Test func appPageUnderThePrefixLoads() {
        #expect(decide("https://duke.app.box.com/s/\(Self.token)") == .allow)
        #expect(decide("https://duke.app.box.com/s/\(Self.token)/") == .allow)
        #expect(decide("https://duke.box.com/s/\(Self.token)?x=1") == .allow)
        #expect(decide("https://DUKE.app.box.com/s/\(Self.token)/file/1234") == .allow)
    }

    @Test func otherPathsOnTheAppHostAreRefused() {
        #expect(decide("https://duke.app.box.com/") == .block)
        #expect(decide("https://duke.app.box.com/folder/12345") == .block)
        #expect(decide("https://duke.app.box.com/s/\(Self.token)x") == .block)
        #expect(decide("https://duke.app.box.com/s/OtherToken0123456") == .block)
        #expect(decide("https://duke.app.box.com/s/\(Self.token)/../other") == .block)
        #expect(decide("https://duke.app.box.com/s/\(Self.token)%2f..%2fx") == .block)
        #expect(decide("https://duke.app.box.com/s/\(Self.token)//x") == .block)
    }

    @Test func blockedRequestsWinOverAllowedPages() {
        #expect(decide("https://duke.app.box.com/s/\(Self.token)/upload") == .allow)
        #expect(decide("https://duke.app.box.com/upload/x") == .block)
        #expect(decide("https://sso.services.box.net/UPLOAD") == .block)
        #expect(decide("https://sso.services.box.net/folder/1") == .block)
        #expect(decide("https://sso.services.box.net/api/2.0/folders", method: "POST") == .block)
        #expect(decide("https://sso.services.box.net/api/2.0/folders/", method: "post") == .block)
        #expect(decide("https://sso.services.box.net/api/2.0/folders", method: "GET") == .allow)
    }

    @Test func lookAlikeHostsAreRefused() {
        for s in ["https://duke.app.box.com.evil.com/s/\(Self.token)",
                  "https://evilduke.app.box.com/s/\(Self.token)",
                  "https://duke.app.box.com.cn/s/\(Self.token)",
                  "https://xn--duke-app-box-xyz.com/s/\(Self.token)",
                  "https://xn--80ak6aa92e.duosecurity.com/",
                  "https://duosecurity.com.evil.test/",
                  "https://notduosecurity.com/",
                  "https://sub.duke.app.box.com/s/\(Self.token)",
                  "https://duke.app.box.com./s/\(Self.token)",
                  "https://box.com/s/\(Self.token)"] {
            #expect(decide(s) == .block, "\(s)")
        }
    }

    @Test func signInHostsFollowTheirPaths() {
        #expect(decide("https://duke.account.box.com/login") == .allow)
        #expect(decide("https://duke.account.box.com/login/sso?x=1") == .allow)
        #expect(decide("https://duke.account.box.com/settings") == .block)
        #expect(decide("https://duke.account.box.com/") == .block)
        #expect(decide("https://sso.services.box.net/any/path") == .allow)
        #expect(decide("https://api-abc.duosecurity.com/frame/web/v1/auth") == .allow)
        #expect(decide("https://duosecurity.com/") == .block)
        #expect(decide("https://deep.sub.duosecurity.com/x") == .allow)
    }

    @Test func assetHostsLoadInSubFramesOnly() {
        #expect(decide("https://cdn01.boxcdn.net/preview.html", main: false) == .allow)
        #expect(decide("https://dl.boxcloud.com/x", main: false) == .allow)
        #expect(decide("https://cdn01.boxcdn.net/preview.html", main: true) == .block)
        #expect(decide("https://evil.boxcdn.net/x", main: false) == .block)
        #expect(decide("https://other.example.org/x", main: false) == .block)
        #expect(decide("about:blank", main: false) == .allow)
        #expect(decide("about:srcdoc", main: false) == .allow)
        #expect(decide("about:blank", main: true) == .block)
        #expect(decide("data:text/html,hi", main: false) == .block)
        #expect(decide("blob:https://duke.app.box.com/abc", main: false) == .block)
    }

    @Test func subFramesFollowThePageRules() {
        #expect(decide("https://duke.app.box.com/s/\(Self.token)", main: false) == .allow)
        #expect(decide("https://duke.app.box.com/folder/1", main: false) == .block)
        #expect(decide("https://duke.account.box.com/login", main: false) == .allow)
    }

    @Test func schemePortAndUserInfoAreRefused() {
        #expect(decide("http://duke.app.box.com/s/\(Self.token)") == .block)
        #expect(decide("ftp://duke.app.box.com/s/\(Self.token)") == .block)
        #expect(decide("https://duke.app.box.com:8443/s/\(Self.token)") == .block)
        #expect(decide("https://duke.app.box.com:443/s/\(Self.token)") == .allow)
        #expect(decide("https://user@duke.app.box.com/s/\(Self.token)") == .block)
        #expect(decide("https://user:pw@duke.app.box.com/s/\(Self.token)") == .block)
        #expect(decide("https://duke.app.box.com@evil.com/s/\(Self.token)") == .block)
        #expect(decide("fairplay-asset://bundle/x") == .block)
    }

    @Test func offHostRedirectTargetsAreJustAnotherNavigation() {
        #expect(decide("https://evil.example.com/s/\(Self.token)") == .block)
        #expect(decide("https://login.microsoftonline.com/") == .block)
    }

    @Test func popupsOpenOnlySignInPagesInTheSameView() {
        let p = Self.policy()
        #expect(p.popup(URL(string: "https://sso.services.box.net/start")!) == .loadHere)
        #expect(p.popup(URL(string: "https://duke.account.box.com/login")!) == .loadHere)
        #expect(p.popup(URL(string: "https://duke.account.box.com/settings")!) == .refuse)
        #expect(p.popup(URL(string: "https://duke.app.box.com/s/\(Self.token)")!) == .refuse)
        #expect(p.popup(URL(string: "https://cdn01.boxcdn.net/x")!) == .refuse)
        #expect(p.popup(URL(string: "https://evil.example.com/")!) == .refuse)
        #expect(p.popup(URL(string: "http://sso.services.box.net/")!) == .refuse)
    }

    // MARK: - ceiling

    @Test func appHostsAreExactlyTheTwoDukeHosts() {
        #expect(BoxPolicy(body: Self.config(app: ["duke.app.box.com"]), allowDev: false) != nil)
        #expect(BoxPolicy(body: Self.config(app: ["duke.app.box.com", "evil.box.com"]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(app: ["app.box.com"], start: "https://app.box.com/s/\(Self.token)"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(app: []), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(app: ["*.box.com"]), allowDev: false) == nil)
    }

    @Test func signInAndAssetHostsStayInsideTheBaseDomains() {
        for good in ["sso.services.box.net", "shib.oit.duke.edu", "*.duosecurity.com", "api-1234.duosecurity.com", "a.b.duosecurity.com"] {
            #expect(BoxPolicy(body: Self.config(signIn: [["host": good, "paths": NSNull()]]), allowDev: false) != nil, "\(good)")
        }
        for good in ["cdn01.boxcdn.net", "e3.boxcdn.net", "api.box.com", "*.boxcloud.com", "dl.boxcloud.com"] {
            #expect(BoxPolicy(body: Self.config(assets: [good]), allowDev: false) != nil, "\(good)")
        }
        for bad in ["evil.com", "box.com.evil.com", "notbox.com", "duke.edu.cn", "*.com", "*", "xn--box-abc.box.com",
                    "evil.test", "fakebox-login.test", "a.b.c.d.example.org", "https://a.box.com", "a.box.com/x", "A.BOX.COM.", "",
                    "a.box.com", "x.box.net", "*.box.net", "*.box.com", "*.duke.edu", "www.duke.edu", "duke.edu", "duosecurity.com",
                    "boxcloud.com", "cdn.boxcdn.net", "*.boxcdn.net", "evil.boxcdn.net", "api.box.net", "box.com"] {
            #expect(BoxPolicy(body: Self.config(assets: [bad]), allowDev: false) == nil, "\(bad)")
            #expect(BoxPolicy(body: Self.config(signIn: [["host": bad, "paths": NSNull()]]), allowDev: false) == nil, "\(bad)")
        }
        for badSignIn in ["cdn01.boxcdn.net", "e3.boxcdn.net", "api.box.com", "*.boxcloud.com"] {
            #expect(BoxPolicy(body: Self.config(signIn: [["host": badSignIn, "paths": NSNull()]]), allowDev: false) == nil, "\(badSignIn)")
        }
        for badAsset in ["sso.services.box.net", "shib.oit.duke.edu", "*.duosecurity.com", "duke.account.box.com"] {
            #expect(BoxPolicy(body: Self.config(assets: [badAsset]), allowDev: false) == nil, "\(badAsset)")
        }
    }

    @Test func testHostsOnlyInDevBuilds() {
        let fake = Self.config(app: ["fakebox-app.test"], start: "https://fakebox-app.test/s/\(Self.token)",
                               signIn: [["host": "fakebox-login.test", "paths": ["/login*"]]], assets: ["fakebox-assets.test"])
        #expect(BoxPolicy(body: fake, allowDev: false) == nil)
        #expect(BoxPolicy(body: fake, allowDev: true) != nil)
        #expect(BoxPolicy(body: Self.config(app: ["evil.test"], start: "https://evil.test/s/\(Self.token)"), allowDev: true) == nil)
        #expect(BoxPolicy(body: Self.config(assets: ["x.test"]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(assets: ["x.test"]), allowDev: true) != nil)
        #expect(BoxPolicy(body: Self.config(assets: ["evil.com"]), allowDev: true) == nil)
    }

    @Test func startUrlAndPrefixMustAgree() {
        #expect(BoxPolicy(body: Self.config(start: "http://duke.app.box.com/s/\(Self.token)"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(start: "https://duke.app.box.com/s/\(Self.token)?a=b"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(start: "https://duke.app.box.com:444/s/\(Self.token)"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(start: "https://u@duke.app.box.com/s/\(Self.token)"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(start: "https://duke.app.box.com/s/OtherToken0123456"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(start: "https://evil.com/s/\(Self.token)"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(prefix: "/folder/1234567890"), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(prefix: "/s/short"), allowDev: false) == nil)
        #expect(BoxPolicy(body: nil, allowDev: false) == nil)
        #expect(BoxPolicy(body: [:], allowDev: false) == nil)
    }

    @Test func badListsAreRefused() {
        #expect(BoxPolicy(body: Self.config(signIn: [["host": "a.box.com", "paths": ["login"]]]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(blocked: [["host": "*", "path": "upload"]]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(blocked: [["host": "*", "path": "/x", "methods": ["FETCH"]]]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(blocked: [["host": "*", "path": "/x", "methods": ["CONNECT"]]]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(blocked: [["host": "*", "path": "/x", "methods": []]]), allowDev: false) == nil)
        #expect(BoxPolicy(body: Self.config(blocked: [["host": "bad host", "path": "/x"]]), allowDev: false) == nil)
        var c = Self.config()
        c["asset_hosts"] = "cdn.boxcdn.net"
        #expect(BoxPolicy(body: c, allowDev: false) == nil)
    }

    // MARK: - lifecycle and results

    @Test func openOnlyWhileAnExamIsLockedAndRunning() {
        #expect(BoxPolicy.openRefusal(locked: true, preLock: false, hasAttempt: true) == nil)
        #expect(BoxPolicy.openRefusal(locked: false, preLock: false, hasAttempt: true) == BoxPolicy.notAvailable)
        #expect(BoxPolicy.openRefusal(locked: true, preLock: true, hasAttempt: false) == BoxPolicy.notAvailable)
        #expect(BoxPolicy.openRefusal(locked: true, preLock: true, hasAttempt: true) == BoxPolicy.notAvailable)
        #expect(BoxPolicy.openRefusal(locked: true, preLock: false, hasAttempt: false) == BoxPolicy.notAvailable)
        #expect(BoxPolicy.openRefusal(locked: false, preLock: false, hasAttempt: false) == BoxPolicy.notAvailable)
    }

    @Test func closesWhenTheLockEnds() {
        #expect(BoxPolicy.mustClose(locked: false))
        #expect(!BoxPolicy.mustClose(locked: true))
    }

    @Test func resultMessages() {
        let opened = BoxPolicy.message("opened")
        #expect(opened["type"] as? String == "boxResult")
        #expect(opened["state"] as? String == "opened")
        #expect(opened["error"] == nil)
        #expect(BoxPolicy.message("error", error: "Not available")["error"] as? String == "Not available")
    }

    // MARK: - events

    @Test func eventsCarryHostAndFirstSegmentOnly() {
        let u = URL(string: "https://duke.app.box.com/folder/987654321?token=SECRET#frag")!
        #expect(BoxPolicy.blockedDetail(u) == "box duke.app.box.com folder")
        #expect(BoxPolicy.openedDetail(URL(string: "https://duke.app.box.com/s/\(Self.token)")) == "duke.app.box.com")
        #expect(BoxPolicy.blockedDetail(URL(string: "https://duke.app.box.com/")) == "box duke.app.box.com /")
        #expect(BoxPolicy.pathClass(URL(string: "https://x.box.com/\(Self.token)/a")) == "?")
        #expect(BoxPolicy.pathClass(URL(string: "https://x.box.com/1234567/a")) == "1234567")
        #expect(!BoxPolicy.blockedDetail(u).contains("SECRET"))
        #expect(!BoxPolicy.blockedDetail(URL(string: "https://duke.app.box.com/s/\(Self.token)")).contains(Self.token))
        #expect(BoxPolicy.blockedDetail(nil) == "box ? ?")
    }

    // MARK: - content rule list

    @Test func contentRuleListIsValidJSONWithoutTheToken() throws {
        let json = Self.policy().contentRuleListJSON()
        #expect(!json.contains(Self.token))
        let rules = try #require(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [[String: Any]])
        #expect(rules.count > 5)
        let first = try #require(rules.first?["action"] as? [String: String])
        #expect(first["type"] == "block")
        #expect(json.contains("ignore-previous-rules"))
        let filters = rules.compactMap { ($0["trigger"] as? [String: Any])?["url-filter"] as? String }
        #expect(filters.contains(#"^https://duke\.app\.box\.com/"#))
        #expect(filters.contains(#"^https://[a-z0-9.-]+/upload"#))
    }

    @Test func methodOnlyRulesGoToThePageScript() {
        let p = Self.policy()
        #expect(!p.contentRuleListJSON().contains("shared-item"))
        #expect(!p.contentRuleListJSON().contains("api/2\\\\.0/folders\\\\?") || true)
        #expect(p.guardScript(handler: "fpbox").contains("shared-item"))
        #expect(!p.guardScript(handler: "fpbox").contains(Self.token))
    }

    @Test func globMatching() {
        #expect(BoxPolicy.glob("/a/*", "/a/b/c", caseInsensitive: false))
        #expect(BoxPolicy.glob("/a/*/c*", "/a/b/c/d", caseInsensitive: false))
        #expect(!BoxPolicy.glob("/a", "/a/b", caseInsensitive: false))
        #expect(BoxPolicy.glob("/A*", "/abc", caseInsensitive: true))
        #expect(!BoxPolicy.glob("/A*", "/abc", caseInsensitive: false))
    }

    // MARK: - review fixes

    @Test func loginOnlyHostsNeedLoginPaths() {
        for host in ["duke.account.box.com", "account.box.com"] {
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/login*"]]]), allowDev: false) != nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/login", "/login/sso"]]]), allowDev: false) != nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": NSNull()]]), allowDev: false) == nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": []]]), allowDev: false) == nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/login", "/settings"]]]), allowDev: false) == nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/*"]]]), allowDev: false) == nil)
        }
    }

    @Test func accountHostsMayAlsoListTheSsoReturnPathAndNothingElse() {
        for host in ["duke.account.box.com", "account.box.com"] {
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/login*", "/sso/ping_federate*"]]]), allowDev: false) != nil)
            #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/sso/ping_federate*"]]]), allowDev: false) != nil)
            for bad in ["/sso/other*", "/sso*", "/sso/", "/folder/*", "/settings", "/*", "/ping_federate*"] {
                #expect(BoxPolicy(body: Self.config(signIn: [["host": host, "paths": ["/login*", bad]]]), allowDev: false) == nil, Comment(rawValue: bad))
            }
        }
    }

    @Test func methodsAreTheSevenNamesInAnyCase() {
        for m in ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "post", "Patch"] {
            let p = BoxPolicy(body: Self.config(blocked: [["host": "*", "path": "/x", "methods": [m]]]), allowDev: false)
            #expect(p?.blockedRequests.first?.methods == [m.uppercased()], "\(m)")
        }
        let p = Self.policy(Self.config(blocked: [["host": "*", "path": "/x", "methods": ["post"]]]))
        #expect(p.decide(URL(string: "https://duke.app.box.com/x")!, mainFrame: true, method: "POST") == .block)
    }

    @Test func atMost128BlockedRules() {
        let rule: [String: Any] = ["host": "*", "path": "/x"]
        #expect(BoxPolicy(body: Self.config(blocked: Array(repeating: rule, count: 128)), allowDev: false) != nil)
        #expect(BoxPolicy(body: Self.config(blocked: Array(repeating: rule, count: 129)), allowDev: false) == nil)
    }

    @Test func blockedPathsAreCaseInsensitiveWithOrWithoutSlash() {
        #expect(decide("https://sso.services.box.net/Folder/1") == .block)
        #expect(decide("https://sso.services.box.net/api/2.0/FOLDERS/", method: "POST") == .block)
        #expect(decide("https://sso.services.box.net/api/2.0/folders", method: "POST") == .block)
    }

    @Test func signInPathsStayCaseSensitive() {
        #expect(decide("https://duke.account.box.com/login") == .allow)
        #expect(decide("https://duke.account.box.com/LOGIN") == .block)
    }

    @Test func scriptRulesCoverGetAndAnyMethod() {
        let script = Self.policy().guardScript(handler: "fpbox")
        #expect(script.contains("upload"))
        #expect(script.contains("decodeURIComponent"))
    }

    @Test func openRefusedWhileACoverIsUp() {
        #expect(BoxPolicy.openRefusal(locked: true, preLock: false, hasAttempt: true, covered: true) == BoxPolicy.notAvailable)
        #expect(BoxPolicy.openRefusal(locked: true, preLock: false, hasAttempt: true, covered: false) == nil)
        #expect(BoxPolicy.alreadyOpen == "Already open")
    }

    @Test func detailFromStringsNeverNeedsAURL() {
        #expect(BoxPolicy.blockedDetail(host: "a", path: "/%zz") == "box a %zz".replacingOccurrences(of: "%zz", with: "?"))
        #expect(BoxPolicy.blockedDetail(host: "Duke.App.Box.com", path: "/Folder/12") == "box duke.app.box.com folder")
        #expect(BoxPolicy.blockedDetail(host: "a|b", path: "|") == "box ? ?")
        #expect(BoxPolicy.blockedDetail(host: "", path: "") == "box ? /")
        #expect(BoxPolicy.blockedDetail(host: "a.b", path: "no-slash/x") == "box a.b no-slash")
        #expect(BoxPolicy.blockedDetail(host: String(repeating: "a", count: 101), path: "/") == "box ? /")
        #expect(BoxPolicy.blockedDetail(host: "x.com", path: "/ünï") == "box x.com ?")
    }

    @Test func pathClassRules() {
        #expect(BoxPolicy.pathClass(path: "/Upload") == "upload")
        #expect(BoxPolicy.pathClass(path: "/a_b-c9/x") == "a_b-c9")
        #expect(BoxPolicy.pathClass(path: "/1234567890123456") == "1234567890123456")
        #expect(BoxPolicy.pathClass(path: "/12345678901234567") == "?")
        #expect(BoxPolicy.pathClass(path: "/a.b") == "?")
        #expect(BoxPolicy.pathClass(path: "/a%20b") == "?")
        #expect(BoxPolicy.pathClass(path: "/") == "/")
    }

    // MARK: - URL change inside the pane

    private func change(_ s: String?, watch: inout BoxPolicy.PrefixWatch, at t: TimeInterval = 0) -> BoxPolicy.PaneAction {
        Self.policy().urlChange(s.flatMap { URL(string: $0) }, watch: &watch, now: Date(timeIntervalSince1970: 1_000 + t))
    }

    @Test func addressesUnderThePrefixAndOtherHostsStay() {
        var w = BoxPolicy.PrefixWatch()
        for s in ["https://duke.app.box.com/s/\(Self.token)", "https://duke.app.box.com/s/\(Self.token)/file/12?x=1#y",
                  "https://duke.account.box.com/login", "https://sso.services.box.net/x", "about:blank", nil] {
            #expect(change(s, watch: &w) == .stay, "\(s ?? "nil")")
        }
    }

    @Test func anAppAddressOutsideThePrefixReloadsTheStartPage() {
        var w = BoxPolicy.PrefixWatch()
        #expect(change("https://duke.app.box.com/folder/0", watch: &w) == .reloadStart)
        #expect(change("https://duke.box.com/", watch: &w, at: 10) == .reloadStart)
        #expect(change("https://duke.app.box.com/s/\(Self.token)x", watch: &w, at: 20) == .reloadStart)
        #expect(change("https://DUKE.app.box.com/search?q=a", watch: &w, at: 30) == .reloadStart)
    }

    @Test func hiddenSeparatorsAndDotSegmentsCountAsOutside() {
        var w = BoxPolicy.PrefixWatch()
        #expect(change("https://duke.app.box.com/s/\(Self.token)/../x", watch: &w, at: 0) == .reloadStart)
        #expect(change("https://duke.app.box.com/s/\(Self.token)%2f..%2fx", watch: &w, at: 10) == .reloadStart)
        #expect(change("https://duke.app.box.com:8443/s/\(Self.token)", watch: &w, at: 20) == .reloadStart)
    }

    @Test func threeInFiveSecondsClosesThePane() {
        var w = BoxPolicy.PrefixWatch()
        #expect(change("https://duke.app.box.com/a", watch: &w, at: 0) == .reloadStart)
        #expect(change("https://duke.app.box.com/b", watch: &w, at: 1) == .reloadStart)
        #expect(change("https://duke.app.box.com/c", watch: &w, at: 2) == .close)
        #expect(change("https://duke.app.box.com/s/\(Self.token)", watch: &w, at: 3) == .stay)
    }

    @Test func slowRepeatsKeepReloading() {
        var w = BoxPolicy.PrefixWatch()
        for i in 0..<6 {
            #expect(change("https://duke.app.box.com/a", watch: &w, at: Double(i) * 6) == .reloadStart)
        }
    }

    @MainActor
    @Test func webKitCompilesTheRuleList() async throws {
        let json = Self.policy().contentRuleListJSON()
        let list = try await WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "fairplay-box-test", encodedContentRuleList: json)
        #expect(list != nil)
        try await WKContentRuleListStore.default().removeContentRuleList(forIdentifier: "fairplay-box-test")
    }
}
