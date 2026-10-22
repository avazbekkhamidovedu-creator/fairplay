// The iPad app's pure logic, checked against the server's and the Mac app's values.
import Testing
import Foundation
@testable import FairPlayCore

@Suite struct SigningTests {
    let devKey = "fairplay-dev-app-key-not-secret"

    @Test func messageFormatsMatchServer() {
        #expect(Signing.startMessage(nonce: "n1", deviceId: "d1") == "start|n1|d1")
        #expect(Signing.beatMessage(aid: "a_1", session: "s", deviceId: "d", ts: 5, locked: true, checks: "vm=0")
                == "beat|a_1|s|d|5|1|vm=0")
        #expect(Signing.offkeyMessage(aid: "a", session: "s", deviceId: "d", ts: 7) == "offkey|a|s|d|7")
        #expect(Signing.handinMessage(aid: "a", session: "s", status: "submitted", nonce: "n") == "handin|a|s|submitted|n")
        #expect(Signing.exitMessage(aid: "a", session: "s", nonce: "n") == "exit|a|s|ok|n")
    }

    @Test func hmacMatchesPython() {
        // python3 -c "import hmac,hashlib;print(hmac.new(b'fairplay-dev-app-key-not-secret',b'start|abc|dev',hashlib.sha256).hexdigest())"
        #expect(Signing.hmacHex(key: devKey, "start|abc|dev")
                == "ed125ffa5a598fd5ad1d6973e0958b4afabee44dd49fa8989c9fc7fb423ebc3b")
    }

    @Test func safeTokens() {
        #expect(Signing.isSafeToken("a_1-B", min: 3, max: 32))
        #expect(!Signing.isSafeToken("a|b|c", min: 3, max: 32))
        #expect(!Signing.isSafeToken("ab", min: 3, max: 32))
        #expect(!Signing.isSafeToken("ä_123", min: 3, max: 32))
    }

    @Test func hexAndConstantTime() {
        #expect(Signing.data(hex: "00ff10") == Data([0, 255, 16]))
        #expect(Signing.data(hex: "0g") == nil)
        #expect(Signing.data(hex: "+f") == nil)
        #expect(Signing.data(hex: "abc") == nil)
        #expect(Signing.constantTimeEqual("abc", "abc"))
        #expect(!Signing.constantTimeEqual("abc", "abd"))
        #expect(!Signing.constantTimeEqual("abc", "ab"))
    }

    @Test func handInProofs() {
        let good = Signing.hmacHex(key: devKey, "handin|a_1|sess-1234|submitted|n1")
        #expect(Signing.handInReplyIsGenuine(key: devKey, proofs: ["x", good], attemptId: "a_1",
                                             session: "sess-1234", status: "submitted", nonce: "n1"))
        // A proof for "in_progress" can't be replayed as "submitted".
        let other = Signing.hmacHex(key: devKey, "handin|a_1|sess-1234|in_progress|n1")
        #expect(!Signing.handInReplyIsGenuine(key: devKey, proofs: [other], attemptId: "a_1",
                                              session: "sess-1234", status: "submitted", nonce: "n1"))
        #expect(Signing.unlockAllowed(status: "moved") && !Signing.unlockAllowed(status: "in_progress"))
    }

    @Test func exitResults() {
        let proof = Signing.hmacHex(key: devKey, "exit|a|s|ok|n")
        #expect(Signing.exitResult(key: devKey, status: 200, json: ["ok": true, "proofs": [proof]],
                                   attemptId: "a", session: "s", nonce: "n") == .ok)
        #expect(Signing.exitResult(key: devKey, status: 200, json: ["ok": true, "proofs": ["bad"]],
                                   attemptId: "a", session: "s", nonce: "n") == .serverError(200))
        #expect(Signing.exitResult(key: devKey, status: 403, json: nil, attemptId: "a", session: "s", nonce: "n") == .wrong)
        #expect(Signing.exitResult(key: devKey, status: 429, json: nil, attemptId: "a", session: "s", nonce: "n") == .tooMany)
        #expect(Signing.exitResult(key: devKey, status: nil, json: nil, attemptId: "a", session: "s", nonce: "n") == .unreachable)
    }

    @Test func tokenOwner() {
        let payload = Data("42:student:123".utf8).base64EncodedString()
            .replacingOccurrences(of: "=", with: "").replacingOccurrences(of: "+", with: "-")
        #expect(Signing.tokenUserId(payload + ".sig") == "42")
        #expect(Signing.mayReplace(currentAttempt: "a", currentToken: payload + ".x", attemptId: "a", token: payload + ".y"))
        let other = Data("7:student:1".utf8).base64EncodedString().replacingOccurrences(of: "=", with: "")
        #expect(!Signing.mayReplace(currentAttempt: "a", currentToken: payload + ".x", attemptId: "a", token: other + ".y"))
        #expect(!Signing.mayReplace(currentAttempt: "a", currentToken: payload + ".x", attemptId: "b", token: payload + ".x"))
    }

    @Test func deviceIdShape() {
        let id = Signing.deviceId(secret: "abc", key: devKey)
        #expect(id.hasPrefix("ipad-") && id.count == 37)
        #expect(id.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" })
        #expect(Signing.deviceId(secret: "abc", key: "other") != id)
    }
}

