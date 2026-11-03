import Testing
import Foundation
import CryptoKit
@testable import Proctora

/// Opt-in TEST TARGET ONLY: native Swift HTTP interoperability with the local
/// admission simulator. This does not invoke or attest to macOS lockdown.
@Suite struct UniversityFixtureIntegrationTests {
    @Test(.enabled(if: ProcessInfo.processInfo.environment["FAIRPLAY_FIXTURE_PORT"] != nil))
    func simulatedNativeAdmissionOverLoopback() async throws {
        let client = try FixtureClient()
        defer { client.close() }
        let profile = try await client.request("GET", "/api/university/profile")
        try #require(profile.status == 200)
        try #require(profile.body["mode"] as? String == "mock_only")
        try #require(profile.body["production_gate_verified"] as? Bool == false)
        let launches = try #require(profile.body["protected_launches"] as? [[String: Any]])
        let launchURL = try #require(launches.first?["launch_url"] as? String)
        let launch = try await client.request("POST", "/mock-lms/launch", body: [
            "launch_url": launchURL, "device": client.device, "session": client.binding
        ], student: true)
        #expect(launch.status == 200)
        #expect(launch.body["state"] as? String == "pending")
        #expect(launch.body["questions"] == nil)
        let aid = try #require(launch.body["id"] as? String)
        let nonce = try #require(launch.body["nonce"] as? String)
        let student = try #require(launch.body["student"] as? String)
        guard FixtureClient.identifier(aid), FixtureClient.identifier(nonce), FixtureClient.identifier(student)
        else { throw FixtureClient.Failure.invalidResponse }
        let questionPath = "/mock-lms/attempts/\(aid)/questions"
        let beforeLock = try await client.request("GET", questionPath)
        #expect(beforeLock.status == 403)
        let failedLock = try await client.request("POST", "/api/attempts/\(aid)/lock", body: [
            "locked": false, "nonce": nonce, "proof": "simulated-lock-failure"
        ])
        #expect(failedLock.status == 403)
        let afterFailure = try await client.request("GET", questionPath)
        #expect(afterFailure.status == 403)

        // Deliberate simulator, NOT a LockdownController result or attestation.
        let simulatedLockSucceeded = true
        let message = ["fairplay.fixture.lock.v1", aid, student, client.binding,
                       client.device, "1", nonce, simulatedLockSucceeded ? "1" : "0"].joined(separator: "|")
        let proof = HMAC<SHA256>.authenticationCode(for: Data(message.utf8),
            using: SymmetricKey(data: Data(client.nativeKey.utf8)))
            .map { String(format: "%02x", $0) }.joined()
        let locked = try await client.request("POST", "/api/attempts/\(aid)/lock", body: [
            "locked": simulatedLockSucceeded, "nonce": nonce, "proof": proof
        ])
        #expect(locked.status == 200)
        let admission = try #require(locked.body["admission"] as? String)
        let beforeAdmission = try await client.request("GET", questionPath)
        #expect(beforeAdmission.status == 403)
        let admitted = try await client.request("POST", "/mock-lms/admit", body: ["admission": admission])
        #expect(admitted.status == 200)
        let replay = try await client.request("POST", "/mock-lms/admit", body: ["admission": admission])
        #expect(replay.status == 403)
        let questions = try await client.request("GET", questionPath)
        #expect(questions.status == 200)
        #expect((questions.body["questions"] as? [[String: Any]])?.count == 1)
        let submitted = try await client.request("POST", "/mock-lms/attempts/\(aid)/submit")
        #expect(submitted.status == 200)
        #expect(submitted.body["state"] as? String == "submitted")
        #expect(submitted.body["receipt"] is String)
        let afterSubmit = try await client.request("GET", questionPath)
        #expect(afterSubmit.status == 403)
    }
}

/// No arbitrary URL, redirects, cookies, persisted credentials, proxy, or disk cache.
/// Only random throwaway fixture keys belong in these environment variables.
private final class FixtureClient: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    enum Failure: Error { case invalidConfiguration, invalidResponse }
    let nativeKey: String
    private let studentKey: String
    private let base: String
    let device = "swift_fixture_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    let binding = "session_" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    private var transport: URLSession!

    init(environment env: [String: String] = ProcessInfo.processInfo.environment) throws {
        guard let raw = env["FAIRPLAY_FIXTURE_PORT"], let port = Int(raw), (1024...65535).contains(port),
              let native = env["FAIRPLAY_FIXTURE_NATIVE_KEY"], native.count >= 24,
              let student = env["FAIRPLAY_FIXTURE_STUDENT_KEY"], student.count >= 24 else {
            throw Failure.invalidConfiguration
        }
        nativeKey = native
        studentKey = student
        base = "http://127.0.0.1:\(port)"
        super.init()
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 5
        config.timeoutIntervalForResource = 10
        config.httpShouldSetCookies = false
        config.httpCookieStorage = nil
        config.urlCredentialStorage = nil
        config.urlCache = nil
        config.connectionProxyDictionary = [:]
        transport = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }

    func close() { transport.invalidateAndCancel() }

    static func identifier(_ value: String) -> Bool {
        !value.isEmpty && value.count <= 128 && value.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
        }
    }

    func request(_ method: String, _ path: String, body: [String: Any]? = nil,
                 student: Bool = false) async throws -> (status: Int, body: [String: Any]) {
        guard path.hasPrefix("/"), !path.hasPrefix("//"), !path.contains(".."),
              let url = URL(string: base + path), url.host == "127.0.0.1" else {
            throw Failure.invalidConfiguration
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue(student ? studentKey : nativeKey,
                         forHTTPHeaderField: student ? "x-fixture-student" : "x-fixture-native")
        request.setValue(binding, forHTTPHeaderField: "x-app-session")
        request.setValue(device, forHTTPHeaderField: "x-device")
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await transport.data(for: request)
        guard let http = response as? HTTPURLResponse, data.count <= 1_048_576,
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { throw Failure.invalidResponse }
        return (http.statusCode, json)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}
