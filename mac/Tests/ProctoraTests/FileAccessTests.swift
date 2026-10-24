// 2026-10-24: file picking only while unlocked, the allowed-site pane with Duke Box (sign-in
// redirects, wildcard companion hosts) and keeping / clearing the other sites' sign-ins.
import Testing
import Foundation
@testable import Proctora

@Suite struct FileAccessTests {

    @Test func filesOnlyWhileUnlockedInTheExamPage() {
        #expect(FilePicker.allowed(locked: false, fromServer: true))
        #expect(!FilePicker.allowed(locked: true, fromServer: true))      // exam lock or pre-exam lock
        #expect(!FilePicker.allowed(locked: false, fromServer: false))    // another site / a frame
        #expect(!FilePicker.allowed(locked: true, fromServer: false))
        #expect(FilePicker.refusedMessage["type"] as? String == "fileRefused")
    }

    @Test func originAsThePageSeesIt() {
        #expect(FilePicker.origin(URL(string: "https://Exam.Example.edu/")!) == "https://exam.example.edu")
        #expect(FilePicker.origin(URL(string: "https://exam.example.edu:443/x")!) == "https://exam.example.edu")
        #expect(FilePicker.origin(URL(string: "http://192.168.1.5:8000")!) == "http://192.168.1.5:8000")
        // Nothing that could leave the script's string.
        #expect(!FilePicker.origin(URL(string: "https://a.com/'x")!).contains("'"))
    }

    @Test func guardScriptIsOffByDefaultAndOnlyTheExamPageTurnsItOn() {
        let js = FilePicker.guardScript(origin: "https://exam.example.edu",
                                        post: "window.webkit.messageHandlers.proctora.postMessage")
        #expect(js.contains("location.origin === 'https://exam.example.edu'"))
        #expect(js.contains("var allowed = false;"))
        #expect(js.contains("__fairplayFiles"))
        #expect(js.contains("'Files'"))                       // dropped files are refused too
        #expect(js.contains("showPicker"))
        #expect(js.contains("window.webkit.messageHandlers.proctora.postMessage({ action: 'fileRefused' })"))
        #expect(FilePicker.setScript(allowed: true).contains("__fairplayFiles(true)"))
        #expect(FilePicker.setScript(allowed: false).contains("__fairplayFiles(false)"))
    }

    @Test func siteFrameScriptKeepsNewTabsInThePane() {
        let js = SiteFrameScript.make(origin: "https://exam.example.edu")
        #expect(js.contains("location.origin !== 'https://exam.example.edu'"))
        #expect(js.contains("'_blank'") && js.contains("'_top'") && js.contains("'_self'"))
        #expect(js.contains("return null;"))                   // window.open never returns a window
    }

    // MARK: Duke Box in the pane

    /// What the server sends for an exam that allows duke.app.box.com (example list; the server
    /// decides the exact companion hosts).
    private let box = ["duke.app.box.com", "app.box.com", "account.box.com", "*.boxcdn.net", "*.boxcloud.com",
                       "*.box.net", "*.duosecurity.com", "shib.oit.duke.edu", "*.box.com"]

    private func frame(_ s: String) -> Bool { AllowedSites.frameAllowed(URL(string: s)!, allowed: box) }

    @Test func anyPathOnAnAllowedHostOpens() {
        #expect(frame("https://duke.app.box.com/folder/0/anything/123"))
        #expect(frame("https://duke.app.box.com/file/987654?s=abc#page=2"))
        #expect(frame("https://duke.app.box.com/"))
    }