@Suite struct OfflineExitTests {
    @Test func vectorsMatchServer() {
        // server tests/test_app_session.py::test_offline_key_vector
        let key = Data((0..<32).map { UInt8($0) })
        #expect(OfflineExitCode.expectedCode(for: "K7QM3XPA", key: key) == "06710479")
        #expect(OfflineExitCode.expectedCode(for: "K7QM3XPA", key: Data("fairplay-dev-app-key-not-secret".utf8))
                == "37496243")
    }

    @Test func fiveWrongThenWait() {
        let key = Data((0..<32).map { UInt8($0) })
        var o = OfflineExitCode(challenge: "K7QM3XPA")
        let t0 = Date()
        for _ in 0..<4 { #expect(o.check("00000000", key: key, now: t0) == .wrong) }
        #expect(o.check("00000000", key: key, now: t0) == .tooMany)
        #expect(o.check("0671-0479", key: key, now: t0.addingTimeInterval(60)) == .tooMany)
        #expect(o.check("0671-0479", key: key, now: t0.addingTimeInterval(301)) == .ok)
        #expect(o.check("1", key: nil, now: t0.addingTimeInterval(400)) == .unavailable)
        #expect(o.challengeForDisplay == "K7QM-3XPA")
    }

    @Test func challengeAlphabet() {
        let c = OfflineExitCode().challenge
        #expect(c.count == 8 && c.allSatisfy { OfflineExitCode.alphabet.contains($0) })
    }
}

@Suite struct SiteRuleTests {
    @Test func exactHostDoesNotOpenSubdomains() {
        let r = SiteRule("canvas.dku.edu")!
        #expect(r.matches(host: "canvas.dku.edu", path: "/x"))
        #expect(!r.matches(host: "x.canvas.dku.edu", path: "/"))
        #expect(!r.matches(host: "evilcanvas.dku.edu", path: "/"))
    }

    @Test func wildcardAndPathPrefix() {
        #expect(SiteRule("*.dku.edu")!.matches(host: "canvas.dku.edu", path: "/"))
        #expect(!SiteRule("*.dku.edu")!.matches(host: "dku.edu.evil.com", path: "/"))
        let forms = SiteRule("docs.google.com/forms/d/e/ABC")!
        #expect(forms.matches(host: "docs.google.com", path: "/forms/d/e/ABC/viewform"))
        #expect(!forms.matches(host: "docs.google.com", path: "/forms/d/e/ABCDEF"))
    }

    @Test func startHostFolderAdded() {
        let rules = SiteRule.rules(from: [], startURL: URL(string: "https://docs.google.com/forms/d/x/viewform")!)
        #expect(rules == [SiteRule("docs.google.com/forms/d/x/")!])
        #expect(SiteRule("") == nil && SiteRule("*.*.com") == nil)
    }
}

