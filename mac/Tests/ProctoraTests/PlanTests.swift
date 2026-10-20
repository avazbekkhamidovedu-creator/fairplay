// Plan 2026-10-20 (M2 check this Mac, M4 sealed answers, M5 clock and version).
// The sealed-file vector was computed in Python exactly as the server verifies it:
// hmac.new(key, payload, sha256).hexdigest() with key = bytes(range(32)).
import Testing
import Foundation
@testable import Proctora

@Suite struct VersionAndClockTests {
    @Test func versionsCompareByNumber() {
        #expect(AppVersion.compare("0.10", "0.9") == .orderedDescending)
        #expect(AppVersion.compare("1.0", "1.0.0") == .orderedSame)
        #expect(AppVersion.compare("0.5", "0.6") == .orderedAscending)
        #expect(AppVersion.meets("0.5", minimum: nil))
        #expect(AppVersion.meets("0.5", minimum: ""))
        #expect(AppVersion.meets("0.5", minimum: "0.5"))
        #expect(!AppVersion.meets("0.5", minimum: "0.5.1"))
    }

    @Test func serverTimeParsesAndFormats() {
        let d = ServerClock.parse("2026-10-20T11:00:00.123Z")
        #expect(d != nil)
        #expect(ServerClock.iso(d!) == "2026-10-20T11:00:00.123Z")
        #expect(ServerClock.parse("2026-10-20T11:00:00Z") != nil)
        #expect(ServerClock.parse("2026-10-20T11:00:00+00:00") != nil)
        #expect(ServerClock.parse("yesterday") == nil)
    }

    @Test func clockSkewLimitIsTwoMinutes() {
        #expect(ServerClock.skewOK(0))
        #expect(ServerClock.skewOK(-120))
        #expect(!ServerClock.skewOK(121))
        #expect(!ServerClock.skewOK(-3600))
    }

    @Test func serverClockMovesWithUptimeNotWallClock() {
        let c = ServerClock()
        #expect(!c.known)
        c.update(serverTime: "2020-01-01T00:00:00.000Z")
        #expect(c.known)
        // Far in the past on purpose: now() follows the server, not the Mac's clock.
        #expect(c.now() < Date(timeIntervalSince1970: 1_600_000_000))
        #expect(c.skewOK == false)
    }
}

@Suite struct SealedAnswerTests {
    let key = Data((0..<32).map { UInt8($0) })
    let answers = #"{"q1":"B"}"#

    @Test func payloadAndProofMatchTheServer() {
        let p = Sealed.payload(attemptId: "att123", session: "sess-abcdefgh", deviceId: "dev-1", clientSeq: 7,
                               sealedAt: "2026-10-20T11:00:00.123Z", answersJSON: answers)
        #expect(p == "att123|sess-abcdefgh|dev-1|7|2026-10-20T11:00:00.123Z|8580d9b0bf7cc97e2a9c72e90c014b1f2550cfea287da6ed2dae6d8a55ae14df")
        #expect(Sealed.proof(payload: p, key: key) == "efcff8909b0cdbc5d769c8f181c403a5c7f8946f879b270593dc78a411c9a5f7")
    }

    private func tempFolder() -> URL {
        FileManager.default.temporaryDirectory.appendingPathComponent("fp-sealed-\(UUID().uuidString)")
    }