    @Test func signInHostsAndWildcardsMatchStrictly() {
        #expect(frame("https://account.box.com/login?redirect_url=%2Ffolder%2F0"))
        #expect(frame("https://api-1234abcd.duosecurity.com/frame/v4/auth/prompt"))
        #expect(frame("https://shib.oit.duke.edu/idp/profile/SAML2/POST/SSO"))
        #expect(frame("https://public.boxcloud.com/api/2.0/internal_files/1/versions/2/representations/pdf"))
        #expect(frame("https://cdn01.boxcdn.net/app/x.js"))
        #expect(frame("https://box.com/"))                            // "*.box.com" is box.com too
        // Never a look-alike or a longer name.
        #expect(!frame("https://evilbox.com/"))
        #expect(!frame("https://box.com.evil.net/"))
        #expect(!frame("https://duosecurity.com.attacker.io/"))
        #expect(!frame("https://notduosecurity.com/"))
        #expect(!frame("https://shib.oit.duke.edu.evil.com/"))
        #expect(!frame("https://other.duke.edu/"))                    // a plain entry is that host only
        // Plain http, another port, user info: never.
        #expect(!frame("http://duke.app.box.com/folder/0"))
        #expect(!frame("https://duke.app.box.com:8443/folder/0"))
        #expect(!frame("https://duke.app.box.com@evil.com/"))
        // A link out of Box stays blocked.
        #expect(!frame("https://www.google.com/search?q=answer"))
        #expect(!AllowedSites.isAllowedPopup(URL(string: "https://chat.openai.com/")!, allowed: box))
        #expect(AllowedSites.isAllowedPopup(URL(string: "https://duke.app.box.com/file/1")!, allowed: box))
    }

    @Test func wildcardEntriesTheServerSendsAreKept() {
        #expect(AllowedSites.parse(box) == box)
        // Never a whole public suffix or shared hosting.
        #expect(AllowedSites.parse(["*.com", "*.net", "*.github.io", "*.amazonaws.com"]) == [])
    }

    @Test func downloadsStayBlocked() {
        #expect(AllowedSites.isAttachment(contentDisposition: "attachment; filename=\"notes.pdf\""))
        #expect(!AllowedSites.isAttachment(contentDisposition: "inline; filename=\"notes.pdf\""))
    }

    // MARK: Sign-ins: kept for the day, cleared at FairPlay sign-out

    @Test func signOutIsTheSignInPageAfterASignedInPageWhileUnlocked() {
        #expect(SiteData.isSignedInPath("/student"))
        #expect(SiteData.isSignedInPath("/student/exams/abc/materials"))
        #expect(!SiteData.isSignedInPath("/teacher"))
        #expect(!SiteData.isSignedInPath("/student/login"))
        #expect(SiteData.isLoginPath("/Student/Login/"))
        #expect(SiteData.isSignOut(sawSignedInPage: true, path: "/student/login", locked: false))
        #expect(!SiteData.isSignOut(sawSignedInPage: false, path: "/student/login", locked: false))   // app start
        #expect(!SiteData.isSignOut(sawSignedInPage: true, path: "/student/login", locked: true))     // never mid-exam
        #expect(!SiteData.isSignOut(sawSignedInPage: true, path: "/student/exams", locked: false))
    }

    @Test func clearedOncePerDayButNotAfterALockedRun() {
        #expect(SiteData.shouldClearAtLaunch(lastDay: nil, today: "2026-10-24", endedLocked: false))
        #expect(SiteData.shouldClearAtLaunch(lastDay: "2026-10-23", today: "2026-10-24", endedLocked: false))
        #expect(!SiteData.shouldClearAtLaunch(lastDay: "2026-10-24", today: "2026-10-24", endedLocked: false))
        #expect(!SiteData.shouldClearAtLaunch(lastDay: "2026-10-23", today: "2026-10-24", endedLocked: true))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Shanghai")!
        let d = cal.date(from: DateComponents(year: 2026, month: 3, day: 7, hour: 23, minute: 59))!
        #expect(SiteData.day(d, calendar: cal) == "2026-03-07")
    }

    @Test func theExamServersDataIsKept() {
        let server = "fairplay.example.edu"
        #expect(SiteData.keeps(domain: "fairplay.example.edu", serverHost: server))
        #expect(SiteData.keeps(domain: "example.edu", serverHost: server))       // WebKit's record name
        #expect(SiteData.keeps(domain: ".example.edu", serverHost: server))      // a cookie domain
        #expect(!SiteData.keeps(domain: "box.com", serverHost: server))
        #expect(!SiteData.keeps(domain: "duosecurity.com", serverHost: server))
        #expect(!SiteData.keeps(domain: "ample.edu", serverHost: server))
        #expect(!SiteData.keeps(domain: "", serverHost: server))
        #expect(SiteData.keeps(domain: "192.168.1.5", serverHost: "192.168.1.5"))
    }
}