@Suite struct PathTests {
    let server = URL(string: "http://127.0.0.1:8000")!

    @Test func serverOrigin() {
        #expect(ExamPaths.isServerURL(URL(string: "http://127.0.0.1:8000/student"), server: server))
        #expect(!ExamPaths.isServerURL(URL(string: "http://127.0.0.1:8001/student"), server: server))
        #expect(!ExamPaths.isServerURL(URL(string: "https://127.0.0.1:8000/student"), server: server))
        #expect(ExamPaths.isServerURL(URL(string: "https://x.edu/a"), server: URL(string: "https://X.edu:443")!))
    }

    @Test func lockedPages() {
        #expect(ExamPaths.allowedWhileLocked(path: "/student/exam/a_1", attemptId: "a_1"))
        #expect(ExamPaths.allowedWhileLocked(path: "/student/done/a_1", attemptId: "a_1"))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student/exam/a_2", attemptId: "a_1"))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student", attemptId: "a_1"))
        #expect(ExamPaths.allowedWhileLocked(path: "/student/exam/offline:e1", attemptId: "a_1"))
        #expect(ExamPaths.isTeacherPath("/teacher/exams/1") && ExamPaths.isTeacherPath("/docs"))
        #expect(!ExamPaths.isTeacherPath("/teachers-guide"))
    }

    @Test func nonceScriptOrigin() {
        #expect(ExamPaths.nonceScript(nonce: "abc'", server: server)
                == "if (location.origin === 'http://127.0.0.1:8000') { window.__fairplayNonce = 'abc'; }")
    }

    @Test func participants() {
        let ids = ExamPaths.allowedParticipants(["com.apple.calculator", "calc", "com.apple.mobilesafari",
                                                 "com.google.chrome.ios", "com.apple.calculator", 5])
        #expect(ids == ["com.apple.calculator"])
    }
}

@Suite struct IntegrityTests {
    @Test func summaryHasServerKeys() {
        var s = DeviceState()
        s.assessment = true
        let sum = s.summary
        #expect(sum.hasPrefix("plat=ipad,aac=1,"))
        #expect(sum.contains("console=1") && sum.contains("secinput=0"))
        s.captured = true
        #expect(s.summary.contains("console=0"))
        #expect(sum.count < 200)
    }

    @Test func problemsReleaseVsDev() {
        var s = DeviceState()
        s.jailbreak = ["/Applications/Cydia.app"]
        s.debugger = true
        #expect(Integrity.problems(s, release: false).isEmpty)
        let rel = Integrity.problems(s, release: true)
        #expect(Set(rel.keys) == ["jailbreak", "debugger", "assessment"])
        s = DeviceState(); s.assessment = true; s.multitasking = true; s.screens = 2
        #expect(Set(Integrity.problems(s, release: true).keys) == ["multitask", "display"])
    }

    @Test func preflightBattery() {
        var s = DeviceState()
        s.batteryPercent = 12
        #expect(Integrity.preflight(s, release: true, osMajor: 26).count == 1)
        s.charging = true
        #expect(Integrity.preflight(s, release: true, osMajor: 26).isEmpty)
    }
}

@Suite struct ThrottleTests {
    @Test func repeatsAndCap() {
        var t = EventThrottle()
        let now = Date()
        #expect(t.decide(type: "screenshot_taken", detail: "", at: now).count == 1)
        #expect(t.decide(type: "screenshot_taken", detail: "", at: now.addingTimeInterval(1)).isEmpty)
        #expect(t.decide(type: "screenshot_taken", detail: "", at: now.addingTimeInterval(11)).count == 1)
        #expect(t.decide(type: "lock_started", detail: "", at: now).count == 1)
        #expect(t.decide(type: "lock_started", detail: "", at: now).count == 1)
    }
}


@Suite struct ReviewFixTests {
    let devKey = "fairplay-dev-app-key-not-secret"