    @Test func sealWritesAPrivateFile() throws {
        let folder = tempFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let exam = AppSession.Exam(attemptId: "att123", token: "tok", session: "sess-abcdefgh")
        let when = ServerClock.parse("2026-10-20T11:00:00.123Z")!
        let url = try Sealed.seal(attemptId: "att123", answersJSON: answers, clientSeq: 7, exam: exam,
                                  offlineKey: key, deviceId: "dev-1", now: when, folder: folder)
        let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
        #expect((attrs[.posixPermissions] as? NSNumber)?.intValue == 0o600)
        let file = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String: Any]
        #expect(file["proof"] as? String == "efcff8909b0cdbc5d769c8f181c403a5c7f8946f879b270593dc78a411c9a5f7")
        #expect(file["answers_json"] as? String == answers)
        #expect(Sealed.pending(folder: folder).count == 1)
    }

    @Test func sealRefusesWithoutKeyExamOrValidAnswers() {
        let folder = tempFolder()
        defer { try? FileManager.default.removeItem(at: folder) }
        let exam = AppSession.Exam(attemptId: "att123", token: "tok", session: "sess-abcdefgh")
        #expect(throws: Sealed.SealError.noKey) {
            try Sealed.seal(attemptId: "att123", answersJSON: answers, clientSeq: 1, exam: exam,
                            offlineKey: nil, deviceId: "d", folder: folder)
        }
        #expect(throws: Sealed.SealError.noExam) {
            try Sealed.seal(attemptId: "other", answersJSON: answers, clientSeq: 1, exam: exam,
                            offlineKey: key, deviceId: "d", folder: folder)
        }
        #expect(throws: Sealed.SealError.badInput) {
            try Sealed.seal(attemptId: "att123", answersJSON: "[1,2]", clientSeq: 1, exam: exam,
                            offlineKey: key, deviceId: "d", folder: folder)
        }
        #expect(Sealed.pending(folder: folder).isEmpty)
    }

    @Test func uploadOutcomes() {
        #expect(Sealed.outcome(status: 200) == .done)
        #expect(Sealed.outcome(status: nil) == .keep)
        #expect(Sealed.outcome(status: 401) == .keep)
        #expect(Sealed.outcome(status: 429) == .keep)
        #expect(Sealed.outcome(status: 503) == .keep)
        #expect(Sealed.outcome(status: 400) == .reject)
        #expect(Sealed.outcome(status: 409) == .reject)
    }
}

@Suite struct CheckThisMacTests {
    @Test func tunnelNames() {
        #expect(Readiness.isTunnelName("utun3"))
        #expect(Readiness.isTunnelName("ipsec0"))
        #expect(!Readiness.isTunnelName("en0"))
        #expect(!Readiness.isTunnelName("utunnel"))
    }

    @Test func batteryRule() {
        typealias B = Readiness.Battery
        #expect(Readiness.batteryOK(nil))
        #expect(Readiness.batteryOK(B(percent: 20, charging: true)))
        #expect(Readiness.batteryOK(B(percent: 50, charging: false)))
        let low = Readiness.batteryOK(B(percent: 49, charging: false))
        #expect(low == false)
    }

    @Test func rowsMergeProblemsByIdAndKeepInfoRowsNonBlocking() {
        let rows = Readiness.rows(problems: [("blocked_apps", "Turn off Screen Sharing."),
                                             ("blocked_apps", "Turn off Remote Login (SSH)."),
                                             ("clock", "Set the clock.")],
                                  serverReached: true, minVersion: "0.1", clockOffset: 500,
                                  freeBytes: 10, battery: .init(percent: 10, charging: false),
                                  vpn: ["utun4"], accessibility: true)
        let byId = Dictionary(grouping: rows, by: \.id)
        #expect(byId.values.allSatisfy { $0.count == 1 })
        #expect(byId["blocked_apps"]?.first?.ok == false)
        #expect(byId["blocked_apps"]?.first?.message.contains("Remote Login") == true)
        #expect(byId["clock"]?.first?.ok == false)
        #expect(byId["disk"]?.first?.ok == false)
        #expect(byId["battery"]?.first?.info == true)
        #expect(byId["vpn"]?.first?.info == true)
        #expect(byId["app_version"]?.first?.ok == true)
        for id in ["macos_version", "server", "accessibility", "displays", "input_source", "vm", "remote"] {
            #expect(byId[id] != nil, "missing row \(id)")
        }
    }

    @Test func tooOldVersionAndNoServer() {
        let rows = Readiness.rows(problems: [], serverReached: false, minVersion: "99.0", clockOffset: nil,
                                  freeBytes: nil, battery: nil, vpn: [], accessibility: false)
        let byId = Dictionary(grouping: rows, by: \.id)
        #expect(byId["app_version"]?.first?.ok == false)
        #expect(byId["server"]?.first?.ok == false)
        #expect(byId["clock"]?.first?.info == true)
        #expect(byId["accessibility"]?.first?.ok == false)
        #expect(byId["battery"] == nil)
    }
}