    @Test func offlineKeyNeedsProof() {
        // python: hmac(devKey, "offkey-resp|a_1|sess-1234|ab|7")
        let good = "c5a4b0ac2efd2a38cec4d0c8ae1416896c28a59a3ed826a394e0e4aa380e7690"
        #expect(Signing.offkeyReplyIsGenuine(key: devKey, json: ["key": "ab", "proofs": ["x", good]],
                                             attemptId: "a_1", session: "sess-1234", keyHex: "ab", ts: 7))
        #expect(Signing.offkeyReplyIsGenuine(key: devKey, json: ["key": "ab", "proof": good],
                                             attemptId: "a_1", session: "sess-1234", keyHex: "ab", ts: 7))
        // M2: no proof at all is refused (it used to be accepted).
        #expect(!Signing.offkeyReplyIsGenuine(key: devKey, json: ["key": "ab"],
                                              attemptId: "a_1", session: "sess-1234", keyHex: "ab", ts: 7))
        #expect(!Signing.offkeyReplyIsGenuine(key: devKey, json: ["key": "ab", "proof": good],
                                              attemptId: "a_1", session: "sess-1234", keyHex: "cd", ts: 7))
    }

    @Test func unreadableTokenOnlyAcceptsItself() {
        #expect(Signing.mayReplace(currentAttempt: "a", currentToken: "junk", attemptId: "a", token: "junk"))
        #expect(!Signing.mayReplace(currentAttempt: "a", currentToken: "junk", attemptId: "a", token: "other"))
    }

    @Test func shieldAndConsole() {
        var s = DeviceState()
        s.shield = false
        s.singleApp = true
        #expect(Integrity.problems(s, release: true)["shield"] != nil)
        s.assessment = true
        #expect(Integrity.problems(s, release: true)["shield"] == nil)
        #expect(s.summary.contains("shield=0") && s.summary.contains("ga=0"))
        // M5: in an allowed app under Assessment Mode the exam is still "on console".
        s.foreground = false
        #expect(!s.onConsole)
        s.participants = 1
        #expect(s.onConsole)
        s.assessment = false
        #expect(!s.onConsole)
    }

    @Test func participantDenylist() {
        let ids = ExamPaths.allowedParticipants(["com.apple.DocumentsApp", "com.apple.iBooks", "com.apple.Pages",
                                                 "com.apple.calculator"])
        #expect(ids == ["com.apple.calculator"])
    }
}

@Suite struct S1S6Tests {
    @Test func sealedMatchesServer() {
        // server: hashlib.sha256(b'{"q1":2}').hexdigest() and hmac(key 00..1f, payload)
        let answers = "{\"q1\":2}"
        #expect(SealedFormat.sha256Hex(answers) == "fd3c78c30e5f4d72e287d24fbf38c4bcda72b614c1aba7a498b2611d5811fefc")
        let p = SealedFormat.payload(attemptId: "a_1", session: "sess-1234", deviceId: "ipad-x", clientSeq: 3,
                                     sealedAt: "2026-10-20T10:00:00.000Z", answersJSON: answers)
        #expect(p == "a_1|sess-1234|ipad-x|3|2026-10-20T10:00:00.000Z|fd3c78c30e5f4d72e287d24fbf38c4bcda72b614c1aba7a498b2611d5811fefc")
        #expect(SealedFormat.proof(payload: p, key: Data((0..<32).map { UInt8($0) })) == "09fca61994fbf78cd3dee950b5c3d727ccebdf6ebd38482f189d588eeec5b4f7")
        #expect(SealedFormat.answersAreValid(answers) && !SealedFormat.answersAreValid("[1]"))
        #expect(SealedFormat.outcome(status: 200) == .done && SealedFormat.outcome(status: nil) == .keep)
        #expect(SealedFormat.outcome(status: 503) == .keep && SealedFormat.outcome(status: 422) == .reject)
    }

    @Test func versionsCompareLikeServer() {
        #expect(AppVersion.meets("0.10", minimum: "0.9"))
        #expect(!AppVersion.meets("0.9", minimum: "0.10"))
        #expect(AppVersion.meets("1.0", minimum: "1.0.0") && AppVersion.meets("0.5", minimum: nil))
        #expect(!AppVersion.meets(nil, minimum: "0.1"))
    }

    @Test func clockUsesServerTimePlusUptime() {
        let c = ServerClock()
        c.update(serverTime: "2026-10-20T10:00:00.000Z", uptime: 100, now: Date(timeIntervalSince1970: 0))
        #expect(ServerClock.iso(c.now(uptime: 130)) == "2026-10-20T10:00:30.000Z")
        #expect(!ServerClock.skewOK(c.offset!))
    }

    @Test func beatSaysSepAndSealed() {
        var s = DeviceState()
        #expect(s.summary.contains("sep=0") && s.summary.contains("sealed=0"))
        s.assessment = true
        s.sealedPending = true
        #expect(s.summary.contains("sep=1") && s.summary.contains("sealed=1"))
        #expect(s.summary.count < 200)
    }

    @Test func deviceCheckRows() {
        let rows = CheckRow.ipadRows(problems: [("display", "Stop recording")], appVersion: "0.5", minVersion: "0.6",
                                     osVersion: "26.5", serverReached: true, clockOffset: 1, batteryPercent: 80,
                                     charging: false, vpn: false)
        #expect(rows.first(where: { $0.id == "app_version" })?.ok == false)
        #expect(rows.first(where: { $0.id == "display" })?.ok == false)
        #expect(rows.filter { !$0.ok && !$0.info }.count == 2)
        #expect(rows.allSatisfy { $0.message.count <= 300 } && rows.count <= 40)
    }
}

/// 0.5.1 (iPad review 2026-10-22): watched test builds, dev capture, jailbreak matcher.
@Suite struct WatchedBuildTests {
    @Test func watchedLockIsNotBlockedButStillReported() {
        var s = DeviceState()
        s.softLock = true
        s.shield = true
        #expect(Integrity.problems(s, release: true).isEmpty)
        #expect(s.summary.contains("aac=0") && s.summary.contains("sam=0") && s.summary.contains("soft=1"))
        // A release build that is not watched never sets softLock: still blocked.
        s.softLock = false
        #expect(Integrity.problems(s, release: true)["assessment"] != nil)
        // Watched builds keep the other release checks.
        s.softLock = true
        s.debugger = true
        s.jailbreak = ["/var/jb"]
        #expect(Set(Integrity.problems(s, release: true).keys) == ["debugger", "jailbreak"])
    }

    @Test func allowedCaptureSkipsOnlyTheRedScreen() {
        var s = DeviceState()
        s.captured = true
        s.captureAllowed = true
        #expect(Integrity.problems(s, release: false)["display"] == nil)
        #expect(Integrity.preflight(s, release: false, osMajor: 26).isEmpty)
        #expect(s.summary.contains("cap=1") && s.summary.contains("console=0"))
        s.screens = 2
        #expect(Integrity.problems(s, release: false)["display"] != nil)
    }

    @Test func hookLibrariesByLocationAndName() {
        // Apple's own code never counts, whatever its name.
        #expect(Integrity.hookLibrary(imagePath: "/System/Library/PrivateFrameworks/ShadowCore.framework/ShadowCore") == nil)
        #expect(Integrity.hookLibrary(imagePath: "/usr/lib/system/libflexible.dylib") == nil)
        #expect(Integrity.hookLibrary(imagePath: "/System/Library/Frameworks/UIKit.framework/UIKit") == nil)
        #expect(Integrity.hookLibrary(imagePath: "/private/var/containers/Bundle/Application/X/FairPlay.app/FairPlay") == nil)
        // Jailbreak install places count everywhere.
        #expect(Integrity.hookLibrary(imagePath: "/Library/MobileSubstrate/DynamicLibraries/Anything.dylib") != nil)
        #expect(Integrity.hookLibrary(imagePath: "/var/jb/usr/lib/libellekit.dylib") != nil)
        #expect(Integrity.hookLibrary(imagePath: "/usr/lib/TweakInject.dylib") != nil)
        // Hooking libraries by name outside the system folders (e.g. injected into the app).
        #expect(Integrity.hookLibrary(imagePath: "/private/var/containers/Bundle/Application/X/FairPlay.app/Frameworks/FridaGadget.dylib") == "frida")
        #expect(Integrity.hookLibrary(imagePath: "/var/mobile/x/libsubstrate.dylib") == "substrate")
    }
}

// 2026-10-23: the pre-exam lock (same decisions as the Mac's PreLockTests).
@Suite struct PreLockTests {

    @Test func decisions() {
        #expect(PreLockState.onPreLock(locked: false, preLocked: false, hasExam: false) == .lock)
        #expect(PreLockState.onPreLock(locked: true, preLocked: true, hasExam: false) == .alreadyPreLocked)
        #expect(PreLockState.onPreLock(locked: true, preLocked: false, hasExam: true) == .examLocked)
        #expect(PreLockState.onPreLock(locked: true, preLocked: false, hasExam: false) == .examLocked)   // offline start

        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: false) == .unlock)
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: true, hasExam: true) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: true, preLocked: false, hasExam: false) == .refuse)
        #expect(PreLockState.onPreUnlock(locked: false, preLocked: false, hasExam: false) == .notLocked)

        #expect(PreLockState.onExamLock(locked: true, preLocked: true) == .promote)
        #expect(PreLockState.onExamLock(locked: true, preLocked: false) == .lock)
        #expect(PreLockState.onExamLock(locked: false, preLocked: false) == .lock)

        #expect(PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: true, hasExam: false))
        #expect(!PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: false, hasExam: false))
        #expect(!PreLockState.emergencyExitUnlocksAtOnce(locked: true, preLocked: true, hasExam: true))
    }

    @Test func pages() {
        #expect(ExamPaths.allowedWhileLocked(path: "/student/exams/e_1/checkin", attemptId: nil, preLock: true))
        #expect(ExamPaths.allowedWhileLocked(path: "/student/exams/e_1/start", attemptId: nil, preLock: true))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student/exams/e_1/start", attemptId: nil))
        #expect(ExamPaths.allowedWhileLocked(path: "/student", attemptId: nil, preLock: true))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student/grades", attemptId: nil, preLock: true))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student/exams/e_1/checkin", attemptId: nil))
        #expect(!ExamPaths.allowedWhileLocked(path: "/student", attemptId: "a_1", preLock: true))
    }

    @Test func startPageAndWayBack() {
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1/start") == "e_1")
        #expect(PreLockState.startPageExamId(path: "/student/exams/E-9/start/") == "E-9")
        #expect(PreLockState.startPageExamId(path: "/Student/Exams/e1/START") == "e1")
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1/checkin") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/e_1") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams//start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student//exams/e_1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/e%2F1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/teacher/exams/e_1/start") == nil)
        #expect(PreLockState.startPageExamId(path: "/student/exams/" + String(repeating: "a", count: 65) + "/start") == nil)
        #expect(PreLockState.homePath(examId: "e_1") == "student/exams/e_1/start")
        #expect(PreLockState.homePath(examId: nil) == "student")
        #expect(PreLockState.homePath(examId: "../teacher") == "student")
        // Both ways back are pages the pre-exam lock shows.
        #expect(PreLockState.allowedWhilePreLocked(path: "/" + PreLockState.homePath(examId: "e_1")))
        #expect(PreLockState.allowedWhilePreLocked(path: "/" + PreLockState.homePath(examId: nil)))
    }

    @Test func relaunchMarker() {
        #expect(PreLockState.markerIsPreLock(PreLockState.marker))
        #expect(PreLockState.markerIsPreLock(PreLockState.marker + "\n"))
        #expect(!PreLockState.markerIsPreLock("-"))          // offline start
        #expect(!PreLockState.markerIsPreLock("a_1"))
        // Never mistaken for an attempt id.
        #expect(!Signing.isSafeToken(PreLockState.marker, min: 3, max: 32))
    }
}
